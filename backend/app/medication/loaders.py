"""Loaders for medication reference data (CSV / YAML), each with a fixed column contract.

Safety rules enforced here (rows that break them are rejected, never repaired by guessing):
- every medication, brand and condition link must carry a source title (citation);
- every ingredient must resolve to the ingredient dictionary, or it is recorded exactly as printed and
  reported as unresolved;
- strengths are kept exactly as printed; no dose regimen fields exist.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.engine.transliterator import fold
from app.ingest.schemas import IngestReport, RowIssue
from app.medication.salt_normalizer import parse_strength, salt_base, salt_key

IngredientKind = Literal["salt", "herb", "mineral", "other"]
MedKind = Literal["ayush_formulation", "ayush_single", "biomedical"]
SCHEDULES = {"H", "H1", "X", "OTC"}
CONDITION_SYSTEMS = {"namaste", "icd11-tm2", "icd11-bio"}


class IngredientIn(BaseModel):
    key: str
    kind: IngredientKind
    canonical_name: str
    botanical_name: str | None = None
    iast: str | None = None
    devanagari: str | None = None
    synonyms: list[dict[str, str]] = Field(default_factory=list)  # {"term", "kind"}
    metal_mineral: bool = False


class CompositionLine(BaseModel):
    ingredient_key: str
    printed_name: str
    botanical_name: str | None = None
    part_used: str | None = None
    proportion: str | None = None
    strength_text: str | None = None
    strength_value: str | None = None
    strength_unit: str | None = None


class MedicationIn(BaseModel):
    code: str
    kind: MedKind
    name: str
    name_iast: str | None = None
    name_devanagari: str | None = None
    discipline: str | None = None
    dosage_form: str
    route: str | None = None
    therapeutic_class: str | None = None
    schedule: str | None = None
    nlem: bool | None = None
    source_title: str
    source_edition: str | None = None
    source_page: str | None = None
    caution_note: str | None = None
    composition: list[CompositionLine]


class BrandIn(BaseModel):
    brand_name: str
    medication_code: str
    source_title: str


class LinkIn(BaseModel):
    condition_system: str
    condition_code: str
    medication_code: str
    source_title: str
    source_edition: str | None = None
    source_page: str | None = None
    status: Literal["approved", "pending"]
    reviewer: str | None = None
    note: str | None = None


def slug(name: str) -> str:
    """Stable ASCII identifier for an ingredient key ("Mefenamic acid" -> "mefenamic-acid")."""
    plain = "".join(ch for ch in unicodedata.normalize("NFD", name) if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def _s(row: Mapping[str, Any], name: str) -> str | None:
    v = row.get(name)
    v = str(v).strip() if v is not None else ""
    return v or None


class IngredientDictionary:
    """Every name of every ingredient (canonical, synonyms, botanical, common, IAST, Devanagari) -> key."""

    def __init__(self, ingredients: list[IngredientIn]) -> None:
        self.by_key = {i.key: i for i in ingredients}
        self._names: dict[str, str] = {}
        for i in ingredients:
            for term in [
                i.canonical_name,
                i.botanical_name,
                i.iast,
                i.devanagari,
                *(s["term"] for s in i.synonyms),
            ]:
                if term:
                    self._names.setdefault(self._fold(term, i.kind), i.key)

    @staticmethod
    def _fold(term: str, kind: str) -> str:
        return salt_key(term) if kind == "salt" else fold(term).replace(" ", "")

    def resolve(self, name: str, kind_hint: str | None = None) -> str | None:
        for kind in ([kind_hint] if kind_hint else []) + ["herb", "mineral", "other", "salt"]:
            key = self._names.get(self._fold(name, kind))
            if key is not None:
                return key
        return None


def parse_medication_synonyms(data: dict[str, Any]) -> tuple[list[IngredientIn], IngestReport]:
    report = IngestReport(source="medication-synonyms")
    out: list[IngredientIn] = []
    sections: list[tuple[str, IngredientKind]] = [
        ("salts", "salt"),
        ("herbs", "herb"),
        ("minerals", "mineral"),
        ("other", "other"),
    ]
    for section, kind in sections:
        for n, (name, block) in enumerate((data.get(section) or {}).items(), start=1):
            report.total_rows += 1
            block = block or {}
            synonyms = [
                {"term": t, "kind": k}
                for k in ("synonyms", "common", "botanical")
                for t in block.get(k) or []
            ]
            synonyms += [{"term": t, "kind": "devanagari"} for t in (block.get("devanagari") or [])[1:]]
            synonyms += [{"term": t, "kind": "iast"} for t in (block.get("iast") or [])[1:]]
            botanical = (block.get("botanical") or [None])[0]
            out.append(
                IngredientIn(
                    key=f"{kind}:{slug(salt_base(name) if kind == 'salt' else name)}",
                    kind=kind,
                    canonical_name=salt_base(name) if kind == "salt" else name,
                    botanical_name=botanical,
                    iast=(block.get("iast") or [None])[0],
                    devanagari=(block.get("devanagari") or [None])[0],
                    synonyms=synonyms,
                    metal_mineral=kind == "mineral",
                )
            )
            report.accepted += 1
            del n
    return out, report


def parse_formulations(
    rows: Iterable[Mapping[str, Any]], dictionary: IngredientDictionary
) -> tuple[list[MedicationIn], list[IngredientIn], IngestReport]:
    """One row per ingredient. Unresolved ingredients are kept exactly as printed (and reported)."""
    report = IngestReport(source="ayush-formulations")
    meds: dict[str, MedicationIn] = {}
    unlisted: dict[str, IngredientIn] = {}
    for idx, row in enumerate(rows, start=2):
        report.total_rows += 1
        code, name, ingredient = _s(row, "medication_code"), _s(row, "name"), _s(row, "ingredient")
        kind, source = _s(row, "kind"), _s(row, "source_title")
        if not (code and name and ingredient):
            report.rejected.append(RowIssue(row=idx, code=code, problem="missing code, name or ingredient"))
            continue
        if not source:
            report.rejected.append(RowIssue(row=idx, code=code, problem="no source citation"))
            continue
        if kind not in {"ayush_formulation", "ayush_single"}:
            report.rejected.append(RowIssue(row=idx, code=code, problem=f"bad kind '{kind}'"))
            continue
        key = dictionary.resolve(ingredient) or dictionary.resolve(_s(row, "botanical_name") or "")
        if key is None:
            key = f"unlisted:{slug(ingredient)}"
            unlisted.setdefault(
                key,
                IngredientIn(
                    key=key, kind="other", canonical_name=ingredient, botanical_name=_s(row, "botanical_name")
                ),
            )
            report.warnings.append(f"row {idx}: ingredient '{ingredient}' not in dictionary; kept as printed")
        med = meds.setdefault(
            code,
            MedicationIn(
                code=code,
                kind=kind,
                name=name,
                name_iast=_s(row, "name_iast"),
                name_devanagari=_s(row, "name_devanagari"),
                discipline=_s(row, "discipline"),
                dosage_form=_s(row, "dosage_form") or "unspecified",
                source_title=source,
                source_edition=_s(row, "source_edition"),
                source_page=_s(row, "source_page"),
                caution_note=_s(row, "caution_note"),
                composition=[],
            ),
        )
        med.composition.append(
            CompositionLine(
                ingredient_key=key,
                printed_name=ingredient,
                botanical_name=_s(row, "botanical_name"),
                part_used=_s(row, "part_used"),
                proportion=_s(row, "proportion"),
            )
        )
        report.accepted += 1
    return list(meds.values()), list(unlisted.values()), report


def parse_drugs(
    rows: Iterable[Mapping[str, Any]], dictionary: IngredientDictionary
) -> tuple[list[MedicationIn], list[IngredientIn], IngestReport]:
    """One row per salt; combination products appear as several rows with the same medication_code."""
    report = IngestReport(source="biomedical-drugs")
    meds: dict[str, MedicationIn] = {}
    new_salts: dict[str, IngredientIn] = {}
    for idx, row in enumerate(rows, start=2):
        report.total_rows += 1
        code, generic, salt, source = (
            _s(row, k) for k in ("medication_code", "generic_name", "salt", "source_title")
        )
        if not (code and generic and salt):
            report.rejected.append(RowIssue(row=idx, code=code, problem="missing code, generic name or salt"))
            continue
        if not source:
            report.rejected.append(RowIssue(row=idx, code=code, problem="no source citation"))
            continue
        schedule = (_s(row, "schedule") or "").upper() or None
        if schedule and schedule not in SCHEDULES:
            report.rejected.append(RowIssue(row=idx, code=code, problem=f"unknown schedule '{schedule}'"))
            continue
        strength_raw = _s(row, "strength")
        strength = parse_strength(strength_raw or "")
        if strength_raw and strength is None:
            report.rejected.append(
                RowIssue(row=idx, code=code, problem=f"unparseable strength '{strength_raw}'")
            )
            continue
        key = dictionary.resolve(salt, "salt")
        if key is None:
            key = f"salt:{slug(salt_base(salt))}"
            new_salts.setdefault(key, IngredientIn(key=key, kind="salt", canonical_name=salt_base(salt)))
            report.warnings.append(
                f"row {idx}: salt '{salt}' not in dictionary; added as '{salt_base(salt)}'"
            )
        nlem_raw = (_s(row, "nlem") or "").lower()
        med = meds.setdefault(
            code,
            MedicationIn(
                code=code,
                kind="biomedical",
                name=generic,
                dosage_form=_s(row, "dosage_form") or "unspecified",
                route=_s(row, "route"),
                therapeutic_class=_s(row, "therapeutic_class"),
                schedule=schedule,
                nlem=True if nlem_raw == "true" else False if nlem_raw == "false" else None,
                source_title=source,
                source_edition=_s(row, "source_edition"),
                source_page=_s(row, "source_page"),
                composition=[],
            ),
        )
        med.composition.append(
            CompositionLine(
                ingredient_key=key,
                printed_name=salt,
                strength_text=strength.text if strength else None,
                strength_value=strength.value if strength else None,
                strength_unit=strength.unit if strength else None,
            )
        )
        report.accepted += 1
    return list(meds.values()), list(new_salts.values()), report


def parse_brands(rows: Iterable[Mapping[str, Any]]) -> tuple[list[BrandIn], IngestReport]:
    report = IngestReport(source="brands")
    out: list[BrandIn] = []
    for idx, row in enumerate(rows, start=2):
        report.total_rows += 1
        brand, code, source = _s(row, "brand"), _s(row, "medication_code"), _s(row, "source_title")
        if not (brand and code and source):
            report.rejected.append(
                RowIssue(row=idx, code=code, problem="missing brand, medication or citation")
            )
            continue
        out.append(BrandIn(brand_name=brand, medication_code=code, source_title=source))
        report.accepted += 1
    return out, report


def parse_links(rows: Iterable[Mapping[str, Any]]) -> tuple[list[LinkIn], IngestReport]:
    report = IngestReport(source="medication-links")
    out: list[LinkIn] = []
    for idx, row in enumerate(rows, start=2):
        report.total_rows += 1
        system, status, source = _s(row, "condition_system"), _s(row, "status"), _s(row, "source_title")
        if system not in CONDITION_SYSTEMS:
            report.rejected.append(RowIssue(row=idx, problem=f"bad condition_system '{system}'"))
            continue
        if not source:
            report.rejected.append(
                RowIssue(row=idx, code=_s(row, "condition_code"), problem="no source citation")
            )
            continue
        if status not in {"approved", "pending"}:
            report.rejected.append(RowIssue(row=idx, problem=f"bad status '{status}'"))
            continue
        if not (_s(row, "condition_code") and _s(row, "medication_code")):
            report.rejected.append(RowIssue(row=idx, problem="missing condition or medication code"))
            continue
        out.append(
            LinkIn(
                condition_system=system,
                condition_code=_s(row, "condition_code") or "",
                medication_code=_s(row, "medication_code") or "",
                source_title=source,
                source_edition=_s(row, "source_edition"),
                source_page=_s(row, "source_page"),
                status=status,
                reviewer=_s(row, "reviewer"),
                note=_s(row, "note"),
            )
        )
        report.accepted += 1
    return out, report
