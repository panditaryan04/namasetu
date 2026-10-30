"""Terminology operations: CodeSystem/$lookup, CodeSystem|ValueSet/$validate-code, ConceptMap/$translate.
Each returns a FHIR Parameters resource."""

from __future__ import annotations

from typing import Any

from app.engine.index import ConceptRecord
from app.engine.service import MappingEngine
from app.fhir.codesystem import LANGUAGE, records_for_system
from app.fhir.common import FHIRError, parameters, system_uri
from app.fhir.valueset import VALUESETS, find_valueset_id, members


def _find(engine: MappingEngine, system: str, code: str) -> tuple[list[ConceptRecord], ConceptRecord | None]:
    recs = records_for_system(engine.index, system)
    if recs is None:
        raise FHIRError(404, f"CodeSystem {system} is not known to this server", "not-found")
    return recs, next((r for r in recs if r.code == code), None)


def lookup(engine: MappingEngine, system: str | None, code: str | None) -> dict[str, Any]:
    if not system or not code:
        raise FHIRError(400, "$lookup requires 'system' and 'code'", "required")
    _, rec = _find(engine, system, code)
    if rec is None:
        raise FHIRError(404, f"code '{code}' not found in {system}", "code-invalid")
    params: list[dict[str, Any]] = [
        {"name": "name", "valueString": "NAMASTE" if rec.system == "namaste" else "ICD-11 MMS"},
        {
            "name": "version",
            "valueString": engine.index.release_version("namaste" if rec.system == "namaste" else "icd11")
            or "unknown",
        },
        {"name": "display", "valueString": rec.display},
    ]
    if rec.definition:
        params.append({"name": "definition", "valueString": rec.definition})
    seen = {rec.display}
    for s in rec.surfaces:
        lang = LANGUAGE.get(s.kind)
        if lang and s.text not in seen:
            seen.add(s.text)
            params.append(
                {
                    "name": "designation",
                    "part": [
                        {"name": "language", "valueCode": lang},
                        {"name": "value", "valueString": s.text},
                    ],
                }
            )
    props: list[tuple[str, str, str]] = [("entity-type", "valueString", rec.entity_type)]
    if rec.parent_code:
        props.insert(0, ("parent", "valueCode", rec.parent_code))
    props += [("child", "valueCode", c) for c in sorted(rec.children)]
    for name, kind, value in props:
        params.append(
            {
                "name": "property",
                "part": [{"name": "code", "valueCode": name}, {"name": "value", kind: value}],
            }
        )
    return parameters(*params)


def validate_code(
    engine: MappingEngine,
    *,
    code: str | None,
    system: str | None = None,
    valueset_url: str | None = None,
    display: str | None = None,
) -> dict[str, Any]:
    if not code or not (system or valueset_url):
        raise FHIRError(400, "$validate-code requires 'code' and a system/url", "required")
    rec: ConceptRecord | None
    if valueset_url:
        vs_id = find_valueset_id(valueset_url)
        _, sys_name, _, _, _ = VALUESETS[vs_id]
        expected_system = system_uri(sys_name, engine.index.is_sample)
        if system and system != expected_system:
            return parameters(
                {"name": "result", "valueBoolean": False},
                {"name": "message", "valueString": f"system {system} is not used by ValueSet {vs_id}"},
            )
        rec = next((r for r in members(engine, vs_id) if r.code == code), None)
        where = f"ValueSet {vs_id}"
    else:
        assert system is not None
        _, rec = _find(engine, system, code)
        where = system
    if rec is None:
        return parameters(
            {"name": "result", "valueBoolean": False},
            {"name": "message", "valueString": f"code '{code}' is not in {where}"},
        )
    valid_displays = {rec.display, *(s.text for s in rec.surfaces)}
    if display and display not in valid_displays:
        return parameters(
            {"name": "result", "valueBoolean": False},
            {
                "name": "message",
                "valueString": f"'{display}' is not a valid display for '{code}' (expected '{rec.display}')",
            },
            {"name": "display", "valueString": rec.display},
        )
    return parameters(
        {"name": "result", "valueBoolean": True}, {"name": "display", "valueString": rec.display}
    )
