"""Count registered Wallet Worth accounts from stored user records.

A registration is one database account. Logging in again does not create
another. Accounts are not assumed to be unique people. Email verification
is not offered, so the verified count stays at the stored flag.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

GOAL = 200
MILESTONES = (25, 50, 100, 150, 200)
TEST_SUFFIXES = ("@example.com", "@example.org", "@example.net")
SOURCES = (
    "linkedin",
    "reddit",
    "instagram",
    "tiktok",
    "discord",
    "github",
    "direct",
    "referral",
    "other",
)

METHODOLOGY = (
    "Eligible registrations are accounts that are not marked excluded and do "
    "not use a reserved test domain (example.com, example.org, example.net). "
    "Each account is counted once. Logins do not add to the total. Deleted "
    "accounts are gone from the database and drop out of the count. "
    "Email verification is not available, so verified users are only those "
    "with emailVerified set, which this app does not set. Visitor counts are "
    "not collected."
)


def clean_acquisition(raw: object) -> dict:
    """Keep a small first-touch record. Unknown sources stay labeled other."""
    source = "direct"
    medium = ""
    campaign = ""
    content = ""
    term = ""
    if isinstance(raw, dict):
        candidate = str(raw.get("source") or "").strip().lower()[:40]
        if candidate in SOURCES:
            source = candidate
        elif candidate:
            source = "other"
        medium = _clip(raw.get("medium"))
        campaign = _clip(raw.get("campaign"))
        content = _clip(raw.get("content"))
        term = _clip(raw.get("term"))
    return {
        "source": source,
        "medium": medium,
        "campaign": campaign,
        "content": content,
        "term": term,
    }


def is_test_email(email: str) -> bool:
    lowered = (email or "").strip().lower()
    return any(lowered.endswith(suffix) for suffix in TEST_SUFFIXES)


def is_excluded(user: dict) -> bool:
    if user.get("excluded") is True:
        return True
    return is_test_email(str(user.get("email") or ""))


def is_eligible(user: dict) -> bool:
    return not is_excluded(user)


def source_of(user: dict) -> str:
    acquisition = user.get("acquisition")
    if not isinstance(acquisition, dict):
        return "unknown"
    source = str(acquisition.get("source") or "").strip().lower()
    if source in SOURCES:
        return source
    return "unknown"


def build_report(users: list[dict], now: datetime) -> dict:
    """Aggregate real account documents. This does not invent dates."""
    now = _aware(now)
    today = now.date()
    week_start = today - timedelta(days=6)
    month_start = today.replace(day=1)
    active_after = now - timedelta(days=30)
    total = len(users)
    verified = 0
    eligible_users = []
    excluded = 0
    undated = 0
    active = 0
    today_count = 0
    week_count = 0
    month_count = 0
    daily: dict[str, int] = {}
    monthly: dict[str, int] = {}
    sources: dict[str, dict[str, int]] = {}
    campaigns: dict[str, int] = {}

    for user in users:
        if user.get("emailVerified") is True:
            verified += 1
        seen = user.get("lastSeenAt")
        if isinstance(seen, datetime) and _aware(seen) >= active_after:
            active += 1
        source = source_of(user)
        bucket = sources.setdefault(source, {"registrations": 0, "eligible": 0})
        bucket["registrations"] += 1
        eligible = is_eligible(user)
        if eligible:
            bucket["eligible"] += 1
            campaign = ""
            acquisition = user.get("acquisition")
            if isinstance(acquisition, dict):
                campaign = str(acquisition.get("campaign") or "").strip()
            if campaign:
                campaigns[campaign] = campaigns.get(campaign, 0) + 1
        else:
            excluded += 1
            continue
        created = user.get("createdAt")
        if not isinstance(created, datetime):
            undated += 1
            eligible_users.append({"createdAt": None, "id": user.get("_id") or user.get("id")})
            continue
        created = _aware(created)
        eligible_users.append({"createdAt": created, "id": user.get("_id") or user.get("id")})
        created_day = created.date()
        if created_day == today:
            today_count += 1
        if week_start <= created_day <= today:
            week_count += 1
        if created_day >= month_start and created_day <= today:
            month_count += 1
        if today - timedelta(days=89) <= created_day <= today:
            key = created_day.isoformat()
            daily[key] = daily.get(key, 0) + 1
        month_key = created.strftime("%Y-%m")
        monthly[month_key] = monthly.get(month_key, 0) + 1

    dated = sorted(
        (item for item in eligible_users if item["createdAt"] is not None),
        key=lambda item: item["createdAt"],
    )
    eligible_count = len(eligible_users)
    milestones = []
    for goal in MILESTONES:
        if eligible_count < goal:
            milestones.append({"goal": goal, "reached": False, "reachedAt": None})
            continue
        if len(dated) >= goal:
            milestones.append(
                {
                    "goal": goal,
                    "reached": True,
                    "reachedAt": dated[goal - 1]["createdAt"].isoformat(),
                }
            )
        else:
            milestones.append(
                {
                    "goal": goal,
                    "reached": True,
                    "reachedAt": None,
                    "note": "Reached, but some accounts have no registration time.",
                }
            )

    daily_series = []
    for offset in range(89, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        daily_series.append({"date": day, "count": daily.get(day, 0)})
    running = 0
    cumulative = []
    # Cumulative line is dated eligible accounts only, plus a starting total
    # that does not invent times for undated accounts.
    dated_by_day: dict[str, int] = {}
    for item in dated:
        key = item["createdAt"].date().isoformat()
        dated_by_day[key] = dated_by_day.get(key, 0) + 1
    if dated:
        first = dated[0]["createdAt"].date()
        cursor = first
        while cursor <= today:
            running += dated_by_day.get(cursor.isoformat(), 0)
            if cursor >= today - timedelta(days=89):
                cumulative.append({"date": cursor.isoformat(), "count": running})
            cursor += timedelta(days=1)

    weekly = []
    for start_offset in (83, 76, 69, 62, 55, 48, 41, 34, 27, 20, 13, 6):
        start = today - timedelta(days=start_offset)
        end = start + timedelta(days=6)
        count = sum(point["count"] for point in daily_series if start.isoformat() <= point["date"] <= end.isoformat())
        weekly.append({"start": start.isoformat(), "end": end.isoformat(), "count": count})

    month_series = [
        {"month": key, "count": monthly[key]} for key in sorted(monthly)
    ]
    source_rows = [
        {"source": key, "registrations": value["registrations"], "eligible": value["eligible"]}
        for key, value in sorted(sources.items(), key=lambda item: item[1]["eligible"], reverse=True)
    ]
    campaign_rows = [
        {"campaign": key, "eligible": value}
        for key, value in sorted(campaigns.items(), key=lambda item: item[1], reverse=True)
    ]
    goal_hit = next((item for item in milestones if item["goal"] == GOAL and item["reached"]), None)
    return {
        "goal": GOAL,
        "methodology": METHODOLOGY,
        "emailVerification": "not_offered",
        "visitorsCollected": False,
        "total": total,
        "verified": verified,
        "eligible": eligible_count,
        "excluded": excluded,
        "undated": undated,
        "active30": active,
        "remaining": max(0, GOAL - eligible_count),
        "percent": round((eligible_count / GOAL) * 100, 1) if GOAL else 0,
        "today": today_count,
        "week": week_count,
        "month": month_count,
        "daily": daily_series,
        "weekly": weekly,
        "monthly": month_series,
        "cumulative": cumulative,
        "milestones": milestones,
        "goalReachedAt": None if goal_hit is None else goal_hit.get("reachedAt"),
        "sources": source_rows,
        "campaigns": campaign_rows,
        "generatedAt": now.isoformat(),
    }


def export_rows(users: list[dict]) -> list[dict]:
    """Anonymous rows for a milestone export. No email addresses."""
    rows = []
    for user in users:
        created = user.get("createdAt")
        rows.append(
            {
                "id": user.get("_id") or user.get("id") or "",
                "registeredAt": created.isoformat() if isinstance(created, datetime) else "",
                "emailVerified": "yes" if user.get("emailVerified") is True else "no",
                "source": source_of(user),
                "eligible": "yes" if is_eligible(user) else "no",
            }
        )
    rows.sort(key=lambda row: row["registeredAt"])
    return rows


def _clip(value: object) -> str:
    text = str(value or "").strip()
    return text[:80]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
