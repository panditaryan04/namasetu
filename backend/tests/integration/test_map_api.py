from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_reports_loaded_index(client: TestClient) -> None:
    index = client.get("/health").json()["index"]
    assert index["loaded"] is True and index["ranker"] == "ranker@1.0.0"
    assert index["semantic_method"] == "weighted-lexical-overlap"
    assert 0 < index["thresholds"]["low"] < index["thresholds"]["high"] <= 1


def test_autocomplete_detects_script(client: TestClient) -> None:
    body = client.get("/search/autocomplete", params={"q": "अम्लपित्त"}).json()
    assert body["query"]["script"] == "devanagari" and body["query"]["normalised"] == "amlapita"
    assert body["items"][0]["code"] == "SAMPLE-AYU-0009" and body["items"][0]["match"] == "exact"
    assert client.get("/search/autocomplete", params={"q": ""}).status_code == 422


def test_map_by_text_and_by_code(client: TestClient) -> None:
    by_text = client.post("/map", json={"text": "Amla Pitta"}).json()
    by_code = client.post("/map", json={"code": "SAMPLE-AYU-0009"}).json()
    assert by_text["resolved"]["code"] == by_code["resolved"]["code"] == "SAMPLE-AYU-0009"
    top = by_text["candidates"][0]
    assert set(top) >= {"query", "namaste", "tm2", "biomedicine", "confidence", "band", "relationship",
                        "scores", "evidence", "reasoning", "dataset_releases", "model_version"}  # fmt: skip
    assert set(top["scores"]) == {
        "exact",
        "lexical",
        "ngram",
        "transliteration",
        "semantic",
        "hierarchy",
        "final",
    }


def test_map_validation_and_404(client: TestClient) -> None:
    assert client.post("/map", json={}).status_code == 422
    assert client.post("/map", json={"text": "x", "top_k": 50}).status_code == 422
    resp = client.post("/map", json={"code": "SAMPLE-AYU-9999"})
    assert resp.status_code == 404 and "not found" in resp.json()["error"]["message"]


def test_map_batch(client: TestClient) -> None:
    items = [{"text": "आमवात"}, {"text": "xyzzy"}, {"code": "NOPE"}, {"text": "Kasa"}]
    body = client.post("/map/batch", json={"items": items}).json()
    assert body["summary"] == {"total": 4, "errors": 1, "suggested": 2, "review": 0, "no_match": 1}
    assert client.post("/map/batch", json={"items": [{"text": "a"}] * 101}).status_code == 422


def test_map_is_audited_and_chain_verifies(client: TestClient) -> None:
    before = client.get("/audit", params={"action": "map"}).json()["total"]
    client.post("/map", json={"text": "Sandhivata"})
    events = client.get("/audit", params={"action": "map"}).json()
    assert events["total"] == before + 1
    after = events["items"][0]["after"]
    assert after["resolved"] == "SAMPLE-AYU-0002" and after["candidates"][0]["tm2"] == "SAMPLE-TM2-0112"
    assert client.get("/audit/verify").json()["ok"] is True


def test_ranker_endpoint(client: TestClient) -> None:
    body = client.get("/eval/ranker").json()
    assert body["version"] == "ranker@1.0.0" and len(body["weights"]) == len(body["features"])
    assert body["calibration"] and body["training"]["queries"] == 42
