"""Dataset releases: versioned, checksummed ingests with a diff against the previous release.

Each ingest_* function runs inside the caller's transaction and appends a ledger event, so a release,
its concept changes and its audit record commit (or roll back) together. An ingest whose checksum
equals the latest release of the same source is a no-op.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    SOURCE_GOLD,
    SOURCE_SYNONYMS,
    SYSTEM_NAMASTE,
    SYSTEM_TM2,
    Concept,
    ConceptLink,
    ConceptTerm,
    DatasetRelease,
    GoldMapping,
    Mapping,
)
from app.ingest.gold_loader import GoldIn
from app.ingest.schemas import ConceptIn, IngestReport, LinkIn, RowIssue
from app.ingest.synonyms_loader import SynonymSet
from app.ledger.hash_chain import append_event, canonical_json


@dataclass
class ReleaseResult:
    release: DatasetRelease
    skipped: bool
    diff: dict[str, Any] = field(default_factory=dict)


def checksum_of(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def latest_release(session: Session, source: str) -> DatasetRelease | None:
    return session.scalars(
        select(DatasetRelease)
        .where(DatasetRelease.source == source)
        .order_by(DatasetRelease.id.desc())
        .limit(1)
    ).first()


def _content(c: ConceptIn) -> dict[str, Any]:
    return {**c.content_fields(), "terms": [t.model_dump() for t in c.terms]}


def _open_release(
    session: Session,
    *,
    source: str,
    version: str,
    checksum: str,
    row_count: int,
    is_sample: bool,
    report: dict[str, Any],
    actor: str,
) -> tuple[DatasetRelease | None, DatasetRelease | None]:
    """Return (new_release, previous). new_release is None when the checksum is unchanged."""
    prev = latest_release(session, source)
    if prev is not None and prev.checksum == checksum:
        return None, prev
    release = DatasetRelease(
        source=source,
        version=version,
        checksum=checksum,
        row_count=row_count,
        is_sample=is_sample,
        previous_id=prev.id if prev else None,
        report=report,
        ingested_by=actor,
    )
    session.add(release)
    session.flush()
    return release, prev


def _flag_mappings(session: Session, codes: set[str], reason: str) -> list[int]:
    if not codes:
        return []
    rows = session.scalars(
        select(Mapping).where(
            Mapping.status == "approved",
            or_(Mapping.source_code.in_(codes), Mapping.target_code.in_(codes)),
        )
    ).all()
    for m in rows:
        m.needs_review = True
        m.review_reason = reason
    return [m.id for m in rows]


def _close_release(
    session: Session,
    release: DatasetRelease,
    prev: DatasetRelease | None,
    diff: dict[str, Any],
    actor: str,
    role: str,
) -> ReleaseResult:
    diff = {"previous": prev.tag if prev else None, **diff}
    release.diff = diff
    summary = {k: len(v) if isinstance(v, list) else v for k, v in diff.items()}
    append_event(
        session,
        actor=actor,
        role=role,
        action="ingest",
        entity_type="DatasetRelease",
        entity_id=release.tag,
        after={"checksum": release.checksum, "row_count": release.row_count, "diff": summary},
    )
    return ReleaseResult(release=release, skipped=False, diff=diff)


# ---------------------------------------------------------------- concepts (NAMASTE, ICD-11)


def ingest_concepts(
    session: Session,
    *,
    source: str,
    version: str,
    systems: list[str],
    concepts: list[ConceptIn],
    report: IngestReport,
    is_sample: bool,
    links: list[LinkIn] | None = None,
    actor: str = "system",
    role: str = "admin",
) -> ReleaseResult:
    links = links or []
    payload = {
        "concepts": sorted((_content(c) for c in concepts), key=lambda d: (d["system"], d["code"])),
        "links": sorted(
            (lk.model_dump() for lk in links), key=lambda d: (d["source_code"], d["target_code"])
        ),
    }
    release, prev = _open_release(
        session,
        source=source,
        version=version,
        checksum=checksum_of(payload),
        row_count=len(concepts),
        is_sample=is_sample,
        report=report.model_dump(),
        actor=actor,
    )
    if release is None:
        assert prev is not None
        return ReleaseResult(release=prev, skipped=True)

    existing = {
        (c.system, c.code): c
        for c in session.scalars(select(Concept).where(Concept.system.in_(systems))).all()
    }
    added: list[str] = []
    changed: list[dict[str, Any]] = []
    removed: list[str] = []

    for cin in concepts:
        content = _content(cin)
        digest = checksum_of(content)
        row = existing.get((cin.system, cin.code))
        if row is None:
            row = Concept(**cin.content_fields(), content_hash=digest, active=True, release_id=release.id)
            session.add(row)
            session.flush()
            added.append(cin.code)
        elif row.content_hash != digest or not row.active:
            fields = [k for k, v in cin.content_fields().items() if getattr(row, k) != v] or (
                ["terms"] if row.active else ["reactivated"]
            )
            for k, v in cin.content_fields().items():
                setattr(row, k, v)
            row.content_hash = digest
            row.active = True
            row.release_id = release.id
            session.execute(
                delete(ConceptTerm).where(ConceptTerm.concept_id == row.id, ConceptTerm.source == source)
            )
            changed.append({"system": cin.system, "code": cin.code, "fields": fields})
        else:
            continue
        session.add_all(
            ConceptTerm(concept_id=row.id, term=t.term, kind=t.kind, source=source, release_id=release.id)
            for t in cin.terms
        )

    incoming = {(c.system, c.code) for c in concepts}
    for key, row in existing.items():
        if key not in incoming and row.active:
            row.active = False
            row.release_id = release.id
            removed.append(row.code)

    # WHO-published TM2 -> biomedicine links are replaced wholesale by each ICD-11 release.
    links_added: list[str] = []
    links_removed: list[str] = []
    if SYSTEM_TM2 in systems:
        old = session.scalars(select(ConceptLink).where(ConceptLink.origin == "who-index")).all()
        old_keys = {(lk.source_code, lk.target_code) for lk in old}
        new_keys = {(lk.source_code, lk.target_code) for lk in links}
        links_removed = [f"{s}->{t}" for s, t in sorted(old_keys - new_keys)]
        links_added = [f"{s}->{t}" for s, t in sorted(new_keys - old_keys)]
        session.execute(delete(ConceptLink).where(ConceptLink.origin == "who-index"))
        session.add_all(
            ConceptLink(**lk.model_dump(), status="approved", release_id=release.id) for lk in links
        )

    touched = set(removed) | {c["code"] for c in changed}
    flagged = _flag_mappings(session, touched, f"{release.tag}: source or target code removed or changed")
    return _close_release(
        session,
        release,
        prev,
        {
            "added": added,
            "removed": removed,
            "changed": changed,
            "links_added": links_added,
            "links_removed": links_removed,
            "mappings_flagged": flagged,
        },
        actor,
        role,
    )


# ---------------------------------------------------------------- synonyms


def ingest_synonyms(
    session: Session,
    synonyms: SynonymSet,
    report: IngestReport,
    *,
    is_sample: bool,
    actor: str = "system",
    role: str = "admin",
) -> ReleaseResult:
    payload = synonyms.model_dump(exclude={"label"})
    namaste = {
        c.code: c
        for c in session.scalars(
            select(Concept).where(Concept.system == SYSTEM_NAMASTE, Concept.active.is_(True))
        ).all()
    }
    for n, code in enumerate(synonyms.entries, start=1):
        if code not in namaste:
            report.rejected.append(
                RowIssue(row=n, code=code, problem="no active NAMASTE concept with this code")
            )
    report.accepted = len(synonyms.entries) - len(report.rejected)

    release, prev = _open_release(
        session,
        source=SOURCE_SYNONYMS,
        version=synonyms.version,
        checksum=checksum_of(payload),
        row_count=sum(len(v) for v in synonyms.entries.values()),
        is_sample=is_sample,
        report={**report.model_dump(), "roots": synonyms.roots},
        actor=actor,
    )
    if release is None:
        assert prev is not None
        return ReleaseResult(release=prev, skipped=True)

    old_terms: dict[str, set[tuple[str, str]]] = {}
    for term, code in session.execute(
        select(ConceptTerm, Concept.code)
        .join(Concept, Concept.id == ConceptTerm.concept_id)
        .where(ConceptTerm.source == SOURCE_SYNONYMS)
    ).all():
        old_terms.setdefault(code, set()).add((term.kind, term.term))
    session.execute(delete(ConceptTerm).where(ConceptTerm.source == SOURCE_SYNONYMS))

    changed_codes: list[str] = []
    for code, terms in synonyms.entries.items():
        concept = namaste.get(code)
        if concept is None:
            continue
        if set(terms) != old_terms.get(code, set()):
            changed_codes.append(code)
        session.add_all(
            ConceptTerm(concept_id=concept.id, term=t, kind=k, source=SOURCE_SYNONYMS, release_id=release.id)
            for k, t in terms
        )
    dropped = sorted(set(old_terms) - set(synonyms.entries))
    return _close_release(
        session,
        release,
        prev,
        {"changed": changed_codes, "removed": dropped, "roots": len(synonyms.roots)},
        actor,
        role,
    )


def current_roots(session: Session) -> list[str]:
    rel = latest_release(session, SOURCE_SYNONYMS)
    roots = (rel.report or {}).get("roots", []) if rel else []
    return [str(r) for r in roots]


# ---------------------------------------------------------------- gold set


def ingest_gold(
    session: Session,
    gold: list[GoldIn],
    report: IngestReport,
    *,
    version: str,
    is_sample: bool,
    actor: str = "system",
    role: str = "admin",
) -> ReleaseResult:
    active = {
        (c.system, c.code) for c in session.scalars(select(Concept).where(Concept.active.is_(True))).all()
    }
    valid: list[GoldIn] = []
    for n, g in enumerate(gold, start=1):
        if (SYSTEM_NAMASTE, g.namaste_code) not in active:
            report.rejected.append(RowIssue(row=n, code=g.namaste_code, problem="unknown NAMASTE code"))
        elif (SYSTEM_TM2, g.tm2_code) not in active:
            report.rejected.append(RowIssue(row=n, code=g.tm2_code, problem="unknown TM2 code"))
        else:
            valid.append(g)
    report.accepted = len(valid)

    payload = sorted((g.model_dump() for g in valid), key=lambda d: (d["namaste_code"], d["tm2_code"]))
    release, prev = _open_release(
        session,
        source=SOURCE_GOLD,
        version=version,
        checksum=checksum_of(payload),
        row_count=len(valid),
        is_sample=is_sample,
        report=report.model_dump(),
        actor=actor,
    )
    if release is None:
        assert prev is not None
        return ReleaseResult(release=prev, skipped=True)

    old = {(g.namaste_code, g.tm2_code) for g in session.scalars(select(GoldMapping)).all()}
    new = {(g.namaste_code, g.tm2_code) for g in valid}
    session.execute(delete(GoldMapping))
    session.add_all(GoldMapping(**g.model_dump(), release_id=release.id) for g in valid)
    splits = {s: sum(1 for g in valid if g.split == s) for s in ("train", "test")}
    return _close_release(
        session,
        release,
        prev,
        {
            "added": [f"{a}->{b}" for a, b in sorted(new - old)],
            "removed": [f"{a}->{b}" for a, b in sorted(old - new)],
            "splits": splits,
        },
        actor,
        role,
    )
