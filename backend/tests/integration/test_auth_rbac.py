"""Authentication (JWT + ABHA mock), rate limiting and the role -> permission matrix."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import DEV_DEMO_PASSWORDS, get_settings
from app.core.rbac import POLICY
from app.core.security import create_token, decode_token, hash_password, verify_password

PASSWORD = DEV_DEMO_PASSWORDS


def test_password_hashing() -> None:
    h = hash_password("s3cret!")
    assert h.startswith("$2b$12$") and verify_password("s3cret!", h) and not verify_password("nope", h)
    assert not verify_password("x", "not-a-hash")


def test_login_refresh_and_me(anon: TestClient) -> None:
    r = anon.post("/auth/login", json={"username": "clinician", "password": PASSWORD["clinician"]})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["user"]["role"] == "clinician"
    assert "review" in body["user"]["permissions"] and "admin" not in body["user"]["permissions"]
    me = anon.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert me["username"] == "clinician" and me["auth_method"] == "password"
    again = anon.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert again.status_code == 200 and again.json()["access_token"] != body["access_token"]
    # A refresh token is not an access token, and vice versa.
    assert (
        anon.get("/auth/me", headers={"Authorization": f"Bearer {body['refresh_token']}"}).status_code == 401
    )
    assert anon.post("/auth/refresh", json={"refresh_token": body["access_token"]}).status_code == 401


def test_login_failures_are_generic(anon: TestClient) -> None:
    for creds in ({"username": "clinician", "password": "wrong"}, {"username": "ghost", "password": "x"}):
        r = anon.post("/auth/login", json=creds)
        assert r.status_code == 401 and r.json()["error"]["message"] == "invalid username or password"


def test_expired_and_tampered_tokens(anon: TestClient) -> None:
    s = get_settings()
    past = datetime.now(UTC) - timedelta(hours=1)
    expired = jwt.encode(
        {
            "iss": "namasetu",
            "sub": "admin",
            "role": "admin",
            "typ": "access",
            "iat": past,
            "exp": past + timedelta(minutes=1),
        },
        s.jwt_secret,
        algorithm="HS256",
    )
    r = anon.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and "expired" in r.json()["error"]["message"]
    assert r.headers["WWW-Authenticate"] == "Bearer"
    forged = jwt.encode(
        {
            "iss": "namasetu",
            "sub": "admin",
            "role": "admin",
            "typ": "access",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        "attacker-secret",
        algorithm="HS256",
    )
    assert anon.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    good = create_token("viewer", "viewer", "access")
    assert decode_token(good, "access")["sub"] == "viewer"


def test_abha_mock_login(anon: TestClient) -> None:
    ok = anon.post("/auth/abha/token", json={"abha_address": "dr.demo@sbx", "otp": "123456"})
    assert ok.status_code == 200 and ok.json()["user"]["auth_method"] == "abha-mock"
    assert ok.json()["user"]["role"] == "clinician"
    assert (
        anon.post("/auth/abha/token", json={"abha_address": "dr.demo@sbx", "otp": "000000"}).status_code
        == 401
    )
    assert (
        anon.post("/auth/abha/token", json={"abha_address": "nobody@sbx", "otp": "123456"}).status_code == 401
    )
    assert (
        anon.post("/auth/abha/token", json={"abha_address": "bad address", "otp": "123456"}).status_code
        == 422
    )


def test_login_is_rate_limited(anon: TestClient) -> None:
    codes = [anon.post("/auth/login", json={"username": "x", "password": "y"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    blocked = anon.post("/auth/login", json={"username": "x", "password": "y"})
    assert int(blocked.headers["Retry-After"]) > 0


def test_public_endpoints_need_no_token(anon: TestClient) -> None:
    for path in ("/health", "/openapi.json", "/fhir/metadata"):
        assert anon.get(path).status_code == 200, path


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/search/autocomplete?q=amla", None),
        ("POST", "/map", {"text": "Amavata"}),
        ("GET", "/fhir/CodeSystem/namaste", None),
        ("GET", "/audit", None),
        ("GET", "/review/queue", None),
        ("GET", "/mappings", None),
        ("GET", "/admin/releases", None),
    ],
)
def test_protected_endpoints_reject_anonymous(anon: TestClient, method: str, path: str, body: object) -> None:
    r = anon.request(method, path, json=body)
    assert r.status_code == 401, path


def test_fhir_auth_errors_are_operation_outcomes(anon: TestClient) -> None:
    r = anon.get("/fhir/CodeSystem/namaste")
    assert r.status_code == 401 and r.json()["resourceType"] == "OperationOutcome"
    assert r.json()["issue"][0]["code"] == "login"


# role -> (method, path, body, permission)
MATRIX = [
    ("GET", "/fhir/ValueSet/namaste-ayurveda", None, "read"),
    ("POST", "/review/propose", {"namaste_code": "SAMPLE-AYU-0016"}, "propose"),
    ("POST", "/review/999999/decision", {"action": "approve"}, "review"),
    (
        "POST",
        "/mappings/bio-links",
        {"tm2_code": "SAMPLE-TM2-0141", "bio_code": "SAMPLE-BIO-0018", "comment": "test"},
        "review",
    ),
    (
        "POST",
        "/fhir/Condition/$build",
        {"namaste_code": "SAMPLE-AYU-9999", "patient": {"abha_number": "91-TEST-1"}},
        "build_condition",
    ),
    ("PUT", "/admin/thresholds", {"high": 0.9, "low": 0.5, "reason": "rbac matrix"}, "admin"),
    ("GET", "/admin/users", None, "admin"),
]


@pytest.mark.parametrize("role", ["viewer", "coder", "clinician", "admin"])
def test_permission_matrix(as_role: Callable[[str], TestClient], role: str) -> None:
    c = as_role(role)
    for method, path, body, permission in MATRIX:
        r = c.request(method, path, json=body)
        allowed = permission in POLICY[role]  # type: ignore[index]
        assert (r.status_code != 403) == allowed, f"{role} {method} {path} -> {r.status_code}"
    c.delete("/admin/thresholds") if role == "admin" else None


def test_token_refresh_does_not_use_up_the_login_budget(anon: TestClient) -> None:
    """Every full page load refreshes the in-memory access token; that must never lock a user out of login."""
    from app.core.config import demo_password, get_settings

    password = demo_password(get_settings(), "viewer")
    login = anon.post("/auth/login", json={"username": "viewer", "password": password})
    refresh = login.json()["refresh_token"]
    for _ in range(15):
        assert anon.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 200
    again = anon.post("/auth/login", json={"username": "viewer", "password": password})
    assert again.status_code == 200
