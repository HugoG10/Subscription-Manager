import unittest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from main import create_app
from model import (
    Subscription,
    monthly_equivalent,
    sample_subscriptions,
    summarize,
    yearly_equivalent,
)


class MoneyTests(unittest.TestCase):
    def test_cycles_convert_to_monthly_and_yearly(self):
        weekly = _plan(cost="10.00", cycle="weekly")
        yearly = _plan(cost="120.00", cycle="yearly")
        self.assertEqual(yearly_equivalent(weekly), Decimal("520.00"))
        self.assertEqual(monthly_equivalent(weekly), Decimal("10.00") * 52 / 12)
        self.assertEqual(monthly_equivalent(yearly), Decimal("10.00"))
        self.assertEqual(yearly_equivalent(yearly), Decimal("120.00"))

    def test_sample_totals_leave_out_paused_plans_and_trials(self):
        today = date(2026, 10, 17)
        summary = summarize(sample_subscriptions(today), today)
        self.assertEqual(summary["monthly"], 185.70)
        self.assertEqual(summary["yearly"], 2228.39)
        self.assertEqual(summary["trialMonthly"], 13.99)
        self.assertEqual(summary["potentialSavings"], 96.65)
        self.assertEqual(summary["activeCount"], 10)
        self.assertEqual(summary["trialCount"], 1)
        self.assertEqual(summary["reminderCount"], 4)
        self.assertEqual(summary["idleCount"], 3)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.database = f"subscription_manager_test_{uuid4().hex}"
        self.app = create_app("mongodb://localhost:27017", self.database)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.store.drop()
        self.app.store.close()

    def test_first_launch_loads_the_sample_list(self):
        response = self.client.get("/api/subscriptions")
        self.assertEqual(response.status_code, 200)
        names = [item["name"] for item in response.get_json()]
        self.assertIn("Netflix", names)
        self.assertEqual(len(names), 12)

    def test_summary_matches_the_sample_list(self):
        response = self.client.get("/api/summary")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["monthly"], 185.70)
        self.assertEqual(body["reminderCount"], 4)

    def test_create_update_and_delete(self):
        created = self.client.post("/api/subscriptions", json=_payload("Hulu"))
        self.assertEqual(created.status_code, 201)
        subscription_id = created.get_json()["id"]
        self.assertEqual(created.get_json()["name"], "Hulu")

        updated = self.client.put(
            f"/api/subscriptions/{subscription_id}",
            json=_payload("Hulu", cost=12.99, usage="rarely"),
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["cost"], 12.99)
        self.assertEqual(updated.get_json()["usage"], "rarely")

        deleted = self.client.delete(f"/api/subscriptions/{subscription_id}")
        self.assertEqual(deleted.status_code, 204)
        missing = self.client.delete(f"/api/subscriptions/{subscription_id}")
        self.assertEqual(missing.status_code, 404)

    def test_invalid_subscription_is_rejected(self):
        response = self.client.post("/api/subscriptions", json={"name": ""})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Enter a name.", response.get_json()["errors"])

    def test_clear_stays_empty_and_sample_route_restores_it(self):
        cleared = self.client.delete("/api/subscriptions")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.get_json(), [])
        again = self.client.get("/api/subscriptions")
        self.assertEqual(again.get_json(), [])

        restored = self.client.post("/api/subscriptions/sample")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(len(restored.get_json()), 12)

    def test_homepage_is_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Subscription Manager", response.data)


def _plan(cost: str, cycle: str) -> Subscription:
    return Subscription(
        id="plan-1",
        name="Example",
        category="other",
        cost=Decimal(cost),
        cycle=cycle,
        renewal_date=date(2026, 11, 1),
        status="active",
        reminder_days=7,
        usage="weekly",
        last_used=None,
        notes="",
    )


def _payload(name: str, cost: float = 9.99, usage: str = "weekly") -> dict:
    return {
        "name": name,
        "category": "streaming",
        "cost": cost,
        "cycle": "monthly",
        "renewalDate": "2026-11-16",
        "status": "active",
        "reminderDays": 7,
        "usage": usage,
        "lastUsed": "",
        "notes": "",
    }


if __name__ == "__main__":
    unittest.main()
