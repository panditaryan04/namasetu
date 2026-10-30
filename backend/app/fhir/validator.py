"""In-process FHIR validation.

1. Structure and cardinality via fhir.resources (R4B models; wire-compatible with R4 for every resource
   NamaSetu emits).
2. Required bindings that the models do not enforce (ConceptMap equivalence, statuses, Condition status
   codes) checked explicitly below.
The authoritative R4 (4.0.1) check is the official HL7 Validator CLI, run in CI over docs/fhir-examples/.
"""

from __future__ import annotations

import importlib
from typing import Any

from pydantic import ValidationError

from app.fhir.common import issue

R4_EQUIVALENCE = {
    "relatedto",
    "equivalent",
    "equal",
    "wider",
    "subsumes",
    "narrower",
    "specializes",
    "inexact",
    "unmatched",
    "disjoint",
}
PUBLICATION_STATUS = {"draft", "active", "retired", "unknown"}
CONDITION_CLINICAL = {"active", "recurrence", "relapse", "inactive", "remission", "resolved"}
CONDITION_VERIFICATION = {
    "unconfirmed",
    "provisional",
    "differential",
    "confirmed",
    "refuted",
    "entered-in-error",
}
CODESYSTEM_CONTENT = {"not-present", "example", "fragment", "complete", "supplement"}
BUNDLE_TYPES = {
    "document",
    "message",
    "transaction",
    "transaction-response",
    "batch",
    "batch-response",
    "history",
    "searchset",
    "collection",
}
ISSUE_SEVERITY = {"fatal", "error", "warning", "information"}

_MODULES = {
    "AuditEvent": "auditevent",
    "Bundle": "bundle",
    "CapabilityStatement": "capabilitystatement",
    "CodeSystem": "codesystem",
    "ConceptMap": "conceptmap",
    "Condition": "condition",
    "OperationOutcome": "operationoutcome",
    "Parameters": "parameters",
    "Patient": "patient",
    "Provenance": "provenance",
    "ValueSet": "valueset",
    "Encounter": "encounter",
    "MedicationStatement": "medicationstatement",
    "MedicationRequest": "medicationrequest",
    "Medication": "medication",
    "MedicationKnowledge": "medicationknowledge",
    "DetectedIssue": "detectedissue",
    "Observation": "observation",
    "Composition": "composition",
}


class FHIRValidationError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(i["diagnostics"] for i in issues))
        self.issues = issues


def _model(resource_type: str) -> Any:
    module = _MODULES.get(resource_type)
    if module is None:
        return None
    return getattr(importlib.import_module(f"fhir.resources.R4B.{module}"), resource_type)


def _coded(value: Any, allowed: set[str], where: str, out: list[dict[str, Any]]) -> None:
    if value is not None and value not in allowed:
        out.append(issue("error", "code-invalid", f"'{value}' is not a valid code for {where}", where))


def _status_codes(
    cc: dict[str, Any] | None, allowed: set[str], where: str, out: list[dict[str, Any]]
) -> None:
    for i, coding in enumerate((cc or {}).get("coding", [])):
        _coded(coding.get("code"), allowed, f"{where}.coding[{i}].code", out)


def binding_issues(res: dict[str, Any], path: str | None = None) -> list[dict[str, Any]]:
    rt = res.get("resourceType", "")
    p = path or rt
    out: list[dict[str, Any]] = []
    if rt in {"CodeSystem", "ValueSet", "ConceptMap", "CapabilityStatement"}:
        _coded(res.get("status"), PUBLICATION_STATUS, f"{p}.status", out)
    if rt == "CodeSystem":
        _coded(res.get("content"), CODESYSTEM_CONTENT, f"{p}.content", out)
    if rt == "ConceptMap":
        for gi, g in enumerate(res.get("group", [])):
            for ei, e in enumerate(g.get("element", [])):
                for ti, t in enumerate(e.get("target", [])):
                    _coded(
                        t.get("equivalence"),
                        R4_EQUIVALENCE,
                        f"{p}.group[{gi}].element[{ei}].target[{ti}].equivalence",
                        out,
                    )
    if rt == "Condition":
        _status_codes(res.get("clinicalStatus"), CONDITION_CLINICAL, f"{p}.clinicalStatus", out)
        _status_codes(res.get("verificationStatus"), CONDITION_VERIFICATION, f"{p}.verificationStatus", out)
    if rt == "OperationOutcome":
        for i, it in enumerate(res.get("issue", [])):
            _coded(it.get("severity"), ISSUE_SEVERITY, f"{p}.issue[{i}].severity", out)
    if rt == "Bundle":
        _coded(res.get("type"), BUNDLE_TYPES, f"{p}.type", out)
        for i, entry in enumerate(res.get("entry", [])):
            if isinstance(entry.get("resource"), dict):
                out += binding_issues(entry["resource"], f"{p}.entry[{i}].resource")
    if rt == "Parameters":
        for i, prm in enumerate(res.get("parameter", [])):
            if isinstance(prm.get("resource"), dict):
                out += binding_issues(prm["resource"], f"{p}.parameter[{i}].resource")
    return out


def validation_issues(resource: dict[str, Any]) -> list[dict[str, Any]]:
    rt = resource.get("resourceType")
    if not isinstance(rt, str):
        return [issue("error", "structure", "missing resourceType")]
    model = _model(rt)
    out: list[dict[str, Any]] = []
    if model is None:
        out.append(issue("warning", "not-supported", f"{rt} is not structurally validated in-process"))
    else:
        try:
            model.model_validate(resource)
        except ValidationError as exc:
            for err in exc.errors()[:50]:
                loc = ".".join(str(x) for x in err["loc"])
                out.append(issue("error", "structure", f"{loc}: {err['msg']}", f"{rt}.{loc}" if loc else rt))
    return out + binding_issues(resource)


def validate(resource: dict[str, Any]) -> dict[str, Any]:
    """Return the resource unchanged if valid; raise FHIRValidationError otherwise."""
    issues = [i for i in validation_issues(resource) if i["severity"] in {"error", "fatal"}]
    if issues:
        raise FHIRValidationError(issues)
    return resource
