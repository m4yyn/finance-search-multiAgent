from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import bcrypt
from jose import jwt

from app.config.settings import get_settings


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt before storing it."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """Compare a plaintext password against a stored bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(
    subject: str,
    expires_delta_minutes: int | None = None,
    additional_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT containing subject, token id, issued-at, and expiry."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_minutes = expires_delta_minutes or settings.jwt_access_token_expire_minutes
    payload: dict[str, Any] = {
        "sub": str(subject),
        "jti": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=expires_minutes),
    }
    if additional_claims:
        payload.update(additional_claims)
    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT, raising jose.JWTError for invalid tokens."""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )


# Compatibility aliases for the initial scaffold naming.
hash_secret = hash_password
verify_secret = verify_password
