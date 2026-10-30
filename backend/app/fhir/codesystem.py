"""CodeSystem resources built from the active terminology index."""

from __future__ import annotations

from typing import Any

from app.db.models import SYSTEM_BIO, SYSTEM_NAMASTE, SYSTEM_TM2
from app.engine.index import ConceptRecord, TerminologyIndex
from app.fhir.common import PUBLISHER, canonical, now_iso, system_uri

NAMASTE_ID = "namaste"
ICD11_SAMPLE_ID = "icd11-mms-sample"
SAMPLE_NOTE = "SAMPLE — illustrative, not official. Codes prefixed SAMPLE- are not real NAMASTE or WHO codes."

# Designation languages (BCP-47) for each kind of surface form.
LANGUAGE = {
    "iast": "sa-Latn",
    "devanagari": "sa-Deva",
    "english": "en",
    "hinglish": "hi-Latn",
    "index_term": "en",
}

PROPERTY_DEFS = [
    {
        "code": "parent",
        "uri": "http://hl7.org/fhir/concept-properties#parent",
        "type": "code",
        "description": "Parent concept in the hierarchy",
    },
    {"code": "entity-type", "type": "string", "description": "disorder | pattern | category"},
    {"code": "discipline", "type": "string", "description": "ayurveda | siddha | unani | biomedicine"},
    {"code": "module", "type": "string", "description": "ICD-11 module: tm2 | biomedicine (ICD-11 only)"},
]


def designations(rec: ConceptRecord) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen = {rec.display}
    for s in rec.surfaces:
        lang = LANGUAGE.get(s.kind)
        if lang and s.text not in seen:
            seen.add(s.text)
            out.append({"language": lang, "value": s.text})
    return out


def concept_entry(rec: ConceptRecord, module: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"code": rec.code, "display": rec.display}
    if rec.definition:
        entry["definition"] = rec.definition
    if d := designations(rec):
        entry["designation"] = d
    props: list[dict[str, Any]] = [{"code": "entity-type", "valueString": rec.entity_type}]
    if rec.parent_code:
        props.insert(0, {"code": "parent", "valueCode": rec.parent_code})
    if rec.discipline:
        props.append({"code": "discipline", "valueString": rec.discipline})
    if module:
        props.append({"code": "module", "valueString": module})
    entry["property"] = props
    return entry


def module_of(rec: ConceptRecord) -> str:
    return "tm2" if rec.system == SYSTEM_TM2 else "biomedicine"


def namaste_codesystem(index: TerminologyIndex) -> dict[str, Any]:
    recs = sorted((r for (s, _), r in index.concepts.items() if s == SYSTEM_NAMASTE), key=lambda r: r.code)
    sample = index.is_sample
    return {
        "resourceType": "CodeSystem",
        "id": NAMASTE_ID,
        "url": system_uri(SYSTEM_NAMASTE, sample),
        "version": index.release_version("namaste") or "unknown",
        "name": "NAMASTE",
        "title": "NAMASTE — National Ayush Morbidity & Standardized Terminologies Electronic"
        + (" (SAMPLE)" if sample else ""),
        "status": "active",
        "experimental": sample,
        "date": now_iso(),
        "publisher": PUBLISHER,
        "description": (SAMPLE_NOTE + " " if sample else "")
        + "Ayush diagnosis codes with IAST, Devanagari, English and Hinglish designations.",
        "caseSensitive": True,
        "hierarchyMeaning": "is-a",
        "content": "complete",
        "count": len(recs),
        "property": PROPERTY_DEFS[:3],
        "concept": [concept_entry(r) for r in recs],
    }


def icd11_sample_codesystem(index: TerminologyIndex) -> dict[str, Any] | None:
    """Only for SAMPLE data: real ICD-11 codes use WHO's URI and are not re-published here."""
    if not index.is_sample:
        return None
    recs = sorted(
        (r for (s, _), r in index.concepts.items() if s in (SYSTEM_TM2, SYSTEM_BIO)), key=lambda r: r.code
    )
    return {
        "resourceType": "CodeSystem",
        "id": ICD11_SAMPLE_ID,
        "url": canonical("CodeSystem", ICD11_SAMPLE_ID),
        "version": index.release_version("icd11") or "unknown",
        "name": "ICD11MMSSample",
        "title": "ICD-11 MMS — TM2 and biomedicine (SAMPLE stand-in)",
        "status": "active",
        "experimental": True,
        "date": now_iso(),
        "publisher": PUBLISHER,
        "description": SAMPLE_NOTE
        + " Stand-in for http://id.who.int/icd/release/11/mms in offline/mock mode.",
        "caseSensitive": True,
        "hierarchyMeaning": "is-a",
        "content": "complete",  # every SAMPLE code is included
        "count": len(recs),
        "property": PROPERTY_DEFS,
        "concept": [concept_entry(r, module_of(r)) for r in recs],
    }


def all_codesystems(index: TerminologyIndex) -> list[dict[str, Any]]:
    return [cs for cs in (namaste_codesystem(index), icd11_sample_codesystem(index)) if cs]


def records_for_system(index: TerminologyIndex, system_url: str) -> list[ConceptRecord] | None:
    """Concepts behind a FHIR system URI (NAMASTE, the SAMPLE ICD-11 stand-in, or WHO's MMS URI)."""
    sample = index.is_sample
    if system_url == system_uri(SYSTEM_NAMASTE, sample):
        return [r for (s, _), r in index.concepts.items() if s == SYSTEM_NAMASTE]
    if system_url == system_uri(SYSTEM_TM2, sample):
        return [r for (s, _), r in index.concepts.items() if s in (SYSTEM_TM2, SYSTEM_BIO)]
    return None
