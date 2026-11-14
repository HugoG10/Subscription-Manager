import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from growth import build_report, is_eligible
from main import create_app


class GrowthMathTests(unittest.TestCase):
    def test_milestone_date_comes_from_the_real_registration(self):
        users = []
        for index in range(25):
            users.append({
                "_id": f"user-{index}",
                "email": f"person{index}@school.edu",
                "createdAt": datetime(2026, 1, index + 1, tzinfo=timezone.utc),
                "emailVerified": False,
            })
        users.append({
            "_id": "demo",
            "email": "bank-demo@example.com",
            "createdAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
        })
        report = build_report(users, datetime(2026, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(report["total"], 26)
        self.assertEqual(report["eligible"], 25)
        self.assertEqual(report["verified"], 0)
        self.assertEqual(report["milestones"][0]["goal"], 25)
        self.assertEqual(report["milestones"][0]["reachedAt"][:10], "2026-01-25")
        self.assertFalse(report["milestones"][1]["reached"])
        self.assertFalse(is_eligible(users[-1]))


class GrowthApiTests(unittest.TestCase):
    def setUp(self):
        self.database = f"subscription_manager_test_{uuid4().hex}"
        self.app = create_app("mongodb://localhost:27017", self.database)
        self.client = self.app.test_client()
        self.previous_admin = os.environ.get("ADMIN_EMAILS")
        os.environ["ADMIN_EMAILS"] = "owner@school.edu"

    def tearDown(self):
        if self.previous_admin is None:
            os.environ.pop("ADMIN_EMAILS", None)
        else:
            os.environ["ADMIN_EMAILS"] = self.previous_admin
        self.app.store.drop()
        self.app.store.close()

    def register(self, client, email, acquisition=None):
        body = {"email": email, "password": "correct-horse", "confirm": "correct-horse"}
        if acquisition:
            body["acquisition"] = acquisition
        response = client.post("/api/register", json=body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response

    def test_logins_do_not_add_accounts_and_test_domains_are_excluded(self):
        self.register(self.client, "ada@example.com")
        self.client.post("/api/login", json={"email": "ada@example.com", "password": "correct-horse"})
        owner = self.app.test_client()
        self.register(owner, "owner@school.edu", {"source": "linkedin", "medium": "social", "campaign": "launch"})
        stranger = self.app.test_client()
        hidden = stranger.get("/api/admin/growth")
        self.assertEqual(hidden.status_code, 401)
        denied = self.client.get("/api/admin/growth")
        self.assertEqual(denied.status_code, 403)
        report = owner.get("/api/admin/growth")
        self.assertEqual(report.status_code, 200)
        payload = report.get_json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["eligible"], 1)
        self.assertEqual(payload["verified"], 0)
        self.assertEqual(payload["sources"][0]["source"], "linkedin")
        exported = owner.get("/api/admin/export.csv")
        self.assertEqual(exported.status_code, 200)
        text = exported.get_data(as_text=True)
        self.assertNotIn("owner@school.edu", text)
        self.assertNotIn("password", text.lower())
        self.assertIn("linkedin", text)

    def test_reviews_stay_private_until_approved(self):
        owner = self.app.test_client()
        self.register(owner, "owner@school.edu")
        saved = owner.post("/api/reviews", json={
            "rating": 5,
            "body": "It helped me list my plans.",
            "displayName": "Alex",
            "consent": True,
        })
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.client.get("/api/reviews").get_json(), [])
        review_id = saved.get_json()["id"]
        approved = owner.post(f"/api/admin/reviews/{review_id}", json={"status": "approved"})
        self.assertEqual(approved.status_code, 200)
        public = self.client.get("/api/reviews").get_json()
        self.assertEqual(len(public), 1)
        self.assertEqual(public[0]["displayName"], "Alex")
        self.assertNotIn("userId", public[0])
        again = owner.post("/api/reviews", json={
            "rating": 4,
            "body": "Updated after another month.",
            "displayName": "Alex",
            "consent": True,
        })
        self.assertEqual(again.get_json()["status"], "pending")
        self.assertEqual(self.client.get("/api/reviews").get_json(), [])

    def test_contact_message_is_saved_and_the_honeypot_is_dropped(self):
        owner = self.app.test_client()
        self.register(owner, "owner@school.edu")
        sent = self.client.post("/api/contact", json={
            "name": "Hugo",
            "email": "reader@school.edu",
            "topic": "bug",
            "message": "The reminder date looks off.",
            "company": "",
        })
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.get_json()["delivered"], "saved")
        dropped = self.client.post("/api/contact", json={
            "name": "Bot",
            "email": "bot@school.edu",
            "topic": "bug",
            "message": "Buy cheap followers now.",
            "company": "spam inc",
        })
        self.assertEqual(dropped.status_code, 200)
        messages = owner.get("/api/admin/messages").get_json()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["topic"], "bug")

    def test_public_pages_and_hidden_milestone(self):
        about = self.client.get("/about")
        self.assertEqual(about.status_code, 200)
        self.assertIn(b"About Wallet Worth", about.data)
        self.assertIn(b"Draft for review", self.client.get("/terms").data)
        self.assertNotIn(b"bank-level", self.client.get("/privacy").data.lower())
        milestone = self.client.get("/api/milestone").get_json()
        self.assertFalse(milestone["visible"])
