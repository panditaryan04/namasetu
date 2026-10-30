"""FHIR R4 terminology service (application/fhir+json). Every response is validated before it is sent."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy import select

from app.api.common import DbSession
from app.api.search import Engine
from app.core.config import APP_VERSION
from app.core.ratelimit import rate_limit
from app.core.rbac import ConditionBuilder, PatientAccess, require
from app.db.models import AuditEvent, FhirProvenance, InteractionAlert, MedicationLink, Patient
from app.fhir import conceptmap_builder as cmb
from app.fhir.audit_provenance import audit_event_resource, list_audit_events, list_provenance
from app.fhir.bundle_ingest import ingest_bundle
from app.fhir.codesystem import all_codesystems
from app.fhir.common import (
    FHIR_VERSION,
    PUBLISHER,
    FHIRError,
    FHIRResponse,
    base_url,
    new_id,
    now_iso,
    operation_outcome,
    param_value,
    searchset,
    system_uri,
    with_narrative,
)
from app.fhir.condition_builder import ConditionBuildRequest, build_condition
from app.fhir.operations import lookup, validate_code
from app.fhir.validator import validate, validation_issues
from app.fhir.valueset import VALUESETS, expand, find_valueset_id, valueset
from app.medication.catalog import catalog_holder
from app.medication.fhir_medication import medication_knowledge_resource, medication_resource
from app.medication.interactions import detected_issue, timeline_bundle

# /fhir/metadata is public; everything else needs at least the "read" permission.
public_router = APIRouter(prefix="/fhir", tags=["fhir"], default_response_class=FHIRResponse)
router = APIRouter(
    prefix="/fhir",
    tags=["fhir"],
    default_response_class=FHIRResponse,
    dependencies=[Depends(require("read"))],
)

Count = Annotated[int, Query(alias="_count", ge=1, le=200)]
Offset = Annotated[int, Query(alias="_offset", ge=0)]
ParametersBody = Annotated[dict[str, Any], Body(description="FHIR Parameters resource")]


def ok(resource: dict[str, Any], status: int = 200) -> FHIRResponse:
    validate(with_narrative(resource))  # never emit an invalid resource
    return FHIRResponse(resource, status_code=status)


def page(
    request: Request, entries: list[dict[str, Any]], total: int, count: int, offset: int
) -> FHIRResponse:
    nxt = None
    if offset + count < total:
        nxt = str(request.url.include_query_params(_offset=offset + count, _count=count))
    return ok(searchset(entries, total, str(request.url), nxt))


# ------------------------------------------------------------------ capability


@public_router.get("/metadata", summary="CapabilityStatement")
def metadata() -> FHIRResponse:
    ops = {
        "CodeSystem": [("lookup", "CodeSystem-lookup"), ("validate-code", "CodeSystem-validate-code")],
        "ValueSet": [("expand", "ValueSet-expand"), ("validate-code", "ValueSet-validate-code")],
        "ConceptMap": [("translate", "ConceptMap-translate")],
    }
    resources = []
    for rt in [
        "CodeSystem",
        "ValueSet",
        "ConceptMap",
        "Condition",
        "Bundle",
        "AuditEvent",
        "Provenance",
        "Medication",
        "MedicationKnowledge",
    ]:
        r: dict[str, Any] = {"type": rt, "interaction": []}
        if rt in {"CodeSystem", "ValueSet", "ConceptMap", "AuditEvent", "Provenance", "MedicationKnowledge"}:
            r["interaction"] = [{"code": "read"}, {"code": "search-type"}]
        if rt == "Medication":
            r["interaction"] = [{"code": "read"}]
        if rt == "Bundle":
            r["interaction"] = [{"code": "create"}]
        if rt == "Condition":
            r["operation"] = [
                {"name": "build", "definition": f"{base_url()}/OperationDefinition/Condition-build"}
            ]
        if rt in ops:
            r["operation"] = [
                {"name": n, "definition": f"http://hl7.org/fhir/OperationDefinition/{d}"} for n, d in ops[rt]
            ]
        if not r["interaction"]:
            r.pop("interaction")
        resources.append(r)
    return ok(
        {
            "resourceType": "CapabilityStatement",
            "id": "namasetu",
            "status": "active",
            "date": now_iso(),
            "publisher": PUBLISHER,
            "kind": "instance",
            "software": {"name": "NamaSetu", "version": APP_VERSION},
            "implementation": {"description": "NamaSetu terminology service", "url": base_url()},
            "fhirVersion": FHIR_VERSION,
            "format": ["json"],
            "rest": [{"mode": "server", "resource": resources}],
        }
    )


# ------------------------------------------------------------------ CodeSystem


@router.get("/CodeSystem", summary="Search CodeSystems")
def search_codesystems(request: Request, engine: Engine, url: str | None = None) -> FHIRResponse:
    found = [cs for cs in all_codesystems(engine.index) if url in (None, cs["url"])]
    return page(request, found, len(found), 200, 0)


@router.get("/CodeSystem/$lookup", summary="CodeSystem/$lookup")
def cs_lookup(engine: Engine, system: str | None = None, code: str | None = None) -> FHIRResponse:
    return ok(lookup(engine, system, code))


@router.post("/CodeSystem/$lookup", summary="CodeSystem/$lookup (Parameters)")
def cs_lookup_post(engine: Engine, body: ParametersBody) -> FHIRResponse:
    coding = param_value(body, "coding") or {}
    return ok(
        lookup(
            engine,
            param_value(body, "system") or coding.get("system"),
            param_value(body, "code") or coding.get("code"),
        )
    )


@router.get("/CodeSystem/$validate-code", summary="CodeSystem/$validate-code")
def cs_validate(
    engine: Engine,
    url: str | None = None,
    system: str | None = None,
    code: str | None = None,
    display: str | None = None,
) -> FHIRResponse:
    return ok(validate_code(engine, code=code, system=url or system, display=display))


@router.post("/CodeSystem/$validate-code", summary="CodeSystem/$validate-code (Parameters)")
def cs_validate_post(engine: Engine, body: ParametersBody) -> FHIRResponse:
    return ok(
        validate_code(
            engine,
            code=param_value(body, "code"),
            system=param_value(body, "url") or param_value(body, "system"),
            display=param_value(body, "display"),
        )
    )


@router.get("/CodeSystem/{cs_id}", summary="Read CodeSystem")
def read_codesystem(cs_id: str, engine: Engine) -> FHIRResponse:
    for cs in all_codesystems(engine.index):
        if cs["id"] == cs_id:
            return ok(cs)
    raise FHIRError(404, f"CodeSystem/{cs_id} not found", "not-found")


# ------------------------------------------------------------------ ValueSet


@router.get("/ValueSet", summary="Search ValueSets")
def search_valuesets(request: Request, engine: Engine) -> FHIRResponse:
    found = [valueset(engine, vs_id) for vs_id in VALUESETS]
    return page(request, found, len(found), 200, 0)


@router.get(
    "/ValueSet/$expand",
    summary="ValueSet/$expand (powers autocomplete)",
    dependencies=[Depends(rate_limit("search"))],
)
def vs_expand(
    engine: Engine,
    url: str = "namaste-ayurveda",
    filter: str | None = Query(None, max_length=200),
    count: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> FHIRResponse:
    return ok(expand(engine, find_valueset_id(url), filter, count, offset))


@router.get("/ValueSet/$validate-code", summary="ValueSet/$validate-code")
def vs_validate(
    engine: Engine, url: str, code: str, system: str | None = None, display: str | None = None
) -> FHIRResponse:
    return ok(validate_code(engine, code=code, system=system, valueset_url=url, display=display))


@router.get("/ValueSet/{vs_id}", summary="Read ValueSet")
def read_valueset(vs_id: str, engine: Engine) -> FHIRResponse:
    return ok(valueset(engine, vs_id))


@router.get("/ValueSet/{vs_id}/$expand", summary="ValueSet/[id]/$expand")
def vs_expand_id(
    vs_id: str,
    engine: Engine,
    filter: str | None = Query(None, max_length=200),
    count: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> FHIRResponse:
    return ok(expand(engine, find_valueset_id(vs_id), filter, count, offset))


# ------------------------------------------------------------------ ConceptMap


@router.get("/ConceptMap", summary="Search ConceptMaps (current versions)")
def search_conceptmaps(request: Request, engine: Engine, db: DbSession) -> FHIRResponse:
    found = [cmb.get_conceptmap(db, engine, cm_id) for cm_id in cmb.CONCEPTMAPS]
    return page(request, found, len(found), 200, 0)


@router.get("/ConceptMap/$translate", summary="ConceptMap/$translate")
def cm_translate(
    engine: Engine,
    db: DbSession,
    system: str | None = None,
    code: str | None = None,
    target: str | None = None,
) -> FHIRResponse:
    return ok(cmb.translate(db, engine, system=system, code=code, target=target))


@router.post("/ConceptMap/$translate", summary="ConceptMap/$translate (Parameters)")
def cm_translate_post(engine: Engine, db: DbSession, body: ParametersBody) -> FHIRResponse:
    coding = param_value(body, "coding") or {}
    return ok(
        cmb.translate(
            db,
            engine,
            system=param_value(body, "system") or coding.get("system"),
            code=param_value(body, "code") or coding.get("code"),
            target=param_value(body, "targetsystem") or param_value(body, "target"),
        )
    )


@router.get("/ConceptMap/{cm_id}", summary="Read ConceptMap (publishes a new version when content changed)")
def read_conceptmap(cm_id: str, engine: Engine, db: DbSession) -> FHIRResponse:
    return ok(cmb.get_conceptmap(db, engine, cm_id))


@router.get("/ConceptMap/{cm_id}/_history", summary="ConceptMap version history")
def conceptmap_history(cm_id: str, engine: Engine, db: DbSession) -> FHIRResponse:
    cmb.get_conceptmap(db, engine, cm_id)
    versions = cmb.history(db, cm_id)
    return ok(
        {
            "resourceType": "Bundle",
            "id": new_id(),
            "type": "history",
            "total": len(versions),
            "entry": [
                {
                    "fullUrl": f"{base_url()}/ConceptMap/{cm_id}/_history/{v['version']}",
                    "resource": v,
                    "request": {"method": "PUT", "url": f"ConceptMap/{cm_id}"},
                    "response": {"status": "200", "lastModified": v["date"]},
                }
                for v in versions
            ],
        }
    )


# ------------------------------------------------------------------ Condition, Bundle, $validate


@router.post("/Condition/$build", summary="Build a tri-coded Condition (NAMASTE + TM2 [+ biomedicine])")
def condition_build(
    req: ConditionBuildRequest, user: ConditionBuilder, engine: Engine, db: DbSession
) -> FHIRResponse:
    return ok(build_condition(db, engine, req, actor=user.username, role=user.role), status=201)


@router.post("/Bundle", summary="Ingest a Bundle: validate codings, add TM2 from approved maps")
def bundle(
    user: ConditionBuilder,
    engine: Engine,
    db: DbSession,
    body: Annotated[
        dict[str, Any], Body(description="FHIR Bundle (transaction | collection | batch | document)")
    ],
    purpose_of_use: Literal["TREAT", "ETREAT", "HPAYMT", "PUBHLTH", "HRESCH"] = "TREAT",
) -> FHIRResponse:
    return ok(ingest_bundle(db, engine, body, purpose_of_use, actor=user.username, role=user.role))


@router.post("/$validate", summary="Validate any resource; returns an OperationOutcome")
def validate_resource(body: Annotated[dict[str, Any], Body()]) -> FHIRResponse:
    resource = param_value(body, "resource") if body.get("resourceType") == "Parameters" else body
    return ok(operation_outcome(validation_issues(resource if isinstance(resource, dict) else {})))


# ------------------------------------------------------------------ AuditEvent, Provenance


@router.get("/AuditEvent", summary="Search AuditEvents (view over the hash-chained ledger)")
def search_audit(
    request: Request, db: DbSession, count: Count = 50, offset: Offset = 0, action: str | None = None
) -> FHIRResponse:
    rows, total = list_audit_events(db, count, offset, action)
    return page(request, [audit_event_resource(r) for r in rows], total, count, offset)


@router.get("/AuditEvent/{event_id}", summary="Read AuditEvent")
def read_audit(event_id: int, db: DbSession) -> FHIRResponse:
    ev = db.get(AuditEvent, event_id)
    if ev is None:
        raise FHIRError(404, f"AuditEvent/{event_id} not found", "not-found")
    return ok(audit_event_resource(ev))


@router.get(
    "/Provenance", summary="Search Provenance (?target=Condition/<id> | ConceptMap/<id> | Mapping/<id>)"
)
def search_provenance(
    request: Request, db: DbSession, target: str | None = None, count: Count = 50, offset: Offset = 0
) -> FHIRResponse:
    rows, total = list_provenance(db, target, count, offset)
    return page(request, rows, total, count, offset)


@router.get("/Provenance/{prov_id}", summary="Read Provenance")
def read_provenance(prov_id: str, db: DbSession) -> FHIRResponse:
    row = db.scalar(select(FhirProvenance).where(FhirProvenance.id == prov_id))
    if row is None:
        raise FHIRError(404, f"Provenance/{prov_id} not found", "not-found")
    return ok(row.content)


# ------------------------------------------------------------------ Medication and MedicationKnowledge


def _indications(
    db: DbSession, engine: Engine, med_row_id: int, condition: tuple[str, str] | None = None
) -> list[tuple[str, str, str | None]]:
    q = select(MedicationLink).where(
        MedicationLink.medication_id == med_row_id, MedicationLink.status == "approved"
    )
    out = []
    for lk in db.scalars(q):
        if condition and (lk.condition_system, lk.condition_code) != condition:
            continue
        rec = engine.index.get(lk.condition_system, lk.condition_code)
        out.append((lk.condition_system, lk.condition_code, rec.display if rec else None))
    return sorted(out)


def _parse_condition(engine: Engine, condition: str) -> tuple[str, str]:
    """`condition` = "<system URI>|<code>" or a bare NAMASTE / ICD-11 code."""
    sample = engine.index.is_sample
    systems = {system_uri(s, sample): s for s in ("namaste", "icd11-tm2")}
    if "|" in condition:
        uri, code = condition.rsplit("|", 1)
        if uri not in systems:
            raise FHIRError(400, f"unknown condition system {uri}", "value")
        sys_name = systems[uri]
        if sys_name == "icd11-tm2" and engine.index.get("icd11-bio", code):
            sys_name = "icd11-bio"
        return sys_name, code
    for sys_name in ("namaste", "icd11-tm2", "icd11-bio"):
        if engine.index.get(sys_name, condition):
            return sys_name, condition
    raise FHIRError(404, f"condition {condition} not found", "not-found")


@router.get("/Medication/{code}", summary="Read Medication (reference only)")
def read_medication(code: str, db: DbSession) -> FHIRResponse:
    view = catalog_holder.get(db).get(code)
    if view is None:
        raise FHIRError(404, f"Medication/{code} not found", "not-found")
    return ok(medication_resource(view))


@router.get("/MedicationKnowledge", summary="Search MedicationKnowledge (?condition=system|code)")
def search_medication_knowledge(
    request: Request, engine: Engine, db: DbSession, condition: str | None = None
) -> FHIRResponse:
    catalog = catalog_holder.get(db)
    cond = _parse_condition(engine, condition) if condition else None
    entries = []
    for rec in catalog.by_code.values():
        if not rec.view.active:
            continue
        ind = _indications(db, engine, rec.row_id, cond)
        if cond and not ind:
            continue
        entries.append(medication_knowledge_resource(rec.view, ind, engine.index.is_sample))
    entries.sort(key=lambda r: r["id"])
    return page(request, entries, len(entries), 200, 0)


@router.get("/MedicationKnowledge/{code}", summary="Read MedicationKnowledge")
def read_medication_knowledge(code: str, engine: Engine, db: DbSession) -> FHIRResponse:
    catalog = catalog_holder.get(db)
    rec = catalog.by_code.get(code)
    if rec is None or not rec.view.active:
        raise FHIRError(404, f"MedicationKnowledge/{code} not found", "not-found")
    return ok(
        medication_knowledge_resource(rec.view, _indications(db, engine, rec.row_id), engine.index.is_sample)
    )


# ------------------------------------------------------------------ DetectedIssue, medication timeline


@router.get("/DetectedIssue", summary="Interaction / duplicate alerts as DetectedIssue (?patient=<id>)")
def search_detected_issues(request: Request, _: PatientAccess, db: DbSession, patient: str) -> FHIRResponse:
    rows = db.scalars(
        select(InteractionAlert).where(InteractionAlert.patient_id == patient).order_by(InteractionAlert.id)
    )
    found = [detected_issue(a) for a in rows]
    return page(request, found, len(found), 200, 0)


@router.get(
    "/Patient/{patient_id}/MedicationStatement", summary="Medication timeline as MedicationStatements"
)
def patient_medication_statements(patient_id: str, _: PatientAccess, db: DbSession) -> FHIRResponse:
    if db.get(Patient, patient_id) is None:
        raise FHIRError(404, f"Patient/{patient_id} not found", "not-found")
    return ok(timeline_bundle(db, catalog_holder.get(db), patient_id))
