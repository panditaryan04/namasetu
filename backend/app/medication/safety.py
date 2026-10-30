"""Safety badges and the duplicate-salt check. Badges describe the data; they are never advice.

- Schedule H / H1 / X -> "prescription-only" (only when the source states the schedule).
- Processed metals / minerals in an Ayush formulation -> "use only under qualified supervision".
- NLEM flag, SAMPLE label.
- Duplicate salt: the same salt in two or more selected products. Shared herbs are reported as information.
Interaction checks (Milestone 5B) cite their dataset; absence of data is never reported as "no interaction".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.medication.catalog import Badge, MedicationCatalog, MedicationView

SCHEDULE_TEXT = {
    "H": "Schedule H — prescription-only",
    "H1": "Schedule H1 — prescription-only, record-keeping required",
    "X": "Schedule X — prescription-only, strictly controlled",
    "OTC": "Over the counter (as stated by source)",
}


def badges_for(view: MedicationView) -> list[Badge]:
    from app.medication.catalog import Badge

    out: list[Badge] = []
    if view.schedule:
        sev = "info" if view.schedule == "OTC" else "warning"
        code, label = f"schedule-{view.schedule.lower()}", SCHEDULE_TEXT[view.schedule]
        out.append(Badge(code=code, label=label, severity=sev))
    if view.kind != "biomedical" and any(c.metal_mineral for c in view.composition):
        names = ", ".join(sorted({c.canonical_name for c in view.composition if c.metal_mineral}))
        out.append(
            Badge(
                code="metal-mineral",
                severity="caution",
                label=f"Contains processed metals/minerals ({names}): use only under qualified supervision",
            )
        )
    if view.nlem:
        out.append(Badge(code="nlem", label="National List of Essential Medicines", severity="info"))
    if view.is_sample:
        out.append(Badge(code="sample", label="SAMPLE — illustrative, not official", severity="info"))
    return out


class DuplicateItem(BaseModel):
    code: str
    name: str
    strength: str | None


class DuplicateWarning(BaseModel):
    kind: str  # salt | herb
    severity: str  # warning (salt) | info (shared herb)
    ingredient_id: int
    ingredient: str
    medications: list[DuplicateItem]
    message: str


def duplicate_ingredients(catalog: MedicationCatalog, codes: list[str]) -> list[DuplicateWarning]:
    seen: dict[int, list[DuplicateItem]] = {}
    for code in dict.fromkeys(codes):
        view = catalog.get(code)
        if view is None:
            continue
        for c in view.composition:
            if c.kind in {"salt", "herb"}:
                seen.setdefault(c.ingredient_id, []).append(
                    DuplicateItem(code=code, name=view.name, strength=c.strength)
                )
    out = []
    for ing_id, items in seen.items():
        if len({i.code for i in items}) < 2:
            continue
        ing = catalog.ingredients[ing_id]
        salt = ing.kind == "salt"
        names = " and ".join(i.name for i in items)
        out.append(
            DuplicateWarning(
                kind=ing.kind,
                severity="warning" if salt else "info",
                ingredient_id=ing_id,
                ingredient=ing.canonical_name,
                medications=items,
                message=(
                    f"Duplicate salt: {ing.canonical_name} is present in {names}."
                    if salt
                    else f"Shared herb: {ing.canonical_name} is present in {names}."
                ),
            )
        )
    return sorted(out, key=lambda w: (w.severity != "warning", w.ingredient))
