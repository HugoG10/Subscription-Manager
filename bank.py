"""Find recurring subscriptions in bank charges.

A charge is money that already left an account. Passwords and account
numbers never reach this module. Callers hand it dates, amounts, and
the text printed on the statement.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from model import CATEGORIES, money

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d")
INCOME_WORDS = (
    "PAYROLL",
    "DIRECT DEP",
    "DIR DEP",
    "SALARY",
    "TAX REFUND",
    "INTEREST",
    "DIVIDEND",
)
SKIP_WORDS = (
    "ATM",
    "ZELLE",
    "TRANSFER",
    "CASH WITHDRAWAL",
    "PAYMENT THANK YOU",
    "CREDIT CARD PAYMENT",
    "AUTOPAY PAYMENT",
    "ONLINE PAYMENT",
    "VENMO",
    "CASH APP",
)
EVERYDAY_WORDS = (
    "MCDONALD",
    "BURGER KING",
    "WENDY",
    "TACO BELL",
    "CHICK FIL",
    "CHIPOTLE",
    "STARBUCKS",
    "DUNKIN",
    "DOORDASH",
    "UBER EATS",
    "GRUBHUB",
    "BUFFALO WILD",
    "PIZZA",
    "CAESAR",
    "PANERA",
    "CHEVRON",
    "CAR WASH",
    "CARWASH",
    "AMAZON",
    "WALMART",
    "TARGET",
    "COSTCO",
)
BRANDS = (
    ("NETFLIX", "Netflix", "streaming"),
    ("SPOTIFY", "Spotify", "music"),
    ("HULU", "Hulu", "streaming"),
    ("DISNEY PLUS", "Disney+", "streaming"),
    ("DISNEYPLUS", "Disney+", "streaming"),
    ("YOUTUBE", "YouTube", "streaming"),
    ("HBO", "Max", "streaming"),
    ("ADOBE", "Adobe", "software"),
    ("ICLOUD", "iCloud+", "cloud"),
    ("APPLE.COM", "Apple", "software"),
    ("GOOGLE ONE", "Google One", "cloud"),
    ("GOOGLE STORAGE", "Google One", "cloud"),
    ("DROPBOX", "Dropbox", "cloud"),
    ("MICROSOFT", "Microsoft", "software"),
    ("NOTION", "Notion", "software"),
    ("OPENAI", "ChatGPT", "software"),
    ("CHATGPT", "ChatGPT", "software"),
    ("GITHUB", "GitHub", "software"),
    ("PLANET FITNESS", "Planet Fitness", "fitness"),
    ("EOS FITNESS", "EOS Fitness", "fitness"),
    ("GOLD'S GYM", "Gold's Gym", "fitness"),
    ("GOLDS GYM", "Gold's Gym", "fitness"),
    ("LA FITNESS", "LA Fitness", "fitness"),
    ("ANYTIME FITNESS", "Anytime Fitness", "fitness"),
    ("ORANGETHEORY", "Orangetheory", "fitness"),
    ("ORANGE THEORY", "Orangetheory", "fitness"),
    ("CRUNCH FITNESS", "Crunch", "fitness"),
    ("EQUINOX", "Equinox", "fitness"),
    ("YMCA", "YMCA", "fitness"),
    ("NYTIMES", "The New York Times", "news"),
    ("NEW YORK TIMES", "The New York Times", "news"),
    ("AMAZON PRIME", "Amazon Prime", "shopping"),
    ("PRIME VIDEO", "Prime Video", "streaming"),
    ("HEADSPACE", "Headspace", "fitness"),
    ("PLAYSTATION", "PlayStation", "gaming"),
    ("XBOX", "Xbox", "gaming"),
    ("STEAMGAMES", "Steam", "gaming"),
    ("STEAM", "Steam", "gaming"),
)
PLAID_SKIP = {"INCOME", "TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS", "BANK_FEES"}
PLAID_CATEGORY = {
    "ENTERTAINMENT": "streaming",
    "GENERAL_MERCHANDISE": "shopping",
    "RENT_AND_UTILITIES": "other",
    "PERSONAL_CARE": "other",
    "GENERAL_SERVICES": "software",
    "HOME_IMPROVEMENT": "other",
}


@dataclass(frozen=True)
class Charge:
    id: str
    posted: date
    amount: Decimal
    description: str


def parse_statement(text: str) -> list[Charge]:
    """Read a bank CSV. Only rows that look like spending are returned."""
    if len(text) > 1_000_000:
        raise ValueError("That statement is too large.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if len(rows) < 2:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    date_at = _column(header, ("date", "posted date", "post date", "transaction date", "trans date"))
    name_at = _column(header, ("description", "memo", "payee", "name", "transaction", "details", "narrative"))
    amount_at = _column(header, ("amount", "amt"))
    debit_at = _column(header, ("debit", "withdrawal", "outflow"))
    type_at = _column(header, ("type", "transaction type", "dr/cr"))
    if date_at is None or name_at is None or (amount_at is None and debit_at is None):
        raise ValueError("Use a CSV with date, description, and amount columns.")

    signed: list[tuple[date, str, Decimal, str]] = []
    for row in rows[1:]:
        posted = _date(row, date_at)
        description = row[name_at].strip() if name_at < len(row) else ""
        if posted is None or not description:
            continue
        kind = row[type_at].strip().lower() if type_at is not None and type_at < len(row) else ""
        if debit_at is not None:
            amount = _money(row, debit_at)
            if amount is None or amount == 0:
                continue
            signed.append((posted, description, abs(amount), "debit"))
            continue
        amount = _money(row, amount_at)
        if amount is None or amount == 0:
            continue
        signed.append((posted, description, amount, kind))

    negatives = any(amount < 0 for _, _, amount, kind in signed if kind != "debit")
    charges = []
    for posted, description, amount, kind in signed:
        if kind == "debit":
            spent = amount
        elif negatives:
            if amount >= 0:
                continue
            spent = abs(amount)
        else:
            if kind in {"credit", "deposit", "income", "cr"} or _looks_like_income(description):
                continue
            spent = abs(amount)
        charges.append(
            Charge(
                id=_charge_id(posted, amount, description),
                posted=posted,
                amount=money(spent),
                description=description[:180],
            )
        )
    return charges


def detect_recurring(charges: list[Charge], today: date | None = None, skip_keys: set[str] | None = None) -> list[dict]:
    """Group repeated charges into subscriptions."""
    today = today or date.today()
    blocked = skip_keys or set()
    groups: dict[str, list[Charge]] = {}
    for charge in charges:
        if charge.amount <= 0 or _skipped(charge.description):
            continue
        key = _merchant_key(charge.description)
        if len(key) < 3 or key in blocked:
            continue
        groups.setdefault(key, []).append(charge)

    plans = []
    for key, grouped in groups.items():
        plan = _plan_from_group(key, grouped, today)
        if plan is not None:
            plans.append(plan)
    plans.sort(key=lambda plan: plan["name"])
    return plans


def plans_from_plaid_streams(streams: list[dict], today: date | None = None) -> list[dict]:
    """Turn Plaid outflow streams into the same plan shape as statement detection."""
    today = today or date.today()
    plans = []
    for stream in streams:
        if not stream.get("is_active", True):
            continue
        status = str(stream.get("status") or "").upper()
        if status in {"TOMBSTONED", "UNKNOWN"}:
            continue
        frequency = str(stream.get("frequency") or "")
        frequency = frequency.split(".")[-1].upper()
        cadence = _plaid_cadence(frequency)
        if cadence is None:
            continue
        cycle, multiplier, note = cadence
        amount = _plain_amount(stream.get("average_amount"))
        if amount is None or amount <= 0:
            continue
        primary = _plaid_primary(stream)
        raw_name = str(stream.get("merchant_name") or stream.get("description") or "").strip()
        if primary in PLAID_SKIP or _not_a_subscription_category(primary, raw_name):
            continue
        if not raw_name or _skipped(raw_name):
            continue
        name, category = _display(raw_name)
        if category == "other":
            category = PLAID_CATEGORY.get(primary, "other")
        if category not in CATEGORIES:
            category = "other"
        renewal = _as_date(stream.get("predicted_next_date")) or _as_date(stream.get("last_date"))
        if renewal is None:
            continue
        if renewal < today:
            renewal = _roll_forward(renewal, _gap_for_cycle(cycle), today)
        stream_id = str(stream.get("stream_id") or raw_name)
        plans.append(
            {
                "external_id": f"plaid:{stream_id}"[:80],
                "name": name[:60],
                "category": category,
                "cost": money(amount * multiplier),
                "cycle": cycle,
                "renewal_date": renewal,
                "status": "active",
                "notes": note[:240],
            }
        )
    return plans


def _plan_from_group(key: str, charges: list[Charge], today: date) -> dict | None:
    ordered = sorted(charges, key=lambda charge: charge.posted)
    if len(ordered) < 2 or _everyday_merchant(ordered[-1].description):
        return None
    minimum = 2 if _membership_merchant(ordered[-1].description) else 3
    for members in _amount_clusters(ordered):
        if len(members) < minimum:
            continue
        plan = _plan_from_regular_charges(key, members, today, _median_decimal([item.amount for item in members]))
        if plan is not None:
            return plan
    if len(ordered) < 4:
        return None
    gaps = _gaps(ordered)
    matched = _dominant_gaps(gaps)
    if matched is None:
        return None
    gap, low, high = matched
    amount = ordered[-1].amount if low <= gaps[-1] <= high else _median_decimal([item.amount for item in ordered])
    plan = _plan_from_regular_charges(key, ordered, today, amount, gap)
    if plan is None:
        return None
    plan["notes"] = "Found on the linked bank account. The latest charge is the current price."
    return plan


def _plan_from_regular_charges(
    key: str,
    ordered: list[Charge],
    today: date,
    amount: Decimal,
    gap: int | None = None,
) -> dict | None:
    if amount < Decimal("0.50"):
        return None
    if gap is None:
        gaps = _gaps(ordered)
        gap = int(round(_median_decimal([Decimal(item) for item in gaps])))
    cadence = _gap_cadence(gap)
    if cadence is None:
        return None
    cycle, multiplier, note = cadence
    name, category = _display(ordered[-1].description)
    return {
        "external_id": merchant_external_id(key),
        "name": name[:60],
        "category": category,
        "cost": money(amount * multiplier),
        "cycle": cycle,
        "renewal_date": _roll_forward(ordered[-1].posted, gap, today),
        "status": "active",
        "notes": note[:240],
    }


def merchant_external_id(key: str) -> str:
    return f"detected:{hashlib.sha256(key.encode()).hexdigest()[:24]}"


def without_dismissed(plans: list[dict], keys: set[str]) -> list[dict]:
    blocked = set(keys)
    hidden_ids = {merchant_external_id(key) for key in blocked}
    return [
        plan
        for plan in plans
        if plan["external_id"] not in hidden_ids and _merchant_key(plan["name"]) not in blocked
    ]


def review_merchants(
    charges: list[Charge],
    today: date | None = None,
    dismissed: set[str] | None = None,
    tracked_ids: set[str] | None = None,
) -> list[dict]:
    """Group account charges so a person can keep or hide each merchant."""
    today = today or date.today()
    hidden = dismissed or set()
    tracked = tracked_ids or set()
    groups: dict[str, list[Charge]] = {}
    for charge in charges:
        if charge.amount <= 0 or _skipped(charge.description):
            continue
        key = _merchant_key(charge.description)
        if len(key) < 3:
            continue
        groups.setdefault(key, []).append(charge)
    likely_ids = {plan["external_id"] for plan in detect_recurring(charges, today)}
    rows = []
    for key, grouped in groups.items():
        ordered = sorted(grouped, key=lambda charge: charge.posted)
        external_id = merchant_external_id(key)
        name, category = _display(ordered[-1].description)
        shown = list(reversed(ordered))
        rows.append(
            {
                "key": key,
                "name": name,
                "category": category,
                "count": len(ordered),
                "latest": ordered[-1].posted.isoformat(),
                "amount": money(ordered[-1].amount),
                "likely": external_id in likely_ids,
                "tracked": external_id in tracked,
                "dismissed": key in hidden,
                "charges": [
                    {"posted": charge.posted.isoformat(), "amount": money(charge.amount)}
                    for charge in shown
                ],
            }
        )
    rows.sort(key=lambda row: row["latest"], reverse=True)
    rows.sort(key=lambda row: (row["dismissed"], not row["likely"]))
    return rows


def subscription_from_merchant(charges: list[Charge], today: date | None = None) -> dict | None:
    """Build one subscription from a single merchant, including a one-time charge."""
    today = today or date.today()
    found = detect_recurring(charges, today)
    if found:
        return found[0]
    ordered = sorted((charge for charge in charges if charge.amount > 0), key=lambda charge: charge.posted)
    if not ordered or _skipped(ordered[-1].description):
        return None
    last = ordered[-1]
    key = _merchant_key(last.description)
    if len(key) < 3:
        return None
    name, category = _display(last.description)
    return {
        "external_id": merchant_external_id(key),
        "name": name[:60],
        "category": category,
        "cost": money(last.amount),
        "cycle": "monthly",
        "renewal_date": _roll_forward(last.posted, 30, today),
        "status": "active",
        "notes": "Marked from a charge on the linked account.",
    }


def _amount_clusters(ordered: list[Charge]) -> list[list[Charge]]:
    seen: set[tuple[str, ...]] = set()
    clusters = []
    for center in ordered:
        tolerance = max(Decimal("1.00"), center.amount * Decimal("0.20"))
        members = [charge for charge in ordered if abs(charge.amount - center.amount) <= tolerance]
        identity = tuple(charge.id for charge in members)
        if len(members) < 2 or identity in seen:
            continue
        seen.add(identity)
        clusters.append(members)
    clusters.sort(key=lambda members: (len(members), members[-1].posted), reverse=True)
    return clusters


def _gaps(ordered: list[Charge]) -> list[int]:
    return [(ordered[index].posted - ordered[index - 1].posted).days for index in range(1, len(ordered))]


def _dominant_gaps(gaps: list[int]) -> tuple[int, int, int] | None:
    best: tuple[int, int, int, int] | None = None
    for low, high in ((6, 9), (12, 17), (26, 35), (80, 100), (170, 200), (330, 400)):
        matched = [gap for gap in gaps if low <= gap <= high]
        if len(matched) < 3 or len(matched) * 3 < len(gaps) * 2:
            continue
        gap = int(round(_median_decimal([Decimal(item) for item in matched])))
        if best is None or len(matched) > best[0]:
            best = (len(matched), gap, low, high)
    if best is None:
        return None
    return best[1], best[2], best[3]


def _gap_cadence(gap: int) -> tuple[str, Decimal, str] | None:
    if 6 <= gap <= 9:
        return "weekly", Decimal(1), "Found on your bank statement. Charged about every week."
    if 12 <= gap <= 17:
        return "monthly", Decimal(26) / Decimal(12), "Found on your bank statement. Charged about every two weeks."
    if 26 <= gap <= 35:
        return "monthly", Decimal(1), "Found on your bank statement."
    if 80 <= gap <= 100:
        return "yearly", Decimal(4), "Found on your bank statement. Charged about every three months."
    if 170 <= gap <= 200:
        return "yearly", Decimal(2), "Found on your bank statement. Charged about twice a year."
    if 330 <= gap <= 400:
        return "yearly", Decimal(1), "Found on your bank statement."
    return None


def _plaid_cadence(frequency: str) -> tuple[str, Decimal, str] | None:
    if frequency == "WEEKLY":
        return "weekly", Decimal(1), "Found on the linked bank account. Charged about every week."
    if frequency == "BIWEEKLY":
        return "monthly", Decimal(26) / Decimal(12), "Found on the linked bank account. Charged about every two weeks."
    if frequency == "SEMI_MONTHLY":
        return "monthly", Decimal(2), "Found on the linked bank account. Charged twice a month."
    if frequency == "MONTHLY":
        return "monthly", Decimal(1), "Found on the linked bank account."
    if frequency == "ANNUALLY":
        return "yearly", Decimal(1), "Found on the linked bank account."
    return None


def _gap_for_cycle(cycle: str) -> int:
    if cycle == "weekly":
        return 7
    if cycle == "yearly":
        return 365
    return 30


def _roll_forward(last: date, gap: int, today: date) -> date:
    gap = max(gap, 1)
    upcoming = last + timedelta(days=gap)
    for _ in range(48):
        if upcoming >= today:
            return upcoming
        upcoming += timedelta(days=gap)
    return upcoming


def _display(description: str) -> tuple[str, str]:
    compact = re.sub(r"[^A-Z0-9]+", " ", description.upper())
    squashed = compact.replace(" ", "")
    for needle, label, category in BRANDS:
        if needle.replace(" ", "") in squashed or needle in compact:
            return label, category
    words = [word.capitalize() for word in _merchant_key(description).split() if word]
    label = " ".join(words[:6]) or "Subscription"
    return label[:60], "other"


def _merchant_key(description: str) -> str:
    text = description.upper()
    text = re.sub(r"\b\d{2}[/-]\d{2}(?:[/-]\d{2,4})?\b", " ", text)
    text = re.sub(r"\b\d{3,}\b", " ", text)
    text = re.sub(r"[^A-Z ]+", " ", text)
    for prefix in ("POS ", "DEBIT ", "PURCHASE ", "RECURRING ", "CHECKCARD ", "ACH ", "VISA "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return re.sub(r"\s+", " ", text).strip()


def _skipped(description: str) -> bool:
    if _display(description)[1] != "other" and _known_brand(description):
        return False
    text = description.upper()
    return any(word in text for word in SKIP_WORDS) or _looks_like_income(description)


def _everyday_merchant(description: str) -> bool:
    if _known_brand(description):
        return False
    text = _merchant_key(description)
    return any(word in text for word in EVERYDAY_WORDS)


def _membership_merchant(description: str) -> bool:
    if _known_brand(description):
        return True
    text = _merchant_key(description)
    return "GYM" in text.split() or "FITNESS" in text.split()


def _not_a_subscription_category(primary: str, name: str) -> bool:
    return primary in {"FOOD_AND_DRINK", "TRANSPORTATION"} and not _known_brand(name)


def _known_brand(description: str) -> bool:
    compact = re.sub(r"[^A-Z0-9]+", "", description.upper())
    return any(needle.replace(" ", "") in compact for needle, _, _ in BRANDS)


def _looks_like_income(description: str) -> bool:
    text = description.upper()
    return any(word in text for word in INCOME_WORDS)


def _column(header: list[str], names: tuple[str, ...]) -> int | None:
    for name in names:
        if name in header:
            return header.index(name)
    return None


def _date(row: list[str], index: int) -> date | None:
    if index >= len(row):
        return None
    raw = row[index].strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _money(row: list[str], index: int | None) -> Decimal | None:
    if index is None or index >= len(row):
        return None
    raw = row[index].strip()
    if not raw:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.replace("$", "").replace(",", "").replace("(", "").replace(")", "").strip()
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    if negative:
        amount = -abs(amount)
    return amount


def _charge_id(posted: date, amount: Decimal, description: str) -> str:
    digest = hashlib.sha256(f"{posted.isoformat()}|{amount}|{description}".encode()).hexdigest()
    return digest[:24]


def _median_decimal(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _plain_amount(value: object) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("amount")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _plaid_primary(stream: dict) -> str:
    category = stream.get("personal_finance_category") or {}
    if isinstance(category, dict):
        return str(category.get("primary") or "").upper()
    return ""
