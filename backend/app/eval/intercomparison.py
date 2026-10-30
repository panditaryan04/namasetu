"""Intercomparison, ablation and per-script robustness on the held-out gold TEST split.

All engines here run with the Stage-0 baseline DISABLED (stage0_gold_split="none"), so no system can read the
answer from the official crosswalk. Ablation engines are retrained without the removed feature.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import SYSTEM_NAMASTE, GoldMapping
from app.engine.gate import band
from app.engine.matcher import Candidate, EngineOptions
from app.engine.relationship import infer_relationships
from app.engine.service import MappingEngine, build_engine
from app.eval.metrics import QueryOutcome, failures, rank_metrics

NO_STAGE0 = Settings(stage0_gold_split="none")
ABLATIONS = {
    "no transliteration": "transliteration",
    "no hierarchy": "hierarchy",
    "no disorder/pattern rule": "type_rule",
    "no semantic": "semantic",
    "no char n-gram": "ngram",
    "no root terms": "roots",
}


def gold_rows(session: Session, split: str | None = "test") -> list[GoldMapping]:
    q = select(GoldMapping).order_by(GoldMapping.namaste_code)
    if split:
        q = q.where(GoldMapping.split == split)
    return list(session.scalars(q))


def engine_for(session: Session, disabled: frozenset[str] = frozenset()) -> MappingEngine:
    return build_engine(session, NO_STAGE0, options=EngineOptions(disabled), save_artifact=False)


def _feature_ranking(engine: MappingEngine, code: str, score: Callable[[Candidate], float]) -> list[str]:
    rec = engine.index.get(SYSTEM_NAMASTE, code)
    assert rec is not None
    cands = engine.matcher.candidates(engine.matcher.profile_for_concept(rec))
    scored = sorted(((score(c), c.target.code) for c in cands), key=lambda x: (-x[0], x[1]))
    return [code for s, code in scored if s > 0]


def hybrid_outcome(engine: MappingEngine, g: GoldMapping, query: str | None = None) -> QueryOutcome:
    """Full hybrid ranking (every candidate, incl. low band) plus the band the pipeline would act on."""
    if query:
        q = engine.index.make_surface(query)
        resolved, _ = engine.resolve(query)
        rec = engine.index.get(SYSTEM_NAMASTE, resolved.code) if resolved else None
        profile = engine.matcher.profile_for_concept(rec, q) if rec else engine.matcher.profile_for_text(q)
    else:
        resolved, rec = None, engine.index.get(SYSTEM_NAMASTE, g.namaste_code)
        assert rec is not None
        profile = engine.matcher.profile_for_concept(rec)
    ranked = engine._rank(profile)
    top = ranked[0] if ranked else None
    rel = infer_relationships(ranked, profile, engine.matcher)[0] if top else None
    return QueryOutcome(
        query=query or g.namaste_code,
        gold=g.tm2_code,
        ranked=[c.target.code for c in ranked],
        top_confidence=round(top.confidence, 4) if top else None,
        top_band=band(top.confidence, engine.thresholds) if top else "low",
        relationship_ok=(rel == g.relationship) if top and top.target.code == g.tm2_code else None,
        extra={
            "resolved_namaste": (resolved.code == g.namaste_code) if resolved else query is None,
            "resolved_wrong": resolved is not None and resolved.code != g.namaste_code,
        },
    )


def intercomparison(session: Session, rows: list[GoldMapping]) -> dict[str, Any]:
    full = engine_for(session)
    no_translit = engine_for(session, frozenset({"transliteration"}))

    def single(
        engine: MappingEngine, score: Callable[[Candidate], float]
    ) -> Callable[[GoldMapping], QueryOutcome]:
        return lambda g: QueryOutcome(
            g.namaste_code, g.tm2_code, _feature_ranking(engine, g.namaste_code, score)
        )

    def translit_lexical(c: Candidate) -> float:
        return max(c.features["lexical"], c.features["exact"], c.features["transliteration"])

    systems: dict[str, Callable[[GoldMapping], QueryOutcome]] = {
        "Exact": single(full, lambda c: c.features["exact"]),
        "Lexical": single(no_translit, lambda c: c.features["lexical"]),
        "Transliteration + Lexical": single(full, translit_lexical),
        "Semantic": single(full, lambda c: c.features["semantic"]),
        "Hybrid": lambda g: hybrid_outcome(full, g),
    }
    out: dict[str, Any] = {"semantic_method": full.matcher.semantic.method, "systems": {}}
    for name, run in systems.items():
        outcomes = [run(g) for g in rows]
        out["systems"][name] = {**rank_metrics(outcomes), "failures": failures(outcomes)}
    return out


def ablation(session: Session, rows: list[GoldMapping]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    full = engine_for(session)
    out["Hybrid (all features)"] = rank_metrics([hybrid_outcome(full, g) for g in rows])
    for label, stage in ABLATIONS.items():
        eng = engine_for(session, frozenset({stage}))
        out[label] = {**rank_metrics([hybrid_outcome(eng, g) for g in rows]), "removed": stage}
    return out


def typo(text: str, edits: int, seed: str) -> str:
    """Deterministic 1-2 character edits (substitution, deletion, transposition), never the first letter."""
    rng = random.Random(f"{seed}:{edits}")  # noqa: S311 - reproducible test data, not security
    s = list(text)
    for _ in range(edits):
        letters = [i for i, ch in enumerate(s) if ch.isalpha() and i > 0]
        if len(letters) < 2:
            break
        i = rng.choice(letters[:-1])
        op = rng.choice(["sub", "del", "swap"])
        if op == "sub":
            s[i] = rng.choice([c for c in "aeioukrstnm" if c != s[i].lower()])
        elif op == "del":
            del s[i]
        else:
            s[i], s[i + 1] = s[i + 1], s[i]
    return "".join(s)


def script_variants(engine: MappingEngine, code: str) -> dict[str, str | None]:
    rec = engine.index.get(SYSTEM_NAMASTE, code)
    assert rec is not None
    hinglish = next((s.text for s in rec.surfaces if s.kind == "hinglish"), None)
    return {
        "English": rec.term_en,
        "IAST": rec.term_iast,
        "Devanagari": rec.term_devanagari,
        "Hinglish": hinglish,
        "Typo (1 edit)": typo(rec.display, 1, code),
        "Typo (2 edits)": typo(rec.display, 2, code),
    }


def robustness(session: Session, rows: list[GoldMapping]) -> dict[str, Any]:
    """The same gold queries typed as text in each script; full pipeline incl. query resolution."""
    engine = engine_for(session)
    per_script: dict[str, list[QueryOutcome]] = {}
    for g in rows:
        for script, text in script_variants(engine, g.namaste_code).items():
            if text:
                per_script.setdefault(script, []).append(hybrid_outcome(engine, g, query=text))
    return {
        script: {
            **rank_metrics(outcomes),
            "resolved_to_correct_namaste": round(
                sum(o.extra["resolved_namaste"] for o in outcomes) / len(outcomes), 4
            ),
            # A query resolved to a DIFFERENT concept would be mapped confidently but wrongly: must stay ~0.
            "resolved_to_wrong_namaste": round(
                sum(o.extra["resolved_wrong"] for o in outcomes) / len(outcomes), 4
            ),
            "failures": failures(outcomes, 20),
        }
        for script, outcomes in per_script.items()
    }
