"""MongoDB storage for accounts, subscriptions, and bank links.

Every subscription belongs to one account. Bank access tokens stored here
are already encrypted.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

import certifi
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError

from model import Subscription, sample_subscriptions

DEFAULT_URI = "mongodb://localhost:27017"
DEFAULT_DATABASE = "subscription_manager"
LOGIN_LIMIT = 8
LOGIN_WINDOW = timedelta(minutes=15)


class DatabaseUnavailable(Exception):
    def __init__(self):
        super().__init__(
            "Could not connect to MongoDB. Check MONGODB_URI and that Atlas allows connections from this host."
        )


def match_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _to_document(
    subscription: Subscription,
    user_id: str,
    external_id: str | None = None,
    source: str = "manual",
) -> dict:
    document = {
        "_id": subscription.id,
        "userId": user_id,
        "name": subscription.name,
        "matchName": match_key(subscription.name),
        "category": subscription.category,
        "costCents": int(subscription.cost * 100),
        "cycle": subscription.cycle,
        "renewalDate": subscription.renewal_date.isoformat(),
        "status": subscription.status,
        "reminderDays": subscription.reminder_days,
        "usage": subscription.usage,
        "lastUsed": subscription.last_used.isoformat() if subscription.last_used else None,
        "notes": subscription.notes,
        "source": source,
    }
    if external_id:
        document["externalId"] = external_id
    return document


def _from_document(document: dict) -> Subscription:
    last_used = document.get("lastUsed")
    return Subscription(
        id=str(document["_id"]),
        name=document["name"],
        category=document["category"],
        cost=Decimal(document["costCents"]) / Decimal(100),
        cycle=document["cycle"],
        renewal_date=date.fromisoformat(document["renewalDate"]),
        status=document["status"],
        reminder_days=document["reminderDays"],
        usage=document["usage"],
        last_used=date.fromisoformat(last_used) if last_used else None,
        notes=document.get("notes") or "",
        source=document.get("source") or "manual",
    )


class SubscriptionStore:
    def __init__(self, uri: str = DEFAULT_URI, database: str = DEFAULT_DATABASE):
        self.uri = uri
        self.database_name = database
        options = {"serverSelectionTimeoutMS": 10000}
        if uri.startswith("mongodb+srv"):
            options["tlsCAFile"] = certifi.where()
        self.client = MongoClient(uri, **options)
        self.db = self.client[database]
        self.subscriptions = self.db["subscriptions"]
        self.users = self.db["users"]
        self.bank_items = self.db["bank_items"]
        self.charges = self.db["charges"]
        self.attempts = self.db["login_attempts"]
        self.dismissed = self.db["dismissed_merchants"]

    def initialize(self) -> None:
        try:
            self.client.admin.command("ping")
        except ServerSelectionTimeoutError as error:
            raise DatabaseUnavailable() from error
        self.users.create_index("email", unique=True)
        self.subscriptions.create_index([("userId", 1), ("renewalDate", 1), ("name", 1)])
        self.subscriptions.create_index(
            [("userId", 1), ("externalId", 1)],
            unique=True,
            partialFilterExpression={"externalId": {"$type": "string"}},
        )
        self.charges.create_index([("userId", 1), ("posted", 1)])
        self.bank_items.create_index("userId")
        self.dismissed.create_index("userId")

    def create_user(self, email: str, password_hash: str) -> dict | None:
        user_id = uuid4().hex
        document = {
            "_id": user_id,
            "email": email,
            "passwordHash": password_hash,
            "createdAt": _now(),
        }
        try:
            self.users.insert_one(document)
        except DuplicateKeyError:
            return None
        return {"id": user_id, "email": email}

    def get_user(self, user_id: str) -> dict | None:
        document = self.users.find_one({"_id": user_id})
        if document is None:
            return None
        return {"id": document["_id"], "email": document["email"], "passwordHash": document["passwordHash"]}

    def get_user_by_email(self, email: str) -> dict | None:
        document = self.users.find_one({"email": email})
        if document is None:
            return None
        return {"id": document["_id"], "email": document["email"], "passwordHash": document["passwordHash"]}

    def login_is_limited(self, email: str) -> bool:
        document = self.attempts.find_one({"_id": email})
        if document is None or document["resetAt"] <= _now():
            return False
        return document["count"] >= LOGIN_LIMIT

    def record_login_failure(self, email: str) -> None:
        now = _now()
        document = self.attempts.find_one({"_id": email})
        if document is None or document["resetAt"] <= now:
            self.attempts.replace_one(
                {"_id": email},
                {"count": 1, "resetAt": now + LOGIN_WINDOW},
                upsert=True,
            )
            return
        self.attempts.update_one({"_id": email}, {"$inc": {"count": 1}})

    def clear_login_failures(self, email: str) -> None:
        self.attempts.delete_one({"_id": email})

    def list_subscriptions(self, user_id: str) -> list[Subscription]:
        documents = self.subscriptions.find({"userId": user_id}).sort([("renewalDate", 1), ("name", 1)])
        return [_from_document(document) for document in documents]

    def get(self, user_id: str, subscription_id: str) -> Subscription | None:
        document = self.subscriptions.find_one({"_id": subscription_id, "userId": user_id})
        return _from_document(document) if document else None

    def add(self, user_id: str, subscription: Subscription) -> Subscription:
        self.subscriptions.insert_one(_to_document(subscription, user_id))
        return subscription

    def update(self, user_id: str, subscription: Subscription) -> Subscription | None:
        existing = self.subscriptions.find_one({"_id": subscription.id, "userId": user_id})
        if existing is None:
            return None
        document = _to_document(
            subscription,
            user_id,
            existing.get("externalId"),
            existing.get("source") or "manual",
        )
        self.subscriptions.replace_one({"_id": subscription.id, "userId": user_id}, document)
        return _from_document(document)

    def delete(self, user_id: str, subscription_id: str) -> bool:
        result = self.subscriptions.delete_one({"_id": subscription_id, "userId": user_id})
        return result.deleted_count > 0

    def delete_all(self, user_id: str) -> None:
        self.subscriptions.delete_many({"userId": user_id})

    def replace_with_sample(self, user_id: str) -> list[Subscription]:
        subscriptions = sample_subscriptions(id_prefix=f"{user_id[:8]}-")
        self.subscriptions.delete_many({"userId": user_id})
        self._insert_many(user_id, subscriptions)
        return subscriptions

    def save_charges(self, user_id: str, charges: list[dict]) -> None:
        for charge in charges:
            self.charges.replace_one(
                {"_id": f"{user_id}:{charge['id']}"},
                {
                    "_id": f"{user_id}:{charge['id']}",
                    "userId": user_id,
                    "chargeId": charge["id"],
                    "posted": charge["posted"],
                    "amountCents": int(
                        (Decimal(str(charge["amount"])) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                    ),
                    "description": charge["description"],
                    "origin": charge.get("origin") or "plaid",
                    "itemId": charge.get("itemId") or "",
                },
                upsert=True,
            )

    def delete_charges(self, user_id: str, charge_ids: list[str]) -> None:
        if not charge_ids:
            return
        self.charges.delete_many(
            {"userId": user_id, "chargeId": {"$in": charge_ids}},
        )

    def list_charges(self, user_id: str) -> list[dict]:
        documents = self.charges.find({"userId": user_id}).sort("posted", 1)
        return [
            {
                "id": document["chargeId"],
                "posted": document["posted"],
                "amountCents": document["amountCents"],
                "description": document["description"],
            }
            for document in documents
        ]

    def dismiss_merchant(self, user_id: str, key: str) -> None:
        self.dismissed.replace_one(
            {"_id": f"{user_id}:{key}"},
            {"_id": f"{user_id}:{key}", "userId": user_id, "key": key},
            upsert=True,
        )

    def restore_merchant(self, user_id: str, key: str) -> None:
        self.dismissed.delete_one({"_id": f"{user_id}:{key}"})

    def dismissed_keys(self, user_id: str) -> set[str]:
        return {document["key"] for document in self.dismissed.find({"userId": user_id}, {"key": 1})}

    def tracked_external_ids(self, user_id: str) -> set[str]:
        documents = self.subscriptions.find(
            {"userId": user_id, "status": {"$ne": "canceled"}, "externalId": {"$type": "string"}},
            {"externalId": 1},
        )
        return {document["externalId"] for document in documents}

    def delete_external(self, user_id: str, external_id: str) -> None:
        self.subscriptions.delete_many(
            {"userId": user_id, "externalId": external_id, "source": "bank"}
        )

    def save_bank_item(self, user_id: str, item_id: str, access_token: str, institution: str) -> None:
        self.bank_items.replace_one(
            {"_id": item_id, "userId": user_id},
            {
                "_id": item_id,
                "userId": user_id,
                "accessToken": access_token,
                "institution": institution[:80],
                "cursor": "",
                "updatedAt": _now(),
            },
            upsert=True,
        )

    def list_bank_items(self, user_id: str) -> list[dict]:
        documents = self.bank_items.find({"userId": user_id}).sort("institution", 1)
        return [
            {
                "id": document["_id"],
                "institution": document.get("institution") or "Bank",
                "accessToken": document["accessToken"],
                "cursor": document.get("cursor") or "",
            }
            for document in documents
        ]

    def get_bank_item(self, item_id: str) -> dict | None:
        document = self.bank_items.find_one({"_id": item_id})
        if document is None:
            return None
        return {
            "id": document["_id"],
            "userId": document["userId"],
            "institution": document.get("institution") or "Bank",
            "accessToken": document["accessToken"],
            "cursor": document.get("cursor") or "",
        }

    def delete_bank_item(self, user_id: str, item_id: str) -> None:
        self.bank_items.delete_one({"_id": item_id, "userId": user_id})

    def delete_imported_bank_data(self, user_id: str) -> None:
        """Drop charges and subscriptions that came from a linked bank.

        A CSV statement is marked origin "statement" and is left in place.
        """
        self.charges.delete_many({"userId": user_id, "origin": {"$ne": "statement"}})
        self.subscriptions.delete_many(
            {"userId": user_id, "externalId": {"$regex": "^plaid:"}}
        )

    def save_bank_cursor(self, user_id: str, item_id: str, cursor: str) -> None:
        self.bank_items.update_one(
            {"_id": item_id, "userId": user_id},
            {"$set": {"cursor": cursor, "updatedAt": _now()}},
        )

    def upsert_detected(self, user_id: str, plans: list[dict], retire_prefixes: tuple[str, ...] = ()) -> dict:
        seen_ids: list[str] = []
        added = 0
        updated = 0
        for plan in plans:
            seen_ids.append(plan["external_id"])
            current = self.subscriptions.find_one({"userId": user_id, "externalId": plan["external_id"]})
            if current is None:
                current = self.subscriptions.find_one(
                    {"userId": user_id, "matchName": match_key(plan["name"])}
                )
            if current:
                notes = current.get("notes") or plan["notes"]
                self.subscriptions.update_one(
                    {"_id": current["_id"], "userId": user_id},
                    {
                        "$set": {
                            "name": plan["name"],
                            "matchName": match_key(plan["name"]),
                            "category": plan["category"],
                            "costCents": int(plan["cost"] * 100),
                            "cycle": plan["cycle"],
                            "renewalDate": plan["renewal_date"].isoformat(),
                            "status": plan["status"],
                            "externalId": plan["external_id"],
                            "source": "bank",
                            "notes": notes,
                        }
                    },
                )
                updated += 1
                continue
            subscription = Subscription(
                id=uuid4().hex,
                name=plan["name"],
                category=plan["category"],
                cost=plan["cost"],
                cycle=plan["cycle"],
                renewal_date=plan["renewal_date"],
                status=plan["status"],
                reminder_days=7,
                usage="monthly",
                last_used=None,
                notes=plan["notes"],
                source="bank",
            )
            self.subscriptions.insert_one(
                _to_document(subscription, user_id, plan["external_id"], "bank")
            )
            added += 1

        retired = 0
        if retire_prefixes:
            filters = [
                {"$or": [{"externalId": {"$regex": f"^{re.escape(prefix)}"}} for prefix in retire_prefixes]}
            ]
            if seen_ids:
                filters.append({"externalId": {"$nin": seen_ids}})
            result = self.subscriptions.update_many(
                {
                    "userId": user_id,
                    "source": "bank",
                    "status": {"$ne": "canceled"},
                    "$and": filters,
                },
                {"$set": {"status": "canceled"}},
            )
            retired = result.modified_count
        return {"added": added, "updated": updated, "retired": retired}

    def drop(self) -> None:
        self.client.drop_database(self.database_name)

    def close(self) -> None:
        self.client.close()

    def _insert_many(self, user_id: str, subscriptions: list[Subscription]) -> None:
        if not subscriptions:
            return
        self.subscriptions.insert_many([_to_document(item, user_id) for item in subscriptions])


def _now() -> datetime:
    return datetime.now(timezone.utc)
