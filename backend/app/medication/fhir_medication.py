"""FHIR R4 Medication and MedicationKnowledge.

- Medication.ingredient.strength is a Ratio only when the source states it: biomedical strengths in UCUM
  (mg, ug, g, [IU]; % w/w as g per 100 g) per unit of dosage form; Ayush proportions as parts of the total.
- MedicationKnowledge lists approved condition links under administrationGuidelines.indicationCodeableConcept.
  No `dosage` element is ever emitted: NamaSetu is reference only and never creates MedicationRequest.
"""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from typing import Any

from app.fhir.common import base_url, system_uri
from app.medication.catalog import CompositionView, MedicationView

UCUM = "http://unitsofmeasure.org"
UCUM_CODES = {"mg": "mg", "mcg": "ug", "g": "g", "IU": "[IU]"}
_PARTS = re.compile(r"^\s*(\d+(?:/\d+)?(?:\.\d+)?)\s*parts?\s*$", re.IGNORECASE)


def med_system() -> str:
    return f"{base_url()}/CodeSystem/namasetu-medication"


def ingredient_system() -> str:
    return f"{base_url()}/CodeSystem/namasetu-ingredient"


def _num(value: Decimal | Fraction) -> float | int:
    f = float(value)
    return int(f) if f.is_integer() else round(f, 6)


def _parts(text: str | None) -> Fraction | None:
    m = _PARTS.match(text or "")
    return Fraction(m.group(1)) if m else None


def _strength_ratio(
    c: CompositionView, view: MedicationView, total_parts: Fraction | None
) -> dict[str, Any] | None:
    if c.strength:
        m = re.match(r"^\s*([\d,]+(?:\.\d+)?)\s*(.*)$", c.strength)
        if not m:
            return None
        value = Decimal(m.group(1).replace(",", ""))
        unit = m.group(2).replace(" ", "").lower()
        if unit.startswith("%w/w") or unit == "%":
            return {
                "numerator": {"value": _num(value), "unit": "g", "system": UCUM, "code": "g"},
                "denominator": {"value": 100, "unit": "g", "system": UCUM, "code": "g"},
            }
        canonical = {"mg": "mg", "mcg": "mcg", "µg": "mcg", "g": "g", "iu": "IU"}.get(unit)
        if canonical is None:
            return None
        return {
            "numerator": {
                "value": _num(value),
                "unit": canonical,
                "system": UCUM,
                "code": UCUM_CODES[canonical],
            },
            "denominator": {"value": 1, "unit": view.dosage_form},
        }
    part = _parts(c.proportion)
    if part is not None and total_parts:
        return {
            "numerator": {"value": _num(part), "unit": "part"},
            "denominator": {"value": _num(total_parts), "unit": "part"},
        }
    return None


def _ingredients(view: MedicationView) -> list[dict[str, Any]]:
    parts = [_parts(c.proportion) for c in view.composition]
    total = Fraction(sum(p for p in parts if p)) if all(parts) and parts else None
    out = []
    for c in view.composition:
        item: dict[str, Any] = {
            "itemCodeableConcept": {
                "coding": [
                    {"system": ingredient_system(), "code": str(c.ingredient_id), "display": c.canonical_name}
                ],
                "text": c.printed_name + (f" ({c.botanical_name})" if c.botanical_name else ""),
            },
            "isActive": c.kind != "other",
        }
        if (ratio := _strength_ratio(c, view, total)) is not None:
            item["strength"] = ratio
        out.append(item)
    return out


def _code(view: MedicationView) -> dict[str, Any]:
    return {"coding": [{"system": med_system(), "code": view.code, "display": view.name}], "text": view.name}


def _meta(view: MedicationView) -> dict[str, Any]:
    if not view.is_sample:
        return {}
    return {
        "meta": {
            "tag": [
                {
                    "system": f"{base_url()}/CodeSystem/namasetu-tags",
                    "code": "sample-data",
                    "display": "SAMPLE — illustrative, not official",
                }
            ]
        }
    }


def medication_resource(view: MedicationView) -> dict[str, Any]:
    return {
        "resourceType": "Medication",
        "id": view.code,
        **_meta(view),
        "code": _code(view),
        "status": "active" if view.active else "inactive",
        "form": {"text": view.dosage_form},
        "ingredient": _ingredients(view),
    }


def medication_knowledge_resource(
    view: MedicationView, indications: list[tuple[str, str, str | None]], sample: bool
) -> dict[str, Any]:
    """`indications`: approved (internal system, code, display) condition links."""
    res: dict[str, Any] = {
        "resourceType": "MedicationKnowledge",
        "id": view.code,
        **_meta(view),
        "code": _code(view),
        "status": "active" if view.active else "inactive",
        "doseForm": {"text": view.dosage_form},
        "ingredient": _ingredients(view),
    }
    if view.brands:
        res["synonym"] = view.brands
    if view.route:
        res["intendedRoute"] = [{"text": view.route}]
    if indications:
        res["administrationGuidelines"] = [
            {
                "indicationCodeableConcept": {
                    "coding": [{"system": system_uri(s, sample), "code": c, **({"display": d} if d else {})}]
                }
            }
            for s, c, d in indications
        ]
    return res
