"""Hit every public endpoint of a running stack; exit non-zero on any failure.

Usage: python scripts/smoke_test.py [--base-url http://localhost:8000] [--frontend-url http://localhost:5173]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable

import httpx

Check = Callable[[httpx.Response], str | None]


def expect_health(resp: httpx.Response) -> str | None:
    body = resp.json()
    if body.get("status") != "ok":
        return f"status={body.get('status')} db={body.get('database')} redis={body.get('redis')}"
    return None


def expect_versions(resp: httpx.Response) -> str | None:
    versions = resp.json().get("dataset_versions", {})
    missing = {"namaste", "icd11", "synonyms", "gold"} - set(versions)
    return f"dataset releases missing: {sorted(missing)}" if missing else None


def expect_items(resp: httpx.Response) -> str | None:
    return None if resp.json().get("items") else "empty page"


def expect_chain_ok(resp: httpx.Response) -> str | None:
    body = resp.json()
    return None if body.get("ok") and body.get("checked", 0) > 0 else f"ledger not verified: {body}"


def expect_index(resp: httpx.Response) -> str | None:
    index = resp.json().get("index", {})
    return None if index.get("loaded") else f"search index not loaded: {index.get('detail')}"


def expect_suggestions(resp: httpx.Response) -> str | None:
    items = resp.json().get("items", [])
    return None if items and items[0]["code"] == "SAMPLE-AYU-0009" else f"unexpected suggestions: {items[:2]}"


def expect_mapping(resp: httpx.Response) -> str | None:
    body = resp.json()
    top = (body.get("candidates") or [{}])[0]
    ok = body.get("status") == "suggested" and top.get("tm2", {}).get("code") == "SAMPLE-TM2-0111"
    return (
        None
        if ok and top.get("evidence") and top.get("reasoning")
        else f"unexpected mapping: {body.get('status')}"
    )


def expect_fhir(resource_type: str) -> Check:
    def check(resp: httpx.Response) -> str | None:
        if not resp.headers.get("content-type", "").startswith("application/fhir+json"):
            return f"content-type {resp.headers.get('content-type')}"
        got = resp.json().get("resourceType")
        return None if got == resource_type else f"resourceType {got} (want {resource_type})"

    return check


def expect_brand(resp: httpx.Response) -> str | None:
    body = resp.json()
    ok = body.get("brand", {}).get("found") and "not a prescription" in body.get("disclaimer", "")
    return None if ok else f"brand not resolved: {body.get('brand')}"


def expect_alerts(resp: httpx.Response) -> str | None:
    alerts = resp.json().get("alerts", [])
    ok = alerts and all(a.get("citation") for a in alerts)
    return None if ok else f"expected cited alerts, got {alerts[:1]}"


def expect_html(resp: httpx.Response) -> str | None:
    return None if '<div id="root">' in resp.text else "root element missing"


def wait_for(url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit(f"FAIL  {url} not reachable within {timeout_s:.0f}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--frontend-url", default=None)
    parser.add_argument("--wait", type=float, default=90.0, help="seconds to wait for the API")
    args = parser.parse_args()

    api = args.base_url.rstrip("/")
    wait_for(f"{api}/health", args.wait)

    # Sign in (dev default admin unless SMOKE_USER / SMOKE_PASSWORD are set).
    creds = {
        "username": os.environ.get("SMOKE_USER", "admin"),
        "password": os.environ.get("SMOKE_PASSWORD", "Admin@NamaSetu1"),
    }
    login = httpx.post(f"{api}/auth/login", json=creds, timeout=10)
    if login.status_code != 200:
        raise SystemExit(f"FAIL  login as {creds['username']}: HTTP {login.status_code}")
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    print(f"ok    POST {api}/auth/login ({creds['username']})")
    anon = httpx.post(f"{api}/map", json={"text": "Amavata"}, timeout=10)
    print(
        f"{'ok  ' if anon.status_code == 401 else 'FAIL'}  POST {api}/map without token -> {anon.status_code}"
    )
    anon_failed = anon.status_code != 401

    amlapitta = "%E0%A4%85%E0%A4%AE%E0%A5%8D%E0%A4%B2%E0%A4%AA%E0%A4%BF%E0%A4%A4%E0%A5%8D%E0%A4%A4"
    checks: list[tuple[str, str, int, Check | None]] = [
        ("GET", f"{api}/health", 200, expect_health),
        ("GET", f"{api}/health", 200, expect_versions),
        ("GET", f"{api}/health", 200, expect_index),
        ("GET", f"{api}/search/autocomplete?q={amlapitta}", 200, expect_suggestions),
        ("POST", f"{api}/map", 200, expect_mapping),
        ("GET", f"{api}/eval/ranker", 200, None),
        ("GET", f"{api}/auth/me", 200, None),
        ("GET", f"{api}/medications/search?q=Combiflam", 200, expect_brand),
        ("GET", f"{api}/medications/by-condition?system=namaste&code=SAMPLE-AYU-0009", 200, None),
        ("GET", f"{api}/fhir/Medication/SAMPLE-MED-B003", 200, expect_fhir("Medication")),
        ("GET", f"{api}/fhir/MedicationKnowledge?condition=SAMPLE-AYU-0009", 200, expect_fhir("Bundle")),
        ("GET", f"{api}/patients", 200, None),
        ("GET", f"{api}/patients/test-patient-01/medications/timeline", 200, None),
        ("GET", f"{api}/patients/test-patient-01/interactions?candidate=SAMPLE-MED-A008", 200, expect_alerts),
        ("GET", f"{api}/fhir/DetectedIssue?patient=test-patient-01", 200, expect_fhir("Bundle")),
        ("GET", f"{api}/eval/medications", 200, None),
        ("GET", f"{api}/review/queue", 200, None),
        ("GET", f"{api}/dashboard", 200, None),
        ("GET", f"{api}/eval/runs", 200, None),
        ("GET", f"{api}/export/csv?kind=mappings", 200, None),
        ("GET", f"{api}/mappings", 200, expect_items),
        ("GET", f"{api}/fhir/metadata", 200, expect_fhir("CapabilityStatement")),
        ("GET", f"{api}/fhir/CodeSystem/namaste", 200, expect_fhir("CodeSystem")),
        (
            "GET",
            f"{api}/fhir/ValueSet/$expand?url=namaste-ayurveda&filter={amlapitta}",
            200,
            expect_fhir("ValueSet"),
        ),
        ("GET", f"{api}/fhir/ConceptMap/namaste-to-tm2", 200, expect_fhir("ConceptMap")),
        ("GET", f"{api}/fhir/AuditEvent?_count=1", 200, expect_fhir("Bundle")),
        ("GET", f"{api}/fhir/CodeSystem/nope", 404, expect_fhir("OperationOutcome")),
        ("GET", f"{api}/admin/releases", 200, expect_items),
        ("GET", f"{api}/admin/releases/1", 200, None),
        ("GET", f"{api}/admin/releases/1/diff", 200, None),
        ("GET", f"{api}/audit", 200, expect_items),
        ("GET", f"{api}/audit/verify", 200, expect_chain_ok),
        ("GET", f"{api}/openapi.json", 200, None),
        ("GET", f"{api}/docs", 200, None),
        ("GET", f"{api}/__missing__", 404, None),
    ]
    if args.frontend_url:
        fe = args.frontend_url.rstrip("/")
        checks += [
            ("GET", f"{fe}/", 200, expect_html),
            ("GET", f"{fe}/api/health", 200, expect_health),
        ]

    failures = int(anon_failed)
    for method, url, status, check in checks:
        try:
            body = {"text": "आमवात"} if method == "POST" else None
            resp = httpx.request(method, url, json=body, headers=auth, timeout=10)
            problem = None if resp.status_code == status else f"HTTP {resp.status_code} (want {status})"
            if problem is None and check is not None:
                problem = check(resp)
        except httpx.HTTPError as exc:
            problem = type(exc).__name__
        print(f"{'FAIL' if problem else 'ok  '}  {method} {url}" + (f"  -> {problem}" if problem else ""))
        failures += bool(problem)

    print(f"\n{len(checks) - failures}/{len(checks)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
