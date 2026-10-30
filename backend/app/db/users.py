"""Demo accounts, one per role. Passwords come from env (DEMO_<ROLE>_PASSWORD); documented dev defaults are
used only outside production. Existing accounts are never overwritten."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, demo_password, get_settings
from app.core.security import hash_password
from app.db.models import User

DEMO_USERS = [
    ("admin", "Demo Admin", None),
    ("clinician", "Dr. Demo Clinician", "dr.demo@sbx"),
    ("coder", "Demo Records Officer", None),
    ("viewer", "Demo Viewer", None),
]


def ensure_demo_users(session: Session, settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    created: list[str] = []
    for role, display, abha in DEMO_USERS:
        password = demo_password(settings, role)
        if password is None or session.scalar(select(User).where(User.username == role)) is not None:
            continue
        session.add(
            User(
                username=role,
                display_name=display,
                role=role,
                password_hash=hash_password(password),
                abha_address=abha,
            )
        )
        created.append(role)
    session.flush()
    return created
