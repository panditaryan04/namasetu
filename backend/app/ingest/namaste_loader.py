"""NAMASTE export loader (CSV or XLSX) driven by a configurable column map.

Bad rows are recorded in the IngestReport and skipped; they never abort the run.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.db.models import SYSTEM_NAMASTE
from app.ingest.schemas import ConceptIn, IngestReport, RowIssue, TermIn

VALID_ENTITY_TYPES = {"disorder", "pattern", "category"}


class NamasteColumnMap(BaseModel):
    """Maps Concept fields to column headers in the export. Override with a YAML file for a real export."""

    code: str = "NAMC_CODE"
    display: str = "NAMC_term"
    term_iast: str | None = "NAMC_term_diacritical"
    term_devanagari: str | None = "NAMC_term_DEVANAGARI"
    term_en: str | None = "Name_English"
    definition: str | None = "Short_definition"
    long_definition: str | None = "Long_definition"
    ontology_branches: str | None = "Ontology_branches"
    parent_code: str | None = "Parent_code"
    entity_type: str | None = "Entity_type"
    discipline: str | None = None
    default_discipline: str = "ayurveda"
    default_entity_type: str = "disorder"

    @classmethod
    def from_yaml(cls, path: Path) -> NamasteColumnMap:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFC", str(value)).strip()
    return text or None


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Read a CSV (UTF-8, optional BOM) or the first sheet of an XLSX into dicts."""
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        sheet = wb.worksheets[0]
        it = sheet.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(it)]
        rows = [dict(zip(header, r, strict=False)) for r in it if any(c is not None for c in r)]
        wb.close()
        return rows
    text = path.read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def parse_namaste(
    rows: Iterable[Mapping[str, Any]], column_map: NamasteColumnMap | None = None
) -> tuple[list[ConceptIn], IngestReport]:
    cmap = column_map or NamasteColumnMap()
    report = IngestReport(source="namaste")
    concepts: list[ConceptIn] = []
    seen: set[str] = set()

    def col(row: Mapping[str, Any], name: str | None) -> str | None:
        return _clean(row.get(name)) if name else None

    for idx, row in enumerate(rows, start=2):  # row 1 is the header
        report.total_rows += 1
        code = col(row, cmap.code)
        display = col(row, cmap.display)
        if not code:
            report.rejected.append(RowIssue(row=idx, problem=f"missing code column '{cmap.code}'"))
            continue
        if not display:
            report.rejected.append(RowIssue(row=idx, code=code, problem="missing term"))
            continue
        if code in seen:
            report.rejected.append(RowIssue(row=idx, code=code, problem="duplicate code"))
            continue
        entity_type = (col(row, cmap.entity_type) or cmap.default_entity_type).lower()
        if entity_type not in VALID_ENTITY_TYPES:
            report.rejected.append(
                RowIssue(row=idx, code=code, problem=f"unknown entity type '{entity_type}'")
            )
            continue
        seen.add(code)

        iast = col(row, cmap.term_iast)
        deva = col(row, cmap.term_devanagari)
        english = col(row, cmap.term_en)
        terms = [TermIn(term=display, kind="primary")]
        terms += [TermIn(term=iast, kind="iast")] if iast else []
        terms += [TermIn(term=deva, kind="devanagari")] if deva else []
        terms += [TermIn(term=english, kind="english")] if english else []

        extra: dict[str, object] = {}
        if long_def := col(row, cmap.long_definition):
            extra["long_definition"] = long_def
        if branches := col(row, cmap.ontology_branches):
            extra["ontology_branches"] = branches

        concepts.append(
            ConceptIn(
                system=SYSTEM_NAMASTE,
                code=code,
                display=display,
                discipline=(col(row, cmap.discipline) or cmap.default_discipline).lower(),
                term_en=english,
                term_iast=iast,
                term_devanagari=deva,
                definition=col(row, cmap.definition),
                parent_code=col(row, cmap.parent_code),
                entity_type=entity_type,
                extra=extra,
                terms=terms,
            )
        )

    known = {c.code for c in concepts}
    for c in concepts:
        if c.parent_code and c.parent_code not in known:
            report.warnings.append(f"{c.code}: parent {c.parent_code} not in this export")
    report.accepted = len(concepts)
    return concepts, report


def load_namaste_file(
    path: Path, column_map: NamasteColumnMap | None = None
) -> tuple[list[ConceptIn], IngestReport]:
    return parse_namaste(read_rows(path), column_map)
