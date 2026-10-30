"""Medication reference: parsing, normalisation, safety rules, brand resolution and FHIR output."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.fhir.validator import validation_issues
from app.medication.brand_resolver import resolve_brand, search
from app.medication.catalog import DISCLAIMER, MedicationCatalog, catalog_holder
from app.medication.fhir_medication import medication_knowledge_resource, medication_resource
from app.medication.loaders import (
    IngredientDictionary,
    parse_brands,
    parse_drugs,
    parse_formulations,
    parse_links,
    parse_medication_synonyms,
)
from app.medication.safety import duplicate_ingredients
from app.medication.salt_normalizer import parse_strength, salt_base


@pytest.fixture(scope="module")
def catalog() -> Iterator[MedicationCatalog]:
    with get_sessionmaker()() as s:
        yield catalog_holder.get(s)


@pytest.fixture(scope="module")
def dictionary(sample_bundle_module: dict[str, Any]) -> IngredientDictionary:
    ingredients, _ = parse_medication_synonyms(sample_bundle_module["medications"]["synonyms"])
    return IngredientDictionary(ingredients)


@pytest.fixture(scope="module")
def sample_bundle_module() -> dict[str, Any]:
    from app.db.seed import load_bundle

    return load_bundle(use_embedded=False)


# ------------------------------------------------------------------ normalisation


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("500 mg", "500", "mg"),
        ("0.5mg", "0.5", "mg"),
        ("60,000 IU", "60000", "IU"),
        ("0.05% w/w", "0.05", "%w/w"),
        ("1 g", "1", "g"),
        ("500 mcg", "500", "mcg"),
        ("10 µg", "10", "mcg"),
        ("2.50 mg", "2.5", "mg"),
    ],
)
def test_parse_strength(text: str, value: str, unit: str) -> None:
    s = parse_strength(text)
    assert s is not None and (s.value, s.unit) == (value, unit) and s.text == text.strip()


@pytest.mark.parametrize("text", ["abc", "5 tablets", "", "mg 500", "1 tsp"])
def test_unparseable_strength(text: str) -> None:
    assert parse_strength(text) is None


@pytest.mark.parametrize(
    ("salt", "base"),
    [
        ("Metformin hydrochloride", "Metformin"),
        ("Sumatriptan succinate", "Sumatriptan"),
        ("Pantoprazole sodium", "Pantoprazole"),
        ("Clobetasol propionate", "Clobetasol"),
        ("Ferrous sulphate", "Ferrous sulphate"),
        ("Folic acid", "Folic acid"),
        ("Paracetamol", "Paracetamol"),
    ],
)
def test_salt_form_stripping(salt: str, base: str) -> None:
    assert salt_base(salt) == base


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("हरिद्रा", "herb:haridra"),
        ("Curcuma longa", "herb:haridra"),
        ("Turmeric", "herb:haridra"),
        ("haldi", "herb:haridra"),
        ("Haridrā", "herb:haridra"),
        ("Mulethi", "herb:yashtimadhu"),
        ("Glycyrrhiza glabra", "herb:yashtimadhu"),
        ("Emblica officinalis", "herb:amalaki"),
        ("आंवला", "herb:amalaki"),
        ("Acetaminophen", "salt:paracetamol"),
        ("Metformin HCl", "salt:metformin"),
        ("Lauha bhasma", "mineral:lauha-bhasma"),
    ],
)
def test_ingredient_names_resolve_across_scripts(
    dictionary: IngredientDictionary, name: str, key: str
) -> None:
    assert dictionary.resolve(name) == key


# ------------------------------------------------------------------ loaders enforce safety rules


def test_rows_without_citation_or_bad_values_are_rejected(dictionary: IngredientDictionary) -> None:
    base = {
        "medication_code": "M1",
        "kind": "ayush_single",
        "name": "Haridra",
        "ingredient": "Haridra",
        "dosage_form": "churna",
        "source_title": "API",
    }
    meds, _, rep = parse_formulations(
        [base, {**base, "medication_code": "M2", "source_title": ""}], dictionary
    )
    assert [m.code for m in meds] == ["M1"] and rep.rejected[0].problem == "no source citation"

    drug = {
        "medication_code": "B1",
        "generic_name": "X",
        "salt": "Paracetamol",
        "strength": "500 mg",
        "dosage_form": "tablet",
        "source_title": "NLEM",
    }
    meds, _, rep = parse_drugs(
        [
            drug,
            {**drug, "medication_code": "B2", "schedule": "Z"},
            {**drug, "medication_code": "B3", "strength": "two spoons"},
            {**drug, "medication_code": "B4", "source_title": None},
        ],
        dictionary,
    )
    assert [m.code for m in meds] == ["B1"]
    assert {i.problem.split()[0] for i in rep.rejected} == {"unknown", "unparseable", "no"}

    _, rep = parse_links(
        [
            {
                "condition_system": "namaste",
                "condition_code": "C",
                "medication_code": "M",
                "status": "approved",
                "source_title": "",
            }
        ]
    )
    assert rep.rejected[0].problem == "no source citation"
    _, rep = parse_brands([{"brand": "B", "medication_code": "M", "source_title": ""}])
    assert rep.rejected


def test_combination_products_split_per_salt(
    sample_bundle_module: dict[str, Any], dictionary: IngredientDictionary
) -> None:
    meds, new_salts, rep = parse_drugs(sample_bundle_module["medications"]["drugs"], dictionary)
    combi = next(m for m in meds if m.code == "SAMPLE-MED-B003")
    assert [(c.printed_name, c.strength_text) for c in combi.composition] == [
        ("Ibuprofen", "400 mg"),
        ("Paracetamol", "325 mg"),
    ]
    assert rep.rejected == [] and new_salts == []


def test_unlisted_ingredient_kept_as_printed(dictionary: IngredientDictionary) -> None:
    row = {
        "medication_code": "M9",
        "kind": "ayush_formulation",
        "name": "X",
        "ingredient": "Mysterium",
        "botanical_name": "Herba ignota",
        "dosage_form": "churna",
        "source_title": "AFI",
    }
    meds, unlisted, rep = parse_formulations([row], dictionary)
    assert unlisted[0].canonical_name == "Mysterium" and "kept as printed" in rep.warnings[0]
    assert meds[0].composition[0].ingredient_key.startswith("unlisted:")


# ------------------------------------------------------------------ brand resolution (never guesses)


@pytest.mark.parametrize(
    ("text", "brand", "match"),
    [
        ("Combiflam", "Combiflam", "exact"),
        ("Tab Dolo 650 BD", "Dolo 650", "exact"),
        ("Dolo", "Dolo 650", "exact"),
        ("Crokin", "Crocin", "fuzzy"),
        ("Cmobiflam", "Combiflam", "fuzzy"),
        ("Glycomt 500", "Glycomet 500", "fuzzy"),
        ("meftal spas", "Meftal-Spas", "exact"),
        ("Pan", "Pan 40", "exact"),
    ],
)
def test_brand_resolution(catalog: MedicationCatalog, text: str, brand: str, match: str) -> None:
    r = resolve_brand(catalog, text)
    assert r.found and r.brand == brand and r.match == match


@pytest.mark.parametrize("text", ["Xyzbrand", "Pa", "Crocinoplex", "Paracetamol", "Combiflaam forte max"])
def test_unknown_brands_are_not_guessed(catalog: MedicationCatalog, text: str) -> None:
    assert resolve_brand(catalog, text).message == "Brand not found"


def test_strength_mismatch_is_flagged(catalog: MedicationCatalog) -> None:
    r = resolve_brand(catalog, "Crocin 650")
    assert r.found and r.strength_note and "650" in r.strength_note


def test_search_covers_brands_salts_formulations_and_herbs(catalog: MedicationCatalog) -> None:
    def top(q: str) -> tuple[str, str]:
        h = search(catalog, q).hits[0]
        return h.type, h.name

    assert top("Combiflam")[0] == "brand"
    assert top("acetaminophen") == ("ingredient", "Paracetamol")
    assert top("सितोपलादि चूर्ण") == ("medication", "Sitopaladi Churna")
    assert top("Giloy") == ("ingredient", "Guduchi")
    assert search(catalog, "Turmeric").disclaimer == DISCLAIMER


# ------------------------------------------------------------------ safety badges and duplicates


def test_badges(catalog: MedicationCatalog) -> None:
    def codes(code: str) -> set[str]:
        view = catalog.get(code)
        assert view is not None
        return {b.code for b in view.badges}

    assert {"schedule-h", "nlem", "sample"} <= codes("SAMPLE-MED-B004")
    assert "metal-mineral" in codes("SAMPLE-MED-A006") and "metal-mineral" in codes("SAMPLE-MED-A007")
    assert "metal-mineral" not in codes("SAMPLE-MED-A001")
    assert not {"schedule-h", "nlem"} & codes("SAMPLE-MED-B015")  # schedule not stated by source -> no badge


def test_duplicate_salt_detection(catalog: MedicationCatalog) -> None:
    warnings = duplicate_ingredients(catalog, ["SAMPLE-MED-B001", "SAMPLE-MED-B003", "SAMPLE-MED-B017"])
    assert [(w.kind, w.ingredient, len(w.medications)) for w in warnings] == [("salt", "Paracetamol", 3)]
    assert duplicate_ingredients(catalog, ["SAMPLE-MED-B001", "SAMPLE-MED-B002"]) == []
    herbs = duplicate_ingredients(catalog, ["SAMPLE-MED-A001", "SAMPLE-MED-A007"])
    assert {w.ingredient for w in herbs} == {"Haritaki", "Bibhitaki", "Amalaki"} and all(
        w.severity == "info" for w in herbs
    )


# ------------------------------------------------------------------ FHIR


def test_fhir_medication_resources_are_valid(catalog: MedicationCatalog) -> None:
    for code, rec in catalog.by_code.items():
        med = medication_resource(rec.view)
        mk = medication_knowledge_resource(rec.view, [("namaste", "SAMPLE-AYU-0001", "Amavata")], True)
        for res in (med, mk):
            errors = [i for i in validation_issues(res) if i["severity"] == "error"]
            assert errors == [], (code, errors)
        assert "dosage" not in str(mk.get("administrationGuidelines", ""))


def test_fhir_strength_ratios(catalog: MedicationCatalog) -> None:
    b001 = medication_resource(catalog.get("SAMPLE-MED-B001"))  # type: ignore[arg-type]
    assert b001["ingredient"][0]["strength"]["numerator"] == {
        "value": 500,
        "unit": "mg",
        "system": "http://unitsofmeasure.org",
        "code": "mg",
    }
    cream = medication_resource(catalog.get("SAMPLE-MED-B011"))  # type: ignore[arg-type]
    assert cream["ingredient"][0]["strength"]["denominator"]["value"] == 100
    d3 = medication_resource(catalog.get("SAMPLE-MED-B015"))  # type: ignore[arg-type]
    assert d3["ingredient"][0]["strength"]["numerator"]["code"] == "[IU]"
    talisadi = medication_resource(catalog.get("SAMPLE-MED-A004"))  # type: ignore[arg-type]
    ratios = [i["strength"]["numerator"]["value"] for i in talisadi["ingredient"]]
    assert (
        ratios == [1, 2, 3, 4, 5, 0.5, 0.5, 32]
        and talisadi["ingredient"][0]["strength"]["denominator"]["value"] == 48
    )
    single = medication_resource(catalog.get("SAMPLE-MED-S003"))  # type: ignore[arg-type]
    assert "strength" not in single["ingredient"][0]  # source states no proportion -> none invented


def test_session_fixture_unused(fresh_db: Session) -> None:  # keeps fixture import paths honest
    assert fresh_db is not None
