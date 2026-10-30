"""Build search artifacts: (1) multilingual concept embeddings when SEMANTIC_MODE != off, (2) the ranker.

Usage: python scripts/build_index.py
The ranker is trained on the gold TRAIN split only and written to data/artifacts/ranker.json together
with its calibrated thresholds and reliability table. Embeddings go to data/artifacts/embeddings/<model>/.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_sessionmaker
from app.engine.index import build_index
from app.engine.semantic import SentenceTransformerEncoder, concept_text, save_embeddings
from app.engine.service import build_engine


def build_embeddings() -> None:
    settings = get_settings()
    with get_sessionmaker()() as session:
        index = build_index(session)
    encoder = SentenceTransformerEncoder(
        settings.semantic_model, settings.semantic_mode, settings.semantic_onnx_file
    )
    records = list(index.concepts.values())
    vectors = encoder.encode([concept_text(r) for r in records])
    path = save_embeddings(
        settings.semantic_model, [(r.system, r.code) for r in records], np.asarray(vectors), index.releases
    )
    print(f"embeddings: {len(records)} concepts x {vectors.shape[1]} dims -> {path}")


def main() -> int:
    settings = get_settings()
    configure_logging("WARNING")
    if settings.semantic_mode != "off":
        build_embeddings()
    with get_sessionmaker()() as session:
        engine = build_engine(session, force_retrain=True)
    a = engine.ranker
    t = a.training
    print(f"ranker: {a.version}  semantic={a.semantic_method}  C={t.regularization_c}")
    print("  weights: " + ", ".join(f"{f}={w:.3f}" for f, w in zip(a.features, a.weights, strict=True)))
    print(f"  trained on {t.queries} train-split queries / {t.pairs} pairs")
    print(f"  grouped-CV top-1={t.cv_top1} top-5={t.cv_top5}")
    print(f"  thresholds: T_HIGH={a.thresholds.high} T_LOW={a.thresholds.low} ({t.threshold_method})")
    print(f"  high-band precision={t.high_precision} coverage={t.high_coverage}; "
          f"absent-answer suppressed={t.absent_answer_suppressed}")  # fmt: skip
    print(f"  -> {settings.ranker_artifact_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
