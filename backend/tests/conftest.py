from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

# Force deterministic LOCAL mode for tests, regardless of the developer's .env.
_TMP = Path(tempfile.mkdtemp(prefix="namasetu-test-"))
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{_TMP.as_posix()}/app.db",
        "REDIS_URL": "",
        "ICD11_MODE": "auto",
        "ICD_CLIENT_ID": "",
        "ICD_CLIENT_SECRET": "",
        "ICD11_CACHE_DIR": str(_TMP / "icd11-cache"),
        "ABHA_MODE": "mock",
        "SEMANTIC_MODE": "off",
        "RANKER_ARTIFACT": str(_TMP / "ranker.json"),
    }
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.ratelimit import limiter
from app.core.security import create_token
from app.db.migrate import upgrade_to_head
from app.db.seed import load_bundle, seed_sample
from app.db.session import get_sessionmaker
from app.db.users import ensure_demo_users
from app.main import create_app


@pytest.fixture(scope="session", autouse=True)
def seeded_app_db() -> None:
    """The app database: migrated and seeded once from embedded_db.json, exactly like LOCAL mode."""
    upgrade_to_head()
    with get_sessionmaker()() as session:
        seed_sample(session, load_bundle(use_embedded=True))
        ensure_demo_users(session)
        session.commit()


def auth_header(role: str) -> dict[str, str]:
    """Bearer token for the seeded demo user of `role` (minted directly; no login round-trip)."""
    return {"Authorization": f"Bearer {create_token(role, role, 'access')}"}


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> None:
    limiter.reset()


@pytest.fixture
def anon() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Authenticated as the demo admin (all permissions)."""
    with TestClient(create_app(), headers=auth_header("admin")) as c:
        yield c


@pytest.fixture
def as_role() -> Iterator[Callable[[str], TestClient]]:
    clients: list[TestClient] = []

    def make(role: str) -> TestClient:
        c = TestClient(create_app(), headers=auth_header(role))
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def fresh_db(tmp_path: Path) -> Iterator[Session]:
    """An isolated, migrated, empty SQLite database."""
    url = f"sqlite:///{tmp_path.as_posix()}/fresh.db"
    upgrade_to_head(url)
    engine = create_engine(url)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def sample_bundle() -> dict[str, object]:
    return load_bundle(use_embedded=False)
