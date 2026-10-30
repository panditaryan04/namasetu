"""Stages 0-2 (+ feature assembly): deterministic baseline, lexical fuzzy, char n-gram, transliteration,
root-term overlap, hierarchy and the DISORDER/PATTERN rule. Every score keeps the evidence behind it."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from rapidfuzz import fuzz

from app.engine.index import GENERIC_ROOTS, ConceptRecord, Surface, TerminologyIndex, content_bag
from app.engine.semantic import SemanticBackend

FEATURES: tuple[str, ...] = (
    "exact",
    "lexical",
    "ngram",
    "transliteration",
    "semantic",
    "hierarchy",
    "root_overlap",
    "ancestor_match",
)
# DISORDER-vs-PATTERN rule: a mismatched entity type multiplies confidence by this factor.
TYPE_MISMATCH_PENALTY = 0.6
# Ablation switches (Milestone 6). Disabling a stage zeroes its feature or neutralises its rule.
ABLATIONS = frozenset({"transliteration", "hierarchy", "type_rule", "semantic", "ngram", "roots"})
SANSKRIT_KINDS = frozenset({"primary", "iast", "devanagari", "hinglish", "index_term", "query"})
LARGE_TARGET_SET = 300  # above this, prefilter candidates by n-gram / exact / semantic retrieval


@dataclass(frozen=True)
class EngineOptions:
    disabled: frozenset[str] = frozenset()

    def on(self, stage: str) -> bool:
        return stage not in self.disabled


@dataclass
class PairEvidence:
    source: Surface
    target: Surface
    score: float
    via: list[str]


@dataclass
class SourceProfile:
    """What we match *from*: a resolved NAMASTE concept (all its surface forms) and/or raw query text."""

    surfaces: list[Surface]
    namaste: ConceptRecord | None = None
    entity_type: str | None = None
    bag: Counter[str] = field(default_factory=Counter)
    roots: frozenset[str] = frozenset()
    ancestor_surfaces: list[Surface] = field(default_factory=list)
    ancestor_bag: Counter[str] = field(default_factory=Counter)
    descendant_surfaces: list[Surface] = field(default_factory=list)


@dataclass
class Candidate:
    target: ConceptRecord
    features: dict[str, float]
    pairs: list[PairEvidence]
    matched_tokens: list[str]
    matched_roots: list[str]
    shared_words: list[str]
    identity: bool  # strong same-name evidence (used by relationship rules)
    type_agree: float = 0.5  # 1 agree, 0 mismatch, 0.5 unknown source type or rule disabled
    stage0: str | None = None  # origin when the deterministic baseline selected this target
    stage0_relationship: str | None = None
    confidence: float = 0.0

    @property
    def best(self) -> PairEvidence | None:
        return self.pairs[0] if self.pairs else None


def _content_key(s: Surface) -> str:
    return " ".join(s.norm.content_tokens) or s.norm.key


def _compact_content(s: Surface) -> str:
    return _content_key(s).replace(" ", "")


@lru_cache(maxsize=262_144)
def _pair_score(
    a: str, b: str, translit: bool, clean_differs: bool, script_differs: bool, same_skeleton: bool
) -> tuple[float, tuple[str, ...]]:
    """Pure string comparison behind Matcher._pair; memoised because surface forms recur across queries."""
    if not a or not b:
        return 0.0, ()
    via: list[str] = []
    if a.replace(" ", "") == b.replace(" ", ""):
        score = 1.0
        via.append("exact")
        if translit and clean_differs:
            via.append("transliteration" if script_differs else "phonetic-folding")
        if (" " in a) != (" " in b):
            via.append("compound")
    else:
        blended = (
            0.45 * fuzz.token_set_ratio(a, b)
            + 0.35 * fuzz.token_sort_ratio(a, b)
            + 0.20 * fuzz.partial_ratio(a, b)
        ) / 100
        score = max(blended, fuzz.ratio(a.replace(" ", ""), b.replace(" ", "")) / 100)
        via.append("fuzzy")
        if translit and same_skeleton:
            score = max(score, 0.9)
            via.append("phonetic-skeleton")
    return score, tuple(via)


def identity_direct(pairs: list[PairEvidence]) -> bool:
    return bool(pairs) and pairs[0].score >= 0.92


def weighted_jaccard(
    a: Counter[str] | frozenset[str], b: Counter[str] | frozenset[str], index: TerminologyIndex
) -> float:
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    inter = sum(index.weight(t) for t in sa & sb)
    return inter / sum(index.weight(t) for t in union)


class Matcher:
    def __init__(
        self, index: TerminologyIndex, semantic: SemanticBackend, options: EngineOptions | None = None
    ) -> None:
        self.index = index
        self.semantic = semantic
        self.options = options or EngineOptions()
        # TM2 ancestor titles (excluding the chapter root) as surfaces + bag, for hierarchy features.
        self._anc_surfaces: dict[str, list[Surface]] = {}
        self._anc_bag: dict[str, Counter[str]] = {}
        for t in index.tm2_targets:
            ancestors = [a for a in index.ancestors(t) if a.parent_code]  # drop the root chapter
            surfaces = [s for a in ancestors[:2] for s in a.surfaces]
            self._anc_surfaces[t.code] = surfaces
            self._anc_bag[t.code] = content_bag(s.text for s in surfaces)

    # ------------------------------------------------------------ profiles

    def profile_for_concept(self, rec: ConceptRecord, query: Surface | None = None) -> SourceProfile:
        surfaces = list(rec.surfaces) + ([query] if query else [])
        ancestors = self.index.ancestors(rec)[:2]
        anc_surfaces = [s for a in ancestors for s in a.surfaces]
        descendants = [d for code in rec.children if (d := self.index.get(rec.system, code))]
        return SourceProfile(
            surfaces=surfaces,
            namaste=rec,
            entity_type=rec.entity_type,
            bag=rec.bag + (content_bag([query.text]) if query else Counter()),
            roots=rec.roots | (frozenset(query.norm.roots) - GENERIC_ROOTS if query else frozenset()),
            ancestor_surfaces=anc_surfaces,
            ancestor_bag=content_bag(s.text for s in anc_surfaces if s.kind in {"primary", "english"}),
            descendant_surfaces=[s for d in descendants for s in d.surfaces],
        )

    def profile_for_text(self, query: Surface) -> SourceProfile:
        return SourceProfile(
            surfaces=[query],
            bag=content_bag([query.text]),
            roots=frozenset(query.norm.roots) - GENERIC_ROOTS,
        )

    # ------------------------------------------------------------ pairwise lexical evidence

    def _pair(self, src: Surface, tgt: Surface) -> PairEvidence:
        translit = self.options.on("transliteration")
        a = _content_key(src) if translit else src.norm.clean
        b = _content_key(tgt) if translit else tgt.norm.clean
        score, via_t = _pair_score(
            a,
            b,
            translit,
            src.norm.clean.replace(" ", "") != tgt.norm.clean.replace(" ", ""),
            src.norm.script != tgt.norm.script,
            len(src.norm.skeleton) >= 3 and src.norm.skeleton == tgt.norm.skeleton,
        )
        via = list(via_t)
        if src.kind in {"english", "hinglish", "iast", "devanagari"} and src.source == "synonyms":
            via.insert(0, "synonym")
        return PairEvidence(src, tgt, score, via)

    # ------------------------------------------------------------ scoring

    def candidates(self, profile: SourceProfile) -> list[Candidate]:
        targets = self.index.tm2_targets
        if not targets or not profile.surfaces:
            return []

        ngram = self._ngram_scores(profile) if self.options.on("ngram") else np.zeros(len(targets))
        if len(targets) > LARGE_TARGET_SET:
            keep = set(np.argsort(-ngram)[:150].tolist())
            keep |= set(self.semantic.top_indices(profile, 50))
            keep |= {
                i
                for i, t in enumerate(targets)
                if any(s.norm.compact in {x.norm.compact for x in profile.surfaces} for s in t.surfaces)
            }
            order = sorted(keep)
        else:
            order = list(range(len(targets)))
        chosen = [targets[i] for i in order]
        semantic = (
            self.semantic.scores(profile, chosen) if self.options.on("semantic") else np.zeros(len(chosen))
        )

        out: list[Candidate] = []
        for pos, i in enumerate(order):
            out.append(self._candidate(profile, targets[i], float(ngram[i]), float(semantic[pos])))
        return out

    def _ngram_scores(self, profile: SourceProfile) -> np.ndarray:
        idx = self.index
        keys = [
            _content_key(s) if self.options.on("transliteration") else s.norm.clean for s in profile.surfaces
        ]
        src = idx.ngram.transform([k or " " for k in keys])
        sims = (src @ idx.target_matrix.T).toarray()  # sources x target surfaces
        per_surface = sims.max(axis=0) if sims.size else np.zeros(idx.target_matrix.shape[0])
        out = np.zeros(len(idx.tm2_targets))
        np.maximum.at(out, idx.target_owner, per_surface)
        return out

    def _candidate(
        self, profile: SourceProfile, target: ConceptRecord, ngram: float, semantic: float
    ) -> Candidate:
        pairs = sorted(
            (self._pair(s, t) for s in profile.surfaces for t in target.surfaces),
            key=lambda p: (p.score, "exact" in p.via, p.source.kind in SANSKRIT_KINDS),
            reverse=True,
        )
        best = pairs[0] if pairs else None
        lexical = best.score if best else 0.0
        exact = 1.0 if best and "exact" in best.via else 0.0

        translit = 0.0
        if self.options.on("transliteration"):
            weights = {"transliteration": 1.0, "phonetic-folding": 0.85, "phonetic-skeleton": 0.75}
            for p in pairs:
                translit = max([translit, *(w for k, w in weights.items() if k in p.via)])

        roots = sorted(profile.roots & target.roots) if self.options.on("roots") else []
        root_union = profile.roots | target.roots
        root_overlap = len(roots) / len(root_union) if root_union and self.options.on("roots") else 0.0

        hierarchy = self._hierarchy(profile, target) if self.options.on("hierarchy") else 0.0

        # The candidate names a broader NAMASTE ancestor of the source (e.g. Vataja Jvara -> "Jvara").
        ancestor_match = (
            1.0
            if self.options.on("hierarchy")
            and not identity_direct(pairs)
            and profile.ancestor_surfaces
            and self.identity_with(profile.ancestor_surfaces, target)
            else 0.0
        )

        if not self.options.on("type_rule") or profile.entity_type is None:
            type_agree = 0.5
        else:
            type_agree = 1.0 if profile.entity_type == target.entity_type else 0.0

        shared = sorted(set(profile.bag) & set(target.bag), key=lambda t: -self.index.weight(t))
        matched_tokens = sorted(set(best.source.norm.tokens) & set(best.target.norm.tokens)) if best else []
        identity = exact == 1.0 or translit > 0 or lexical >= 0.92

        return Candidate(
            target=target,
            features={
                "exact": exact,
                "lexical": round(lexical, 4),
                "ngram": round(ngram, 4),
                "transliteration": translit,
                "semantic": round(semantic, 4),
                "hierarchy": round(hierarchy, 4),
                "root_overlap": round(root_overlap, 4),
                "ancestor_match": ancestor_match,
            },
            type_agree=type_agree,
            pairs=pairs[:3],
            matched_tokens=matched_tokens,
            matched_roots=roots,
            shared_words=shared[:5],
            identity=identity,
        )

    def _hierarchy(self, profile: SourceProfile, target: ConceptRecord) -> float:
        anc_surfaces = self._anc_surfaces.get(target.code, [])
        if profile.ancestor_surfaces and anc_surfaces:
            for s in profile.ancestor_surfaces:
                for t in anc_surfaces:
                    if self._pair(s, t).score >= 0.92:
                        return 1.0
        return weighted_jaccard(profile.ancestor_bag, self._anc_bag.get(target.code, Counter()), self.index)

    def identity_with(self, surfaces: list[Surface], target: ConceptRecord) -> bool:
        """Does any of `surfaces` name `target` (exact, transliterated or near-identical)?"""
        return any(self._pair(s, t).score >= 0.92 for s in surfaces for t in target.surfaces)
