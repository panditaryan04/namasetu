"""Shared FHIR plumbing: canonical URLs, the application/fhir+json response, OperationOutcome errors,
Parameters helpers and searchset Bundles."""

from __future__ import annotations

import html
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from starlette.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.db.models import SYSTEM_BIO, SYSTEM_NAMASTE, SYSTEM_TM2

FHIR_JSON = "application/fhir+json"
FHIR_VERSION = "4.0.1"
PUBLISHER = "Team Byte Strong"
ICD11_MMS_URI = "http://id.who.int/icd/release/11/mms"
# ABDM health ID identifier system as used in NRCeS examples; mock/TEST values only in the sandbox.
ABHA_SYSTEM = "https://healthid.ndhm.gov.in"
# NRCeS ABDM Condition profile. NOT claimed in meta.profile: in ndhm.in 6.5.0 Condition.code.coding is
# CLOSED-sliced to ICD-10 + SNOMED CT, so a NAMASTE / ICD-11 coding cannot conform (see docs/standards.md).
NRCES_CONDITION_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Condition"

CONCEPTMAP_NAMASTE_TM2 = "namaste-to-tm2"
CONCEPTMAP_TM2_BIO = "tm2-to-biomedicine"


def base_url(settings: Settings | None = None) -> str:
    return (settings or get_settings()).public_base_url.rstrip("/") + "/fhir"


def canonical(kind: str, logical_id: str, settings: Settings | None = None) -> str:
    return f"{base_url(settings)}/{kind}/{logical_id}"


def system_uri(system: str, sample: bool, settings: Settings | None = None) -> str:
    """Coding.system for each internal system. SAMPLE ICD-11 codes never use WHO's official URI."""
    if system == SYSTEM_NAMASTE:
        return canonical("CodeSystem", "namaste", settings)
    if system in (SYSTEM_TM2, SYSTEM_BIO):
        return canonical("CodeSystem", "icd11-mms-sample", settings) if sample else ICD11_MMS_URI
    raise ValueError(f"unknown system {system}")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def urn_uuid() -> str:
    return f"urn:uuid:{uuid.uuid4()}"


NARRATIVE_SKIP = {"Bundle", "Parameters", "Binary"}


def _summary(res: dict[str, Any]) -> str:
    rt = res.get("resourceType", "")
    if rt == "Condition":
        codes = ", ".join(
            f"{c.get('code')} {c.get('display', '')}".strip() for c in res.get("code", {}).get("coding", [])
        )
        return f"Condition: {res.get('code', {}).get('text', '')} [{codes}]"
    if rt == "Provenance":
        target = res["target"][0]
        activity = res.get("activity", {}).get("text", "")
        return f"Provenance ({activity}) for {target.get('reference') or target.get('display')}"
    if rt == "AuditEvent":
        action = res.get("subtype", [{}])[0].get("code", "")
        return f"AuditEvent {action}: {res['entity'][0]['what'].get('display', '')}"
    if rt == "OperationOutcome":
        return "; ".join(i.get("diagnostics", "") for i in res.get("issue", []))
    return f"{rt}: {res.get('title') or res.get('name') or res.get('id', '')}"


def with_narrative(res: dict[str, Any]) -> dict[str, Any]:
    """Add a generated narrative (dom-6) to a resource and to searchset/history entries."""
    rt = res.get("resourceType")
    if rt == "Bundle" and res.get("type") in {"searchset", "history"}:
        for e in res.get("entry", []):
            with_narrative(e["resource"])
    elif rt not in NARRATIVE_SKIP and "text" not in res:
        div = html.escape(_summary(res))
        res["text"] = {
            "status": "generated",
            "div": f'<div xmlns="http://www.w3.org/1999/xhtml"><p>{div}</p></div>',
        }
    return res


class FHIRResponse(JSONResponse):
    media_type = FHIR_JSON

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class FHIRError(Exception):
    """Raised inside /fhir routes; rendered as an OperationOutcome with the given HTTP status."""

    def __init__(
        self, status: int, diagnostics: str, code: str = "processing", severity: str = "error"
    ) -> None:
        super().__init__(diagnostics)
        self.status = status
        self.diagnostics = diagnostics
        self.code = code
        self.severity = severity


def issue(severity: str, code: str, diagnostics: str, expression: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"severity": severity, "code": code, "diagnostics": diagnostics}
    if expression:
        out["expression"] = [expression]
    return out


def operation_outcome(issues: list[dict[str, Any]]) -> dict[str, Any]:
    if not issues:
        issues = [issue("information", "informational", "No issues detected")]
    return {"resourceType": "OperationOutcome", "id": new_id(), "issue": issues}


def parameters(*params: dict[str, Any]) -> dict[str, Any]:
    return {"resourceType": "Parameters", "parameter": [p for p in params if p]}


def param_value(body: dict[str, Any] | None, name: str) -> Any:
    """First value[x] (or resource) of a named parameter in a Parameters resource."""
    for p in (body or {}).get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value") or k == "resource":
                    return v
    return None


def searchset(
    entries: list[dict[str, Any]], total: int, self_url: str, next_url: str | None
) -> dict[str, Any]:
    links = [{"relation": "self", "url": self_url}]
    if next_url:
        links.append({"relation": "next", "url": next_url})
    return {
        "resourceType": "Bundle",
        "id": new_id(),
        "type": "searchset",
        "total": total,
        "link": links,
        "entry": [
            {
                "fullUrl": f"{base_url()}/{e['resourceType']}/{e['id']}",
                "resource": e,
                "search": {"mode": "match"},
            }
            for e in entries
        ],
    }
