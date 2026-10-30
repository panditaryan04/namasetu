"""ICD-11 client tests. LIVE mode is exercised against a simulated WHO API (httpx.MockTransport)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import SAMPLE_DIR, Settings
from app.db.models import SYSTEM_BIO, SYSTEM_TM2
from app.ingest.icd11_client import (
    ICD11ApiError,
    LiveICD11Client,
    MockICD11Client,
    get_icd11_client,
)

# ------------------------------------------------------------------ MOCK


def test_mock_client_serves_sample() -> None:
    snap = get_icd11_client(Settings(), SAMPLE_DIR).fetch()
    assert snap.mode == "mock" and snap.is_sample
    tm2 = {c.code: c for c in snap.tm2}
    assert tm2["SAMPLE-TM2-0111"].entity_type == "disorder"
    assert tm2["SAMPLE-TM2-0511"].entity_type == "pattern"  # inherits "pattern" from ancestor block
    assert tm2["SAMPLE-TM2-0110"].entity_type == "category"
    assert all(c.system == SYSTEM_TM2 for c in snap.tm2)
    assert all(c.system == SYSTEM_BIO for c in snap.bio)
    assert {(lk.source_code, lk.target_code) for lk in snap.links} >= {("SAMPLE-TM2-0111", "SAMPLE-BIO-0001")}
    assert all(lk.relationship == "relatedto" and lk.origin == "who-index" for lk in snap.links)
    assert len(snap.links) == 8
    assert snap.report.warnings == []


def test_mock_drops_links_to_unknown_biomedical_codes() -> None:
    tm2 = {"entities": [{"code": "T1", "title": "x", "bio_links": [{"code": "NOPE"}]}]}
    snap = MockICD11Client(tm2, {"entities": []}).fetch()
    assert snap.links == []
    assert "NOPE not ingested" in snap.report.warnings[0]


def test_live_requires_keys() -> None:
    with pytest.raises(ICD11ApiError, match="required"):
        LiveICD11Client(Settings(icd_client_id="", icd_client_secret=""))


# ------------------------------------------------------------------ simulated WHO API

BASE = "https://id.who.int/icd/release/11/2025-01/mms"


def _ent(
    code: str | None, title: str, children: list[str], kind: str = "category", **kw: Any
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": {"@language": "en", "@value": title},
        "classKind": kind,
        "child": [f"http://id.who.int/icd/release/11/2025-01/mms/{c}" for c in children],
        **kw,
    }
    if code:
        data["code"] = code
    return data


WHO = {
    BASE: {"child": [f"http://id.who.int/icd/release/11/2025-01/mms/{c}" for c in ("ch15", "ch26")]},
    f"{BASE}/ch15": _ent("15", "Diseases of the musculoskeletal system", ["ra"], kind="chapter"),
    f"{BASE}/ra": _ent("FA20", "Rheumatoid arthritis", [], definition={"@value": "Inflammatory arthritis"}),
    f"{BASE}/ch26": _ent(
        "26", "Supplementary Chapter Traditional Medicine Conditions", ["tm1", "tm2"], kind="chapter"
    ),
    f"{BASE}/tm1": _ent(None, "Module 1", [], kind="block", blockId="BlockTM1"),
    f"{BASE}/tm2": _ent(
        None, "Traditional medicine conditions - Module 2", ["dis", "pat"], kind="block", blockId="BlockTM2"
    ),
    f"{BASE}/dis": _ent(None, "Disorders (TM2)", ["d1"], kind="block", blockId="BlockDis"),
    f"{BASE}/d1": _ent(
        "SK00",
        "Joint disorder (TM2)",
        [],
        indexTerm=[{"label": {"@value": "Amavata"}}, {"label": {"@value": "ama vata"}}],
    ),
    f"{BASE}/pat": _ent(None, "Patterns (TM2)", ["p1"], kind="block", blockId="BlockPat"),
    f"{BASE}/p1": _ent("SM00", "Vata pattern (TM2)", []),
}


class FakeWHO:
    def __init__(self, fail_first: dict[str, list[int]] | None = None) -> None:
        self.calls: list[httpx.Request] = []
        self.fail_first = fail_first or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        if url.endswith("/connect/token"):
            form = dict(x.split("=") for x in request.content.decode().split("&"))
            assert form["grant_type"] == "client_credentials" and form["scope"] == "icdapi_access"
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.headers["API-Version"] == "v2"
        assert request.headers["Accept-Language"] == "en"
        if self.fail_first.get(url):
            status = self.fail_first[url].pop(0)
            return httpx.Response(status, headers={"Retry-After": "2"} if status == 429 else {})
        if url not in WHO:
            return httpx.Response(404)
        return httpx.Response(200, json=WHO[url])


def _live(tmp_path: Path, fake: FakeWHO, **overrides: Any) -> tuple[LiveICD11Client, list[float]]:
    settings = Settings(
        icd_client_id="id",
        icd_client_secret="secret",
        icd11_cache_dir=str(tmp_path / "cache"),
        icd11_max_rps=1000,
        **overrides,
    )
    sleeps: list[float] = []
    client = LiveICD11Client(
        settings, http=httpx.Client(transport=httpx.MockTransport(fake)), sleep=sleeps.append
    )
    return client, sleeps


def test_live_walks_tm2_subtree_and_biomedical_chapter(tmp_path: Path) -> None:
    fake = FakeWHO()
    client, _ = _live(tmp_path, fake)
    progress: list[int] = []
    snap = client.fetch(lambda n, _m: progress.append(n))

    tm2 = {c.code: c for c in snap.tm2}
    assert set(tm2) == {"BlockTM2", "BlockDis", "SK00", "BlockPat", "SM00"}  # Module 1 not walked
    assert tm2["SK00"].entity_type == "disorder"
    assert tm2["SM00"].entity_type == "pattern"
    assert tm2["BlockDis"].entity_type == "category"
    assert tm2["SK00"].parent_code == "BlockDis"
    assert [t.term for t in tm2["SK00"].terms if t.kind == "index_term"] == ["Amavata", "ama vata"]
    assert [c.code for c in snap.bio] == ["15", "FA20"]
    assert snap.bio[1].definition == "Inflammatory arthritis"
    assert snap.mode == "live" and not snap.is_sample and snap.links == []
    assert sum(1 for c in fake.calls if c.url.path.endswith("/token")) == 1  # token reused


def test_live_uses_disk_cache_on_rerun(tmp_path: Path) -> None:
    first, _ = _live(tmp_path, FakeWHO())
    first.fetch()
    fake = FakeWHO()
    second, _ = _live(tmp_path, fake)
    snap = second.fetch()
    assert fake.calls == []  # fully offline from cache
    assert second.cache_hits > 0 and len(snap.tm2) == 5
    cached = list((tmp_path / "cache" / "2025-01").glob("*.json"))
    assert json.loads(cached[0].read_text(encoding="utf-8"))


def test_live_retries_429_and_5xx_with_backoff(tmp_path: Path) -> None:
    fake = FakeWHO(fail_first={f"{BASE}/d1": [429, 503]})
    client, sleeps = _live(tmp_path, fake)
    snap = client.fetch()
    assert "SK00" in {c.code for c in snap.tm2}
    assert 2.0 in sleeps  # honoured Retry-After
    assert 2.0 in sleeps or 4.0 in sleeps


def test_live_gives_up_after_max_retries(tmp_path: Path) -> None:
    fake = FakeWHO(fail_first={f"{BASE}/d1": [503] * 10})
    client, _ = _live(tmp_path, fake, icd11_max_retries=2)
    with pytest.raises(ICD11ApiError, match="503 after 3 attempts"):
        client.fetch()


def test_live_falls_back_to_whole_chapter_with_warning(tmp_path: Path) -> None:
    client, _ = _live(tmp_path, FakeWHO(), icd11_tm2_title_match="No Such Module")
    snap = client.fetch()
    assert "26" in {c.code for c in snap.tm2}
    assert any("ingested the whole chapter" in w for w in snap.report.warnings)


def test_live_missing_chapter_is_an_error(tmp_path: Path) -> None:
    client, _ = _live(tmp_path, FakeWHO(), icd11_tm_chapter="99")
    with pytest.raises(ICD11ApiError, match="chapter 99 not found"):
        client.fetch()
