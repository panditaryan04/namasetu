"""In-memory terminology index built once from the database (rebuilt when a dataset release changes).

Holds every active concept with its normalised surface forms, hierarchy, token bags, approved
TM2->biomedicine links, the Stage-0 deterministic baseline, and a char n-gram TF-IDF model.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    SOURCE_GOLD,
    SOURCE_ICD11,
    SOURCE_NAMASTE,
    SOURCE_SYNONYMS,
    SYSTEM_BIO,
    SYSTEM_NAMASTE,
    SYSTEM_TM2,
    AppSetting,
    Concept,
    ConceptLink,
    ConceptTerm,
    DatasetRelease,
    GoldMapping,
    Mapping,
)
from app.engine.normalizer import FOLDED_STOPWORDS, Normalized, RootSplitter, clean_text, normalize
from app.ingest.release import current_roots, latest_release

# Roots too generic to count as shared evidence.
GENERIC_ROOTS = frozenset({"roga", "dosa"})
_WORD = re.compile(r"[a-z0-9]+")


def stem(token: str) -> str:
    """Tiny English stemmer for bag-of-words overlap: joints -> joint, stools -> stool."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


# Readable word for a folded, stemmed bag key ("sveling" -> "swelling"), for explanations only.
_DISPLAY_WORD: dict[str, str] = {}


def display_word(key: str) -> str:
    return _DISPLAY_WORD.get(key, key)


def content_bag(texts: Iterable[str]) -> Counter[str]:
    bag: Counter[str] = Counter()
    for t in texts:
        folded = _WORD.findall(normalize(t).key)
        plain = clean_text(t).split()
        aligned = len(plain) == len(folded)
        for i, tok in enumerate(folded):
            if tok not in FOLDED_STOPWORDS and len(tok) > 2:
                key = stem(tok)
                bag[key] += 1
                if aligned and plain[i].isascii():
                    _DISPLAY_WORD.setdefault(key, plain[i])
    return bag


@dataclass
class Surface:
    """One surface form of a concept (title, index term, synonym...)."""

    text: str
    kind: str  # primary | english | iast | devanagari | hinglish | index_term | query
    source: str  # dataset source, or "query"
    norm: Normalized


@dataclass
class ConceptRecord:
    system: str
    code: str
    display: str
    entity_type: str
    parent_code: str | None
    definition: str | None
    term_en: str | None
    term_iast: str | None
    term_devanagari: str | None
    discipline: str | None = None
    surfaces: list[Surface] = field(default_factory=list)
    ancestors: list[str] = field(default_factory=list)  # nearest first
    children: list[str] = field(default_factory=list)
    bag: Counter[str] = field(default_factory=Counter)  # words from glosses, titles, definition
    roots: frozenset[str] = frozenset()

    @property
    def is_category(self) -> bool:
        return self.entity_type == "category"


@dataclass(frozen=True)
class Stage0Entry:
    tm2_code: str
    relationship: str
    origin: str  # approved-mapping | official-crosswalk


class TerminologyIndex:
    def __init__(
        self,
        concepts: dict[tuple[str, str], ConceptRecord],
        splitter: RootSplitter,
        links: dict[str, list[ConceptLink]],
        stage0: dict[str, Stage0Entry],
        releases: list[str],
        version_key: tuple[int, ...],
    ) -> None:
        self.concepts = concepts
        self.splitter = splitter
        self.links = links
        self.stage0 = stage0
        self.releases = releases
        self.version_key = version_key
        self.valid_codes = frozenset(concepts)
        self.tm2_targets = [c for (s, _), c in concepts.items() if s == SYSTEM_TM2 and not c.is_category]
        self.namaste = [c for (s, _), c in concepts.items() if s == SYSTEM_NAMASTE and not c.is_category]

        # IDF over all concept bags (used by the weighted-overlap "semantic" fallback and hierarchy).
        df: Counter[str] = Counter()
        for c in concepts.values():
            df.update(set(c.bag))
        n = max(len(concepts), 1)
        self.idf = {tok: math.log((n + 1) / (cnt + 1)) + 1.0 for tok, cnt in df.items()}
        self.default_idf = math.log(n + 1) + 1.0

        # Char 3-4-gram TF-IDF over folded keys of every TM2 target surface.
        self.target_surfaces: list[tuple[int, Surface]] = [
            (i, s) for i, c in enumerate(self.tm2_targets) for s in c.surfaces
        ]
        self.ngram = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4), sublinear_tf=True)
        corpus = [s.norm.key or s.norm.clean for _, s in self.target_surfaces] or ["x"]
        self.target_matrix: csr_matrix = self.ngram.fit_transform(corpus)
        self.target_owner = np.array([i for i, _ in self.target_surfaces], dtype=np.int32)

    # -- lookups

    @cached_property
    def is_sample(self) -> bool:
        return any(t.startswith("icd11@SAMPLE") for t in self.releases)

    def release_version(self, source: str) -> str | None:
        return next((t.split("@", 1)[1] for t in self.releases if t.startswith(f"{source}@")), None)

    def get(self, system: str, code: str) -> ConceptRecord | None:
        return self.concepts.get((system, code))

    def is_valid(self, system: str, code: str) -> bool:
        return (system, code) in self.valid_codes

    def ancestors(self, rec: ConceptRecord) -> list[ConceptRecord]:
        return [a for code in rec.ancestors if (a := self.get(rec.system, code))]

    def hierarchy_path(self, rec: ConceptRecord) -> list[str]:
        return [a.display for a in reversed(self.ancestors(rec))] + [rec.display]

    def weight(self, token: str) -> float:
        return self.idf.get(token, self.default_idf)

    def make_surface(self, text: str, kind: str = "query", source: str = "query") -> Surface:
        return Surface(text=text, kind=kind, source=source, norm=normalize(text, self.splitter))


# ---------------------------------------------------------------- building


def index_version_key(session: Session) -> tuple[int, ...]:
    """Changes whenever any dataset release or approved mapping changes."""
    ids = [
        r.id if (r := latest_release(session, s)) else 0
        for s in (SOURCE_NAMASTE, SOURCE_ICD11, SOURCE_SYNONYMS, SOURCE_GOLD)
    ]
    mapping_max = session.scalar(select(Mapping.id).order_by(Mapping.id.desc()).limit(1)) or 0
    thresholds = session.get(AppSetting, "thresholds")
    links = session.execute(
        select(func.count(ConceptLink.id), func.max(ConceptLink.id)).where(ConceptLink.status == "approved")
    ).one()
    return (*ids, mapping_max, thresholds.version if thresholds else 0, links[0] or 0, links[1] or 0)


def build_index(session: Session, stage0_gold_split: str | None = "train") -> TerminologyIndex:
    """Load the active terminology. `stage0_gold_split` controls which gold rows act as the official
    crosswalk baseline: "train" (default; the test split stays held out), "all", or None."""
    splitter = RootSplitter(current_roots(session))
    rows = session.scalars(select(Concept).where(Concept.active.is_(True))).all()
    concepts: dict[tuple[str, str], ConceptRecord] = {}
    by_id: dict[int, ConceptRecord] = {}
    for c in rows:
        rec = ConceptRecord(
            system=c.system,
            code=c.code,
            display=c.display,
            entity_type=c.entity_type,
            parent_code=c.parent_code,
            definition=c.definition,
            term_en=c.term_en,
            term_iast=c.term_iast,
            term_devanagari=c.term_devanagari,
            discipline=c.discipline,
        )
        concepts[(c.system, c.code)] = rec
        by_id[c.id] = rec

    for term in session.scalars(select(ConceptTerm)).all():
        owner = by_id.get(term.concept_id)
        if owner is None:
            continue
        if any(s.text == term.term and s.kind == term.kind for s in owner.surfaces):
            continue
        owner.surfaces.append(
            Surface(text=term.term, kind=term.kind, source=term.source, norm=normalize(term.term, splitter))
        )

    for rec in concepts.values():
        seen: set[str] = set()
        node = rec
        while node.parent_code and node.parent_code not in seen:
            seen.add(node.parent_code)
            parent = concepts.get((rec.system, node.parent_code))
            if parent is None:
                break
            rec.ancestors.append(parent.code)
            node = parent
        if rec.parent_code and (parent := concepts.get((rec.system, rec.parent_code))):
            parent.children.append(rec.code)
        # Bag of words: English-language glosses, titles, index terms and the definition.
        texts = [s.text for s in rec.surfaces if s.kind in {"primary", "english", "index_term"}]
        rec.bag = content_bag([*texts, rec.definition or ""])
        rec.roots = frozenset(r for s in rec.surfaces for r in s.norm.roots if r not in GENERIC_ROOTS)

    links: dict[str, list[ConceptLink]] = {}
    for link in session.scalars(select(ConceptLink).where(ConceptLink.target_system == SYSTEM_BIO)).all():
        if (SYSTEM_BIO, link.target_code) in concepts:
            links.setdefault(link.source_code, []).append(link)

    stage0: dict[str, Stage0Entry] = {}
    if stage0_gold_split:
        q = select(GoldMapping)
        if stage0_gold_split != "all":
            q = q.where(GoldMapping.split == stage0_gold_split)
        for g in session.scalars(q).all():
            stage0[g.namaste_code] = Stage0Entry(g.tm2_code, g.relationship, "official-crosswalk")
    for m in session.scalars(
        select(Mapping).where(
            Mapping.status == "approved",
            Mapping.needs_review.is_(False),
            Mapping.source_system == SYSTEM_NAMASTE,
            Mapping.target_system == SYSTEM_TM2,
        )
    ).all():
        origin = "official-crosswalk" if m.reviewer == "official-crosswalk" else "approved-mapping"
        if origin == "official-crosswalk" and not stage0_gold_split:
            continue  # crosswalk baseline disabled (e.g. for evaluation without Stage 0)
        stage0[m.source_code] = Stage0Entry(m.target_code, m.relationship, origin)
    # Never let a baseline point at a code that is not in the dataset.
    stage0 = {
        k: v
        for k, v in stage0.items()
        if (SYSTEM_NAMASTE, k) in concepts and (SYSTEM_TM2, v.tm2_code) in concepts
    }

    releases = [
        r.tag
        for s in (SOURCE_NAMASTE, SOURCE_ICD11, SOURCE_SYNONYMS)
        if (r := latest_release(session, s)) is not None
    ]
    return TerminologyIndex(concepts, splitter, links, stage0, releases, index_version_key(session))


def release_tags(session: Session) -> list[str]:
    return [r.tag for r in session.scalars(select(DatasetRelease).order_by(DatasetRelease.id)).all()]
