"""MongoDB storage for subscriptions.

Plans live in the `subscriptions` collection. A `meta` document records that
the sample list was loaded, so clearing the collection does not bring the
samples back until the sample route is called.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import certifi
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import ServerSelectionTimeoutError

from model import Subscription, sample_subscriptions

DEFAULT_URI = "mongodb://localhost:27017"
DEFAULT_DATABASE = "subscription_manager"


class DatabaseUnavailable(Exception):
    def __init__(self, uri: str):
        self.uri = uri
        super().__init__(
            f"Could not connect to MongoDB at {uri}. "
            "Start it with: brew services start mongodb-community"
        )


def _to_document(subscription: Subscription) -> dict:
    return {
        "_id": subscription.id,
        "name": subscription.name,
        "category": subscription.category,
        "costCents": int(subscription.cost * 100),
        "cycle": subscription.cycle,
        "renewalDate": subscription.renewal_date.isoformat(),
        "status": subscription.status,
        "reminderDays": subscription.reminder_days,
        "usage": subscription.usage,
        "lastUsed": subscription.last_used.isoformat() if subscription.last_used else None,
        "notes": subscription.notes,
    }


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
        self.meta = self.db["meta"]

    def initialize(self) -> None:
        try:
            self.client.admin.command("ping")
        except ServerSelectionTimeoutError as error:
            raise DatabaseUnavailable(self.uri) from error
        self.subscriptions.create_index([("renewalDate", 1), ("name", 1)])
        claimed = self.meta.find_one_and_update(
            {"_id": "initialized"},
            {"$setOnInsert": {"value": True}},
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )
        if claimed is None:
            self._insert_many(sample_subscriptions())

    def list_subscriptions(self) -> list[Subscription]:
        documents = self.subscriptions.find().sort([("renewalDate", 1), ("name", 1)])
        return [_from_document(document) for document in documents]

    def get(self, subscription_id: str) -> Subscription | None:
        document = self.subscriptions.find_one({"_id": subscription_id})
        return _from_document(document) if document else None

    def add(self, subscription: Subscription) -> Subscription:
        self.subscriptions.insert_one(_to_document(subscription))
        return subscription

    def update(self, subscription: Subscription) -> Subscription | None:
        result = self.subscriptions.replace_one(
            {"_id": subscription.id},
            _to_document(subscription),
        )
        if result.matched_count == 0:
            return None
        return subscription

    def delete(self, subscription_id: str) -> bool:
        result = self.subscriptions.delete_one({"_id": subscription_id})
        return result.deleted_count > 0

    def delete_all(self) -> None:
        self.subscriptions.delete_many({})

    def replace_with_sample(self) -> list[Subscription]:
        subscriptions = sample_subscriptions()
        self.subscriptions.delete_many({})
        self._insert_many(subscriptions)
        return subscriptions

    def drop(self) -> None:
        self.client.drop_database(self.database_name)

    def close(self) -> None:
        self.client.close()

    def _insert_many(self, subscriptions: list[Subscription]) -> None:
        if not subscriptions:
            return
        self.subscriptions.insert_many([_to_document(item) for item in subscriptions])
