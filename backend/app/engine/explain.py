"""The Explanation Object returned for every candidate, and its template-built reasoning.

`reasoning` is assembled ONLY from the evidence fields below (no generative model), so it can never
state anything that is not in the data.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.engine.gate import Band
from app.engine.relationship import Relationship

ICD11_MMS_URI = "http://id.who.int/icd/release/11/mms"
NO_BIO_LINK = "No biomedical link (clinician may add)"


class QueryInfo(BaseModel):
    text: str
    normalised: str
    script: str


class CodeRef(BaseModel):
    system: str
    code: str
    display: str


class TM2Ref(CodeRef):
    entity_type: str


class BiomedicineRef(BaseModel):
    system: str | None = None
    code: str | None = None
    display: str | None = None
    relationship: Literal["relatedto", "equivalent", "wider", "narrower"] = "relatedto"
    source: Literal["who-index", "clinician", "none"] = "none"
    status: Literal["approved", "pending", "none"] = "none"
    message: str | None = None


class Scores(BaseModel):
    exact: float
    lexical: float
    ngram: float
    transliteration: float
    semantic: float
    hierarchy: float
    final: float


class MatchedTerm(BaseModel):
    source: str
    target: str
    via: str


class Evidence(BaseModel):
    matched_tokens: list[str]
    matched_root_terms: list[str]
    matched_terms: list[MatchedTerm]
    shared_concepts: list[str]
    hierarchy_path: list[str]
    entity_type_check: str
    transliteration_hit: bool
    stages_hit: list[str]
    baseline: str | None = Field(None, description="official-crosswalk | approved-mapping when Stage 0 fired")


class Explanation(BaseModel):
    query: QueryInfo
    namaste: CodeRef | None
    tm2: TM2Ref
    biomedicine: BiomedicineRef
    confidence: float
    band: Band
    relationship: Relationship
    scores: Scores
    evidence: Evidence
    reasoning: str
    dataset_releases: list[str]
    model_version: str


STAGE_THRESHOLDS = {"lexical": 0.6, "ngram": 0.5, "semantic": 0.3, "hierarchy": 0.3}

RELATIONSHIP_TEXT = {
    "equivalent": "the target names the same concept",
    "wider": "the target is broader than the source",
    "narrower": "the target is more specific than the source",
    "relatedto": "related, but not the same concept",
}


def stages_hit(scores: Scores, baseline: str | None) -> list[str]:
    hit = ["exact"] if scores.exact >= 1.0 or baseline else []
    hit += [s for s, t in STAGE_THRESHOLDS.items() if getattr(scores, s) >= t]
    if scores.transliteration > 0:
        hit.insert(1 if hit[:1] == ["exact"] else 0, "transliteration")
    return hit


def build_reasoning(
    ev: Evidence, relationship: str, band: Band, source_type: str | None, target_type: str
) -> str:
    parts: list[str] = []
    if ev.baseline:
        parts.append(f"Deterministic baseline ({ev.baseline.replace('-', ' ')})")
    if ev.matched_terms:
        m = ev.matched_terms[0]
        parts.append(f"Matched '{m.source}' to '{m.target}' via {m.via}")
    if ev.matched_root_terms:
        parts.append("shared root terms " + ", ".join(f"'{r}'" for r in ev.matched_root_terms))
    words = [w for w in ev.shared_concepts if w not in ev.matched_root_terms]
    if words:
        parts.append("shared concepts " + ", ".join(f"'{w}'" for w in words[:3]))
    if "hierarchy" in ev.stages_hit:
        parts.append("parent categories align")
    if source_type is None:
        pass
    elif source_type == target_type:
        parts.append(f"entity types agree ({target_type})")
    else:
        parts.append(f"entity type mismatch ({source_type} vs {target_type}), penalised")
    sentence = "; ".join(parts) if parts else "Weak evidence only"
    rel_text = RELATIONSHIP_TEXT[relationship]
    return f"{sentence}. Relationship '{relationship}': {rel_text}. Confidence band: {band}."
