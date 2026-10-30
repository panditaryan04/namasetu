"""Clinician review queue and the Approved Mapping Store.

Nothing below the HIGH band, and no biomedical link, enters the Approved Mapping Store (and therefore the
ConceptMap / Condition output) without a clinician decision recorded here. Every decision is versioned,
has a Provenance and is written to the hash-chained ledger. Decisions and comments are kept for future
feedback learning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.rbac import Principal
from app.db.models import SYSTEM_BIO, SYSTEM_NAMASTE, SYSTEM_TM2, ConceptLink, Mapping, ReviewItem
from app.engine.service import MappingEngine, MapResult
from app.fhir.audit_provenance import record_provenance
from app.ledger.hash_chain import append_event

Action = Literal["approve", "approve_wider", "approve_narrower", "choose_other", "reject"]
RELATIONSHIPS = ("equivalent", "wider", "narrower", "relatedto")


class WorkflowError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class DecisionRequest(BaseModel):
    action: Action
    tm2_code: str | None = Field(
        None, max_length=64, description="Required for choose_other (incl. manual search)"
    )
    relationship: Literal["equivalent", "wider", "narrower", "relatedto"] | None = None
    comment: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _needs_target(self) -> DecisionRequest:
        if self.action == "choose_other" and not self.tm2_code:
            raise ValueError("choose_other requires tm2_code")
        return self


class BioLinkRequest(BaseModel):
    tm2_code: str = Field(min_length=1, max_length=64)
    bio_code: str = Field(min_length=1, max_length=64)
    comment: str = Field(min_length=3, max_length=2000, description="Clinical justification (mandatory)")


# ------------------------------------------------------------------ queue


def approved_mapping(session: Session, code: str) -> Mapping | None:
    return session.scalars(
        select(Mapping)
        .where(
            Mapping.source_code == code, Mapping.status == "approved", Mapping.source_system == SYSTEM_NAMASTE
        )
        .order_by(Mapping.version.desc(), Mapping.id.desc())
        .limit(1)
    ).first()


def pending_item(session: Session, code: str) -> ReviewItem | None:
    return session.scalars(
        select(ReviewItem).where(
            ReviewItem.source_code == code, ReviewItem.status == "pending", ReviewItem.kind == "mapping"
        )
    ).first()


def enqueue(
    session: Session,
    engine: MappingEngine,
    *,
    namaste_code: str,
    reason: str,
    actor: Principal | str,
    query_text: str | None = None,
    proposed_target: str | None = None,
    result: MapResult | None = None,
) -> ReviewItem:
    """Queue a NAMASTE code for review (idempotent: an existing pending item is returned)."""
    rec = engine.index.get(SYSTEM_NAMASTE, namaste_code)
    if rec is None or rec.is_category:
        raise WorkflowError(404, f"NAMASTE code {namaste_code} not found")
    if proposed_target and not engine.index.is_valid(SYSTEM_TM2, proposed_target):
        raise WorkflowError(422, f"TM2 code {proposed_target} is not in the dataset")
    existing = pending_item(session, namaste_code)
    if existing is not None:
        return existing
    result = result or engine.map(code=namaste_code, top_k=5)
    top = result.candidates[0] if result.candidates else None
    who, role = (actor.username, actor.role) if isinstance(actor, Principal) else (actor, "system")
    item = ReviewItem(
        source_system=SYSTEM_NAMASTE,
        source_code=namaste_code,
        discipline=rec.discipline,
        query_text=query_text,
        band=top.band if top else "low",
        top_confidence=top.confidence if top else 0.0,
        proposed_target=proposed_target,
        reason=reason,
        candidates=[c.model_dump(mode="json") for c in result.candidates[:5]],
        dataset_releases=result.dataset_releases,
        ranker_version=result.model_version,
        created_by=who,
    )
    session.add(item)
    session.flush()
    append_event(
        session,
        actor=who,
        role=role,
        action="propose",
        entity_type="ReviewItem",
        entity_id=str(item.id),
        after={"source": namaste_code, "reason": reason, "band": item.band, "proposed": proposed_target},
    )
    return item


def route_if_medium(
    session: Session, engine: MappingEngine, result: MapResult, actor: Principal
) -> ReviewItem | None:
    """Stage 6: a resolved query whose best candidate is MEDIUM goes to the review queue automatically."""
    if result.status != "review" or result.resolved is None:
        return None
    code = result.resolved.code
    if approved_mapping(session, code) is not None:
        return None
    return enqueue(
        session,
        engine,
        namaste_code=code,
        reason="medium-band",
        actor=actor,
        query_text=result.query.text,
        result=result,
    )


def enqueue_unmapped(session: Session, engine: MappingEngine, actor: str = "system") -> int:
    """Queue every NAMASTE code with no approved mapping (or flagged for re-review after a release)."""
    count = 0
    for rec in engine.index.namaste:
        current = approved_mapping(session, rec.code)
        if current is not None and not current.needs_review:
            continue
        if pending_item(session, rec.code) is not None:
            continue
        result = engine.map(code=rec.code, top_k=5)
        if current is not None:
            reason = "re-review"
        elif not result.candidates:
            reason = "no-safe-match"
        else:
            reason = "high-band-confirm" if result.candidates[0].band == "high" else "medium-band"
        enqueue(session, engine, namaste_code=rec.code, reason=reason, actor=actor, result=result)
        count += 1
    return count


# ------------------------------------------------------------------ decisions


def decide(
    session: Session, engine: MappingEngine, item_id: int, req: DecisionRequest, principal: Principal
) -> ReviewItem:
    item = session.get(ReviewItem, item_id)
    if item is None:
        raise WorkflowError(404, f"review item {item_id} not found")
    if item.status != "pending":
        raise WorkflowError(409, f"review item {item_id} is already {item.status}")
    top = item.candidates[0] if item.candidates else None
    top_code = top["tm2"]["code"] if top else None
    by_code = {c["tm2"]["code"]: c for c in item.candidates}

    if req.action == "reject":
        override = top is not None
        target, relationship = None, None
    else:
        target = req.tm2_code or item.proposed_target or top_code
        if target is None:
            raise WorkflowError(
                422, "no candidate to approve; use choose_other with a manually searched TM2 code"
            )
        tm2 = engine.index.get(SYSTEM_TM2, target)
        if tm2 is None or tm2.is_category:
            raise WorkflowError(422, f"TM2 code {target} is not a selectable code in the dataset")
        suggested_rel = by_code.get(target, {}).get("relationship")
        relationship = (
            {
                "approve_wider": "wider",
                "approve_narrower": "narrower",
            }.get(req.action)
            or req.relationship
            or suggested_rel
            or "relatedto"
        )
        override = target != top_code or relationship != (top or {}).get("relationship")
    if override and not (req.comment and req.comment.strip()):
        raise WorkflowError(422, "a comment is mandatory when overriding the top suggestion")

    now = datetime.now(UTC)
    before: dict[str, Any] | None = None
    mapping: Mapping | None = None
    if target is not None and relationship is not None:
        previous = approved_mapping(session, item.source_code)
        if previous is not None:
            before = {
                "mapping": previous.id,
                "target": previous.target_code,
                "relationship": previous.relationship,
                "version": previous.version,
            }
            previous.status = "superseded"
        last_version = session.scalar(
            select(func.max(Mapping.version)).where(Mapping.source_code == item.source_code)
        )
        mapping = Mapping(
            source_system=SYSTEM_NAMASTE,
            source_code=item.source_code,
            target_system=SYSTEM_TM2,
            target_code=target,
            relationship=relationship,
            status="approved",
            version=(last_version or 0) + 1,
            reviewer=principal.username,
            ranker_version=item.ranker_version,
            dataset_releases=list(item.dataset_releases),
        )
        session.add(mapping)
        session.flush()
        record_provenance(
            session,
            target=f"Mapping/{mapping.id}",
            activity="approve",
            agent=principal.username,
            entities=[
                f"ReviewItem/{item.id}",
                *(item.dataset_releases or []),
                item.ranker_version or "manual",
            ],
            reason=req.comment or f"{req.action} (suggested)",
        )

    item.status = "rejected" if req.action == "reject" else "approved"
    item.decided_by = principal.username
    item.decided_at = now
    item.comment = req.comment
    item.mapping_id = mapping.id if mapping else None
    item.decision = {
        "action": req.action,
        "target": target,
        "relationship": relationship,
        "override": override,
    }
    append_event(
        session,
        actor=principal.username,
        role=principal.role,
        action="reject" if req.action == "reject" else "approve",
        entity_type="Mapping" if mapping else "ReviewItem",
        entity_id=str(mapping.id) if mapping else str(item.id),
        before=before,
        after={**item.decision, "source": item.source_code, "review_item": item.id, "comment": req.comment},
    )
    return item


def add_bio_link(
    session: Session, engine: MappingEngine, req: BioLinkRequest, principal: Principal
) -> ConceptLink:
    """Clinician-approved TM2 -> biomedicine link. Always `relatedto` (never a diagnosis equivalence)."""
    tm2 = engine.index.get(SYSTEM_TM2, req.tm2_code)
    bio = engine.index.get(SYSTEM_BIO, req.bio_code)
    if tm2 is None or tm2.is_category:
        raise WorkflowError(422, f"TM2 code {req.tm2_code} is not in the dataset")
    if bio is None or bio.is_category:
        raise WorkflowError(422, f"biomedical code {req.bio_code} is not in the dataset")
    link = session.scalars(
        select(ConceptLink).where(
            ConceptLink.source_code == tm2.code,
            ConceptLink.target_code == bio.code,
            ConceptLink.origin == "clinician",
        )
    ).first()
    if link is None:
        link = ConceptLink(
            source_system=SYSTEM_TM2,
            source_code=tm2.code,
            target_system=SYSTEM_BIO,
            target_code=bio.code,
            relationship="relatedto",
            origin="clinician",
            status="approved",
        )
        session.add(link)
    else:
        link.status = "approved"
    session.flush()
    record_provenance(
        session,
        target=f"ConceptLink/{link.id}",
        activity="approve",
        agent=principal.username,
        entities=[f"{tm2.code}->{bio.code}"],
        reason=req.comment,
    )
    append_event(
        session,
        actor=principal.username,
        role=principal.role,
        action="approve",
        entity_type="ConceptLink",
        entity_id=str(link.id),
        after={"tm2": tm2.code, "bio": bio.code, "relationship": "relatedto", "comment": req.comment},
    )
    return link
