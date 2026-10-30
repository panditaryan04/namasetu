"""Authentication: local accounts (JWT access + refresh) and an ABHA-style bearer path (mock in sandbox).

Plugging in the real ABDM gateway: set ABHA_MODE=live with ABHA_CLIENT_ID/SECRET, then implement
`exchange_abha_live` against the ABDM sandbox (session token via client credentials, then the ABHA login/OTP
flow) and map the verified ABHA address to a local user. Until then LIVE returns 501 rather than pretending.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.common import DbSession
from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.core.rbac import POLICY, Principal, current_principal
from app.core.security import TokenError, burn_password_check, create_token, decode_token, verify_password
from app.db.models import User
from app.ledger.hash_chain import append_event

logger = logging.getLogger("namasetu.auth")
router = APIRouter(prefix="/auth", tags=["auth"])
Limited = Depends(rate_limit("auth"))
RefreshLimited = Depends(rate_limit("refresh"))


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=4096)


class AbhaTokenRequest(BaseModel):
    abha_address: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._\-]+@[A-Za-z0-9.]+$")
    otp: str = Field(min_length=4, max_length=8, pattern=r"^[0-9]+$")


class UserInfo(BaseModel):
    username: str
    display_name: str
    role: str
    permissions: list[str]
    auth_method: str = "password"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int
    user: UserInfo


def _tokens(user: User, method: str) -> TokenResponse:
    return TokenResponse(
        access_token=create_token(user.username, user.role, "access", method),
        refresh_token=create_token(user.username, user.role, "refresh", method),
        expires_in=get_settings().jwt_access_minutes * 60,
        user=UserInfo(
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            permissions=sorted(POLICY.get(user.role, frozenset())),  # type: ignore[call-overload]
            auth_method=method,
        ),
    )


@router.post("/login", response_model=TokenResponse, dependencies=[Limited])
def login(req: LoginRequest, db: DbSession) -> TokenResponse:
    user = db.scalar(select(User).where(User.username == req.username, User.active.is_(True)))
    if user is None:
        burn_password_check(req.password)
        logger.info("login failed", extra={"extra_fields": {"reason": "unknown-user"}})
        raise HTTPException(status_code=401, detail="invalid username or password")
    if not verify_password(req.password, user.password_hash):
        logger.info("login failed", extra={"extra_fields": {"reason": "bad-password"}})
        raise HTTPException(status_code=401, detail="invalid username or password")
    append_event(
        db,
        actor=user.username,
        role=user.role,
        action="login",
        entity_type="Session",
        entity_id=user.username,
    )
    db.commit()
    return _tokens(user, "password")


@router.post("/refresh", response_model=TokenResponse, dependencies=[RefreshLimited])
def refresh(req: RefreshRequest, db: DbSession) -> TokenResponse:
    try:
        claims = decode_token(req.refresh_token, "refresh")
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user = db.scalar(select(User).where(User.username == claims["sub"], User.active.is_(True)))
    if user is None:
        raise HTTPException(status_code=401, detail="unknown or disabled user")
    return _tokens(user, str(claims.get("amr", "password")))


@router.post("/abha/token", response_model=TokenResponse, dependencies=[Limited])
def abha_token(req: AbhaTokenRequest, db: DbSession) -> TokenResponse:
    settings = get_settings()
    if settings.effective_abha_mode != "mock":
        raise HTTPException(
            status_code=501, detail="ABHA LIVE mode needs the ABDM gateway integration (see docs)"
        )
    user = db.scalar(select(User).where(User.abha_address == req.abha_address, User.active.is_(True)))
    if user is None or req.otp != settings.abha_mock_otp:
        raise HTTPException(status_code=401, detail="ABHA verification failed (mock)")
    append_event(
        db, actor=user.username, role=user.role, action="login", entity_type="Session", entity_id="abha-mock"
    )
    db.commit()
    return _tokens(user, "abha-mock")


@router.get("/me", response_model=UserInfo)
def me(principal: Annotated[Principal, Depends(current_principal)]) -> UserInfo:
    return UserInfo(
        username=principal.username,
        display_name=principal.display_name,
        role=principal.role,
        permissions=sorted(POLICY[principal.role]),
        auth_method=principal.auth_method,
    )
