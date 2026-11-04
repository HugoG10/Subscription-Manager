"""Talk to Plaid. Bank passwords stay on Plaid's side.

This module only runs after someone chooses Connect a bank. It exchanges
a short-lived public token for an access token and reads recurring charges.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

import certifi

from bank import plans_from_plaid_streams

try:
    import jwt
except ImportError:  # pragma: no cover - installed with the app
    jwt = None

try:
    import plaid
    from plaid.api import plaid_api
    from plaid.model.accounts_get_request import AccountsGetRequest
    from plaid.model.country_code import CountryCode
    from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
    from plaid.model.item_remove_request import ItemRemoveRequest
    from plaid.model.link_token_create_request import LinkTokenCreateRequest
    from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
    from plaid.model.link_token_transactions import LinkTokenTransactions
    from plaid.model.products import Products
    from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
    from plaid.model.transactions_recurring_get_request import TransactionsRecurringGetRequest
    from plaid.model.transactions_refresh_request import TransactionsRefreshRequest
    from plaid.model.transactions_sync_request import TransactionsSyncRequest
    from plaid.model.webhook_verification_key_get_request import WebhookVerificationKeyGetRequest
except ImportError:  # pragma: no cover - the dependency is installed with the app
    plaid = None

_VERIFICATION_KEYS: dict[str, object] = {}
WEBHOOK_MAX_AGE_SECONDS = 5 * 60


class BankLinkUnavailable(Exception):
    """Plaid keys are not configured."""


class BankSyncPending(Exception):
    """Plaid has the account but the history is not ready yet."""


def environment() -> str:
    """Sandbox uses fake banks. Production is the only environment with real accounts."""
    name = os.environ.get("PLAID_ENV", "sandbox").strip().lower()
    return "production" if name == "production" else "sandbox"


def configured() -> bool:
    return bool(plaid and os.environ.get("PLAID_CLIENT_ID", "").strip() and _secret())


def sandbox_mode() -> bool:
    return environment() != "production"


def create_link_token(user_id: str, webhook: str | None = None, redirect_uri: str | None = None) -> str:
    client = _client()
    arguments = {
        "products": [Products("transactions")],
        "client_name": "Wallet Worth",
        "country_codes": [CountryCode("US")],
        "language": "en",
        "user": LinkTokenCreateRequestUser(client_user_id=user_id[:128]),
        "transactions": LinkTokenTransactions(days_requested=730),
    }
    if webhook:
        arguments["webhook"] = webhook
    if redirect_uri:
        arguments["redirect_uri"] = redirect_uri
    request = LinkTokenCreateRequest(**arguments)
    response = _plain(client.link_token_create(request))
    token = response.get("link_token")
    if not token:
        raise BankSyncPending()
    return str(token)


def exchange_public_token(public_token: str) -> tuple[str, str]:
    client = _client()
    response = _plain(
        client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=public_token)
        )
    )
    access_token = response.get("access_token")
    item_id = response.get("item_id")
    if not access_token or not item_id:
        raise BankSyncPending()
    return str(access_token), str(item_id)


def fetch_plans(access_token: str, today: date | None = None) -> list[dict] | None:
    """Return plans, or None when Plaid is still preparing transaction history."""
    client = _client()
    try:
        accounts = _plain(client.accounts_get(AccountsGetRequest(access_token=access_token)))
        account_ids = [str(account["account_id"]) for account in accounts.get("accounts") or [] if account.get("account_id")]
        if not account_ids:
            return []
        recurring = _plain(
            client.transactions_recurring_get(
                TransactionsRecurringGetRequest(
                    access_token=access_token,
                    account_ids=account_ids,
                )
            )
        )
    except plaid.ApiException as error:
        if _error_code(error) in {"PRODUCT_NOT_READY", "ITEM_LOGIN_REQUIRED"}:
            return None
        raise
    streams = recurring.get("outflow_streams") or []
    return plans_from_plaid_streams(streams, today)


def public_token_allowed(public_token: str) -> bool:
    """A sandbox token must never be exchanged on the production API, or the reverse."""
    if not isinstance(public_token, str) or not 20 <= len(public_token) <= 512 or any(character.isspace() for character in public_token):
        return False
    prefix = "public-sandbox-" if sandbox_mode() else "public-production-"
    return public_token.startswith(prefix)


def callback_url(value: str, *, https_only: bool) -> str | None:
    """Accept only the callback we intend to register with Plaid."""
    text = value.strip()
    if not text or len(text) > 300 or "#" in text:
        return None
    parsed = urlsplit(text)
    host = parsed.hostname or ""
    if parsed.username or parsed.password or not host:
        return None
    if parsed.scheme == "https":
        return text
    if not https_only and parsed.scheme == "http" and host in {"127.0.0.1", "localhost"}:
        return text
    return None


def remove_item(access_token: str) -> None:
    """Revoke the token at Plaid so this app can no longer read the account."""
    client = _client()
    try:
        client.item_remove(ItemRemoveRequest(access_token=access_token))
    except plaid.ApiException as error:
        if _error_code(error) not in {"ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"}:
            raise


def verify_webhook(raw_body: bytes, verification_header: str, fetch_key=None) -> bool:
    """Confirm a webhook was signed by Plaid and matches this exact body."""
    if jwt is None or not verification_header or not isinstance(raw_body, (bytes, bytearray)):
        return False
    try:
        header = jwt.get_unverified_header(verification_header)
    except jwt.PyJWTError:
        return False
    if header.get("alg") != "ES256":
        return False
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        return False
    key = _verification_key(kid, fetch_key)
    if key is None:
        return False
    try:
        claims = jwt.decode(verification_header, key, algorithms=["ES256"])
    except jwt.PyJWTError:
        return False
    issued = claims.get("iat")
    if not isinstance(issued, (int, float)) or issued < time.time() - WEBHOOK_MAX_AGE_SECONDS:
        return False
    expected = claims.get("request_body_sha256")
    if not isinstance(expected, str):
        return False
    digest = hashlib.sha256(raw_body).hexdigest()
    try:
        return hmac.compare_digest(digest, expected)
    except ValueError:
        return False


def create_sandbox_public_token() -> str:
    """Create a First Platypus Bank item that includes the standard sandbox charges."""
    if not sandbox_mode():
        raise BankLinkUnavailable()
    client = _client()
    response = _plain(
        client.sandbox_public_token_create(
            SandboxPublicTokenCreateRequest(
                institution_id="ins_109508",
                initial_products=[Products("transactions")],
            )
        )
    )
    token = response.get("public_token")
    if not token:
        raise BankSyncPending()
    return str(token)


def sync_charges(access_token: str, cursor: str) -> tuple[list[dict], list[str], str]:
    """Pull posted outflows. Positive Plaid amounts are money leaving the account.

    Sandbox often answers the first sync before any charges exist. Refresh and
    poll until the history is ready, or until Plaid says the history is complete.
    """
    client = _client()
    if not cursor:
        try:
            client.transactions_refresh(TransactionsRefreshRequest(access_token=access_token))
        except plaid.ApiException:
            pass
    added: list[dict] = []
    removed: list[str] = []
    next_cursor = cursor or ""
    for attempt in range(8):
        arguments = {"access_token": access_token, "count": 200}
        if next_cursor:
            arguments["cursor"] = next_cursor
        try:
            page = _plain(client.transactions_sync(TransactionsSyncRequest(**arguments)))
        except plaid.ApiException as error:
            if _error_code(error) == "PRODUCT_NOT_READY":
                if attempt == 7:
                    raise BankSyncPending() from error
                time.sleep(1)
                continue
            raise
        page_added, page_removed = _charges_from_page(page)
        added.extend(page_added)
        removed.extend(page_removed)
        next_cursor = str(page.get("next_cursor") or next_cursor)
        status = str(page.get("transactions_update_status") or "")
        if page.get("has_more"):
            continue
        if added or status == "HISTORICAL_UPDATE_COMPLETE" or cursor:
            break
        time.sleep(1)
        next_cursor = ""
    return added, removed, next_cursor


def _charges_from_page(page: dict) -> tuple[list[dict], list[str]]:
    added = []
    removed = []
    for transaction in page.get("added") or []:
        if transaction.get("pending"):
            continue
        amount = transaction.get("amount")
        try:
            spent = Decimal(str(amount))
        except Exception:
            continue
        if spent <= 0:
            continue
        posted = str(transaction.get("date") or "")[:10]
        name = str(transaction.get("merchant_name") or transaction.get("name") or "").strip()
        transaction_id = str(transaction.get("transaction_id") or "")
        if not posted or not name or not transaction_id:
            continue
        added.append(
            {
                "id": transaction_id[:80],
                "posted": posted,
                "amount": spent,
                "description": name[:180],
            }
        )
    for transaction in page.get("removed") or []:
        transaction_id = str(transaction.get("transaction_id") or "")
        if transaction_id:
            removed.append(transaction_id[:80])
    return added, removed


def _secret() -> str:
    if environment() == "production":
        return os.environ.get("PLAID_PRODUCTION_SECRET", "").strip()
    return os.environ.get("PLAID_SECRET", "").strip()


def _client():
    if plaid is None or not os.environ.get("PLAID_CLIENT_ID", "").strip():
        raise BankLinkUnavailable("Bank linking is not set up on this server.")
    secret = _secret()
    if not secret:
        if environment() == "production":
            raise BankLinkUnavailable(
                "Add the production secret from the Plaid dashboard before connecting a real bank."
            )
        raise BankLinkUnavailable("Bank linking is not set up on this server.")
    host = plaid.Environment.Production if environment() == "production" else plaid.Environment.Sandbox
    configuration = plaid.Configuration(
        host=host,
        api_key={
            "clientId": os.environ["PLAID_CLIENT_ID"].strip(),
            "secret": secret,
        },
        ssl_ca_cert=certifi.where(),
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def _verification_key(kid: str, fetch_key):
    if fetch_key is None and kid in _VERIFICATION_KEYS:
        return _VERIFICATION_KEYS[kid]
    raw = fetch_key(kid) if fetch_key else _fetch_jwk(kid)
    if not isinstance(raw, dict) or raw.get("kty") != "EC" or raw.get("expired_at"):
        return None
    if raw.get("alg") not in {None, "ES256"}:
        return None
    try:
        key = jwt.PyJWK.from_dict(raw).key
    except Exception:
        return None
    if fetch_key is None:
        _VERIFICATION_KEYS[kid] = key
    return key


def _fetch_jwk(kid: str) -> dict | None:
    client = _client()
    response = _plain(
        client.webhook_verification_key_get(WebhookVerificationKeyGetRequest(key_id=kid))
    )
    key = response.get("key")
    if hasattr(key, "to_dict"):
        key = key.to_dict()
    return key if isinstance(key, dict) else None


def _plain(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


def _error_code(error) -> str:
    body = getattr(error, "body", "") or ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("error_code") or "")
