"""Postgres-only behaviour. Runs when TEST_POSTGRES_URL points at a server we may create databases on,
e.g. postgresql+psycopg://namasetu:namasetu@localhost:5432/postgres (CI uses a service container)."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, exc, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from app.db.migrate import upgrade_to_head
from app.db.models import AuditEvent, Concept, DatasetRelease
from app.db.seed import seed_sample
from app.ledger.hash_chain import append_event
from app.ledger.verify import verify_chain

ADMIN_URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ADMIN_URL, reason="TEST_POSTGRES_URL not set"),
]
TEST_DB = "namasetu_pytest"


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[Engine]:
    assert ADMIN_URL
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    url = make_url(ADMIN_URL).set(database=TEST_DB).render_as_string(hide_password=False)
    upgrade_to_head(url)
    engine = create_engine(url)
    yield engine
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
    admin.dispose()


def test_extensions_installed(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        names = set(conn.scalars(text("SELECT extname FROM pg_extension")))
    assert {"pg_trgm", "unaccent"} <= names


def test_seed_on_postgres(pg_engine: Engine, sample_bundle: dict[str, Any]) -> None:
    with Session(pg_engine) as session:
        seed_sample(session, sample_bundle)
        session.commit()
        assert (
            session.scalar(select(func.count()).select_from(DatasetRelease)) == 6
        )  # incl. medications, interactions
        deva = session.scalar(select(Concept).where(Concept.term_devanagari == "अम्लपित्त"))
        assert deva is not None and deva.code == "SAMPLE-AYU-0009"
        assert verify_chain(session).ok


def test_append_only_trigger(pg_engine: Engine) -> None:
    with Session(pg_engine) as session:
        append_event(session, actor="a", role="admin", action="x", entity_type="t", entity_id="1")
        session.commit()
        with pytest.raises(exc.DBAPIError, match="append-only"):
            session.execute(text("UPDATE audit_event SET actor = 'mallory'"))
        session.rollback()
        with pytest.raises(exc.DBAPIError, match="append-only"):
            session.execute(text("DELETE FROM audit_event"))
        session.rollback()


def test_concurrent_appends_keep_a_single_chain(pg_engine: Engine) -> None:
    errors: list[BaseException] = []

    def writer(n: int) -> None:
        try:
            for i in range(15):
                with Session(pg_engine) as s:
                    append_event(
                        s, actor=f"w{n}", role="coder", action="propose", entity_type="t", entity_id=str(i)
                    )
                    s.commit()
        except BaseException as e:  # noqa: BLE001 - surface any thread failure to the test
            errors.append(e)

    with Session(pg_engine) as s:
        before = s.scalar(select(func.count()).select_from(AuditEvent)) or 0
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with Session(pg_engine) as s:
        assert (s.scalar(select(func.count()).select_from(AuditEvent)) or 0) == before + 60
        result = verify_chain(s)
        assert result.ok, result
