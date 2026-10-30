"""Calibration: reliability of the hybrid confidence on held-out TEST pairs, plus the chosen thresholds."""

from __future__ import annotations

from typing import Any

import numpy as np

from app.db.models import SYSTEM_NAMASTE, GoldMapping
from app.engine.ranker import reliability
from app.engine.service import MappingEngine


def calibration(engine: MappingEngine, rows: list[GoldMapping]) -> dict[str, Any]:
    probs, labels = [], []
    for g in rows:
        rec = engine.index.get(SYSTEM_NAMASTE, g.namaste_code)
        assert rec is not None
        for c in engine.matcher.candidates(engine.matcher.profile_for_concept(rec)):
            probs.append(engine.ranker.score(c.features, c.type_agree))
            labels.append(int(c.target.code == g.tm2_code))
    p, y = np.array(probs), np.array(labels)
    ece = 0.0
    bins = reliability(p, y)
    for b in bins:
        ece += b.count / len(p) * abs(b.mean_predicted - b.observed)
    return {
        "thresholds": engine.thresholds.model_dump(),
        "threshold_method": engine.ranker.training.threshold_method,
        "train_cv_reliability": [b.model_dump() for b in engine.ranker.calibration],
        "test_reliability": [b.model_dump() for b in bins],
        "test_pairs": len(p),
        "test_expected_calibration_error": round(ece, 4),
        "brier_score": round(float(np.mean((p - y) ** 2)), 5) if len(p) else None,
    }
