from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    SOURCE_NAMASTE,
    SYSTEM_NAMASTE,
    SYSTEM_TM2,
    AuditEvent,
    Concept,
    ConceptLink,
    ConceptTerm,
    DatasetRelease,
    GoldMapping,
    Mapping,
)
from app.db.seed import seed_sample
from app.ingest.gold_loader import GoldIn
from app.ingest.namaste_loader import parse_namaste
from app.ingest.release import current_roots, ingest_concepts, ingest_gold, ingest_synonyms
from app.ingest.schemas import IngestReport
from app.ingest.synonyms_loader import parse_synonyms
from app.ledger.verify import verify_chain


def _count(session: Session, model: Any) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _seed(session: Session, bundle: dict[str, Any]) -> None:
    seed_sample(session, bundle)
    session.commit()


def test_seed_loads_all_sources(fresh_db: Session, sample_bundle: dict[str, Any]) -> None:
    _seed(fresh_db, sample_bundle)
    tags = [r.tag for r in fresh_db.scalars(select(DatasetRelease).order_by(DatasetRelease.id))]
    assert tags == [
        "namaste@SAMPLE-1",
        "icd11@SAMPLE-1",
        "synonyms@SAMPLE-1",
        "gold@SAMPLE-1",
        "medications@SAMPLE-1",
        "interactions@SAMPLE-1",
    ]
    assert all(r.is_sample for r in fresh_db.scalars(select(DatasetRelease)))
    assert _count(fresh_db, GoldMapping) == 53
    assert _count(fresh_db, ConceptLink) == 8
    amavata = fresh_db.scalar(select(Concept).where(Concept.code == "SAMPLE-AYU-0001"))
    assert amavata is not None
    kinds = {t.kind for t in amavata.terms}
    assert kinds == {"primary", "iast", "devanagari", "english", "hinglish"}
    assert "aamvaat" in {t.term for t in amavata.terms}
    assert "amla" in current_roots(fresh_db)
    events = fresh_db.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
    actions = [e.action for e in events]
    assert actions[:7] == ["ingest"] * 4 + ["crosswalk_import", "ingest", "ingest"]
    assert actions[7:] == ["history_import"] * 10  # synthetic patients, counts only
    assert verify_chain(fresh_db).ok


def test_reseeding_identical_data_is_a_noop(fresh_db: Session, sample_bundle: dict[str, Any]) -> None:
    _seed(fresh_db, sample_bundle)
    before = (_count(fresh_db, DatasetRelease), _count(fresh_db, AuditEvent), _count(fresh_db, ConceptTerm))
    results = seed_sample(fresh_db, sample_bundle)
    fresh_db.commit()
    assert all(r.skipped for r in results)
    assert (
        _count(fresh_db, DatasetRelease),
        _count(fresh_db, AuditEvent),
        _count(fresh_db, ConceptTerm),
    ) == before


def test_new_namaste_release_diffs_and_flags_mappings(
    fresh_db: Session, sample_bundle: dict[str, Any]
) -> None:
    _seed(fresh_db, sample_bundle)
    # Clinician-approved mappings that the new release will touch (and one it won't).
    for code in ("SAMPLE-AYU-0001", "SAMPLE-AYU-0002", "SAMPLE-AYU-0009"):
        fresh_db.add(
            Mapping(
                source_system=SYSTEM_NAMASTE,
                source_code=code,
                target_system=SYSTEM_TM2,
                target_code="SAMPLE-TM2-0111",
                relationship="equivalent",
            )
        )
    fresh_db.commit()

    rows = copy.deepcopy(sample_bundle["namaste_rows"])
    for r in rows:
        if r["NAMC_CODE"] == "SAMPLE-AYU-0001":
            r["Short_definition"] = "Revised definition."
    rows = [r for r in rows if r["NAMC_CODE"] != "SAMPLE-AYU-0002"]
    rows.append({"NAMC_CODE": "SAMPLE-AYU-0099", "NAMC_term": "Shotha", "Entity_type": "disorder"})
    concepts, report = parse_namaste(rows)
    result = ingest_concepts(
        fresh_db,
        source=SOURCE_NAMASTE,
        version="SAMPLE-2",
        systems=[SYSTEM_NAMASTE],
        concepts=concepts,
        report=report,
        is_sample=True,
    )
    fresh_db.commit()

    assert not result.skipped
    assert result.diff["previous"] == "namaste@SAMPLE-1"
    assert result.diff["added"] == ["SAMPLE-AYU-0099"]
    assert result.diff["removed"] == ["SAMPLE-AYU-0002"]
    assert result.diff["changed"] == [
        {"system": SYSTEM_NAMASTE, "code": "SAMPLE-AYU-0001", "fields": ["definition"]}
    ]
    assert result.release.previous_id is not None

    removed = fresh_db.scalar(select(Concept).where(Concept.code == "SAMPLE-AYU-0002"))
    assert removed is not None and removed.active is False
    flagged = fresh_db.scalars(select(Mapping).where(Mapping.needs_review.is_(True))).all()
    assert {m.source_code for m in flagged} == {"SAMPLE-AYU-0001", "SAMPLE-AYU-0002"}
    assert sorted(result.diff["mappings_flagged"]) == sorted(m.id for m in flagged)
    assert "namaste@SAMPLE-2" in (flagged[0].review_reason or "")

    # Reintroducing the removed code reactivates it.
    concepts2, report2 = parse_namaste(sample_bundle["namaste_rows"])
    again = ingest_concepts(
        fresh_db,
        source=SOURCE_NAMASTE,
        version="SAMPLE-3",
        systems=[SYSTEM_NAMASTE],
        concepts=concepts2,
        report=report2,
        is_sample=True,
    )
    fresh_db.commit()
    assert again.diff["removed"] == ["SAMPLE-AYU-0099"]
    assert {c["code"] for c in again.diff["changed"]} == {"SAMPLE-AYU-0001", "SAMPLE-AYU-0002"}
    assert removed.active is True
    assert verify_chain(fresh_db).ok


def test_synonym_release_rejects_unknown_codes_and_diffs(
    fresh_db: Session, sample_bundle: dict[str, Any]
) -> None:
    _seed(fresh_db, sample_bundle)
    data = copy.deepcopy(sample_bundle["synonyms"])
    data["version"] = "SAMPLE-2"
    data["concepts"]["SAMPLE-AYU-0001"]["hinglish"].append("amvat")
    data["concepts"]["NOT-A-CODE"] = {"english": ["ghost"]}
    synonyms, report = parse_synonyms(data)
    result = ingest_synonyms(fresh_db, synonyms, report, is_sample=True)
    fresh_db.commit()
    assert result.diff["changed"] == ["SAMPLE-AYU-0001"]
    assert [i["code"] for i in result.release.report["rejected"]] == ["NOT-A-CODE"]
    assert fresh_db.scalar(select(func.count()).where(ConceptTerm.term == "ghost")) == 0


def test_gold_release_validates_codes(fresh_db: Session, sample_bundle: dict[str, Any]) -> None:
    _seed(fresh_db, sample_bundle)
    gold = [
        GoldIn(
            namaste_code="SAMPLE-AYU-0001",
            tm2_code="SAMPLE-TM2-0111",
            relationship="equivalent",
            split="train",
        ),
        GoldIn(
            namaste_code="SAMPLE-AYU-0001",
            tm2_code="SAMPLE-TM2-9999",
            relationship="equivalent",
            split="test",
        ),
        GoldIn(namaste_code="NOPE", tm2_code="SAMPLE-TM2-0111", relationship="equivalent", split="test"),
    ]
    report = IngestReport(source="gold")
    result = ingest_gold(fresh_db, gold, report, version="SAMPLE-2", is_sample=True)
    fresh_db.commit()
    assert result.release.row_count == 1
    assert {i.problem for i in report.rejected} == {"unknown TM2 code", "unknown NAMASTE code"}
    assert len(result.diff["removed"]) == 52
    assert _count(fresh_db, GoldMapping) == 1


def test_icd11_release_replaces_who_links(fresh_db: Session, sample_bundle: dict[str, Any]) -> None:
    from app.db.models import SOURCE_ICD11, SYSTEM_BIO
    from app.ingest.icd11_client import MockICD11Client

    _seed(fresh_db, sample_bundle)
    tm2 = copy.deepcopy(sample_bundle["icd11_tm2"])
    tm2["release"] = "SAMPLE-2"
    for e in tm2["entities"]:
        if e["code"] == "SAMPLE-TM2-0111":
            e.pop("bio_links")
    snap = MockICD11Client(tm2, sample_bundle["icd11_bio"]).fetch()
    result = ingest_concepts(
        fresh_db,
        source=SOURCE_ICD11,
        version=snap.release,
        systems=[SYSTEM_TM2, SYSTEM_BIO],
        concepts=snap.tm2 + snap.bio,
        links=snap.links,
        report=snap.report,
        is_sample=True,
    )
    fresh_db.commit()
    assert result.diff["links_removed"] == ["SAMPLE-TM2-0111->SAMPLE-BIO-0001"]
    assert result.diff["changed"] == []  # links are diffed separately; the concept itself is unchanged
    assert _count(fresh_db, ConceptLink) == 7
