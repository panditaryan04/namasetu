"""Password hashing (bcrypt) and JWT access/refresh tokens (HS256)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import get_settings

TokenType = Literal["access", "refresh"]
ISSUER = "namasetu"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


# Constant-time dummy check so unknown usernames take as long as wrong passwords.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=12)).decode("ascii")


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def create_token(subject: str, role: str, token_type: TokenType, auth_method: str = "password") -> str:
    s = get_settings()
    now = datetime.now(UTC)
    is_access = token_type == "access"  # noqa: S105 - token kind, not a secret
    ttl = timedelta(minutes=s.jwt_access_minutes) if is_access else timedelta(days=s.jwt_refresh_days)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": subject,
        "role": role,
        "typ": token_type,
        "amr": auth_method,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, s.jwt_secret, algorithm="HS256")


class TokenError(ValueError):
    pass


def decode_token(token: str, expected: TokenType) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if claims.get("typ") != expected:
        raise TokenError(f"expected a {expected} token")
    return claims
