"""POST /fhir/Bundle: validate an incoming Bundle and enrich its Conditions.

- Structural validation of the Bundle and every entry.
- Every Coding in a known system (NAMASTE / ICD-11) must exist in the dataset; wrong displays are warnings.
- A Condition with a NAMASTE coding but no TM2 coding gets the TM2 coding from an APPROVED mapping only.
Returns Parameters { outcome: OperationOutcome, return: enriched Bundle }.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import SYSTEM_BIO, SYSTEM_NAMASTE, SYSTEM_TM2
from app.engine.service import MappingEngine
from app.fhir.audit_provenance import record_provenance
from app.fhir.common import FHIRError, issue, operation_outcome, system_uri
from app.fhir.condition_builder import _approved_mapping
from app.fhir.validator import validation_issues
from app.ledger.hash_chain import append_event

ACCEPTED_TYPES = {"transaction", "collection", "batch", "document"}


def ingest_bundle(
    session: Session,
    engine: MappingEngine,
    bundle: dict[str, Any],
    purpose_of_use: str,
    actor: str = "system",
    role: str = "system",
) -> dict[str, Any]:
    if bundle.get("resourceType") != "Bundle":
        raise FHIRError(400, "body must be a FHIR Bundle", "structure")
    if bundle.get("type") not in ACCEPTED_TYPES:
        raise FHIRError(400, f"Bundle.type must be one of {sorted(ACCEPTED_TYPES)}", "value")
    issues = validation_issues(bundle)
    if any(i["severity"] in {"error", "fatal"} for i in issues):
        return {
            "resourceType": "Parameters",
            "parameter": [{"name": "outcome", "resource": operation_outcome(issues)}],
        }

    idx = engine.index
    sample = idx.is_sample
    systems = {system_uri(SYSTEM_NAMASTE, sample): SYSTEM_NAMASTE, system_uri(SYSTEM_TM2, sample): SYSTEM_TM2}
    out = copy.deepcopy(bundle)
    stats = {"conditions": 0, "codings_checked": 0, "tm2_added": 0, "errors": 0}

    for ei, entry in enumerate(out.get("entry", [])):
        res = entry.get("resource") or {}
        if res.get("resourceType") != "Condition":
            continue
        stats["conditions"] += 1
        codings = res.setdefault("code", {}).setdefault("coding", [])
        path = f"Bundle.entry[{ei}].resource.code"
        namaste_code, has_tm2 = None, False
        for ci, coding in enumerate(codings):
            sys_name = systems.get(coding.get("system", ""))
            where = f"{path}.coding[{ci}]"
            if sys_name is None:
                issues.append(
                    issue(
                        "information",
                        "informational",
                        f"system {coding.get('system')} not validated (external)",
                        where,
                    )
                )
                continue
            stats["codings_checked"] += 1
            code = coding.get("code", "")
            rec = idx.get(sys_name, code) or (idx.get(SYSTEM_BIO, code) if sys_name == SYSTEM_TM2 else None)
            if rec is None:
                stats["errors"] += 1
                issues.append(
                    issue("error", "code-invalid", f"code '{code}' is not in {coding.get('system')}", where)
                )
                continue
            if coding.get("display") and coding["display"] != rec.display:
                issues.append(
                    issue(
                        "warning",
                        "value",
                        f"display '{coding['display']}' differs from '{rec.display}'",
                        f"{where}.display",
                    )
                )
            if rec.system == SYSTEM_NAMASTE:
                namaste_code = rec.code
            if rec.system == SYSTEM_TM2:
                has_tm2 = True
        if namaste_code and not has_tm2:
            mapping = _approved_mapping(session, namaste_code)
            tm2 = idx.get(SYSTEM_TM2, mapping.target_code) if mapping else None
            if mapping and tm2:
                codings.append(
                    {"system": system_uri(SYSTEM_TM2, sample), "code": tm2.code, "display": tm2.display}
                )
                stats["tm2_added"] += 1
                issues.append(
                    issue(
                        "information",
                        "informational",
                        f"added TM2 {tm2.code} from approved mapping {mapping.id} ({mapping.relationship})",
                        path,
                    )
                )
            else:
                issues.append(
                    issue(
                        "warning",
                        "business-rule",
                        f"no approved TM2 mapping for {namaste_code}; not added (send to review)",
                        path,
                    )
                )

    append_event(
        session,
        actor=actor,
        role=role,
        action="bundle_ingest",
        entity_type="Bundle",
        entity_id=str(bundle.get("id") or "anonymous"),
        after=stats,
        purpose_of_use=purpose_of_use,
    )
    if stats["tm2_added"]:
        record_provenance(
            session,
            target=f"Bundle/{out.get('id') or 'anonymous'}",
            activity="build",
            agent=actor,
            entities=["ConceptMap/namaste-to-tm2", *idx.releases],
            reason="TM2 codings added from approved mappings",
        )
    session.commit()
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "outcome", "resource": operation_outcome(issues)},
            {"name": "return", "resource": out},
        ],
    }
