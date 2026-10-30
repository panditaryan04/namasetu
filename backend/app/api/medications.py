"""Medication & salt reference endpoints. Reference only: never a prescription, no dose regimens."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.common import DbSession
from app.api.search import Engine
from app.core.ratelimit import rate_limit
from app.core.rbac import Reader, Reviewer
from app.db.models import Patient
from app.medication.brand_resolver import SearchResponse, search
from app.medication.catalog import DISCLAIMER, MedicationCatalog, MedicationView, catalog_holder
from app.medication.interactions import CheckOut, check, screen
from app.medication.links import (
    ByConditionResponse,
    IngredientDetail,
    LinkDecision,
    LinkDecisionError,
    LinkInfo,
    LinkReviewItem,
    by_condition,
    decide_link,
    ingredient_detail,
    link_info,
    review_list,
)
from app.medication.safety import DuplicateWarning, duplicate_ingredients

router = APIRouter(tags=["medications"])


def get_catalog(db: DbSession) -> MedicationCatalog:
    return catalog_holder.get(db)


Catalog = Annotated[MedicationCatalog, Depends(get_catalog)]
ConditionSystem = Literal["namaste", "icd11-tm2", "icd11-bio"]


class CheckRequest(BaseModel):
    medication_codes: list[str] = Field(min_length=1, max_length=50)
    patient_id: str | None = Field(None, max_length=64, description="Also check this patient's active set")


class CheckResponse(BaseModel):
    medications: list[MedicationView]
    unknown_codes: list[str]
    duplicates: list[DuplicateWarning]
    prescription_only: list[str]
    interactions: CheckOut
    interaction_check: str
    disclaimer: str = DISCLAIMER


@router.get(
    "/medications/search", response_model=SearchResponse, dependencies=[Depends(rate_limit("search"))]
)
def medication_search(
    _: Reader,
    catalog: Catalog,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: int = Query(20, ge=1, le=50),
) -> SearchResponse:
    """Brand, salt, generic, formulation or herb (any script) -> what is inside."""
    return search(catalog, q, limit)


@router.get("/medications/by-condition", response_model=ByConditionResponse)
def medications_by_condition(
    _: Reader,
    catalog: Catalog,
    engine: Engine,
    db: DbSession,
    system: ConditionSystem,
    code: Annotated[str, Query(min_length=1, max_length=64)],
) -> ByConditionResponse:
    """Approved reference medications for a condition (Ayush: NAMASTE/TM2 codes; biomedical: ICD-11 bio)."""
    if engine.index.get(system, code) is None:
        raise HTTPException(status_code=404, detail=f"condition {system}|{code} not found")
    return by_condition(db, catalog, engine.index, system, code)


class ActiveMedicine(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    salts: list[str] = Field(max_length=10)


class ScreenRequest(BaseModel):
    candidate_codes: list[str] = Field(min_length=1, max_length=20)
    active: list[ActiveMedicine] = Field(default_factory=list, max_length=50)


class ScreenResponse(CheckOut):
    unknown_codes: list[str]
    unresolved_salts: list[str]
    disclaimer: str = DISCLAIMER


@router.post(
    "/medications/screen", response_model=ScreenResponse, dependencies=[Depends(rate_limit("search"))]
)
def medications_screen(req: ScreenRequest, _: Reader, catalog: Catalog, db: DbSession) -> ScreenResponse:
    """Screen formulations against medicines listed by salt (records kept outside NamaSetu). Not persisted."""
    unknown = [c for c in dict.fromkeys(req.candidate_codes) if catalog.get(c) is None]
    result, unresolved = screen(
        db,
        catalog,
        [c for c in req.candidate_codes if c not in unknown],
        [(m.label, m.salts) for m in req.active],
        datetime.now(UTC).date(),
    )
    return ScreenResponse(**result.model_dump(), unknown_codes=unknown, unresolved_salts=unresolved)


@router.post("/medications/check", response_model=CheckResponse)
def medications_check(req: CheckRequest, user: Reader, catalog: Catalog, db: DbSession) -> CheckResponse:
    """Duplicate-salt and schedule checks for a selected list (interaction checks arrive in Milestone 5B)."""
    views = [v for c in dict.fromkeys(req.medication_codes) if (v := catalog.get(c))]
    unknown = [c for c in dict.fromkeys(req.medication_codes) if catalog.get(c) is None]
    patient = None
    if req.patient_id:
        if not user.can("patient"):
            raise HTTPException(
                status_code=403, detail="checking against a patient needs the 'patient' permission"
            )
        patient = db.get(Patient, req.patient_id)
        if patient is None:
            raise HTTPException(status_code=404, detail=f"patient {req.patient_id} not found")
    found = [v.code for v in views]
    result = check(
        db, catalog, found, patient=patient, as_of=datetime.now(UTC).date(), persist=patient is not None
    )
    db.commit()
    return CheckResponse(
        medications=views,
        unknown_codes=unknown,
        duplicates=duplicate_ingredients(catalog, req.medication_codes),
        prescription_only=[v.code for v in views if v.schedule in {"H", "H1", "X"}],
        interactions=result,
        interaction_check=result.message,
    )


@router.get("/medications/links", response_model=list[LinkReviewItem])
def medication_links(
    _: Reader,
    catalog: Catalog,
    engine: Engine,
    db: DbSession,
    status: Literal["pending", "approved", "rejected", "all"] = "pending",
) -> list[LinkReviewItem]:
    """Condition -> medication links for the review tab."""
    return review_list(db, catalog, engine.index, status)


@router.post("/medications/links/{link_id}/decision", response_model=LinkInfo)
def medication_link_decision(link_id: int, req: LinkDecision, user: Reviewer, db: DbSession) -> LinkInfo:
    try:
        link = decide_link(db, link_id, req, user)
    except LinkDecisionError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    db.commit()
    return link_info(link)


@router.get("/medications/{code}", response_model=MedicationView)
def medication_detail(code: str, _: Reader, catalog: Catalog) -> MedicationView:
    view = catalog.get(code)
    if view is None:
        raise HTTPException(status_code=404, detail=f"medication {code} not found")
    return view


@router.get("/salts/{ingredient_id}", response_model=IngredientDetail)
def salt_detail(
    ingredient_id: int, _: Reader, catalog: Catalog, engine: Engine, db: DbSession
) -> IngredientDetail:
    """A salt or herb: what it is, its names in every script, medications containing it, linked conditions."""
    detail = ingredient_detail(db, catalog, engine.index, ingredient_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"ingredient {ingredient_id} not found")
    return detail


@router.get("/eval/medications", tags=["eval"])
def medication_eval(_: Reader, catalog: Catalog, db: DbSession) -> dict[str, object]:
    """Free-text line resolution and interaction detection on the labelled SAMPLE sets (computed live)."""
    from app.eval.medication import interaction_detection, line_resolution

    return {
        "line_resolution": line_resolution(db, catalog),
        "interaction_detection": interaction_detection(db, catalog),
    }
