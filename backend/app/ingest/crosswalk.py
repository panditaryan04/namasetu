"""Import the official NAMASTE -> TM2 crosswalk (the gold release) into the Approved Mapping Store.

Official mappings are authoritative, so they are stored as approved with reviewer "official-crosswalk",
each with a Provenance. With SAMPLE data only the TRAIN split is imported (STAGE0_GOLD_SPLIT=train), so
the TEST split stays held out for evaluation. Idempotent: existing approved pairs are not duplicated.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SOURCE_GOLD, SYSTEM_NAMASTE, SYSTEM_TM2, GoldMapping, Mapping
from app.fhir.audit_provenance import record_provenance
from app.ingest.release import latest_release
from app.ledger.hash_chain import append_event

CROSSWALK_REVIEWER = "official-crosswalk"


def import_official_crosswalk(session: Session, split: str = "train", actor: str = "system") -> int:
    if split == "none":
        return 0
    gold_release = latest_release(session, SOURCE_GOLD)
    if gold_release is None:
        return 0
    q = select(GoldMapping)
    if split != "all":
        q = q.where(GoldMapping.split == split)
    existing = {
        (m.source_code, m.target_code)
        for m in session.scalars(
            select(Mapping).where(Mapping.status == "approved", Mapping.reviewer == CROSSWALK_REVIEWER)
        ).all()
    }
    added: list[Mapping] = []
    for g in session.scalars(q.order_by(GoldMapping.namaste_code)).all():
        if (g.namaste_code, g.tm2_code) in existing:
            continue
        m = Mapping(
            source_system=SYSTEM_NAMASTE,
            source_code=g.namaste_code,
            target_system=SYSTEM_TM2,
            target_code=g.tm2_code,
            relationship=g.relationship,
            status="approved",
            reviewer=CROSSWALK_REVIEWER,
            dataset_releases=[gold_release.tag],
        )
        session.add(m)
        added.append(m)
    if not added:
        return 0
    session.flush()
    for m in added:
        record_provenance(
            session, target=f"Mapping/{m.id}", activity="import", agent=CROSSWALK_REVIEWER,
            entities=[gold_release.tag], reason=f"{m.source_code} -> {m.target_code} ({m.relationship})",
        )  # fmt: skip
    append_event(
        session, actor=actor, role="admin", action="crosswalk_import", entity_type="MappingStore",
        entity_id=gold_release.tag, after={"added": len(added), "split": split},
    )  # fmt: skip
    return len(added)
