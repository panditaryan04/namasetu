"""Role-based access control, centralised (in the spirit of ISO 22600: policy decided in one place).

Roles are hierarchical in what they may do; each endpoint declares the *permission* it needs, never a role.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.api.common import DbSession
from app.core.security import TokenError, decode_token
from app.db.models import User

Role = Literal["admin", "clinician", "coder", "viewer"]
Permission = Literal[
    "read",  # search, map, terminology reads, audit reads
    "propose",  # send a mapping to the review queue
    "review",  # approve / reject / override mappings, add biomedical links
    "build_condition",  # emit patient-level FHIR (Condition) and ingest Bundles
    "admin",  # ingest datasets, change thresholds, manage users
    "patient",  # read / import / check a patient's medication history (synthetic in the sandbox)
]

POLICY: dict[Role, frozenset[Permission]] = {
    "viewer": frozenset({"read"}),
    "coder": frozenset({"read", "propose"}),
    "clinician": frozenset({"read", "propose", "review", "build_condition", "patient"}),
    "admin": frozenset({"read", "propose", "review", "build_condition", "admin", "patient"}),
}

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    username: str
    role: Role
    display_name: str
    auth_method: str

    def can(self, permission: Permission) -> bool:
        return permission in POLICY.get(self.role, frozenset())


def current_principal(
    request: Request,
    db: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401, detail="authentication required", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        claims = decode_token(creds.credentials, "access")
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    user = db.scalar(select(User).where(User.username == claims["sub"], User.active.is_(True)))
    if user is None:
        raise HTTPException(status_code=401, detail="unknown or disabled user")
    principal = Principal(user.username, user.role, user.display_name, str(claims.get("amr", "password")))  # type: ignore[arg-type]
    request.state.principal = principal
    return principal


def require(permission: Permission):  # type: ignore[no-untyped-def]
    """FastAPI dependency factory: `Depends(require("review"))`."""

    def check(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if not principal.can(permission):
            raise HTTPException(
                status_code=403, detail=f"role '{principal.role}' lacks permission '{permission}'"
            )
        return principal

    return check


Reader = Annotated[Principal, Depends(require("read"))]
Proposer = Annotated[Principal, Depends(require("propose"))]
Reviewer = Annotated[Principal, Depends(require("review"))]
ConditionBuilder = Annotated[Principal, Depends(require("build_condition"))]
Admin = Annotated[Principal, Depends(require("admin"))]
PatientAccess = Annotated[Principal, Depends(require("patient"))]
