"""Subscription rules shared by the API.

Costs are stored as dollars with cent precision. Weekly plans count as 52
charges a year. Yearly plans are split across 12 months. Paused and canceled
plans stay in the list and stay out of the spending totals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

CATEGORIES = {
    "streaming",
    "music",
    "software",
    "cloud",
    "news",
    "fitness",
    "gaming",
    "shopping",
    "other",
}
CYCLES = {"weekly", "monthly", "yearly"}
STATUSES = {"active", "trial", "paused", "canceled"}
USAGES = {"daily", "weekly", "monthly", "rarely", "never"}
REMINDER_DAYS = {1, 3, 7, 14, 30}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONEY_QUANTUM = Decimal("0.01")

# id, name, category, cost, cycle, days until renewal, status,
# reminder days, usage, days since last use (None when unknown), notes
SAMPLE_ROWS = [
    ("seed-adobe", "Adobe Creative Cloud", "software", "59.99", "monthly", 2, "active", 7, "rarely", -45, "For the photography class"),
    ("seed-netflix", "Netflix", "streaming", "15.49", "monthly", 3, "active", 7, "weekly", -2, ""),
    ("seed-youtube", "YouTube Premium", "streaming", "13.99", "monthly", 4, "trial", 7, "weekly", -1, "Cancel if I am not watching"),
    ("seed-fitness", "Planet Fitness", "fitness", "24.99", "monthly", 6, "active", 7, "never", None, "Joined in January"),
    ("seed-chatgpt", "ChatGPT Plus", "software", "20.00", "monthly", 9, "active", 7, "daily", 0, ""),
    ("seed-notion", "Notion", "software", "10.00", "monthly", 11, "active", 7, "weekly", -4, ""),
    ("seed-nyt", "The New York Times", "news", "17.00", "monthly", 15, "active", 7, "monthly", -10, ""),
    ("seed-spotify", "Spotify", "music", "11.99", "monthly", 19, "active", 7, "daily", 0, ""),
    ("seed-icloud", "iCloud+", "cloud", "2.99", "monthly", 24, "active", 3, "daily", 0, ""),
    ("seed-disney", "Disney+", "streaming", "139.99", "yearly", 27, "active", 14, "rarely", -60, ""),
    ("seed-prime", "Amazon Prime", "shopping", "139.00", "yearly", 50, "active", 14, "weekly", -6, ""),
    ("seed-headspace", "Headspace", "fitness", "69.99", "yearly", 12, "paused", 7, "rarely", -70, "Paused until winter break"),
]


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class Subscription:
    id: str
    name: str
    category: str
    cost: Decimal
    cycle: str
    renewal_date: date
    status: str
    reminder_days: int
    usage: str
    last_used: date | None
    notes: str

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "cost": float(self.cost),
            "cycle": self.cycle,
            "renewalDate": self.renewal_date.isoformat(),
            "status": self.status,
            "reminderDays": self.reminder_days,
            "usage": self.usage,
            "lastUsed": self.last_used.isoformat() if self.last_used else "",
            "notes": self.notes,
        }


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def monthly_equivalent(subscription: Subscription) -> Decimal:
    if subscription.cycle == "weekly":
        return subscription.cost * Decimal(52) / Decimal(12)
    if subscription.cycle == "yearly":
        return subscription.cost / Decimal(12)
    return subscription.cost


def yearly_equivalent(subscription: Subscription) -> Decimal:
    if subscription.cycle == "weekly":
        return subscription.cost * Decimal(52)
    if subscription.cycle == "yearly":
        return subscription.cost
    return subscription.cost * Decimal(12)


def is_billable(subscription: Subscription) -> bool:
    return subscription.status in {"active", "trial"}


def is_idle(subscription: Subscription) -> bool:
    return is_billable(subscription) and subscription.usage in {"rarely", "never"}


def days_until(renewal: date, today: date) -> int:
    return (renewal - today).days


def is_reminder(subscription: Subscription, today: date) -> bool:
    if not is_billable(subscription):
        return False
    return days_until(subscription.renewal_date, today) <= subscription.reminder_days


def summarize(subscriptions: list[Subscription], today: date | None = None) -> dict:
    today = today or date.today()
    active = [item for item in subscriptions if item.status == "active"]
    trials = [item for item in subscriptions if item.status == "trial"]
    idle = [item for item in subscriptions if is_idle(item)]
    reminders = [item for item in subscriptions if is_reminder(item, today)]
    monthly = sum((monthly_equivalent(item) for item in active), Decimal(0))
    yearly = sum((yearly_equivalent(item) for item in active), Decimal(0))
    trial_monthly = sum((monthly_equivalent(item) for item in trials), Decimal(0))
    savings = sum((monthly_equivalent(item) for item in idle), Decimal(0))
    return {
        "monthly": float(money(monthly)),
        "yearly": float(money(yearly)),
        "trialMonthly": float(money(trial_monthly)),
        "potentialSavings": float(money(savings)),
        "activeCount": len(active),
        "trialCount": len(trials),
        "reminderCount": len(reminders),
        "idleCount": len(idle),
    }


def sample_subscriptions(today: date | None = None) -> list[Subscription]:
    today = today or date.today()
    subscriptions = []
    for row in SAMPLE_ROWS:
        (
            subscription_id,
            name,
            category,
            cost,
            cycle,
            renewal_in,
            status,
            reminder_days,
            usage,
            last_used_in,
            notes,
        ) = row
        last_used = None if last_used_in is None else today + timedelta(days=last_used_in)
        subscriptions.append(
            Subscription(
                id=subscription_id,
                name=name,
                category=category,
                cost=Decimal(cost),
                cycle=cycle,
                renewal_date=today + timedelta(days=renewal_in),
                status=status,
                reminder_days=reminder_days,
                usage=usage,
                last_used=last_used,
                notes=notes,
            )
        )
    return subscriptions


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not DATE_PATTERN.match(value):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return parsed


def subscription_from_payload(payload: object, subscription_id: str | None = None) -> Subscription:
    if not isinstance(payload, dict):
        raise ValidationError(["Expected a subscription object."])

    errors: list[str] = []
    name = payload.get("name", "")
    if not isinstance(name, str) or not name.strip():
        errors.append("Enter a name.")
        clean_name = ""
    else:
        clean_name = name.strip()
        if len(clean_name) > 60:
            errors.append("Name has to be 60 characters or fewer.")

    cost = payload.get("cost", None)
    clean_cost = Decimal(0)
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost != cost:
        errors.append("Enter a price.")
    else:
        try:
            clean_cost = money(Decimal(str(cost)))
        except Exception:
            errors.append("Enter a price.")
        else:
            if clean_cost < 0 or clean_cost > Decimal("999999.99"):
                errors.append("Enter a price from $0 to $999,999.99.")

    category = payload.get("category")
    if category not in CATEGORIES:
        errors.append("Choose a category.")

    cycle = payload.get("cycle")
    if cycle not in CYCLES:
        errors.append("Choose a billing cycle.")

    status = payload.get("status")
    if status not in STATUSES:
        errors.append("Choose a status.")

    renewal = _parse_date(payload.get("renewalDate"))
    if renewal is None:
        errors.append("Choose a renewal date.")

    reminder_days = payload.get("reminderDays")
    if isinstance(reminder_days, bool) or reminder_days not in REMINDER_DAYS:
        errors.append("Choose when to be reminded.")

    usage = payload.get("usage")
    if usage not in USAGES:
        errors.append("Choose how often you use it.")

    last_used_raw = payload.get("lastUsed", "")
    last_used = None
    if last_used_raw not in ("", None):
        last_used = _parse_date(last_used_raw)
        if last_used is None:
            errors.append("Choose a valid last-used date.")
        elif last_used > date.today():
            errors.append("Last used has to be today or earlier.")

    notes = payload.get("notes", "")
    clean_notes = ""
    if not isinstance(notes, str):
        errors.append("Notes have to be text.")
    else:
        clean_notes = notes.strip()
        if len(clean_notes) > 240:
            errors.append("Notes have to be 240 characters or fewer.")

    if errors or renewal is None:
        raise ValidationError(errors or ["Choose a renewal date."])

    return Subscription(
        id=subscription_id or str(uuid4()),
        name=clean_name,
        category=str(category),
        cost=clean_cost,
        cycle=str(cycle),
        renewal_date=renewal,
        status=str(status),
        reminder_days=int(reminder_days),
        usage=str(usage),
        last_used=last_used,
        notes=clean_notes,
    )
