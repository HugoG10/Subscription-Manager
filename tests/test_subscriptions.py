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
        registered = self.client.post(
            "/api/register",
            json={"email": "ada@example.com", "password": "correct-horse", "confirm": "correct-horse"},
        )
        self.assertEqual(registered.status_code, 201)
        cookie = registered.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("passwordHash", registered.get_json())

    def tearDown(self):
        self.app.store.drop()
        self.app.store.close()

    def test_new_account_starts_empty_until_sample_is_loaded(self):
        response = self.client.get("/api/subscriptions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [])
        restored = self.client.post("/api/subscriptions/sample")
        names = [item["name"] for item in restored.get_json()]
        self.assertIn("Netflix", names)
        self.assertEqual(len(names), 12)

    def test_summary_matches_the_sample_list(self):
        self.client.post("/api/subscriptions/sample")
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
        self.assertIn(b"Wallet Worth", response.data)
        self.assertIn(b"Sign in", response.data)

    def test_subscriptions_require_sign_in(self):
        stranger = self.app.test_client()
        response = stranger.get("/api/subscriptions")
        self.assertEqual(response.status_code, 401)

    def test_another_account_cannot_see_or_delete_a_subscription(self):
        created = self.client.post("/api/subscriptions", json=_payload("Hulu"))
        subscription_id = created.get_json()["id"]
        other = self.app.test_client()
        registered = other.post(
            "/api/register",
            json={"email": "bea@example.com", "password": "another-secret", "confirm": "another-secret"},
        )
        self.assertEqual(registered.status_code, 201)
        self.assertEqual(other.get("/api/subscriptions").get_json(), [])
        missing = other.delete(f"/api/subscriptions/{subscription_id}")
        self.assertEqual(missing.status_code, 404)
        still_there = self.client.get("/api/subscriptions")
        self.assertEqual(still_there.get_json()[0]["name"], "Hulu")

    def test_signup_rejects_a_weak_or_unconfirmed_password(self):
        client = self.app.test_client()
        common = client.post(
            "/api/register",
            json={"email": "cam@example.com", "password": "password123", "confirm": "password123"},
        )
        self.assertEqual(common.status_code, 400)
        self.assertIn("too common", common.get_json()["errors"][0])
        letters = client.post(
            "/api/register",
            json={"email": "cam@example.com", "password": "onlyletters", "confirm": "onlyletters"},
        )
        self.assertEqual(letters.status_code, 400)
        self.assertIn("number or symbol", letters.get_json()["errors"][0])
        mismatch = client.post(
            "/api/register",
            json={"email": "cam@example.com", "password": "correct-horse", "confirm": "correct-horses"},
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertIn("same password", mismatch.get_json()["errors"][0])
        copied = client.post(
            "/api/register",
            json={
                "email": "longname12@example.com",
                "password": "longname12",
                "confirm": "longname12",
            },
        )
        self.assertEqual(copied.status_code, 400)
        self.assertIn("email", copied.get_json()["errors"][0])
        self.assertIsNone(self.app.store.get_user_by_email("cam@example.com"))

    def test_wrong_password_is_rejected_without_detail(self):
        wrong = self.app.test_client().post(
            "/api/login",
            json={"email": "ada@example.com", "password": "not-the-password"},
        )
        unknown = self.app.test_client().post(
            "/api/login",
            json={"email": "nobody@example.com", "password": "not-the-password"},
        )
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(wrong.get_json(), unknown.get_json())

    def test_statement_finds_a_recurring_charge(self):
        statement = "\n".join(
            [
                "Date,Description,Amount",
                "2026-01-05,NETFLIX.COM,-15.49",
                "2026-02-05,NETFLIX.COM,-15.49",
                "2026-03-05,NETFLIX.COM,-15.49",
                "2026-01-15,PAYROLL ACME,2500.00",
            ]
        )
        imported = self.client.post("/api/bank/statement", json={"csv": statement})
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.get_json()["added"], 1)
        names = [item["name"] for item in self.client.get("/api/subscriptions").get_json()]
        self.assertEqual(names, ["Netflix"])
        again = self.client.post("/api/bank/statement", json={"csv": statement})
        self.assertEqual(again.get_json()["added"], 0)
        self.assertEqual(len(self.client.get("/api/subscriptions").get_json()), 1)

    def test_webhook_without_a_signature_is_rejected(self):
        response = self.client.post("/api/plaid/webhook", data=b"{}", content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_disconnect_removes_bank_imports_and_keeps_the_statement(self):
        statement = "\n".join(
            [
                "Date,Description,Amount",
                "2026-01-05,NETFLIX.COM,-15.49",
                "2026-02-05,NETFLIX.COM,-15.49",
                "2026-03-05,NETFLIX.COM,-15.49",
            ]
        )
        self.client.post("/api/bank/statement", json={"csv": statement})
        user = self.app.store.get_user_by_email("ada@example.com")
        self.app.store.save_bank_item(user["id"], "item-test", "not-a-real-token", "Example Bank")
        self.app.store.save_charges(
            user["id"],
            [
                {
                    "id": "txn-1",
                    "posted": "2026-01-01",
                    "amount": "12.00",
                    "description": "Gym",
                    "origin": "plaid",
                    "itemId": "item-test",
                }
            ],
        )
        self.app.store.subscriptions.insert_one(
            {
                "_id": "plaid-sub",
                "userId": user["id"],
                "externalId": "plaid:gym",
                "name": "Gym",
                "source": "bank",
            }
        )
        disconnected = self.client.post("/api/bank/disconnect")
        self.assertEqual(disconnected.status_code, 200)
        names = [item["name"] for item in self.client.get("/api/subscriptions").get_json()]
        self.assertEqual(names, ["Netflix"])
        self.assertEqual(self.app.store.list_bank_items(user["id"]), [])
        self.assertEqual(self.client.get("/api/session").get_json()["bank"]["linked"], False)

    def test_review_can_hide_a_detected_charge(self):
        statement = "\n".join(
            [
                "Date,Description,Amount",
                "2026-01-05,NETFLIX.COM,-15.49",
                "2026-02-05,NETFLIX.COM,-15.49",
                "2026-03-05,NETFLIX.COM,-15.49",
                "2026-01-08,CORNER CAFE,-4.00",
            ]
        )
        self.client.post("/api/bank/statement", json={"csv": statement})
        review = self.client.get("/api/bank/review")
        self.assertEqual(review.status_code, 200)
        rows = {row["name"]: row for row in review.get_json()}
        self.assertTrue(rows["Netflix"]["likely"])
        self.assertTrue(rows["Netflix"]["tracked"])
        hidden = self.client.post("/api/bank/review", json={"key": rows["Netflix"]["key"], "decision": "dismiss"})
        self.assertEqual(hidden.status_code, 200)
        names = [item["name"] for item in self.client.get("/api/subscriptions").get_json()]
        self.assertNotIn("Netflix", names)
        again = self.client.post("/api/bank/statement", json={"csv": statement})
        self.assertEqual(again.get_json()["added"], 0)
        names = [item["name"] for item in self.client.get("/api/subscriptions").get_json()]
        self.assertNotIn("Netflix", names)


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
