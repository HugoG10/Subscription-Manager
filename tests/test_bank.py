import base64
import hashlib
import os
import time
import unittest
from datetime import date
from decimal import Decimal

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from bank import Charge, detect_recurring, parse_statement, plans_from_plaid_streams
from plaid_link import callback_url, public_token_allowed, verify_webhook


class StatementTests(unittest.TestCase):
    def test_negative_amounts_are_spending_and_payroll_is_ignored(self):
        charges = parse_statement(
            "\n".join(
                [
                    "Date,Description,Amount",
                    "01/05/2026,NETFLIX.COM,-15.49",
                    "01/15/2026,PAYROLL,2500.00",
                    "01/20/2026,SPOTIFY,11.99",
                ]
            )
        )
        descriptions = [charge.description for charge in charges]
        self.assertEqual(descriptions, ["NETFLIX.COM"])

    def test_monthly_charges_become_one_subscription(self):
        charges = [
            Charge("a", date(2026, 1, 5), Decimal("15.49"), "NETFLIX.COM"),
            Charge("b", date(2026, 2, 5), Decimal("15.49"), "NETFLIX.COM"),
            Charge("c", date(2026, 3, 5), Decimal("15.99"), "NETFLIX.COM"),
        ]
        plans = detect_recurring(charges, date(2026, 3, 20))
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["name"], "Netflix")
        self.assertEqual(plans[0]["category"], "streaming")
        self.assertEqual(plans[0]["cycle"], "monthly")
        self.assertEqual(plans[0]["renewal_date"], date(2026, 4, 4))

    def test_plaid_stream_uses_the_predicted_renewal(self):
        plans = plans_from_plaid_streams(
            [
                {
                    "stream_id": "stream-1",
                    "merchant_name": "Spotify",
                    "frequency": "MONTHLY",
                    "average_amount": 11.99,
                    "is_active": True,
                    "status": "MATURE",
                    "predicted_next_date": "2026-11-02",
                    "personal_finance_category": {"primary": "ENTERTAINMENT"},
                }
            ],
            date(2026, 10, 28),
        )
        self.assertEqual(plans[0]["name"], "Spotify")
        self.assertEqual(plans[0]["external_id"], "plaid:stream-1")
        self.assertEqual(plans[0]["renewal_date"], date(2026, 11, 2))
        self.assertEqual(plans[0]["cost"], Decimal("11.99"))

    def test_two_similar_restaurant_visits_are_not_a_subscription(self):
        charges = [
            Charge("a", date(2025, 5, 5), Decimal("20.45"), "McDonald's"),
            Charge("b", date(2025, 6, 2), Decimal("17.34"), "McDonald's"),
        ]
        self.assertEqual(detect_recurring(charges, date(2026, 10, 28)), [])

    def test_gym_membership_keeps_the_latest_price_when_the_amount_changes(self):
        charges = [
            Charge("a", date(2026, 6, 25), Decimal("29.99"), "EOS Fitness"),
            Charge("b", date(2026, 7, 23), Decimal("69.98"), "EOS Fitness"),
            Charge("c", date(2026, 8, 26), Decimal("24.99"), "EOS Fitness"),
            Charge("d", date(2026, 9, 23), Decimal("9.99"), "EOS Fitness"),
            Charge("e", date(2026, 10, 23), Decimal("9.99"), "EOS Fitness"),
        ]
        plans = detect_recurring(charges, date(2026, 10, 28))
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["name"], "EOS Fitness")
        self.assertEqual(plans[0]["category"], "fitness")
        self.assertEqual(plans[0]["cycle"], "monthly")
        self.assertEqual(plans[0]["cost"], Decimal("9.99"))
        self.assertEqual(plans[0]["renewal_date"], date(2026, 11, 22))

    def test_plaid_ignores_a_restaurant_stream(self):
        plans = plans_from_plaid_streams(
            [
                {
                    "stream_id": "stream-food",
                    "merchant_name": "McDonald's",
                    "frequency": "MONTHLY",
                    "average_amount": 12.00,
                    "is_active": True,
                    "status": "MATURE",
                    "predicted_next_date": "2026-11-02",
                    "personal_finance_category": {"primary": "FOOD_AND_DRINK"},
                }
            ],
            date(2026, 10, 28),
        )
        self.assertEqual(plans, [])

    def test_review_marks_the_gym_and_leaves_out_the_restaurant(self):
        from bank import review_merchants

        charges = [
            Charge("a", date(2025, 5, 5), Decimal("20.45"), "McDonald's"),
            Charge("b", date(2025, 6, 2), Decimal("17.34"), "McDonald's"),
            Charge("c", date(2026, 9, 23), Decimal("9.99"), "EOS Fitness"),
            Charge("d", date(2026, 10, 23), Decimal("9.99"), "EOS Fitness"),
        ]
        rows = review_merchants(charges, date(2026, 10, 28))
        by_name = {row["name"]: row for row in rows}
        self.assertTrue(by_name["EOS Fitness"]["likely"])
        self.assertFalse(by_name["Mcdonald S"]["likely"])
        self.assertEqual(by_name["EOS Fitness"]["charges"][0]["posted"], "2026-10-23")

    def test_a_hidden_merchant_is_not_detected_again(self):
        charges = [
            Charge("a", date(2026, 1, 5), Decimal("15.49"), "NETFLIX.COM"),
            Charge("b", date(2026, 2, 5), Decimal("15.49"), "NETFLIX.COM"),
            Charge("c", date(2026, 3, 5), Decimal("15.49"), "NETFLIX.COM"),
        ]
        found = detect_recurring(charges, date(2026, 3, 20))
        self.assertEqual(found[0]["name"], "Netflix")
        hidden = detect_recurring(charges, date(2026, 3, 20), skip_keys={"NETFLIX COM"})
        self.assertEqual(hidden, [])

    def test_irregular_shopping_is_not_a_subscription(self):
        charges = [
            Charge("a", date(2025, 1, 15), Decimal("7.29"), "Amazon"),
            Charge("b", date(2025, 4, 16), Decimal("26.15"), "Amazon"),
            Charge("c", date(2025, 8, 11), Decimal("43.14"), "Amazon"),
            Charge("d", date(2026, 1, 6), Decimal("18.52"), "Amazon"),
            Charge("e", date(2026, 6, 8), Decimal("18.52"), "Amazon"),
            Charge("f", date(2026, 10, 9), Decimal("21.73"), "Amazon"),
        ]
        self.assertEqual(detect_recurring(charges, date(2026, 10, 28)), [])


class PlaidSafetyTests(unittest.TestCase):
    def test_production_rejects_a_sandbox_public_token(self):
        previous = os.environ.get("PLAID_ENV")
        os.environ["PLAID_ENV"] = "production"
        try:
            self.assertFalse(public_token_allowed("public-sandbox-12345678901234567890"))
            self.assertTrue(public_token_allowed("public-production-12345678901234567890"))
        finally:
            if previous is None:
                os.environ.pop("PLAID_ENV", None)
            else:
                os.environ["PLAID_ENV"] = previous

    def test_callbacks_must_be_https_except_local_sandbox_redirects(self):
        self.assertEqual(
            callback_url("https://example.com/api/plaid/webhook", https_only=True),
            "https://example.com/api/plaid/webhook",
        )
        self.assertIsNone(callback_url("http://evil.example/hook", https_only=True))
        self.assertIsNone(callback_url("https://example.com/back#token", https_only=True))
        self.assertEqual(
            callback_url("http://127.0.0.1:8000/oauth", https_only=False),
            "http://127.0.0.1:8000/oauth",
        )

    def test_webhook_signature_must_match_the_body_and_be_fresh(self):
        key = ec.generate_private_key(ec.SECP256R1())
        numbers = key.public_key().public_numbers()
        jwk = {
            "kty": "EC",
            "crv": "P-256",
            "alg": "ES256",
            "kid": "test-key",
            "x": _b64(numbers.x),
            "y": _b64(numbers.y),
        }
        body = b'{"webhook_code":"SYNC_UPDATES_AVAILABLE"}'
        token = _signed(key, body, int(time.time()))
        self.assertTrue(verify_webhook(body, token, fetch_key=lambda kid: jwk))
        self.assertFalse(verify_webhook(b'{"webhook_code":"TAMPERED"}', token, fetch_key=lambda kid: jwk))
        expired = _signed(key, body, int(time.time()) - 600)
        self.assertFalse(verify_webhook(body, expired, fetch_key=lambda kid: jwk))
        other = jwt.encode({"iat": int(time.time())}, "secret", algorithm="HS256", headers={"kid": "test-key"})
        self.assertFalse(verify_webhook(body, other, fetch_key=lambda kid: jwk))
        self.assertFalse(verify_webhook(body, ""))


def _b64(value: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(32, "big")).rstrip(b"=").decode()


def _signed(key, body: bytes, issued: int) -> str:
    return jwt.encode(
        {"iat": issued, "request_body_sha256": hashlib.sha256(body).hexdigest()},
        key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )


if __name__ == "__main__":
    unittest.main()
