from __future__ import annotations

from itertools import pairwise

import pytest
from sqlalchemy import exc, select, text
from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.db.models import AuditEvent
from app.ledger.hash_chain import GENESIS_HASH, append_event
from app.ledger.verify import verify_chain


def _append(session: Session, n: int) -> list[AuditEvent]:
    events = [
        append_event(
            session,
            actor=f"user{i}",
            role="clinician",
            action="approve",
            entity_type="Mapping",
            entity_id=str(i),
            before={"status": "pending"},
            after={"status": "approved", "term": "आमवात", "score": 0.92},
        )
        for i in range(n)
    ]
    session.commit()
    return events


def _drop_guards(session: Session) -> None:
    """Simulate an attacker with raw DB access: remove the append-only triggers first."""
    session.execute(text("DROP TRIGGER audit_event_no_update"))
    session.execute(text("DROP TRIGGER audit_event_no_delete"))
    session.commit()


def test_empty_chain_verifies(fresh_db: Session) -> None:
    assert verify_chain(fresh_db).model_dump() == {
        "ok": True,
        "checked": 0,
        "first_broken_id": None,
        "reason": None,
        "head_hash": None,
    }


def test_chain_links_and_verifies(fresh_db: Session) -> None:
    token = request_id_var.set("req-42")
    try:
        events = _append(fresh_db, 5)
    finally:
        request_id_var.reset(token)
    assert events[0].prev_hash == GENESIS_HASH
    assert all(b.prev_hash == a.hash for a, b in pairwise(events))
    assert events[0].request_id == "req-42"
    result = verify_chain(fresh_db, batch_size=2)  # exercise batching
    assert result.ok and result.checked == 5 and result.head_hash == events[-1].hash


def test_append_only_triggers_block_update_and_delete(fresh_db: Session) -> None:
    _append(fresh_db, 1)
    with pytest.raises(exc.DBAPIError, match="append-only"):
        fresh_db.execute(text("UPDATE audit_event SET actor = 'mallory'"))
    fresh_db.rollback()
    with pytest.raises(exc.DBAPIError, match="append-only"):
        fresh_db.execute(text("DELETE FROM audit_event"))
    fresh_db.rollback()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("actor", "'mallory'"),
        ("after", '\'{"status": "rejected"}\''),
        ("ts", "'2020-01-01T00:00:00.000000+00:00'"),
        ("request_id", "'forged'"),
    ],
)
def test_content_tampering_is_detected(fresh_db: Session, column: str, value: str) -> None:
    events = _append(fresh_db, 4)
    _drop_guards(fresh_db)
    fresh_db.execute(text(f"UPDATE audit_event SET {column} = {value} WHERE id = :id"), {"id": events[2].id})
    fresh_db.commit()
    result = verify_chain(fresh_db)
    assert not result.ok
    assert result.first_broken_id == events[2].id
    assert result.reason == "content hash mismatch"
    assert result.checked == 2


def test_rehashing_a_row_breaks_the_next_link(fresh_db: Session) -> None:
    events = _append(fresh_db, 3)
    _drop_guards(fresh_db)
    fresh_db.execute(
        text("UPDATE audit_event SET hash = :h WHERE id = :id"), {"h": "f" * 64, "id": events[0].id}
    )
    fresh_db.commit()
    result = verify_chain(fresh_db)
    assert not result.ok and result.first_broken_id == events[0].id


def test_deleted_row_is_detected(fresh_db: Session) -> None:
    events = _append(fresh_db, 4)
    _drop_guards(fresh_db)
    fresh_db.execute(text("DELETE FROM audit_event WHERE id = :id"), {"id": events[1].id})
    fresh_db.commit()
    result = verify_chain(fresh_db)
    assert not result.ok
    assert result.first_broken_id == events[2].id
    assert result.reason == "prev_hash does not match"


def test_event_rolls_back_with_its_transaction(fresh_db: Session) -> None:
    _append(fresh_db, 1)
    append_event(fresh_db, actor="a", role="r", action="x", entity_type="t", entity_id="1")
    fresh_db.rollback()
    assert len(fresh_db.scalars(select(AuditEvent)).all()) == 1
    assert verify_chain(fresh_db).ok
