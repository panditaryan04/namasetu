"""ValueSets (per discipline + TM2) and $expand. NAMASTE filtering reuses the multi-script autocomplete."""

from __future__ import annotations

from typing import Any

from app.db.models import SYSTEM_NAMASTE, SYSTEM_TM2
from app.engine.index import ConceptRecord
from app.engine.service import MappingEngine
from app.engine.transliterator import compact, fold
from app.fhir.codesystem import SAMPLE_NOTE
from app.fhir.common import PUBLISHER, FHIRError, canonical, now_iso, system_uri, urn_uuid

# id -> (title, system, filter property, filter value, status)
VALUESETS: dict[str, tuple[str, str, str, str, str]] = {
    "namaste-ayurveda": ("NAMASTE Ayurveda diagnoses", SYSTEM_NAMASTE, "discipline", "ayurveda", "active"),
    "namaste-siddha": (
        "NAMASTE Siddha diagnoses (coming soon)",
        SYSTEM_NAMASTE,
        "discipline",
        "siddha",
        "draft",
    ),
    "namaste-unani": (
        "NAMASTE Unani diagnoses (coming soon)",
        SYSTEM_NAMASTE,
        "discipline",
        "unani",
        "draft",
    ),
    "icd11-tm2": ("ICD-11 Traditional Medicine Module 2", SYSTEM_TM2, "module", "tm2", "active"),
}


def valueset(engine: MappingEngine, vs_id: str) -> dict[str, Any]:
    if vs_id not in VALUESETS:
        raise FHIRError(404, f"ValueSet/{vs_id} not found", "not-found")
    title, system, prop, value, status = VALUESETS[vs_id]
    sample = engine.index.is_sample
    return {
        "resourceType": "ValueSet",
        "id": vs_id,
        "url": canonical("ValueSet", vs_id),
        "version": engine.index.release_version("namaste" if system == SYSTEM_NAMASTE else "icd11")
        or "unknown",
        "name": "".join(w.capitalize() for w in vs_id.split("-")),
        "title": title + (" (SAMPLE)" if sample else ""),
        "status": status,
        "experimental": sample,
        "date": now_iso(),
        "publisher": PUBLISHER,
        "description": (SAMPLE_NOTE + " " if sample else "")
        + f"All non-grouping codes with {prop} = {value}.",
        "compose": {
            "include": [
                {
                    "system": system_uri(system, sample),
                    "filter": [{"property": prop, "op": "=", "value": value}],
                }
            ]
        },
    }


def members(engine: MappingEngine, vs_id: str) -> list[ConceptRecord]:
    _, system, prop, value, _ = VALUESETS[vs_id]
    out = []
    for (s, _), rec in engine.index.concepts.items():
        if s != system or rec.is_category:
            continue
        if (prop == "discipline" and rec.discipline == value) or (prop == "module" and system == SYSTEM_TM2):
            out.append(rec)
    return sorted(out, key=lambda r: r.code)


def _tm2_filter(recs: list[ConceptRecord], text: str) -> list[ConceptRecord]:
    key = compact(fold(text))
    return [r for r in recs if any(key in s.norm.compact for s in r.surfaces)] if key else recs


def expand(
    engine: MappingEngine, vs_id: str, filter_text: str | None, count: int, offset: int
) -> dict[str, Any]:
    vs = valueset(engine, vs_id)
    recs = members(engine, vs_id)
    _, system, _, _, _ = VALUESETS[vs_id]
    if filter_text:
        if system == SYSTEM_NAMASTE:
            allowed = {r.code for r in recs}
            ranked = [s.code for s in engine.suggest(filter_text, limit=25) if s.code in allowed]
            by_code = {r.code: r for r in recs}
            recs = [by_code[c] for c in ranked]
        else:
            recs = _tm2_filter(recs, filter_text)
    page = recs[offset : offset + count]
    url = system_uri(system, engine.index.is_sample)
    params: list[dict[str, Any]] = [
        {"name": "count", "valueInteger": count},
        {"name": "offset", "valueInteger": offset},
    ]
    if filter_text:
        params.insert(0, {"name": "filter", "valueString": filter_text})
    version = engine.index.release_version("namaste" if system == SYSTEM_NAMASTE else "icd11") or "unknown"
    params.append({"name": "used-codesystem", "valueUri": f"{url}|{version}"})
    vs.pop("compose", None)
    vs["expansion"] = {
        "identifier": urn_uuid(),
        "timestamp": now_iso(),
        "total": len(recs),
        "offset": offset,
        "parameter": params,
        "contains": [{"system": url, "code": r.code, "display": r.display} for r in page],
    }
    return vs


def find_valueset_id(url_or_id: str) -> str:
    vs_id = url_or_id.rstrip("/").rsplit("/", 1)[-1]
    if vs_id not in VALUESETS:
        raise FHIRError(404, f"unknown ValueSet {url_or_id}", "not-found")
    return vs_id
