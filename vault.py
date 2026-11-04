"""Encrypt bank access tokens before they are stored.

The key comes from SECRET_KEY. A database copy alone cannot be used to
call the bank.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def encrypt(secret: str, value: str) -> str:
    return _box(secret).encrypt(value.encode()).decode()


def decrypt(secret: str, value: str) -> str:
    try:
        return _box(secret).decrypt(value.encode()).decode()
    except InvalidToken as error:
        raise ValueError("Stored bank token could not be read.") from error


def _box(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))
