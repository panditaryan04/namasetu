"""Write real API responses to docs/fhir-examples/ (validated in CI by the HL7 Validator, FHIR 4.0.1).

Usage: python scripts/export_fhir_examples.py   (needs a migrated + seeded database)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.core.config import BACKEND_ROOT
from app.core.logging import configure_logging
from app.core.security import create_token
from app.db.session import get_sessionmaker
from app.db.users import ensure_demo_users
from app.fhir.common import ABHA_SYSTEM
from app.fhir.validator import validation_issues
from app.main import create_app

OUT = BACKEND_ROOT.parent / "docs" / "fhir-examples"


def main() -> int:
    configure_logging("WARNING")
    OUT.mkdir(parents=True, exist_ok=True)
    examples: dict[str, Any] = {}
    with get_sessionmaker()() as session:
        ensure_demo_users(session)
        session.commit()
    token = create_token("clinician", "clinician", "access")
    with TestClient(create_app(), headers={"Authorization": f"Bearer {token}"}) as c:
        base = c.get("/fhir/CodeSystem/namaste").json()["url"].rsplit("/CodeSystem/", 1)[0]
        namaste = f"{base}/CodeSystem/namaste"

        def get(path: str, **params: Any) -> Any:
            r = c.get(path, params=params)
            r.raise_for_status()
            return r.json()

        examples["capabilitystatement"] = get("/fhir/metadata")
        examples["codesystem-namaste"] = get("/fhir/CodeSystem/namaste")
        examples["codesystem-icd11-mms-sample"] = get("/fhir/CodeSystem/icd11-mms-sample")
        examples["valueset-namaste-ayurveda"] = get("/fhir/ValueSet/namaste-ayurveda")
        examples["valueset-expand-devanagari-filter"] = get(
            "/fhir/ValueSet/$expand", url="namaste-ayurveda", filter="आमवात", count=5
        )
        examples["conceptmap-namaste-to-tm2"] = get("/fhir/ConceptMap/namaste-to-tm2")
        examples["conceptmap-tm2-to-biomedicine"] = get("/fhir/ConceptMap/tm2-to-biomedicine")
        examples["parameters-lookup"] = get(
            "/fhir/CodeSystem/$lookup", system=namaste, code="SAMPLE-AYU-0001"
        )
        examples["parameters-validate-code"] = get(
            "/fhir/CodeSystem/$validate-code", url=namaste, code="SAMPLE-AYU-0001", display="Amavata"
        )
        examples["parameters-translate"] = get(
            "/fhir/ConceptMap/$translate", system=namaste, code="SAMPLE-AYU-0001"
        )

        built = c.post(
            "/fhir/Condition/$build",
            json={
                "namaste_code": "SAMPLE-AYU-0001",
                "patient": {"abha_number": "91-TEST-0000-0001", "display": "Test Patient (SYNTHETIC)"},
                "encounter_id": "enc-test-1",
                "recorded_date": "2026-10-30",
            },
        )
        built.raise_for_status()
        examples["condition-tricoded"] = built.json()

        bundle = {
            "resourceType": "Bundle",
            "id": "example-encounter-problem-list",
            "type": "collection",
            "entry": [
                {
                    "fullUrl": f"{base}/Patient/test-patient-1",
                    "resource": {
                        "resourceType": "Patient",
                        "id": "test-patient-1",
                        "identifier": [{"system": ABHA_SYSTEM, "value": "91-TEST-0000-0001"}],
                        "name": [{"text": "Test Patient (SYNTHETIC)"}],
                    },
                },
                {
                    "fullUrl": f"{base}/Condition/problem-1",
                    "resource": {
                        "resourceType": "Condition",
                        "id": "problem-1",
                        "code": {
                            "coding": [{"system": namaste, "code": "SAMPLE-AYU-0009", "display": "Amlapitta"}]
                        },
                        "subject": {"reference": "Patient/test-patient-1"},
                    },
                },
            ],
        }
        examples["bundle-ingest-input"] = bundle
        ingested = c.post("/fhir/Bundle", json=bundle)
        ingested.raise_for_status()
        examples["parameters-bundle-ingest-result"] = ingested.json()

        examples["medication-ayush-formulation"] = get("/fhir/Medication/SAMPLE-MED-A004")
        examples["medication-biomedical-combination"] = get("/fhir/Medication/SAMPLE-MED-B003")
        examples["medicationknowledge-paracetamol"] = get("/fhir/MedicationKnowledge/SAMPLE-MED-B001")
        examples["bundle-medicationknowledge-by-condition"] = get(
            "/fhir/MedicationKnowledge", condition=f"{namaste}|SAMPLE-AYU-0009"
        )
        get("/patients/test-patient-01/interactions", candidate="SAMPLE-MED-A008", as_of="2026-10-30")
        examples["detectedissue-herb-drug"] = get("/fhir/DetectedIssue", patient="test-patient-01")["entry"][
            0
        ]["resource"]
        examples["bundle-medicationstatement-timeline"] = get(
            "/fhir/Patient/test-patient-10/MedicationStatement"
        )
        examples["auditevent"] = get("/fhir/AuditEvent", _count=1)["entry"][0]["resource"]
        cond_id = examples["condition-tricoded"]["id"]
        examples["provenance-condition"] = get("/fhir/Provenance", target=f"Condition/{cond_id}")["entry"][0][
            "resource"
        ]
        examples["provenance-conceptmap"] = get("/fhir/Provenance", target="ConceptMap/namaste-to-tm2")[
            "entry"
        ][0]["resource"]
        examples["operationoutcome-unknown-code"] = c.get(
            "/fhir/CodeSystem/$lookup", params={"system": namaste, "code": "SAMPLE-AYU-9999"}
        ).json()

    failures = 0
    for name, res in examples.items():
        errors = [i for i in validation_issues(res) if i["severity"] in {"error", "fatal"}]
        failures += bool(errors)
        (OUT / f"{name}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{'FAIL' if errors else 'ok  '}  {name}.json" + (f"  {errors[:2]}" if errors else ""))
    print(f"\n{len(examples) - failures}/{len(examples)} examples valid (in-process) -> {OUT}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
