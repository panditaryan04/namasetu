"""Source-neutral records produced by every loader and consumed by release.py."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntityType = Literal["disorder", "pattern", "category"]


class TermIn(BaseModel):
    term: str
    kind: Literal["primary", "english", "iast", "devanagari", "hinglish", "index_term"]


class ConceptIn(BaseModel):
    system: str
    code: str
    display: str
    discipline: str | None = None
    term_en: str | None = None
    term_iast: str | None = None
    term_devanagari: str | None = None
    definition: str | None = None
    parent_code: str | None = None
    entity_type: EntityType
    class_kind: str | None = None
    extra: dict[str, object] = Field(default_factory=dict)
    terms: list[TermIn] = Field(default_factory=list)

    def content_fields(self) -> dict[str, object]:
        """Fields that define the concept's content; a change here is a 'changed' code in a release diff."""
        return self.model_dump(exclude={"terms"})


class LinkIn(BaseModel):
    source_system: str
    source_code: str
    target_system: str
    target_code: str
    relationship: Literal["equivalent", "wider", "narrower", "relatedto"] = "relatedto"
    origin: Literal["who-index", "clinician"]


class RowIssue(BaseModel):
    row: int
    code: str | None = None
    problem: str


class IngestReport(BaseModel):
    source: str
    total_rows: int = 0
    accepted: int = 0
    rejected: list[RowIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
