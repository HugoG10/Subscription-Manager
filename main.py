"""Run the Wallet Worth server.

Subscriptions are stored in MongoDB. Each person signs in, then connects a
bank through Plaid or uploads a CSV statement. Put MONGODB_URI in .env.
Plaid also needs PLAID_CLIENT_ID and PLAID_SECRET. Then:

    python main.py

Open http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from bank import (
    Charge,
    _merchant_key,
    detect_recurring,
    merchant_external_id,
    parse_statement,
    review_merchants,
    subscription_from_merchant,
    without_dismissed,
)
from model import ValidationError, subscription_from_payload, summarize
from plaid_link import (
    BankLinkUnavailable,
    BankSyncPending,
    callback_url,
    create_link_token,
    create_sandbox_public_token,
    environment,
    exchange_public_token,
    fetch_plans,
    public_token_allowed,
    remove_item,
    sandbox_mode,
    sync_charges,
    verify_webhook,
)
from store import DEFAULT_DATABASE, DEFAULT_URI, SubscriptionStore
from vault import decrypt, encrypt

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
ID_MAX_LENGTH = 64
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PUBLIC_API = {"/api/session", "/api/login", "/api/register", "/api/plaid/webhook"}
PLAID_SYNC_CODES = {
    "SYNC_UPDATES_AVAILABLE",
    "RECURRING_TRANSACTIONS_UPDATE",
    "INITIAL_UPDATE",
    "HISTORICAL_UPDATE",
    "DEFAULT_UPDATE",
}
DUMMY_HASH = generate_password_hash("not-a-real-password", method="scrypt")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' https://cdn.plaid.com; "
        "connect-src 'self' https://cdn.plaid.com https://production.plaid.com "
        "https://sandbox.plaid.com https://development.plaid.com; "
        "frame-src https://cdn.plaid.com; "
        "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}


def load_env(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_secret() -> str:
    load_env()
    current = os.environ.get("SECRET_KEY", "").strip()
    if current:
        return current
    if os.environ.get("VERCEL"):
        raise RuntimeError("Set SECRET_KEY in the Vercel project environment.")
    generated = secrets.token_urlsafe(32)
    path = ROOT / ".env"
    existing = path.read_text() if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(f"{existing}SECRET_KEY={generated}\n")
    os.environ["SECRET_KEY"] = generated
    return generated


def create_app(
    mongo_uri: str | None = None,
    database: str | None = None,
    *,
    connect: bool = True,
) -> Flask:
    load_env()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=ensure_secret(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("VERCEL") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    )
    store = SubscriptionStore(
        mongo_uri or os.environ.get("MONGODB_URI", DEFAULT_URI),
        database or os.environ.get("MONGODB_DB", DEFAULT_DATABASE),
    )
    app.store = store
    if connect:
        store.initialize()
    else:
        # Vercel imports this module at build time, before a request exists.
        ready = False

        @app.before_request
        def connect_database():
            nonlocal ready
            if ready:
                return None
            store.initialize()
            ready = True
            return None

    @app.after_request
    def security_headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.before_request
    def guard_request():
        if request.method == "OPTIONS":
            return None
        if _cross_site():
            return jsonify({"errors": ["That request was rejected."]}), 403
        if not request.path.startswith("/api/") or request.path in PUBLIC_API:
            return None
        user = _current_user()
        if user is None:
            return jsonify({"errors": ["Sign in to continue."]}), 401
        return None

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND, "index.html")

    @app.get("/css/<path:filename>")
    def styles(filename):
        return send_from_directory(FRONTEND / "css", filename)

    @app.get("/js/<path:filename>")
    def scripts(filename):
        return send_from_directory(FRONTEND / "js", filename)

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    @app.get("/api/session")
    def current_session():
        user = _current_user()
        if user is None:
            return jsonify({"user": None, "bank": {"linked": False, "institutions": []}})
        items = store.list_bank_items(user["id"])
        return jsonify(
            {
                "user": {"email": user["email"]},
                "bank": {
                    "linked": bool(items),
                    "institutions": [item["institution"] for item in items],
                    "sandbox": sandbox_mode(),
                },
            }
        )

    @app.post("/api/register")
    def register():
        payload = request.get_json(silent=True) or {}
        email = _clean_email(payload.get("email"))
        password = payload.get("password")
        if email is None:
            return jsonify({"errors": ["Enter a valid email."]}), 400
        if store.login_is_limited(email):
            return jsonify({"errors": ["Too many attempts. Try again in a few minutes."]}), 429
        problem = _password_error(password, email)
        if problem:
            return jsonify({"errors": [problem]}), 400
        confirm = payload.get("confirm")
        if not isinstance(confirm, str) or confirm != password:
            return jsonify({"errors": ["Type the same password again."]}), 400
        user = store.create_user(email, generate_password_hash(password, method="scrypt"))
        if user is None:
            store.record_login_failure(email)
            return jsonify({"errors": ["An account with that email already exists."]}), 400
        _start_session(user["id"])
        return jsonify({"email": user["email"]}), 201

    @app.post("/api/login")
    def login():
        payload = request.get_json(silent=True) or {}
        email = _clean_email(payload.get("email")) or ""
        password = payload.get("password")
        if not isinstance(password, str):
            password = ""
        if email and store.login_is_limited(email):
            check_password_hash(DUMMY_HASH, password)
            return jsonify({"errors": ["Too many attempts. Try again in a few minutes."]}), 429
        user = store.get_user_by_email(email) if email else None
        password_hash = user["passwordHash"] if user else DUMMY_HASH
        if user is None or not check_password_hash(password_hash, password):
            if email:
                store.record_login_failure(email)
            return jsonify({"errors": ["Email or password is incorrect."]}), 401
        store.clear_login_failures(email)
        _start_session(user["id"])
        return jsonify({"email": user["email"]})

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    @app.post("/api/bank/link-token")
    def bank_link_token():
        webhook, redirect = _plaid_callbacks()
        try:
            token = create_link_token(session["user_id"], webhook=webhook, redirect_uri=redirect)
        except BankLinkUnavailable as error:
            return jsonify({"errors": [_bank_message(error)]}), 503
        except Exception:
            return jsonify({"errors": ["The bank window could not be opened."]}), 502
        return jsonify({"linkToken": token, "sandbox": sandbox_mode()})

    @app.post("/api/bank/sandbox")
    def bank_sandbox():
        if not sandbox_mode():
            return jsonify({"errors": ["The test bank is only available in sandbox."]}), 404
        try:
            public_token = create_sandbox_public_token()
            access_token, item_id = exchange_public_token(public_token)
        except BankLinkUnavailable:
            return jsonify({"errors": ["Bank linking is not set up on this server."]}), 503
        except Exception:
            return jsonify({"errors": ["The test bank could not be loaded."]}), 502
        user_id = session["user_id"]
        store.save_bank_item(user_id, item_id, encrypt(app.secret_key, access_token), "First Platypus Bank")
        try:
            result = _sync_user(user_id, item_id)
        except Exception:
            return jsonify({"errors": ["The test bank could not be loaded."]}), 502
        result["institution"] = "First Platypus Bank"
        return jsonify(result)

    @app.post("/api/bank/exchange")
    def bank_exchange():
        payload = request.get_json(silent=True) or {}
        public_token = payload.get("publicToken")
        if not public_token_allowed(public_token):
            return jsonify({"errors": ["The bank connection was not completed."]}), 400
        institution = payload.get("institution")
        institution = institution.strip()[:80] if isinstance(institution, str) else ""
        try:
            access_token, item_id = exchange_public_token(public_token)
        except BankLinkUnavailable as error:
            return jsonify({"errors": [_bank_message(error)]}), 503
        except Exception:
            return jsonify({"errors": ["The bank could not be linked."]}), 502
        user_id = session["user_id"]
        store.save_bank_item(
            user_id,
            item_id,
            encrypt(app.secret_key, access_token),
            institution or "Bank",
        )
        result = _sync_user(user_id, item_id)
        result["institution"] = institution or "Bank"
        return jsonify(result)

    @app.post("/api/bank/sync")
    def bank_sync():
        user_id = session["user_id"]
        if not store.list_bank_items(user_id):
            return jsonify({"errors": ["Connect a bank first."]}), 400
        try:
            return jsonify(_sync_user(user_id))
        except BankLinkUnavailable as error:
            return jsonify({"errors": [_bank_message(error)]}), 503
        except Exception:
            return jsonify({"errors": ["The bank could not be synced."]}), 502

    @app.post("/api/bank/disconnect")
    def bank_disconnect():
        user_id = session["user_id"]
        failed = False
        for item in store.list_bank_items(user_id):
            try:
                remove_item(decrypt(app.secret_key, item["accessToken"]))
            except ValueError:
                store.delete_bank_item(user_id, item["id"])
                continue
            except BankLinkUnavailable as error:
                return jsonify({"errors": [_bank_message(error)]}), 503
            except Exception:
                failed = True
                continue
            store.delete_bank_item(user_id, item["id"])
        if failed:
            return jsonify({"errors": ["The bank could not be disconnected. Try again."]}), 502
        if not store.list_bank_items(user_id):
            store.delete_imported_bank_data(user_id)
        return jsonify({"ok": True})

    @app.post("/api/plaid/webhook")
    def plaid_webhook():
        raw = request.get_data(cache=True) or b""
        if not verify_webhook(raw, request.headers.get("Plaid-Verification", "")):
            return "", 401
        try:
            payload = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "", 400
        if payload.get("environment") != environment():
            return "", 401
        item_id = payload.get("item_id")
        code = payload.get("webhook_code")
        if not isinstance(item_id, str) or not item_id:
            return "", 200
        item = store.get_bank_item(item_id)
        if item is None:
            return "", 200
        if code == "USER_PERMISSION_REVOKED":
            store.delete_bank_item(item["userId"], item_id)
            if not store.list_bank_items(item["userId"]):
                store.delete_imported_bank_data(item["userId"])
            return "", 200
        if code not in PLAID_SYNC_CODES:
            return "", 200
        try:
            _sync_user(item["userId"], item_id)
        except Exception:
            return "", 500
        return "", 200

    @app.post("/api/bank/statement")
    def bank_statement():
        payload = request.get_json(silent=True) or {}
        csv_text = payload.get("csv")
        if not isinstance(csv_text, str) or not csv_text.strip():
            return jsonify({"errors": ["Choose a CSV statement."]}), 400
        try:
            charges = parse_statement(csv_text)
        except ValueError as error:
            return jsonify({"errors": [str(error)]}), 400
        user_id = session["user_id"]
        store.save_charges(user_id, [_charge_document(charge) for charge in charges])
        detected = detect_recurring(_stored_charges(user_id), skip_keys=store.dismissed_keys(user_id))
        result = store.upsert_detected(user_id, detected, ("detected:",))
        result["detected"] = len(detected)
        return jsonify(result)

    @app.get("/api/bank/review")
    def bank_review():
        user_id = session["user_id"]
        rows = review_merchants(
            _stored_charges(user_id),
            dismissed=store.dismissed_keys(user_id),
            tracked_ids=store.tracked_external_ids(user_id),
        )
        return jsonify([_review_row(row) for row in rows])

    @app.post("/api/bank/review")
    def bank_review_decision():
        payload = request.get_json(silent=True) or {}
        key = payload.get("key")
        decision = payload.get("decision")
        if not _valid_merchant_key(key) or decision not in {"keep", "dismiss"}:
            return jsonify({"errors": ["That charge could not be updated."]}), 400
        user_id = session["user_id"]
        if decision == "dismiss":
            store.dismiss_merchant(user_id, key)
            store.delete_external(user_id, merchant_external_id(key))
            return jsonify({"ok": True})
        store.restore_merchant(user_id, key)
        plan = subscription_from_merchant(
            [charge for charge in _stored_charges(user_id) if _same_merchant(charge, key)]
        )
        if plan is None:
            return jsonify({"errors": ["That charge could not be tracked."]}), 400
        result = store.upsert_detected(user_id, [plan])
        result["name"] = plan["name"]
        return jsonify(result)

    @app.get("/api/subscriptions")
    def list_subscriptions():
        user_id = session["user_id"]
        return jsonify([item.to_json() for item in store.list_subscriptions(user_id)])

    @app.get("/api/summary")
    def summary():
        user_id = session["user_id"]
        return jsonify(summarize(store.list_subscriptions(user_id)))

    @app.post("/api/subscriptions")
    def create_subscription():
        user_id = session["user_id"]
        try:
            subscription = subscription_from_payload(request.get_json(silent=True))
        except ValidationError as error:
            return jsonify({"errors": error.errors}), 400
        saved = store.add(user_id, subscription)
        return jsonify(saved.to_json()), 201

    @app.put("/api/subscriptions/<subscription_id>")
    def update_subscription(subscription_id):
        user_id = session["user_id"]
        if not _valid_id(subscription_id) or store.get(user_id, subscription_id) is None:
            return jsonify({"errors": ["That subscription was not found."]}), 404
        try:
            subscription = subscription_from_payload(
                request.get_json(silent=True),
                subscription_id,
            )
        except ValidationError as error:
            return jsonify({"errors": error.errors}), 400
        saved = store.update(user_id, subscription)
        if saved is None:
            return jsonify({"errors": ["That subscription was not found."]}), 404
        return jsonify(saved.to_json())

    @app.delete("/api/subscriptions/<subscription_id>")
    def delete_subscription(subscription_id):
        user_id = session["user_id"]
        if not _valid_id(subscription_id) or not store.delete(user_id, subscription_id):
            return jsonify({"errors": ["That subscription was not found."]}), 404
        return "", 204

    @app.delete("/api/subscriptions")
    def delete_all():
        store.delete_all(session["user_id"])
        return jsonify([])

    @app.post("/api/subscriptions/sample")
    def load_sample():
        subscriptions = store.replace_with_sample(session["user_id"])
        return jsonify([item.to_json() for item in subscriptions])

    def _current_user() -> dict | None:
        user_id = session.get("user_id")
        if not isinstance(user_id, str):
            return None
        user = store.get_user(user_id)
        if user is None:
            session.clear()
        return user

    def _start_session(user_id: str) -> None:
        session.clear()
        session.permanent = True
        session["user_id"] = user_id

    def _stored_charges(user_id: str) -> list[Charge]:
        charges = []
        for document in store.list_charges(user_id):
            try:
                posted = date.fromisoformat(document["posted"])
            except ValueError:
                continue
            charges.append(
                Charge(
                    id=document["id"],
                    posted=posted,
                    amount=Decimal(document["amountCents"]) / Decimal(100),
                    description=document["description"],
                )
            )
        return charges

    def _sync_user(user_id: str, only_item_id: str | None = None) -> dict:
        totals = {"added": 0, "updated": 0, "retired": 0, "detected": 0, "pending": False}
        items = store.list_bank_items(user_id)
        if only_item_id:
            items = [item for item in items if item["id"] == only_item_id]
        for item in items:
            try:
                access_token = decrypt(app.secret_key, item["accessToken"])
            except ValueError:
                continue
            try:
                added, removed, cursor = sync_charges(access_token, item["cursor"])
            except BankSyncPending:
                totals["pending"] = True
                added, removed, cursor = [], [], item["cursor"]
            except BankLinkUnavailable:
                raise
            except Exception:
                added, removed, cursor = [], [], item["cursor"]
            if added:
                store.save_charges(
                    user_id,
                    [
                        {
                            "id": charge["id"],
                            "posted": charge["posted"],
                            "amount": charge["amount"],
                            "description": charge["description"],
                            "origin": "plaid",
                            "itemId": item["id"],
                        }
                        for charge in added
                    ],
                )
            store.delete_charges(user_id, removed)
            if cursor and cursor != item["cursor"]:
                store.save_bank_cursor(user_id, item["id"], cursor)
            plans = None
            try:
                plans = fetch_plans(access_token)
            except BankSyncPending:
                totals["pending"] = True
            except BankLinkUnavailable:
                raise
            except Exception:
                totals["pending"] = True
        if plans:
            plans = without_dismissed(plans, store.dismissed_keys(user_id))
        if plans:
            result = store.upsert_detected(user_id, plans, ("plaid:",))
            _add_counts(totals, result)
            totals["detected"] += len(plans)
        else:
            detected = detect_recurring(_stored_charges(user_id), skip_keys=store.dismissed_keys(user_id))
            result = store.upsert_detected(user_id, detected, ("detected:",))
            _add_counts(totals, result)
            totals["detected"] += len(detected)
        return totals

    return app


def _review_row(row: dict) -> dict:
    return {
        "key": row["key"],
        "name": row["name"],
        "category": row["category"],
        "count": row["count"],
        "latest": row["latest"],
        "amount": float(row["amount"]),
        "likely": row["likely"],
        "tracked": row["tracked"],
        "dismissed": row["dismissed"],
        "charges": [
            {"posted": charge["posted"], "amount": float(charge["amount"])}
            for charge in row["charges"]
        ],
    }


def _same_merchant(charge: Charge, key: str) -> bool:
    return _merchant_key(charge.description) == key


def _valid_merchant_key(key: object) -> bool:
    if not isinstance(key, str) or not 3 <= len(key) <= 80:
        return False
    return all(character.isalpha() or character == " " for character in key)


def _charge_document(charge: Charge) -> dict:
    return {
        "id": charge.id,
        "posted": charge.posted.isoformat(),
        "amount": charge.amount,
        "description": charge.description,
        "origin": "statement",
    }


def _bank_message(error: BankLinkUnavailable) -> str:
    text = str(error).strip()
    return text or "Bank linking is not set up on this server."


def _plaid_callbacks() -> tuple[str | None, str | None]:
    webhook = os.environ.get("PLAID_WEBHOOK_URL", "")
    redirect = os.environ.get("PLAID_REDIRECT_URI", "")
    if not webhook.strip():
        proto = request.headers.get("X-Forwarded-Proto", request.scheme)
        host = (request.host or "").split(":")[0]
        if proto == "https" and host not in {"127.0.0.1", "localhost"}:
            webhook = f"https://{request.host}/api/plaid/webhook"
    return (
        callback_url(webhook, https_only=True),
        callback_url(redirect, https_only=environment() == "production"),
    )


def _add_counts(totals: dict, result: dict) -> None:
    totals["added"] += result["added"]
    totals["updated"] += result["updated"]
    totals["retired"] += result["retired"]


def _cross_site() -> bool:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return False
    origin = request.headers.get("Origin")
    if not origin:
        return False
    return origin.rstrip("/") != request.host_url.rstrip("/")


def _clean_email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if len(email) > 254 or not EMAIL_PATTERN.match(email):
        return None
    return email


COMMON_PASSWORDS = {
    "1234567890",
    "12345678901",
    "abcdefghij",
    "admin12345",
    "baseball123",
    "changeme123",
    "football123",
    "iloveyou123",
    "letmein123",
    "password1!",
    "password123",
    "passw0rd12",
    "qwerty1234",
    "qwertyuiop",
    "trustno1!!",
    "welcome123",
}


def _password_error(password: object, email: str) -> str | None:
    if not isinstance(password, str) or len(password) < 10:
        return "Use at least 10 characters."
    if len(password) > 128:
        return "Use a password of 128 characters or fewer."
    lowered = password.lower()
    local = email.split("@", 1)[0]
    if lowered == email or lowered == local:
        return "Use a password that is not your email."
    if lowered in COMMON_PASSWORDS:
        return "That password is too common. Choose another."
    kinds = sum(
        [
            any(character.isalpha() for character in password),
            any(character.isdigit() for character in password),
            any(not character.isalnum() for character in password),
        ]
    )
    if kinds < 2:
        return "Use letters and a number or symbol."
    return None


def _valid_id(subscription_id: str) -> bool:
    if not subscription_id or len(subscription_id) > ID_MAX_LENGTH:
        return False
    return all(character.isalnum() or character in "-_" for character in subscription_id)


# Vercel loads this Flask instance. Local `python main.py` builds its own.
app = create_app(connect=False)


def main() -> None:
    load_env()
    # The Werkzeug debugger must stay off once real bank tokens are in use.
    live = os.environ.get("PLAID_ENV", "sandbox").strip().lower() == "production"
    print("Wallet Worth running at http://127.0.0.1:8000")
    create_app().run(host="127.0.0.1", port=8000, debug=not live)


if __name__ == "__main__":
    main()
