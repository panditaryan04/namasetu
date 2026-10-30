"""Stage 3: semantic similarity.

SEMANTIC_MODE=full | onnx-int8: multilingual sentence embeddings (default
paraphrase-multilingual-MiniLM-L12-v2), L2-normalised, in a FAISS IndexFlatIP. Concept vectors are
precomputed by scripts/build_index.py into data/artifacts/embeddings/; a query that resolves to a
NAMASTE concept reuses that concept's precomputed vector, so no live inference is needed for it.

SEMANTIC_MODE=off (default), or whenever the model / vectors are unavailable: an IDF-weighted overlap
of the English glosses, titles, index terms and definitions on both sides. It never crashes; /health
reports which method is active.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from app.core.config import BACKEND_ROOT, Settings

if TYPE_CHECKING:
    from app.engine.index import ConceptRecord, TerminologyIndex
    from app.engine.matcher import SourceProfile

logger = logging.getLogger("namasetu.semantic")

EMBEDDINGS_DIR = BACKEND_ROOT / "data" / "artifacts" / "embeddings"


class SemanticBackend(Protocol):
    method: str

    def scores(self, profile: SourceProfile, targets: list[ConceptRecord]) -> np.ndarray: ...

    def top_indices(self, profile: SourceProfile, k: int) -> list[int]: ...


@dataclass
class SemanticStatus:
    mode: str  # requested SEMANTIC_MODE
    method: str  # what is actually running
    degraded: bool
    detail: str


class LexicalOverlapBackend:
    """IDF-weighted Jaccard over content words. Deterministic, dependency-free, multilingual via folding."""

    method = "weighted-lexical-overlap"

    def __init__(self, index: TerminologyIndex) -> None:
        self.index = index

    def scores(self, profile: SourceProfile, targets: list[ConceptRecord]) -> np.ndarray:
        from app.engine.matcher import weighted_jaccard

        return np.array([weighted_jaccard(profile.bag, t.bag, self.index) for t in targets], dtype=float)

    def top_indices(self, profile: SourceProfile, k: int) -> list[int]:
        s = self.scores(profile, self.index.tm2_targets)
        return [int(i) for i in np.argsort(-s)[:k]]


def concept_text(rec: ConceptRecord) -> str:
    parts = [
        rec.display,
        rec.term_en or "",
        rec.term_iast or "",
        rec.term_devanagari or "",
        rec.definition or "",
    ]
    parts += [s.text for s in rec.surfaces if s.kind in {"english", "index_term", "hinglish"}]
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return ". ".join(seen)


class Encoder(Protocol):
    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray: ...


class SentenceTransformerEncoder:
    def __init__(self, model_name: str, mode: str, onnx_file: str | None) -> None:
        from sentence_transformers import SentenceTransformer

        kwargs: dict[str, Any] = {}
        if mode == "onnx-int8":
            kwargs = (
                {"backend": "onnx", "model_kwargs": {"file_name": onnx_file}}
                if onnx_file
                else {"backend": "onnx"}
            )
        self.model = SentenceTransformer(model_name, **kwargs)
        self.prefix = ("query: ", "passage: ") if "e5" in model_name.lower() else ("", "")

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        prefix = self.prefix[0] if is_query else self.prefix[1]
        vecs = self.model.encode(
            [prefix + t for t in texts], normalize_embeddings=True, convert_to_numpy=True
        )
        return np.asarray(vecs, dtype=np.float32)


class EmbeddingBackend:
    def __init__(
        self,
        index: TerminologyIndex,
        vectors: np.ndarray,
        keys: list[tuple[str, str]],
        model_name: str,
        encoder: Encoder | None,
    ) -> None:
        self.index = index
        self.method = f"embedding:{model_name}"
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        self.vectors = (vectors / np.clip(norms, 1e-12, None)).astype(np.float32)
        self.row = {k: i for i, k in enumerate(keys)}
        self.encoder = encoder
        self.fallback = LexicalOverlapBackend(index)
        self.tm2_rows = np.array([self.row.get((t.system, t.code), -1) for t in index.tm2_targets])
        self._faiss = None
        try:
            import faiss

            present = self.tm2_rows[self.tm2_rows >= 0]
            self._faiss = faiss.IndexFlatIP(self.vectors.shape[1])
            self._faiss.add(self.vectors[present])
            self._faiss_rows = np.flatnonzero(self.tm2_rows >= 0)
        except ImportError:
            logger.info("faiss not installed; using numpy inner product")

    def _query_vector(self, profile: SourceProfile) -> np.ndarray | None:
        if profile.namaste is not None:
            row = self.row.get((profile.namaste.system, profile.namaste.code))
            if row is not None:
                vec: np.ndarray = self.vectors[row]
                return vec
        if self.encoder is None:
            return None
        text = ". ".join(s.text for s in profile.surfaces)
        encoded: np.ndarray = self.encoder.encode([text], is_query=True)[0]
        return encoded

    def scores(self, profile: SourceProfile, targets: list[ConceptRecord]) -> np.ndarray:
        q = self._query_vector(profile)
        if q is None:
            return self.fallback.scores(profile, targets)
        rows = [self.row.get((t.system, t.code)) for t in targets]
        out = np.zeros(len(targets))
        for i, r in enumerate(rows):
            if r is not None:
                out[i] = max(0.0, float(self.vectors[r] @ q))
        return out

    def top_indices(self, profile: SourceProfile, k: int) -> list[int]:
        q = self._query_vector(profile)
        if q is None:
            return self.fallback.top_indices(profile, k)
        if self._faiss is not None:
            _, idx = self._faiss.search(q.reshape(1, -1).astype(np.float32), k)
            return [int(self._faiss_rows[i]) for i in idx[0] if i >= 0]
        s = self.scores(profile, self.index.tm2_targets)
        return [int(i) for i in np.argsort(-s)[:k]]


def embeddings_path(model_name: str) -> Path:
    return EMBEDDINGS_DIR / model_name.replace("/", "__")


def save_embeddings(
    model_name: str, keys: list[tuple[str, str]], vectors: np.ndarray, releases: list[str]
) -> Path:
    path = embeddings_path(model_name)
    path.mkdir(parents=True, exist_ok=True)
    np.save(path / "vectors.npy", vectors.astype(np.float32))
    (path / "meta.json").write_text(
        json.dumps({"model": model_name, "keys": keys, "dataset_releases": releases}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def build_semantic_backend(
    settings: Settings, index: TerminologyIndex, encoder: Encoder | None = None
) -> tuple[SemanticBackend, SemanticStatus]:
    """Pick the best backend for SEMANTIC_MODE, degrading (never crashing) when something is missing."""
    mode = settings.semantic_mode
    lexical = LexicalOverlapBackend(index)
    if mode == "off":
        return lexical, SemanticStatus(mode, lexical.method, False, "SEMANTIC_MODE=off")

    path = embeddings_path(settings.semantic_model)
    if not (path / "vectors.npy").exists():
        return lexical, SemanticStatus(
            mode, lexical.method, True, f"no precomputed vectors at {path.name}; run scripts/build_index.py"
        )
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    if meta.get("dataset_releases") != index.releases:
        return lexical, SemanticStatus(
            mode, lexical.method, True, "vectors are from other dataset releases; rebuild"
        )
    if encoder is None:
        try:
            encoder = SentenceTransformerEncoder(settings.semantic_model, mode, settings.semantic_onnx_file)
            detail = "live encoder loaded"
        except Exception as exc:  # noqa: BLE001 - any import/model failure degrades to precomputed-only
            detail = f"no live encoder ({type(exc).__name__}); free-text queries use lexical overlap"
            encoder = None
    else:
        detail = "injected encoder"
    vectors = np.load(path / "vectors.npy")
    keys: list[tuple[str, str]] = [(str(k[0]), str(k[1])) for k in meta["keys"]]
    backend = EmbeddingBackend(index, vectors, keys, settings.semantic_model, encoder)
    return backend, SemanticStatus(mode, backend.method, encoder is None, detail)
