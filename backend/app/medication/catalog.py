"""In-memory medication catalogue (rebuilt when the medication release changes) and its response views.

Response models deliberately contain NO dose / frequency / duration / regimen fields; a test scans the
OpenAPI schema to keep it that way. Every view carries the reference-only disclaimer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import SOURCE_MEDICATIONS, Brand, Ingredient, Medication
from app.ingest.release import latest_release

DISCLAIMER = "Reference only — not a prescription. Consult a qualified clinician."
SAMPLE_LABEL = "SAMPLE — illustrative, not official"


class SourceRef(BaseModel):
    title: str
    edition: str | None = None
    page: str | None = None


class Badge(BaseModel):
    code: str
    label: str
    severity: Literal["info", "caution", "warning"]


class CompositionView(BaseModel):
    ingredient_id: int
    printed_name: str
    canonical_name: str
    kind: str  # salt | herb | mineral | other
    botanical_name: str | None
    part_used: str | None
    proportion: str | None  # Ayush, as printed
    strength: str | None  # biomedical, as printed (e.g. "500 mg")
    metal_mineral: bool


class MedicationView(BaseModel):
    code: str
    kind: str
    name: str
    name_iast: str | None
    name_devanagari: str | None
    discipline: str | None
    dosage_form: str
    route: str | None
    therapeutic_class: str | None
    schedule: str | None
    nlem: bool | None
    composition: list[CompositionView]
    badges: list[Badge]
    brands: list[str]
    source: SourceRef
    caution_note: str | None  # only ever as printed in the cited source
    is_sample: bool
    sample_label: str | None
    active: bool
    disclaimer: str = DISCLAIMER


@dataclass
class IngredientRec:
    id: int
    key: str
    kind: str
    canonical_name: str
    botanical_name: str | None
    iast: str | None
    devanagari: str | None
    synonyms: list[dict[str, str]]
    metal_mineral: bool


@dataclass
class MedRec:
    row_id: int
    view: MedicationView
    ingredient_ids: list[int] = field(default_factory=list)


class MedicationCatalog:
    def __init__(
        self, meds: list[Medication], brands: list[Brand], ingredients: list[Ingredient], version: int
    ) -> None:
        from app.medication.safety import badges_for

        self.version = version
        self.ingredients = {
            i.id: IngredientRec(
                i.id,
                i.key,
                i.kind,
                i.canonical_name,
                i.botanical_name,
                i.iast,
                i.devanagari,
                list(i.synonyms or []),
                i.metal_mineral,
            )
            for i in ingredients
        }
        brands_by_med: dict[int, list[str]] = {}
        for b in brands:
            brands_by_med.setdefault(b.medication_id, []).append(b.brand_name)
        self.brands = [(b.brand_name, b.key, b.medication_id) for b in brands]
        self.by_code: dict[str, MedRec] = {}
        self.by_id: dict[int, MedRec] = {}
        for m in meds:
            comp = [
                CompositionView(
                    ingredient_id=c.ingredient_id,
                    printed_name=c.printed_name,
                    canonical_name=self.ingredients[c.ingredient_id].canonical_name,
                    kind=self.ingredients[c.ingredient_id].kind,
                    botanical_name=c.botanical_name or self.ingredients[c.ingredient_id].botanical_name,
                    part_used=c.part_used,
                    proportion=c.proportion,
                    strength=c.strength_text,
                    metal_mineral=self.ingredients[c.ingredient_id].metal_mineral,
                )
                for c in m.composition
            ]
            view = MedicationView(
                code=m.code,
                kind=m.kind,
                name=m.name,
                name_iast=m.name_iast,
                name_devanagari=m.name_devanagari,
                discipline=m.discipline,
                dosage_form=m.dosage_form,
                route=m.route,
                therapeutic_class=m.therapeutic_class,
                schedule=m.schedule,
                nlem=m.nlem,
                composition=comp,
                badges=[],
                brands=sorted(brands_by_med.get(m.id, [])),
                source=SourceRef(title=m.source_title, edition=m.source_edition, page=m.source_page),
                caution_note=m.caution_note,
                is_sample=m.is_sample,
                sample_label=SAMPLE_LABEL if m.is_sample else None,
                active=m.active,
            )
            view.badges = badges_for(view)
            rec = MedRec(m.id, view, [c.ingredient_id for c in m.composition])
            self.by_code[m.code] = rec
            self.by_id[m.id] = rec

    def get(self, code: str) -> MedicationView | None:
        rec = self.by_code.get(code)
        return rec.view if rec and rec.view.active else None

    def containing(self, ingredient_id: int) -> list[MedicationView]:
        return [r.view for r in self.by_code.values() if r.view.active and ingredient_id in r.ingredient_ids]


def medication_version(session: Session) -> int:
    rel = latest_release(session, SOURCE_MEDICATIONS)
    return rel.id if rel else 0


def build_catalog(session: Session) -> MedicationCatalog:
    meds = session.scalars(select(Medication).options(selectinload(Medication.composition))).all()
    return MedicationCatalog(
        list(meds),
        list(session.scalars(select(Brand))),
        list(session.scalars(select(Ingredient))),
        medication_version(session),
    )


class CatalogHolder:
    def __init__(self) -> None:
        self._catalog: MedicationCatalog | None = None
        self._lock = threading.Lock()

    def get(self, session: Session) -> MedicationCatalog:
        version = medication_version(session)
        if self._catalog is None or self._catalog.version != version:
            with self._lock:
                if self._catalog is None or self._catalog.version != version:
                    self._catalog = build_catalog(session)
        return self._catalog


catalog_holder = CatalogHolder()
