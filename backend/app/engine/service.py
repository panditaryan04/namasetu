"""Mapping engine: resolve the query, run Stages 0-5, validate every code, and explain every candidate."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from rapidfuzz import fuzz, process
from rapidfuzz.distance import OSA
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import SOURCE_GOLD, SYSTEM_BIO, SYSTEM_NAMASTE, SYSTEM_TM2, AppSetting, GoldMapping
from app.engine.explain import (
    NO_BIO_LINK,
    BiomedicineRef,
    CodeRef,
    Evidence,
    Explanation,
    MatchedTerm,
    QueryInfo,
    Scores,
    TM2Ref,
    build_reasoning,
    stages_hit,
)
from app.engine.gate import Thresholds, band
from app.engine.index import ConceptRecord, TerminologyIndex, build_index, display_word, index_version_key
from app.engine.matcher import Candidate, EngineOptions, Matcher, SourceProfile
from app.engine.ranker import RankerArtifact, train_ranker
from app.engine.relationship import infer_relationships
from app.engine.semantic import SemanticStatus, build_semantic_backend
from app.fhir.common import system_uri as fhir_system_uri
from app.ingest.release import latest_release

logger = logging.getLogger("namasetu.engine")

RESOLVE_MIN = 0.90  # query -> NAMASTE concept resolution needs a near-identical surface form
TYPO_SCORE = 0.9  # reported score of a unique bounded-edit (typo) resolution


class CodeNotInDatasetError(RuntimeError):
    """Raised instead of ever returning a code that is not in an ingested dataset."""


class ResolvedSource(BaseModel):
    code: str
    display: str
    matched_term: str
    method: Literal["code", "exact", "fuzzy", "typo"]
    score: float
    iast: str | None = None
    devanagari: str | None = None


class Suggestion(BaseModel):
    match: Literal["exact", "prefix", "root", "fuzzy"]
    system: str
    code: str
    display: str
    matched_term: str
    term_kind: str
    devanagari: str | None
    entity_type: str
    score: float


class MapResult(BaseModel):
    query: QueryInfo
    resolved: ResolvedSource | None
    alternatives: list[Suggestion]
    status: Literal["suggested", "review", "no_match"]
    message: str
    candidates: list[Explanation]
    suppressed_low: int
    thresholds: Thresholds
    semantic_method: str
    dataset_releases: list[str]
    model_version: str


@dataclass
class MappingEngine:
    index: TerminologyIndex
    matcher: Matcher
    ranker: RankerArtifact
    thresholds: Thresholds
    semantic_status: SemanticStatus
    settings: Settings

    def __post_init__(self) -> None:
        # Flat list of NAMASTE surface keys for fast autocomplete / resolution (RapidFuzz in C).
        self._ac_keys: list[str] = []
        self._ac_owner: list[tuple[ConceptRecord, int]] = []
        for rec in self.index.namaste:
            for i, s in enumerate(rec.surfaces):
                self._ac_keys.append(s.norm.compact)
                self._ac_owner.append((rec, i))

    # ------------------------------------------------------------ systems

    def system_uri(self, system: str) -> str:
        return fhir_system_uri(system, self.index.is_sample, self.settings)

    def validate(self, system: str, code: str) -> None:
        if not self.index.is_valid(system, code):
            raise CodeNotInDatasetError(f"{system}|{code} is not in any ingested dataset")

    # ------------------------------------------------------------ autocomplete / resolution

    def suggest(self, text: str, limit: int = 10) -> list[Suggestion]:
        q = self.index.make_surface(text)
        if not q.norm.compact:
            return []
        best: dict[str, Suggestion] = {}
        qkey = q.norm.compact
        q_roots = set(q.norm.roots) if self.index.splitter.covers(q.norm.key) else set()
        hits = process.extract(qkey, self._ac_keys, scorer=fuzz.WRatio, limit=limit * 6)
        prefix = [i for i, k in enumerate(self._ac_keys) if k.startswith(qkey)]
        # A query that is itself a root term ("pitta") retrieves compounds containing it ("Amlapitta").
        rooted = [
            i
            for i, (rec, si) in enumerate(self._ac_owner)
            if q_roots and len(qkey) >= 3 and q_roots <= set(rec.surfaces[si].norm.roots)
        ]
        rooted_set = set(rooted)
        for i in {h[2] for h in hits} | set(prefix[: limit * 4]) | set(rooted[: limit * 8]):
            rec, si = self._ac_owner[i]
            surface = rec.surfaces[si]
            key = self._ac_keys[i]
            match: Literal["exact", "prefix", "root", "fuzzy"]
            if key == qkey:
                score, match = 1.0, "exact"
            elif len(q.norm.skeleton) >= 3 and q.norm.skeleton == surface.norm.skeleton:
                # Same consonant skeleton: a spelling variant (e.g. dropped final schwa), not a partial query.
                score, match = 0.95, "fuzzy"
            elif key.startswith(qkey):
                score, match = 0.85 + 0.1 * min(1.0, len(qkey) / max(len(key), 1)), "prefix"
            elif i in rooted_set:
                score, match = 0.8 + 0.1 * min(1.0, len(qkey) / max(len(key), 1)), "root"
            else:
                pair = self.matcher._pair(q, surface)
                # A shared consonant skeleton (Hinglish schwa-dropping: "amlpitt") is near-identical spelling.
                score = 0.92 if "phonetic-skeleton" in pair.via else pair.score * 0.95
                match = "fuzzy"
            prev = best.get(rec.code)
            if prev is None or score > prev.score:
                best[rec.code] = Suggestion(
                    match=match,
                    system=SYSTEM_NAMASTE,
                    code=rec.code,
                    display=rec.display,
                    matched_term=surface.text,
                    term_kind=surface.kind,
                    devanagari=rec.term_devanagari,
                    entity_type=rec.entity_type,
                    score=round(score, 4),
                )
        ranked = sorted(best.values(), key=lambda s: (-s.score, s.display))[:limit]
        for s in ranked:
            self.validate(SYSTEM_NAMASTE, s.code)
        return ranked

    def resolve(self, text: str) -> tuple[ResolvedSource | None, list[Suggestion]]:
        alternatives = self.suggest(text, limit=5)
        # Only an exact surface form or a near-identical spelling resolves; a prefix or root does not.
        top_match = alternatives[0].match if alternatives else None
        if top_match in (None, "prefix", "root") or alternatives[0].score < RESOLVE_MIN:
            typo = self._typo_resolution(text)
            if typo is None:
                return None, alternatives
            typo_rec, matched = typo
            return (
                ResolvedSource(
                    code=typo_rec.code,
                    display=typo_rec.display,
                    matched_term=matched,
                    method="typo",
                    score=TYPO_SCORE,
                    iast=typo_rec.term_iast,
                    devanagari=typo_rec.term_devanagari,
                ),
                alternatives,
            )
        top = alternatives[0]
        method: Literal["exact", "fuzzy"] = "exact" if top.score >= 1.0 else "fuzzy"
        rec = self.index.get(SYSTEM_NAMASTE, top.code)
        return (
            ResolvedSource(
                code=top.code,
                display=top.display,
                matched_term=top.matched_term,
                method=method,
                score=top.score,
                iast=rec.term_iast if rec else None,
                devanagari=rec.term_devanagari if rec else None,
            ),
            alternatives,
        )

    def _typo_resolution(self, text: str) -> tuple[ConceptRecord, str] | None:
        """A typed term within a small edit distance of exactly ONE concept's surface form resolves to it.

        Same policy as brand typos: none under 5 letters, 1 edit (incl. transposition) for 5-7, 2 for 8+.
        Several terms differ by a letter or two (Vataja / Pittaja Jvara, piles / hives), so the winner must be
        at least 2 edits closer than every other concept; otherwise the query stays unresolved and the user
        picks from "Did you mean ...?".
        """
        key = self.index.make_surface(text).norm.compact
        edits = 0 if len(key) < 5 else 1 if len(key) < 8 else 2
        if edits == 0:
            return None
        hits = process.extract(key, self._ac_keys, scorer=OSA.distance, score_cutoff=edits + 1, limit=None)
        if not hits or (best := min(h[1] for h in hits)) > edits:
            return None
        nearest: dict[str, int] = {}
        for _, dist, i in hits:
            code = self._ac_owner[i][0].code
            nearest[code] = min(dist, nearest.get(code, dist))
        winners = [c for c, dist in nearest.items() if dist == best]
        if len(winners) != 1 or any(dist <= best + 1 for c, dist in nearest.items() if c != winners[0]):
            return None
        i = next(h[2] for h in hits if h[1] == best)
        rec, si = self._ac_owner[i]
        self.validate(SYSTEM_NAMASTE, rec.code)
        return rec, rec.surfaces[si].text

    # ------------------------------------------------------------ mapping

    def map(self, *, text: str | None = None, code: str | None = None, top_k: int = 5) -> MapResult:
        if code:
            rec = self.index.get(SYSTEM_NAMASTE, code)
            if rec is None or rec.is_category:
                raise KeyError(code)
            query_text = text or rec.display
            q = self.index.make_surface(query_text)
            resolved: ResolvedSource | None = ResolvedSource(
                code=rec.code,
                display=rec.display,
                matched_term=rec.display,
                method="code",
                score=1.0,
                iast=rec.term_iast,
                devanagari=rec.term_devanagari,
            )
            alternatives: list[Suggestion] = []
            profile = self.matcher.profile_for_concept(rec, q if text else None)
        else:
            assert text is not None
            q = self.index.make_surface(text)
            resolved, alternatives = self.resolve(text)
            rec = self.index.get(SYSTEM_NAMASTE, resolved.code) if resolved else None
            profile = self.matcher.profile_for_concept(rec, q) if rec else self.matcher.profile_for_text(q)
        query = QueryInfo(text=q.text, normalised=q.norm.key, script=q.norm.script)

        ranked = self._rank(profile)
        relationships = infer_relationships(ranked, profile, self.matcher)
        bands = [band(c.confidence, self.thresholds) for c in ranked]
        # Explain only what is returned: the first top_k candidates above the low band.
        keep = [i for i, b in enumerate(bands) if b != "low"][:top_k]
        shown = [self._explain(ranked[i], relationships[i], profile, query) for i in keep]
        suppressed = sum(1 for b in bands[:top_k] if b == "low")

        if not shown:
            status, message = "no_match", "No safe automated match found"
        elif shown[0].band == "high":
            status, message = "suggested", "High-confidence suggestion; confirm with one click"
        else:
            status, message = "review", "Medium confidence: routed to the clinician review queue"
        return MapResult(
            query=query,
            resolved=resolved,
            alternatives=alternatives,
            status=status,
            message=message,
            candidates=shown,
            suppressed_low=suppressed,
            thresholds=self.thresholds,
            semantic_method=self.matcher.semantic.method,
            dataset_releases=self.index.releases,
            model_version=self.ranker.version,
        )

    def _rank(self, profile: SourceProfile) -> list[Candidate]:
        cands = self.matcher.candidates(profile)
        for c in cands:
            c.confidence = self.ranker.score(c.features, c.type_agree)
        if profile.namaste is not None and (entry := self.index.stage0.get(profile.namaste.code)):
            for c in cands:
                if c.target.code == entry.tm2_code:
                    c.stage0, c.stage0_relationship, c.confidence = entry.origin, entry.relationship, 1.0
        return sorted(cands, key=lambda c: (-c.confidence, c.target.code))

    def _explain(
        self, c: Candidate, relationship: str, profile: SourceProfile, query: QueryInfo
    ) -> Explanation:
        t = c.target
        self.validate(SYSTEM_TM2, t.code)
        namaste = None
        if profile.namaste is not None:
            self.validate(SYSTEM_NAMASTE, profile.namaste.code)
            namaste = CodeRef(
                system=self.system_uri(SYSTEM_NAMASTE),
                code=profile.namaste.code,
                display=profile.namaste.display,
            )
        f = c.features
        scores = Scores(
            exact=1.0 if c.stage0 else f["exact"],
            lexical=f["lexical"],
            ngram=f["ngram"],
            transliteration=f["transliteration"],
            semantic=f["semantic"],
            hierarchy=f["hierarchy"],
            final=round(c.confidence, 4),
        )
        b = band(c.confidence, self.thresholds)
        matched = [
            MatchedTerm(source=p.source.text, target=p.target.text, via="+".join(p.via)) for p in c.pairs
        ]
        if c.stage0:
            matched.insert(
                0, MatchedTerm(source=namaste.code if namaste else "", target=t.code, via=c.stage0)
            )
        src_type = profile.entity_type
        type_check = (
            "unknown source type"
            if src_type is None
            else f"{src_type}→{t.entity_type} {'✓' if src_type == t.entity_type else '✗'}"
        )
        evidence = Evidence(
            matched_tokens=c.matched_tokens,
            matched_root_terms=c.matched_roots,
            matched_terms=matched,
            shared_concepts=list(dict.fromkeys([*c.matched_roots, *map(display_word, c.shared_words)]))[:6],
            hierarchy_path=self.index.hierarchy_path(t),
            entity_type_check=type_check,
            transliteration_hit=f["transliteration"] > 0,
            stages_hit=[],
            baseline=c.stage0,
        )
        evidence.stages_hit = stages_hit(scores, c.stage0)
        return Explanation(
            query=query,
            namaste=namaste,
            tm2=TM2Ref(
                system=self.system_uri(SYSTEM_TM2), code=t.code, display=t.display, entity_type=t.entity_type
            ),
            biomedicine=self._biomedicine(t.code),
            confidence=round(c.confidence, 4),
            band=b,
            relationship=relationship,
            scores=scores,
            evidence=evidence,
            reasoning=build_reasoning(evidence, relationship, b, src_type, t.entity_type),
            dataset_releases=self.index.releases,
            model_version=self.ranker.version,
        )

    def _biomedicine(self, tm2_code: str) -> BiomedicineRef:
        links = sorted(
            self.index.links.get(tm2_code, []), key=lambda lk: (lk.status != "approved", lk.target_code)
        )
        if not links:
            return BiomedicineRef(message=NO_BIO_LINK)
        link = links[0]
        self.validate(SYSTEM_BIO, link.target_code)
        bio = self.index.get(SYSTEM_BIO, link.target_code)
        assert bio is not None
        return BiomedicineRef(
            system=self.system_uri(SYSTEM_BIO),
            code=bio.code,
            display=bio.display,
            # A traditional-medicine condition is never "equivalent" to a biomedical diagnosis.
            relationship="relatedto",
            source=link.origin,
            status=link.status,
        )


# ---------------------------------------------------------------- construction and caching


def gold_train_pairs(session: Session) -> list[tuple[str, str]]:
    rows = session.scalars(
        select(GoldMapping).where(GoldMapping.split == "train").order_by(GoldMapping.id)
    ).all()
    return [(g.namaste_code, g.tm2_code) for g in rows]


def build_engine(
    session: Session,
    settings: Settings | None = None,
    options: EngineOptions | None = None,
    save_artifact: bool = True,
    force_retrain: bool = False,
) -> MappingEngine:
    settings = settings or get_settings()
    split = None if settings.stage0_gold_split == "none" else settings.stage0_gold_split
    index = build_index(session, stage0_gold_split=split)
    semantic, status = build_semantic_backend(settings, index)
    matcher = Matcher(index, semantic, options)
    gold = latest_release(session, SOURCE_GOLD)
    releases = [*index.releases, *([gold.tag] if gold else [])]

    artifact_path = settings.ranker_artifact_path
    artifact = None if force_retrain or (options and options.disabled) else RankerArtifact.load(artifact_path)
    stale = (
        artifact is None
        or artifact.dataset_releases != releases
        or artifact.semantic_method != semantic.method
    )
    if stale:
        artifact = train_ranker(matcher, gold_train_pairs(session), releases)
        if save_artifact and not (options and options.disabled):
            try:
                artifact.save(artifact_path)
            except OSError as exc:
                logger.warning("could not save ranker artifact: %s", exc)
        logger.info("ranker trained", extra={"extra_fields": artifact.training.model_dump()})
    assert artifact is not None

    thresholds = artifact.thresholds
    admin_override = session.get(AppSetting, "thresholds")
    if admin_override is not None:
        thresholds = Thresholds(
            high=admin_override.value["high"], low=admin_override.value["low"], source="admin"
        )
    elif settings.t_high is not None or settings.t_low is not None:
        thresholds = Thresholds(
            high=settings.t_high if settings.t_high is not None else thresholds.high,
            low=settings.t_low if settings.t_low is not None else thresholds.low,
            source="override",
        )
    return MappingEngine(index, matcher, artifact, thresholds, status, settings)


class EngineHolder:
    """Process-wide engine, rebuilt when dataset releases or approved mappings change."""

    def __init__(self) -> None:
        self._engine: MappingEngine | None = None
        self._lock = threading.Lock()
        self.last_error: str | None = None

    def get(self, session: Session) -> MappingEngine:
        key = index_version_key(session)
        engine = self._engine
        if engine is not None and engine.index.version_key == key:
            return engine
        with self._lock:
            if self._engine is None or self._engine.index.version_key != key:
                try:
                    self._engine = build_engine(session)
                    self.last_error = None
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    raise
            return self._engine

    @property
    def current(self) -> MappingEngine | None:
        return self._engine


engine_holder = EngineHolder()
