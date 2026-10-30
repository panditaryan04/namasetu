"""FHIR terminology service: resources, operations, safety rules, versioning, audit and provenance."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import BACKEND_ROOT
from app.db.models import SYSTEM_BIO, SYSTEM_NAMASTE, SYSTEM_TM2, ConceptLink, GoldMapping, Mapping
from app.db.session import get_sessionmaker
from app.fhir.validator import R4_EQUIVALENCE, validation_issues

BASE = "http://localhost:8000/fhir"
NAMASTE = f"{BASE}/CodeSystem/namaste"
ICD = f"{BASE}/CodeSystem/icd11-mms-sample"
EXAMPLES = BACKEND_ROOT.parent / "docs" / "fhir-examples"


def errors(resource: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in validation_issues(resource) if i["severity"] in {"error", "fatal"}]


def p(params: dict[str, Any], name: str) -> Any:
    for prm in params["parameter"]:
        if prm["name"] == name:
            return next((v for k, v in prm.items() if k.startswith("value")), prm.get("part"))
    return None


@pytest.fixture(scope="module")
def gold() -> dict[str, list[GoldMapping]]:
    with get_sessionmaker()() as s:
        rows = s.scalars(select(GoldMapping)).all()
    return {"train": [g for g in rows if g.split == "train"], "test": [g for g in rows if g.split == "test"]}


@pytest.fixture(scope="module")
def train_with_bio(gold: dict[str, list[GoldMapping]]) -> GoldMapping:
    with get_sessionmaker()() as s:
        linked = {
            lk.source_code for lk in s.scalars(select(ConceptLink).where(ConceptLink.status == "approved"))
        }
    return next(g for g in gold["train"] if g.tm2_code in linked and g.relationship == "equivalent")


# ------------------------------------------------------------------ resources


def test_every_fhir_response_is_fhir_json(client: TestClient) -> None:
    for path in ("/fhir/metadata", "/fhir/CodeSystem/namaste", "/fhir/ValueSet", "/fhir/ConceptMap"):
        resp = client.get(path)
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/fhir+json")
        assert errors(resp.json()) == []


def test_capability_statement(client: TestClient) -> None:
    cs = client.get("/fhir/metadata").json()
    assert cs["fhirVersion"] == "4.0.1" and cs["kind"] == "instance"
    ops = {o["name"] for r in cs["rest"][0]["resource"] for o in r.get("operation", [])}
    assert {"lookup", "validate-code", "expand", "translate", "build"} <= ops


def test_namaste_codesystem(client: TestClient) -> None:
    cs = client.get("/fhir/CodeSystem/namaste").json()
    assert cs["url"] == NAMASTE and cs["content"] == "complete" and cs["count"] == len(cs["concept"]) == 64
    assert cs["experimental"] is True and "SAMPLE" in cs["description"]
    amavata = next(c for c in cs["concept"] if c["code"] == "SAMPLE-AYU-0001")
    langs = {d["language"]: d["value"] for d in amavata["designation"]}
    assert langs["sa-Deva"] == "आमवात" and langs["sa-Latn"] == "Āmavāta" and "hi-Latn" in langs
    assert {"code": "parent", "valueCode": "SAMPLE-AYU-G01"} in amavata["property"]


def test_icd11_sample_codesystem_never_claims_who_uri(client: TestClient) -> None:
    cs = client.get("/fhir/CodeSystem/icd11-mms-sample").json()
    assert cs["url"] == ICD and cs["content"] == "complete" and "id.who.int" not in cs["url"]
    assert {c["code"] for c in cs["concept"]} >= {"SAMPLE-TM2-0111", "SAMPLE-BIO-0001"}


def test_valuesets_per_discipline(client: TestClient) -> None:
    bundle = client.get("/fhir/ValueSet").json()
    status = {e["resource"]["id"]: e["resource"]["status"] for e in bundle["entry"]}
    assert status == {
        "namaste-ayurveda": "active",
        "namaste-siddha": "draft",
        "namaste-unani": "draft",
        "icd11-tm2": "active",
    }
    assert client.get("/fhir/ValueSet/nope").status_code == 404


# ------------------------------------------------------------------ operations


def test_lookup_get_and_post(client: TestClient) -> None:
    r = client.get("/fhir/CodeSystem/$lookup", params={"system": NAMASTE, "code": "SAMPLE-AYU-0009"}).json()
    assert p(r, "display") == "Amlapitta" and p(r, "version") == "SAMPLE-1"
    body = {
        "resourceType": "Parameters",
        "parameter": [{"name": "coding", "valueCoding": {"system": ICD, "code": "SAMPLE-TM2-0111"}}],
    }
    r2 = client.post("/fhir/CodeSystem/$lookup", json=body).json()
    assert p(r2, "display") == "Ama-vata joint disorder"
    missing = client.get("/fhir/CodeSystem/$lookup", params={"system": NAMASTE, "code": "SAMPLE-AYU-9999"})
    assert missing.status_code == 404 and missing.json()["resourceType"] == "OperationOutcome"
    assert client.get("/fhir/CodeSystem/$lookup").status_code == 400


def test_validate_code(client: TestClient) -> None:
    ok = client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"url": NAMASTE, "code": "SAMPLE-AYU-0001", "display": "आमवात"},
    ).json()
    assert p(ok, "result") is True
    bad = client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"url": NAMASTE, "code": "SAMPLE-AYU-0001", "display": "Gout"},
    ).json()
    assert p(bad, "result") is False and "expected 'Amavata'" in p(bad, "message")
    unknown = client.get("/fhir/CodeSystem/$validate-code", params={"url": NAMASTE, "code": "NOPE"}).json()
    assert p(unknown, "result") is False
    # ValueSet membership: grouping categories are not selectable members.
    vs = f"{BASE}/ValueSet/namaste-ayurveda"
    assert (
        p(
            client.get("/fhir/ValueSet/$validate-code", params={"url": vs, "code": "SAMPLE-AYU-0001"}).json(),
            "result",
        )
        is True
    )
    assert (
        p(
            client.get("/fhir/ValueSet/$validate-code", params={"url": vs, "code": "SAMPLE-AYU-G01"}).json(),
            "result",
        )
        is False
    )


def test_expand_with_multiscript_filter_and_paging(client: TestClient) -> None:
    exp = client.get(
        "/fhir/ValueSet/$expand", params={"url": f"{BASE}/ValueSet/namaste-ayurveda", "filter": "अम्लपित्त"}
    ).json()
    assert exp["expansion"]["contains"][0]["code"] == "SAMPLE-AYU-0009"
    allv = client.get("/fhir/ValueSet/namaste-ayurveda/$expand", params={"count": 10, "offset": 50}).json()[
        "expansion"
    ]
    assert allv["total"] == 53 and len(allv["contains"]) == 3
    tm2 = client.get("/fhir/ValueSet/$expand", params={"url": "icd11-tm2", "filter": "fever"}).json()[
        "expansion"
    ]
    assert [c["code"] for c in tm2["contains"]] == ["SAMPLE-TM2-0201"]


def test_translate_uses_only_approved_mappings(
    client: TestClient, gold: dict[str, list[GoldMapping]]
) -> None:
    g = gold["train"][0]
    r = client.get("/fhir/ConceptMap/$translate", params={"system": NAMASTE, "code": g.namaste_code}).json()
    assert p(r, "result") is True
    match = {part["name"]: part for part in p(r, "match")}
    assert match["concept"]["valueCoding"]["code"] == g.tm2_code
    assert match["equivalence"]["valueCode"] == g.relationship
    held_out = client.get(
        "/fhir/ConceptMap/$translate", params={"system": NAMASTE, "code": gold["test"][0].namaste_code}
    ).json()
    assert p(held_out, "result") is False  # test split is never in the approved store
    bio = client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": ICD},
                {"name": "code", "valueCode": "SAMPLE-TM2-0111"},
            ],
        },
    ).json()
    assert {pt["name"]: pt for pt in p(bio, "match")}["equivalence"]["valueCode"] == "relatedto"


# ------------------------------------------------------------------ ConceptMap content + versioning


def test_conceptmap_built_only_from_approved_store(
    client: TestClient, gold: dict[str, list[GoldMapping]]
) -> None:
    cm = client.get("/fhir/ConceptMap/namaste-to-tm2").json()
    elements = cm["group"][0]["element"]
    assert {e["code"] for e in elements} == {g.namaste_code for g in gold["train"]}
    assert not {e["code"] for e in elements} & {g.namaste_code for g in gold["test"]}
    targets = [t for e in elements for t in e["target"]]
    assert all(t["equivalence"] in R4_EQUIVALENCE for t in targets)
    assert cm["publisher"] == "Team Byte Strong" and cm["group"][0]["source"] == NAMASTE


def test_conceptmap_versioning_provenance_and_ledger(
    client: TestClient, gold: dict[str, list[GoldMapping]]
) -> None:
    v1 = client.get("/fhir/ConceptMap/namaste-to-tm2").json()
    assert (
        client.get("/fhir/ConceptMap/namaste-to-tm2").json()["version"] == v1["version"]
    )  # stable when unchanged
    g = gold["test"][0]
    with get_sessionmaker()() as s:
        s.add(
            Mapping(
                source_system=SYSTEM_NAMASTE,
                source_code=g.namaste_code,
                target_system=SYSTEM_TM2,
                target_code=g.tm2_code,
                relationship=g.relationship,
                reviewer="dr-test",
            )
        )
        s.commit()
    v2 = client.get("/fhir/ConceptMap/namaste-to-tm2").json()
    assert int(v2["version"]) == int(v1["version"]) + 1
    assert g.namaste_code in {e["code"] for e in v2["group"][0]["element"]}
    hist = client.get("/fhir/ConceptMap/namaste-to-tm2/_history").json()
    assert hist["type"] == "history" and [e["resource"]["version"] for e in hist["entry"]][:2] == [
        v2["version"],
        v1["version"],
    ]
    prov = client.get(
        "/fhir/Provenance", params={"target": f"ConceptMap/namaste-to-tm2/_history/{v2['version']}"}
    ).json()
    assert prov["total"] == 1
    published = client.get("/audit", params={"action": "publish"}).json()["items"]
    assert any(e["entity_id"] == f"namaste-to-tm2|{v2['version']}" for e in published)
    # Clean up so other tests see the original approved store.
    with get_sessionmaker()() as s:
        s.query(Mapping).filter(Mapping.reviewer == "dr-test").delete()
        s.commit()


# ------------------------------------------------------------------ Condition $build


def _build(client: TestClient, **kw: Any) -> Any:
    body = {
        "patient": {"abha_number": "91-TEST-0000-0001", "display": "Synthetic"},
        "encounter_id": "enc-1",
        **kw,
    }
    return client.post("/fhir/Condition/$build", json=body)


def test_condition_tricoded_from_approved_mapping(client: TestClient, train_with_bio: GoldMapping) -> None:
    r = _build(client, namaste_code=train_with_bio.namaste_code, purpose_of_use="TREAT")
    assert r.status_code == 201, r.json()
    cond = r.json()
    assert errors(cond) == []
    systems = [c["system"] for c in cond["code"]["coding"]]
    assert systems == [NAMASTE, ICD, ICD] and cond["code"]["coding"][1]["code"] == train_with_bio.tm2_code
    assert (
        "profile" not in cond["meta"]
    )  # NRCeS profile closes code.coding to ICD-10/SNOMED (see standards.md)
    assert cond["subject"]["identifier"]["value"] == "91-TEST-0000-0001"
    assert cond["meta"]["tag"][0]["code"] == "sample-data"
    prov = client.get("/fhir/Provenance", params={"target": f"Condition/{cond['id']}"}).json()
    assert prov["total"] == 1
    audits = client.get("/fhir/AuditEvent", params={"action": "fhir_export"}).json()
    top = audits["entry"][0]["resource"]
    assert top["entity"][0]["what"]["display"] == f"Condition/{cond['id']}"
    assert top["purposeOfEvent"][0]["coding"][0]["code"] == "TREAT"

    no_bio = _build(client, namaste_code=train_with_bio.namaste_code, include_biomedicine=False).json()
    assert len(no_bio["code"]["coding"]) == 2


def test_condition_refuses_unapproved_tm2(client: TestClient, gold: dict[str, list[GoldMapping]]) -> None:
    g = gold["test"][0]  # held out: no approved mapping
    r = _build(client, namaste_code=g.namaste_code)
    assert r.status_code == 422 and r.json()["resourceType"] == "OperationOutcome"
    not_suggested = _build(
        client, namaste_code=g.namaste_code, tm2_code="SAMPLE-TM2-0223", confirm_suggestion=True
    )
    assert (
        not_suggested.status_code == 422
        and "only approved or HIGH-band" in not_suggested.json()["issue"][0]["diagnostics"]
    )


def test_condition_high_band_requires_explicit_confirmation(
    client: TestClient, gold: dict[str, list[GoldMapping]]
) -> None:
    g = gold["test"][0]
    top = client.post("/map", json={"code": g.namaste_code}).json()["candidates"][0]
    assert top["band"] == "high"
    assert _build(client, namaste_code=g.namaste_code, tm2_code=top["tm2"]["code"]).status_code == 422
    ok = _build(client, namaste_code=g.namaste_code, tm2_code=top["tm2"]["code"], confirm_suggestion=True)
    assert ok.status_code == 201
    assert "clinician-confirmed high-band suggestion" in ok.json()["note"][-1]["text"]


def test_condition_never_uses_pending_biomedical_links(
    client: TestClient, gold: dict[str, list[GoldMapping]]
) -> None:
    g = next(x for x in gold["train"] if x.tm2_code == "SAMPLE-TM2-0141" or x.relationship == "equivalent")
    with get_sessionmaker()() as s:
        s.add(
            ConceptLink(
                source_system=SYSTEM_TM2,
                source_code=g.tm2_code,
                target_system=SYSTEM_BIO,
                target_code="SAMPLE-BIO-0013",
                origin="clinician",
                status="pending",
            )
        )
        s.commit()
    cond = _build(client, namaste_code=g.namaste_code).json()
    assert "SAMPLE-BIO-0013" not in {c["code"] for c in cond["code"]["coding"]}
    with get_sessionmaker()() as s:
        s.query(ConceptLink).filter(ConceptLink.status == "pending").delete()
        s.commit()


def test_condition_request_validation(client: TestClient) -> None:
    r = client.post("/fhir/Condition/$build", json={"namaste_code": "SAMPLE-AYU-0001"})
    assert r.status_code == 422 and r.json()["resourceType"] == "OperationOutcome"
    assert _build(client, namaste_code="SAMPLE-AYU-G01").status_code == 404  # categories are not diagnoses


# ------------------------------------------------------------------ Bundle ingest


def _bundle(*codings: dict[str, str]) -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "resource": {
                    "resourceType": "Condition",
                    "subject": {"reference": "Patient/p1"},
                    "code": {"coding": list(codings)},
                }
            }
        ],
    }


def outcome_and_bundle(resp: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    params = resp.json()["parameter"]
    out = next(x["resource"] for x in params if x["name"] == "outcome")
    ret = next((x["resource"] for x in params if x["name"] == "return"), None)
    return out, ret


def test_bundle_ingest_adds_tm2_from_approved_map(
    client: TestClient, gold: dict[str, list[GoldMapping]]
) -> None:
    g = gold["train"][0]
    resp = client.post("/fhir/Bundle", json=_bundle({"system": NAMASTE, "code": g.namaste_code}))
    assert resp.status_code == 200 and errors(resp.json()) == []
    outcome, bundle = outcome_and_bundle(resp)
    assert bundle is not None
    codes = [c["code"] for c in bundle["entry"][0]["resource"]["code"]["coding"]]
    assert codes == [g.namaste_code, g.tm2_code]
    assert any("added TM2" in i["diagnostics"] for i in outcome["issue"])


def test_bundle_ingest_reports_issues(client: TestClient, gold: dict[str, list[GoldMapping]]) -> None:
    resp = client.post(
        "/fhir/Bundle",
        params={"purpose_of_use": "PUBHLTH"},
        json=_bundle(
            {"system": NAMASTE, "code": gold["test"][0].namaste_code},
            {"system": NAMASTE, "code": "SAMPLE-AYU-0001", "display": "Wrong name"},
            {"system": ICD, "code": "SAMPLE-TM2-NOPE"},
            {"system": "http://snomed.info/sct", "code": "69896004"},
        ),
    )
    outcome, _ = outcome_and_bundle(resp)
    sev = [(i["severity"], i["code"]) for i in outcome["issue"]]
    assert (
        ("error", "code-invalid") in sev
        and ("warning", "value") in sev
        and ("information", "informational") in sev
    )
    events = client.get("/fhir/AuditEvent", params={"action": "bundle_ingest"}).json()["entry"]
    assert events[0]["resource"]["purposeOfEvent"][0]["coding"][0]["code"] == "PUBHLTH"


def test_bundle_ingest_rejects_bad_input(client: TestClient) -> None:
    assert client.post("/fhir/Bundle", json={"resourceType": "Patient"}).status_code == 400
    assert (
        client.post("/fhir/Bundle", json={"resourceType": "Bundle", "type": "searchset"}).status_code == 400
    )
    bad = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": {"resourceType": "Condition"}}],
    }
    outcome, ret = outcome_and_bundle(client.post("/fhir/Bundle", json=bad))
    assert ret is None and outcome["issue"][0]["severity"] == "error"


# ------------------------------------------------------------------ $validate, AuditEvent, Provenance


def test_validate_operation(client: TestClient) -> None:
    bad = client.post(
        "/fhir/$validate",
        json={"resourceType": "Condition", "clinicalStatus": {"coding": [{"code": "nope"}]}},
    ).json()
    diags = " ".join(i["diagnostics"] for i in bad["issue"])
    assert "subject" in diags and "'nope' is not a valid code" in diags
    good = client.post(
        "/fhir/$validate", json={"resourceType": "Condition", "subject": {"reference": "Patient/1"}}
    ).json()
    assert good["issue"][0]["severity"] == "information"


def test_audit_events_expose_hash_chain(client: TestClient) -> None:
    bundle = client.get("/fhir/AuditEvent", params={"_count": 2}).json()
    assert bundle["type"] == "searchset" and bundle["total"] >= 4 and len(bundle["entry"]) == 2
    assert any(link["relation"] == "next" for link in bundle["link"])
    ev = bundle["entry"][0]["resource"]
    details = {d["type"]: d["valueString"] for d in ev["entity"][0]["detail"]}
    assert len(details["ledger-hash"]) == 64
    assert client.get(f"/fhir/AuditEvent/{ev['id']}").json()["id"] == ev["id"]
    assert client.get("/fhir/AuditEvent/999999").status_code == 404


def test_mapping_provenance_from_crosswalk_import(
    client: TestClient, gold: dict[str, list[GoldMapping]]
) -> None:
    prov = client.get("/fhir/Provenance", params={"target": "Mapping/", "_count": 200}).json()
    assert prov["total"] >= len(gold["train"])
    res = prov["entry"][0]["resource"]
    assert res["agent"][0]["who"]["display"] == "official-crosswalk"
    assert client.get(f"/fhir/Provenance/{res['id']}").json()["id"] == res["id"]


# ------------------------------------------------------------------ committed examples


def example_files() -> Iterator[Any]:
    yield from sorted(EXAMPLES.glob("*.json"))


@pytest.mark.parametrize("path", list(example_files()), ids=lambda p: p.name)
def test_committed_examples_are_valid(path: Any) -> None:
    assert errors(json.loads(path.read_text(encoding="utf-8"))) == []


def test_all_required_example_kinds_exist() -> None:
    names = {p.stem for p in example_files()}
    for needed in (
        "codesystem-namaste",
        "conceptmap-namaste-to-tm2",
        "parameters-translate",
        "condition-tricoded",
        "bundle-ingest-input",
        "auditevent",
        "provenance-condition",
    ):
        assert needed in names
