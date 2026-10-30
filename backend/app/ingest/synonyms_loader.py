"""Synonym dictionary (YAML): per-concept English / IAST / Devanagari / Hinglish variants + root terms."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from app.ingest.schemas import IngestReport, RowIssue

SynonymKind = Literal["english", "iast", "devanagari", "hinglish"]
KINDS: tuple[SynonymKind, ...] = ("english", "iast", "devanagari", "hinglish")


class SynonymSet(BaseModel):
    version: str
    label: str | None = None
    roots: list[str] = Field(default_factory=list)
    entries: dict[str, list[tuple[SynonymKind, str]]] = Field(default_factory=dict)


def parse_synonyms(data: dict[str, Any]) -> tuple[SynonymSet, IngestReport]:
    report = IngestReport(source="synonyms")
    version = str(data.get("version") or "").strip()
    if not version:
        raise ValueError("synonyms file has no 'version'")
    entries: dict[str, list[tuple[SynonymKind, str]]] = {}
    for n, (code, block) in enumerate((data.get("concepts") or {}).items(), start=1):
        report.total_rows += 1
        if not isinstance(block, dict):
            report.rejected.append(RowIssue(row=n, code=str(code), problem="entry is not a mapping"))
            continue
        unknown = set(block) - set(KINDS)
        if unknown:
            report.warnings.append(f"{code}: ignored unknown keys {sorted(unknown)}")
        terms: list[tuple[SynonymKind, str]] = []
        for kind in KINDS:
            for term in block.get(kind) or []:
                clean = unicodedata.normalize("NFC", str(term)).strip()
                if clean and (kind, clean) not in terms:
                    terms.append((kind, clean))
        entries[str(code)] = terms
        report.accepted += 1
    roots = sorted({str(r).strip().lower() for r in data.get("roots") or [] if str(r).strip()})
    return SynonymSet(version=version, label=data.get("label"), roots=roots, entries=entries), report


def load_synonyms_file(path: Path) -> tuple[SynonymSet, IngestReport]:
    return parse_synonyms(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
