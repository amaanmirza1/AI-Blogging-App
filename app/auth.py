from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

import jwt


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    salt, known_hash = stored_hash.split("$", maxsplit=1)
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return hmac.compare_digest(candidate.hex(), known_hash)


def create_access_token(user_id: int, expires_minutes: int | None = None) -> str:
    minutes = expires_minutes or int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120"))
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, os.getenv("SECRET_KEY", "dev-secret-key"), algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, os.getenv("SECRET_KEY", "dev-secret-key"), algorithms=["HS256"])
