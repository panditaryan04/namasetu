"""Condition -> reference medication links, ingredient (salt / herb) detail, and clinician decisions on links.

Only APPROVED links are ever shown as approved; sample links are labelled "illustrative sample".
Pending links are visible only in the review tab.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import Principal
from app.db.models import Medication, MedicationLink
from app.engine.index import TerminologyIndex
from app.fhir.audit_provenance import record_provenance
from app.ledger.hash_chain import append_event
from app.medication.catalog import DISCLAIMER, MedicationCatalog, MedicationView, SourceRef


class ConditionRef(BaseModel):
    system: str
    code: str
    display: str | None


class LinkInfo(BaseModel):
    id: int
    status: Literal["approved", "pending", "rejected"]
    status_label: str  # "approved" | "illustrative sample" | "pending review" | "rejected"
    reviewer: str | None
    source: SourceRef
    note: str | None


class LinkedMedication(BaseModel):
    medication: MedicationView
    link: LinkInfo


class ByConditionResponse(BaseModel):
    condition: ConditionRef
    ayush: list[LinkedMedication]
    biomedical: list[LinkedMedication]
    disclaimer: str = DISCLAIMER


class IngredientDetail(BaseModel):
    id: int
    kind: str
    canonical_name: str
    botanical_name: str | None
    iast: str | None
    devanagari: str | None
    synonyms: list[dict[str, str]]
    metal_mineral: bool
    medications: list[MedicationView]
    conditions: list[ConditionRef]
    disclaimer: str = DISCLAIMER


class LinkReviewItem(BaseModel):
    link: LinkInfo
    condition: ConditionRef
    medication: MedicationView


class LinkDecision(BaseModel):
    action: Literal["approve", "reject"]
    comment: str | None = Field(None, max_length=2000)


def _status_label(link: MedicationLink) -> str:
    if link.status == "approved":
        return "illustrative sample" if link.is_sample and link.decided_at is None else "approved"
    return "pending review" if link.status == "pending" else "rejected"


def link_info(link: MedicationLink) -> LinkInfo:
    return LinkInfo(
        id=link.id,
        status=link.status,
        status_label=_status_label(link),
        reviewer=link.reviewer,
        source=SourceRef(title=link.source_title, edition=link.source_edition, page=link.source_page),
        note=link.note,
    )


def condition_ref(index: TerminologyIndex, system: str, code: str) -> ConditionRef:
    rec = index.get(system, code)
    return ConditionRef(system=system, code=code, display=rec.display if rec else None)


def by_condition(
    session: Session, catalog: MedicationCatalog, index: TerminologyIndex, system: str, code: str
) -> ByConditionResponse:
    rows = session.scalars(
        select(MedicationLink).where(
            MedicationLink.condition_system == system,
            MedicationLink.condition_code == code,
            MedicationLink.status == "approved",
        )
    ).all()
    ayush: list[LinkedMedication] = []
    bio: list[LinkedMedication] = []
    for lk in rows:
        rec = catalog.by_id.get(lk.medication_id)
        if rec is None or not rec.view.active:
            continue
        item = LinkedMedication(medication=rec.view, link=link_info(lk))
        (bio if rec.view.kind == "biomedical" else ayush).append(item)
    key = lambda x: x.medication.name  # noqa: E731
    return ByConditionResponse(
        condition=condition_ref(index, system, code),
        ayush=sorted(ayush, key=key),
        biomedical=sorted(bio, key=key),
    )


def ingredient_detail(
    session: Session, catalog: MedicationCatalog, index: TerminologyIndex, ingredient_id: int
) -> IngredientDetail | None:
    ing = catalog.ingredients.get(ingredient_id)
    if ing is None:
        return None
    meds = catalog.containing(ingredient_id)
    med_ids = [catalog.by_code[m.code].row_id for m in meds]
    links = session.scalars(
        select(MedicationLink).where(
            MedicationLink.medication_id.in_(med_ids), MedicationLink.status == "approved"
        )
    ).all()
    conditions = {(lk.condition_system, lk.condition_code) for lk in links}
    return IngredientDetail(
        id=ing.id,
        kind=ing.kind,
        canonical_name=ing.canonical_name,
        botanical_name=ing.botanical_name,
        iast=ing.iast,
        devanagari=ing.devanagari,
        synonyms=ing.synonyms,
        metal_mineral=ing.metal_mineral,
        medications=sorted(meds, key=lambda m: m.name),
        conditions=sorted(
            (condition_ref(index, s, c) for s, c in conditions), key=lambda c: (c.system, c.code)
        ),
    )


def review_list(
    session: Session, catalog: MedicationCatalog, index: TerminologyIndex, status: str
) -> list[LinkReviewItem]:
    q = select(MedicationLink).order_by(MedicationLink.id)
    if status != "all":
        q = q.where(MedicationLink.status == status)
    out = []
    for lk in session.scalars(q):
        rec = catalog.by_id.get(lk.medication_id)
        if rec is not None:
            out.append(
                LinkReviewItem(
                    link=link_info(lk),
                    medication=rec.view,
                    condition=condition_ref(index, lk.condition_system, lk.condition_code),
                )
            )
    return out


class LinkDecisionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def decide_link(session: Session, link_id: int, req: LinkDecision, principal: Principal) -> MedicationLink:
    link = session.get(MedicationLink, link_id)
    if link is None:
        raise LinkDecisionError(404, f"medication link {link_id} not found")
    if link.status != "pending":
        raise LinkDecisionError(409, f"medication link {link_id} is already {link.status}")
    if req.action == "reject" and not (req.comment and req.comment.strip()):
        raise LinkDecisionError(422, "a comment is mandatory when rejecting a cited link")
    med = session.get(Medication, link.medication_id)
    before = {"status": link.status}
    link.status = "approved" if req.action == "approve" else "rejected"
    link.reviewer = principal.username
    link.decided_at = datetime.now(UTC)
    if req.comment:
        link.note = req.comment
    record_provenance(
        session,
        target=f"MedicationLink/{link.id}",
        activity="approve",
        agent=principal.username,
        entities=[f"{link.source_title} | {link.source_edition or ''} | p. {link.source_page or ''}"],
        reason=req.comment
        or f"{req.action}: {link.condition_code} -> {med.code if med else link.medication_id}",
    )
    append_event(
        session,
        actor=principal.username,
        role=principal.role,
        action=req.action,
        entity_type="MedicationLink",
        entity_id=str(link.id),
        before=before,
        after={
            "status": link.status,
            "condition": link.condition_code,
            "medication": med.code if med else None,
            "comment": req.comment,
        },
    )
    return link
