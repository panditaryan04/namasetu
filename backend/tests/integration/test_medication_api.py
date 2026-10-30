"""Medication API: reference-only output, approved-only links, review tab, FHIR, no dose regimens anywhere."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.db.models import Medication, MedicationLink
from app.db.session import get_sessionmaker
from app.fhir.validator import validation_issues
from app.medication.catalog import DISCLAIMER

DOSE_FIELD = re.compile(
    r"^(dose|doses|dosing|dosage|frequency|regimen|duration|posology|sig)$|_dose$|^dose_|regimen|frequency"
)


def test_no_dose_regimen_field_in_any_response_schema(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    offenders = [
        f"{name}.{prop}"
        for name, schema in schemas.items()
        for prop in schema.get("properties", {})
        if DOSE_FIELD.search(prop.lower())
    ]
    assert offenders == []
    assert "dosage_form" in schemas["MedicationView"]["properties"]  # the form is allowed; a regimen is not


def test_by_condition_shows_only_approved_links(client: TestClient) -> None:
    r = client.get(
        "/medications/by-condition", params={"system": "namaste", "code": "SAMPLE-AYU-0001"}
    ).json()
    names = [x["medication"]["name"] for x in r["ayush"]]
    assert names == ["Trikatu Churna"]  # Guggulu link is pending -> hidden
    assert r["ayush"][0]["link"]["status_label"] == "illustrative sample"
    assert r["disclaimer"] == DISCLAIMER and r["condition"]["display"] == "Amavata"
    comp = r["ayush"][0]["medication"]["composition"]
    first = comp[0]
    assert (first["canonical_name"], first["botanical_name"], first["part_used"], first["proportion"]) == (
        "Shunthi",
        "Zingiber officinale",
        "Rhizome",
        "1 part",
    )
    bio = client.get(
        "/medications/by-condition", params={"system": "icd11-bio", "code": "SAMPLE-BIO-0001"}
    ).json()
    assert {x["medication"]["code"] for x in bio["biomedical"]} == {"SAMPLE-MED-B002", "SAMPLE-MED-B004"}
    assert (
        client.get("/medications/by-condition", params={"system": "namaste", "code": "NOPE"}).status_code
        == 404
    )


def test_medication_detail_salt_detail_and_check(client: TestClient) -> None:
    combi = client.get("/medications/SAMPLE-MED-B003").json()
    assert combi["brands"] == ["Combiflam"] and combi["source"]["title"].startswith("CDSCO")
    assert {b["code"] for b in combi["badges"]} >= {"schedule-h", "sample"}
    para_id = next(c["ingredient_id"] for c in combi["composition"] if c["canonical_name"] == "Paracetamol")
    salt = client.get(f"/salts/{para_id}").json()
    assert {m["code"] for m in salt["medications"]} == {
        "SAMPLE-MED-B001",
        "SAMPLE-MED-B003",
        "SAMPLE-MED-B017",
    }
    assert {"SAMPLE-BIO-0002", "SAMPLE-BIO-0012"} <= {c["code"] for c in salt["conditions"]}
    assert {"term": "Acetaminophen", "kind": "synonyms"} in salt["synonyms"]
    check = client.post(
        "/medications/check", json={"medication_codes": ["SAMPLE-MED-B001", "SAMPLE-MED-B003", "NOPE"]}
    ).json()
    assert check["duplicates"][0]["ingredient"] == "Paracetamol" and check["unknown_codes"] == ["NOPE"]
    assert "loaded" in check["interaction_check"]  # always names the dataset it checked against
    assert (
        client.get("/medications/NOPE").status_code == 404 and client.get("/salts/999999").status_code == 404
    )


def test_link_review_flow(as_role: Callable[[str], TestClient]) -> None:
    viewer, clinician = as_role("viewer"), as_role("clinician")
    pending = clinician.get("/medications/links").json()
    guggulu = next(x for x in pending if x["condition"]["code"] == "SAMPLE-AYU-0001")
    assert guggulu["link"]["status_label"] == "pending review"
    lid = guggulu["link"]["id"]
    assert viewer.post(f"/medications/links/{lid}/decision", json={"action": "approve"}).status_code == 403
    assert clinician.post(f"/medications/links/{lid}/decision", json={"action": "reject"}).status_code == 422
    try:
        ok = clinician.post(
            f"/medications/links/{lid}/decision", json={"action": "approve", "comment": "per STG"}
        )
        assert (
            ok.status_code == 200
            and ok.json()["status_label"] == "approved"
            and ok.json()["reviewer"] == "clinician"
        )
        shown = clinician.get(
            "/medications/by-condition", params={"system": "namaste", "code": "SAMPLE-AYU-0001"}
        ).json()
        assert "Guggulu" in [x["medication"]["name"] for x in shown["ayush"]]
        assert (
            clinician.post(f"/medications/links/{lid}/decision", json={"action": "approve"}).status_code
            == 409
        )
        prov = clinician.get("/fhir/Provenance", params={"target": f"MedicationLink/{lid}"}).json()
        assert prov["total"] >= 1
    finally:
        with get_sessionmaker()() as s:
            s.execute(
                update(MedicationLink)
                .where(MedicationLink.id == lid)
                .values(status="pending", reviewer=None, decided_at=None)
            )
            s.commit()


def test_fhir_medication_endpoints(client: TestClient) -> None:
    med = client.get("/fhir/Medication/SAMPLE-MED-A006").json()
    assert med["resourceType"] == "Medication" and len(med["ingredient"]) == 6
    assert [i for i in validation_issues(med) if i["severity"] == "error"] == []
    mk = client.get("/fhir/MedicationKnowledge", params={"condition": "SAMPLE-AYU-0009"}).json()
    assert {e["resource"]["id"] for e in mk["entry"]} == {
        "SAMPLE-MED-A005",
        "SAMPLE-MED-A006",
        "SAMPLE-MED-S006",
    }
    ns = "http://localhost:8000/fhir/CodeSystem/namaste"
    assert (
        client.get("/fhir/MedicationKnowledge", params={"condition": f"{ns}|SAMPLE-AYU-0009"}).json()["total"]
        == 3
    )
    one = client.get("/fhir/MedicationKnowledge/SAMPLE-MED-B001").json()
    assert one["synonym"] == ["Calpol 500", "Crocin"]
    indications = {
        g["indicationCodeableConcept"]["coding"][0]["code"] for g in one["administrationGuidelines"]
    }
    assert indications == {"SAMPLE-BIO-0002", "SAMPLE-BIO-0012"}
    assert all("dosage" not in g for g in one["administrationGuidelines"])
    assert client.get("/fhir/Medication/NOPE").json()["resourceType"] == "OperationOutcome"


def test_every_medication_has_a_citation() -> None:
    with get_sessionmaker()() as s:
        meds = s.scalars(select(Medication)).all()
        links = s.scalars(select(MedicationLink)).all()
    assert meds and all(m.source_title for m in meds) and all(lk.source_title for lk in links)


def test_admin_replaces_brand_table(client: TestClient) -> None:
    current = client.get("/admin/releases", params={"source": "medications"}).json()["items"][0]
    csv_text = (
        "brand,medication_code,source_title,source_edition,source_page\n"
        "Crocin,SAMPLE-MED-B001,Brand reference (SAMPLE),SAMPLE,verify\n"
        "Newbrand 500,SAMPLE-MED-B001,Brand reference (SAMPLE),SAMPLE,verify\n"
        "Ghost,SAMPLE-MED-NOPE,Brand reference (SAMPLE),SAMPLE,verify\n"
    )
    r = client.post(
        "/admin/ingest/brands",
        files={"file": ("brands.csv", csv_text.encode(), "text/csv")},
        data={"version": "SAMPLE-2"},
    )
    assert r.status_code == 202
    result: dict[str, Any] = client.get(f"/admin/tasks/{r.json()['task_id']}").json()["result"]
    assert result["status"] == "ingested" and result["diff"]["rejected_rows"] == 1
    assert client.get("/medications/search", params={"q": "Newbrand"}).json()["brand"]["found"] is True
    assert client.get("/medications/search", params={"q": "Combiflam"}).json()["brand"]["found"] is False
    # Restore the sample brand table.
    from app.core.config import SAMPLE_DIR

    original = (SAMPLE_DIR / "brand_to_salt_sample.csv").read_bytes()
    client.post(
        "/admin/ingest/brands", files={"file": ("b.csv", original, "text/csv")}, data={"version": "SAMPLE-3"}
    )
    assert client.get("/medications/search", params={"q": "Combiflam"}).json()["brand"]["found"] is True
    assert current["tag"] == "medications@SAMPLE-1"
