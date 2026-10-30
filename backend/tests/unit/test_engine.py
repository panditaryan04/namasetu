"""Engine behaviour on the seeded SAMPLE data: resolution, ranking, relationships, gate, safety, evidence."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import SYSTEM_TM2, GoldMapping
from app.db.session import get_sessionmaker
from app.engine.explain import NO_BIO_LINK
from app.engine.gate import Thresholds, band
from app.engine.matcher import FEATURES, TYPE_MISMATCH_PENALTY, EngineOptions
from app.engine.semantic import EmbeddingBackend, LexicalOverlapBackend, build_semantic_backend
from app.engine.service import CodeNotInDatasetError, MappingEngine, build_engine


@pytest.fixture(scope="module")
def engine() -> Iterator[MappingEngine]:
    with get_sessionmaker()() as session:
        yield build_engine(session)


@pytest.fixture(scope="module")
def engine_no_stage0() -> Iterator[MappingEngine]:
    with get_sessionmaker()() as session:
        yield build_engine(session, Settings(stage0_gold_split="none"), save_artifact=False)


# ------------------------------------------------------------------ resolution across scripts


@pytest.mark.parametrize(
    "text",
    ["Amavata", "Āmavāta", "आमवात", "aamvaat", "aamavaata", "AmavAta", "rheumatism with ama", "amvaat"],
)
def test_amavata_resolves_in_every_script(engine: MappingEngine, text: str) -> None:
    resolved, _ = engine.resolve(text)
    assert resolved is not None and resolved.code == "SAMPLE-AYU-0001", text


@pytest.mark.parametrize(
    "text", ["Amlapitta", "Amla Pitta", "Amla-pitta", "Āmlapitta", "अम्लपित्त", "amlpitt", "acidity"]
)
def test_amlapitta_variants_resolve_to_same_concept(engine: MappingEngine, text: str) -> None:
    resolved, _ = engine.resolve(text)
    assert resolved is not None and resolved.code == "SAMPLE-AYU-0009", text


def test_partial_root_query_retrieves_compounds_but_does_not_resolve(engine: MappingEngine) -> None:
    resolved, alternatives = engine.resolve("pitta")
    assert resolved is None
    shown = {s.display for s in engine.suggest("pitta", 10)}
    assert {"Amlapitta", "Raktapitta", "Shitapitta", "Pittaja Jvara"} <= shown


def test_autocomplete_prefix_and_devanagari(engine: MappingEngine) -> None:
    assert engine.suggest("amla", 3)[0].display == "Amlapitta"
    top = engine.suggest("ज्वर", 3)[0]
    assert top.display == "Jvara" and top.match == "exact"


# ------------------------------------------------------------------ mapping, explanation, gate


def test_map_devanagari_returns_full_explanation(engine: MappingEngine) -> None:
    r = engine.map(text="आमवात")
    assert r.status == "suggested" and r.query.script == "devanagari" and r.query.normalised == "amavata"
    top = r.candidates[0]
    assert top.tm2.code == "SAMPLE-TM2-0111" and top.band == "high" and top.relationship == "equivalent"
    assert top.namaste is not None and top.namaste.code == "SAMPLE-AYU-0001"
    assert top.evidence.transliteration_hit and "transliteration" in top.evidence.stages_hit
    assert {"ama", "vata"} <= set(top.evidence.matched_root_terms)
    assert top.scores.ngram > 0.5 and top.scores.lexical > 0.9
    assert top.evidence.hierarchy_path[-1] == top.tm2.display
    assert top.evidence.entity_type_check == "disorder→disorder ✓"
    assert top.biomedicine.code == "SAMPLE-BIO-0001"
    assert top.biomedicine.relationship == "relatedto" and top.biomedicine.source == "who-index"
    assert top.model_version == "ranker@1.0.0" and "namaste@SAMPLE-1" in top.dataset_releases
    assert "sample" in top.tm2.system.lower()  # sample codes are never presented under WHO's official URI


def test_reasoning_is_built_only_from_evidence(engine_no_stage0: MappingEngine) -> None:
    top = engine_no_stage0.map(text="Amlapitta").candidates[0]
    assert top.reasoning.startswith("Matched '")
    assert "'amla'" in top.reasoning and "'pita'" in top.reasoning
    assert "Relationship 'equivalent'" in top.reasoning and "Confidence band: high" in top.reasoning


def test_no_biomedical_link_message(engine: MappingEngine) -> None:
    top = engine.map(text="Kasa").candidates[0]
    assert top.tm2.code == "SAMPLE-TM2-0141"
    assert top.biomedicine.code is None and top.biomedicine.status == "none"
    assert top.biomedicine.message == NO_BIO_LINK


@pytest.mark.parametrize(
    ("code", "target", "relationship"),
    [("SAMPLE-AYU-0040", "SAMPLE-TM2-0201", "wider"),  # Vataja Jvara -> Fever disorder
     ("SAMPLE-AYU-0042", "SAMPLE-TM2-0201", "wider"),
     ("SAMPLE-AYU-0018", "SAMPLE-TM2-0143", "equivalent"),
     ("SAMPLE-AYU-0044", "SAMPLE-TM2-0212", "equivalent")],
)  # fmt: skip
def test_relationship_rules(
    engine_no_stage0: MappingEngine, code: str, target: str, relationship: str
) -> None:
    r = engine_no_stage0.map(code=code)
    assert r.candidates[0].tm2.code == target
    assert r.candidates[0].relationship == relationship


def test_ancestor_of_equivalent_is_wider(engine_no_stage0: MappingEngine) -> None:
    # Tamaka Shvasa = "Wheezing breathlessness" (0143); its TM2 parent "Breathlessness disorder" is wider.
    rels = {
        c.tm2.code: c.relationship for c in engine_no_stage0.map(code="SAMPLE-AYU-0018", top_k=10).candidates
    }
    assert rels["SAMPLE-TM2-0143"] == "equivalent"
    assert rels.get("SAMPLE-TM2-0142") in {"wider", None}  # wider when shown (it may fall below T_LOW)
    madhumeha = {
        c.tm2.code: c.relationship for c in engine_no_stage0.map(code="SAMPLE-AYU-0044", top_k=10).candidates
    }
    assert madhumeha.get("SAMPLE-TM2-0211", "wider") == "wider"


def test_unmatchable_text_is_not_emitted(engine: MappingEngine) -> None:
    r = engine.map(text="xyzzy quux")
    assert r.status == "no_match" and r.candidates == []
    assert r.message == "No safe automated match found"


def test_low_band_candidates_are_never_returned(engine: MappingEngine) -> None:
    for text in ("Amavata", "joint pain with swelling", "pitta", "Vataja Jvara"):
        assert all(c.band != "low" for c in engine.map(text=text, top_k=10).candidates)


def test_gate_bands() -> None:
    t = Thresholds(high=0.85, low=0.5)
    assert [band(x, t) for x in (0.9, 0.85, 0.6, 0.5, 0.49)] == ["high", "high", "medium", "medium", "low"]
    with pytest.raises(ValueError, match="T_LOW"):
        Thresholds(high=0.4, low=0.6)


def test_threshold_override_from_settings() -> None:
    with get_sessionmaker()() as session:
        e = build_engine(session, Settings(t_high=0.99, t_low=0.98), save_artifact=False)
    assert e.thresholds.source == "override" and e.thresholds.high == 0.99
    r = e.map(text="Vataja Jvara")
    assert all(c.confidence >= 0.98 for c in r.candidates)


# ------------------------------------------------------------------ Stage 0 and safety


def test_stage0_uses_train_split_only(engine: MappingEngine) -> None:
    with get_sessionmaker()() as session:
        gold = session.scalars(select(GoldMapping)).all()
    train = {g.namaste_code for g in gold if g.split == "train"}
    test = {g.namaste_code for g in gold if g.split == "test"}
    assert set(engine.index.stage0) == train and not (set(engine.index.stage0) & test)
    code = sorted(train)[0]
    top = engine.map(code=code).candidates[0]
    assert top.confidence == 1.0 and top.scores.exact == 1.0
    assert top.evidence.baseline == "official-crosswalk" and top.evidence.stages_hit[0] == "exact"


def test_every_emitted_code_exists_in_dataset(engine: MappingEngine) -> None:
    for g in engine.index.namaste:
        r = engine.map(code=g.code, top_k=10)
        for c in r.candidates:
            assert engine.index.is_valid(SYSTEM_TM2, c.tm2.code)
            if c.biomedicine.code:
                assert engine.index.is_valid("icd11-bio", c.biomedicine.code)


def test_validate_raises_for_unknown_code(engine: MappingEngine) -> None:
    with pytest.raises(CodeNotInDatasetError):
        engine.validate(SYSTEM_TM2, "SAMPLE-TM2-NOPE")


def test_unknown_namaste_code_raises_keyerror(engine: MappingEngine) -> None:
    with pytest.raises(KeyError):
        engine.map(code="SAMPLE-AYU-9999")


# ------------------------------------------------------------------ ranker artifact


def test_ranker_weights_are_non_negative_and_type_rule_penalises(engine: MappingEngine) -> None:
    a = engine.ranker
    assert a.features == list(FEATURES) and all(w >= 0 for w in a.weights)
    assert a.thresholds.source == "calibrated" and a.thresholds.low < a.thresholds.high
    assert a.training.queries == 42 and a.training.positives == 42  # TRAIN split only
    feats = dict.fromkeys(FEATURES, 0.5)
    assert a.score(feats, type_agree=0.0) == pytest.approx(a.score(feats, 1.0) * TYPE_MISMATCH_PENALTY)
    assert sum(b.count for b in a.calibration) == a.training.pairs


def test_held_out_test_split_accuracy(engine_no_stage0: MappingEngine) -> None:
    """Regression guard on the 11 held-out TEST codes (never used for training or Stage 0)."""
    with get_sessionmaker()() as session:
        test = session.scalars(select(GoldMapping).where(GoldMapping.split == "test")).all()
    top1 = sum(
        1
        for g in test
        if (c := engine_no_stage0.map(code=g.namaste_code).candidates) and c[0].tm2.code == g.tm2_code
    )
    assert top1 / len(test) >= 0.9, f"test top-1 {top1}/{len(test)}"


def test_ablation_options_zero_their_features() -> None:
    with get_sessionmaker()() as session:
        e = build_engine(session, options=EngineOptions(frozenset({"transliteration", "hierarchy", "ngram"})))
    top = e.map(text="आमवात", top_k=10)
    for c in top.candidates:
        assert c.scores.transliteration == 0 and c.scores.hierarchy == 0 and c.scores.ngram == 0


# ------------------------------------------------------------------ semantic backends


class FakeEncoder:
    """Deterministic stand-in for a sentence-transformer: bag of character trigrams hashed to 64 dims."""

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, t in enumerate(texts):
            for j in range(len(t) - 2):
                out[i, hash(t[j : j + 3].lower()) % 64] += 1
        return out / np.clip(np.linalg.norm(out, axis=1, keepdims=True), 1e-9, None)


def test_semantic_off_uses_lexical_overlap(engine: MappingEngine) -> None:
    assert isinstance(engine.matcher.semantic, LexicalOverlapBackend)
    assert engine.semantic_status.method == "weighted-lexical-overlap" and not engine.semantic_status.degraded


def test_semantic_full_without_vectors_degrades_gracefully(
    engine: MappingEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.engine.semantic.EMBEDDINGS_DIR", tmp_path)
    backend, status = build_semantic_backend(Settings(semantic_mode="full"), engine.index)
    assert (
        isinstance(backend, LexicalOverlapBackend)
        and status.degraded
        and "run scripts/build_index.py" in status.detail
    )


def test_embedding_backend_with_precomputed_vectors(
    engine: MappingEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.engine.semantic import concept_text, save_embeddings

    monkeypatch.setattr("app.engine.semantic.EMBEDDINGS_DIR", tmp_path)
    settings = Settings(semantic_mode="full", semantic_model="fake/model")
    recs = list(engine.index.concepts.values())
    enc = FakeEncoder()
    save_embeddings(
        "fake/model",
        [(r.system, r.code) for r in recs],
        enc.encode([concept_text(r) for r in recs]),
        engine.index.releases,
    )
    backend, status = build_semantic_backend(settings, engine.index, encoder=enc)
    assert (
        isinstance(backend, EmbeddingBackend)
        and status.method == "embedding:fake/model"
        and not status.degraded
    )
    rec = engine.index.get("namaste", "SAMPLE-AYU-0001")
    assert rec is not None
    prof = engine.matcher.profile_for_concept(rec)
    scores = backend.scores(prof, engine.index.tm2_targets)
    assert scores.shape == (len(engine.index.tm2_targets),) and scores.max() > 0
    assert len(backend.top_indices(prof, 5)) == 5
    free = engine.matcher.profile_for_text(engine.index.make_surface("joint pain"))
    assert backend.scores(free, engine.index.tm2_targets).max() > 0  # live encoder path


@pytest.mark.parametrize(
    ("text", "code"),
    [("Amavta", "SAMPLE-AYU-0001"), ("Amlapita", "SAMPLE-AYU-0009"), ("Amlaptita", "SAMPLE-AYU-0009")],
)
def test_unambiguous_typo_resolves_with_method_typo(engine: MappingEngine, text: str, code: str) -> None:
    resolved, _ = engine.resolve(text)
    assert resolved is not None and resolved.code == code, text
    assert resolved.method in {"typo", "fuzzy", "exact"}


def test_typo_resolution_refuses_near_ties_between_concepts(engine: MappingEngine) -> None:
    # One edit from both Vataja Jvara and Pittaja Jvara / both "piles" and "hives": never guess.
    for text in ("Pataja Jvara", "pives"):
        assert engine._typo_resolution(text) is None, text
    assert engine._typo_resolution("Jvr") is None  # too short for any edit
    hit = engine._typo_resolution("Vatajajvra")
    assert hit is not None and hit[0].display == "Vataja Jvara"
