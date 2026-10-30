"""Stage 4: hybrid re-ranker (logistic regression over stage features) + threshold calibration.

Trained ONLY on the gold TRAIN split, with the Stage-0 baseline disabled so the model learns from the
automated evidence. Thresholds are calibrated on 5-fold cross-validated predictions (grouped by
NAMASTE code), so they are not fitted on the same rows they are judged on. The artifact is plain JSON.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, Field
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold

from app.core.config import BACKEND_ROOT
from app.engine.gate import DEFAULT_T_HIGH, DEFAULT_T_LOW, Thresholds
from app.engine.matcher import FEATURES, TYPE_MISMATCH_PENALTY

if TYPE_CHECKING:
    from app.engine.matcher import Matcher

RANKER_VERSION = "ranker@1.0.0"
ARTIFACT_PATH = BACKEND_ROOT / "data" / "artifacts" / "ranker.json"
TARGET_HIGH_PRECISION = 0.95
MIN_HIGH_COVERAGE = 0.25
MAX_LOST_CORRECT = 0.05  # T_LOW may drop at most 5% of correct top-1 suggestions
MIN_T_HIGH = 0.50  # never call anything below 0.5 "high", whatever the data says
C_GRID = (0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0)


class CalibrationBin(BaseModel):
    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed: float


class TrainingSummary(BaseModel):
    queries: int
    pairs: int
    positives: int
    cv_top1: float
    cv_top5: float
    high_precision: float | None
    high_coverage: float
    absent_answer_suppressed: float | None = None
    regularization_c: float | None = None
    threshold_method: str


class RankerArtifact(BaseModel):
    version: str = RANKER_VERSION
    features: list[str]
    weights: list[float]
    intercept: float
    thresholds: Thresholds
    calibration: list[CalibrationBin] = Field(default_factory=list)
    training: TrainingSummary
    dataset_releases: list[str]
    semantic_method: str
    disabled_stages: list[str] = Field(default_factory=list)
    trained_at: str

    def score(self, features: dict[str, float], type_agree: float = 0.5) -> float:
        z = self.intercept + sum(
            w * features.get(f, 0.0) for f, w in zip(self.features, self.weights, strict=True)
        )
        p = 1.0 / (1.0 + math.exp(-z))
        return p * TYPE_MISMATCH_PENALTY if type_agree == 0.0 else p

    def save(self, path: Path = ARTIFACT_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path = ARTIFACT_PATH) -> RankerArtifact | None:
        if not path.exists():
            return None
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


def training_rows(matcher: Matcher, gold: list[tuple[str, str]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Feature matrix X, labels y, query group ids, over every TM2 candidate of every gold source.
    Rows of type-mismatched candidates are kept; the type rule is applied after the blend."""
    xs: list[list[float]] = []
    ys: list[int] = []
    groups: list[int] = []
    for g, (namaste_code, tm2_code) in enumerate(gold):
        rec = matcher.index.get("namaste", namaste_code)
        if rec is None:
            continue
        for c in matcher.candidates(matcher.profile_for_concept(rec)):
            xs.append([c.features[f] for f in FEATURES])
            ys.append(int(c.target.code == tm2_code))
            groups.append(g)
    return np.array(xs, dtype=float), np.array(ys), np.array(groups)


def _top_k_hits(
    probs: np.ndarray, y: np.ndarray, groups: np.ndarray
) -> tuple[list[float], list[bool], list[int]]:
    """Per query: top-1 probability, whether top-1 is correct, rank of the correct candidate (1-based)."""
    top_p, top_ok, ranks = [], [], []
    for g in np.unique(groups):
        mask = groups == g
        p, labels = probs[mask], y[mask]
        order = np.argsort(-p)
        top_p.append(float(p[order[0]]))
        top_ok.append(bool(labels[order[0]]))
        hit = np.flatnonzero(labels[order])
        ranks.append(int(hit[0]) + 1 if hit.size else 10**6)
    return top_p, top_ok, ranks


def absent_answer_top1(probs: np.ndarray, y: np.ndarray, groups: np.ndarray) -> list[float]:
    """Top-1 probability per query after removing its correct target: how confident the model is when the
    right answer is not in the dataset. These act as negatives when calibrating thresholds."""
    out: list[float] = []
    for g in np.unique(groups):
        mask = (groups == g) & (y == 0)
        if mask.any():
            out.append(float(probs[mask].max()))
    return out


def calibrate(
    top_p: list[float], top_ok: list[bool], absent_p: list[float] | None = None
) -> tuple[Thresholds, float | None, float, str]:
    """Lowest T_HIGH (>= 0.5) whose top-1 precision >= target at >= minimum coverage, judged on real queries
    plus absent-answer queries (always wrong); T_LOW keeps >= 95% of correct top-1 suggestions."""
    absent = list(absent_p or [])
    p = np.array(list(top_p) + absent)
    ok = np.array(list(top_ok) + [False] * len(absent))
    n = len(p)
    if n == 0:
        return Thresholds(source="default"), None, 0.0, "no training queries"
    t_high: float | None = None
    n_real = len(top_p)
    for t in np.round(np.arange(MIN_T_HIGH, 0.995, 0.01), 2):
        sel = p >= t
        covered = (np.array(top_p) >= t).mean() if n_real else 0.0
        if sel.sum() and ok[sel].mean() >= TARGET_HIGH_PRECISION and covered >= MIN_HIGH_COVERAGE:
            t_high = float(t)
            break
    method = (
        f"precision>={TARGET_HIGH_PRECISION} at coverage>={MIN_HIGH_COVERAGE}, CV top-1 incl. "
        f"{len(absent)} absent-answer queries; floor {MIN_T_HIGH}"
    )
    if t_high is None:
        t_high, method = DEFAULT_T_HIGH, "default (calibration target not reachable)"
    correct = np.sort(np.array(top_p)[np.array(top_ok, dtype=bool)]) if n_real else np.array([])
    t_low = (
        float(np.floor(correct[int(MAX_LOST_CORRECT * len(correct))] * 100) / 100)
        if len(correct)
        else DEFAULT_T_LOW
    )
    t_low = min(max(t_low, 0.20), t_high - 0.05)
    sel = p >= t_high
    precision = float(ok[sel].mean()) if sel.sum() else None
    coverage = float((np.array(top_p) >= t_high).mean()) if n_real else 0.0
    return Thresholds(high=t_high, low=round(t_low, 2), source="calibrated"), precision, coverage, method


def reliability(probs: np.ndarray, y: np.ndarray, bins: int = 10) -> list[CalibrationBin]:
    out: list[CalibrationBin] = []
    edges = np.linspace(0, 1, bins + 1)
    for lo, hi in pairwise(edges):
        mask = (probs >= lo) & (probs < hi if hi < 1 else probs <= hi)
        if mask.sum():
            out.append(
                CalibrationBin(
                    lower=round(float(lo), 2),
                    upper=round(float(hi), 2),
                    count=int(mask.sum()),
                    mean_predicted=round(float(probs[mask].mean()), 4),
                    observed=round(float(y[mask].mean()), 4),
                )
            )
    return out


def train_ranker(matcher: Matcher, gold_train: list[tuple[str, str]], releases: list[str]) -> RankerArtifact:
    X, y, groups = training_rows(matcher, gold_train)
    if len(X) == 0 or y.sum() == 0 or y.sum() == len(y):
        raise ValueError("not enough training data to fit the ranker (need gold train rows with candidates)")

    def fit(Xf: np.ndarray, yf: np.ndarray, c: float) -> LogisticRegression:
        return LogisticRegression(C=c, max_iter=5000).fit(Xf, yf)

    folds = min(5, len(np.unique(groups)))

    def cross_val(c: float) -> np.ndarray:
        probs = np.zeros(len(y))
        for train_idx, test_idx in GroupKFold(n_splits=folds).split(X, y, groups):
            if y[train_idx].sum() == 0:
                continue
            probs[test_idx] = fit(X[train_idx], y[train_idx], c).predict_proba(X[test_idx])[:, 1]
        return probs

    # Regularisation chosen by grouped CV: best top-1, then best log-loss on the true targets. Only models
    # whose weights are all >= 0 are eligible, so more evidence can never lower confidence (explainability).
    best: tuple[float, float, float, np.ndarray] | None = None
    for c in C_GRID:
        if (fit(X, y, c).coef_[0] < 0).any():
            continue
        probs = cross_val(c)
        top1 = float(np.mean(_top_k_hits(probs, y, groups)[1]))
        logloss = float(-np.mean(np.log(np.clip(np.where(y == 1, probs, 1 - probs), 1e-9, 1))))
        if best is None or (top1, -logloss) > (best[1], -best[2]):
            best = (c, top1, logloss, probs)
    if best is None:  # every C produced a negative weight: fall back to the strongest regularisation
        best = (C_GRID[0], 0.0, 0.0, cross_val(C_GRID[0]))
    chosen_c, _, _, cv_probs = best
    top_p, top_ok, ranks = _top_k_hits(cv_probs, y, groups)
    absent = absent_answer_top1(cv_probs, y, groups)
    thresholds, precision, coverage, method = calibrate(top_p, top_ok, absent)

    model = fit(X, y, chosen_c)
    return RankerArtifact(
        features=list(FEATURES),
        weights=[round(float(w), 6) for w in model.coef_[0]],
        intercept=round(float(model.intercept_[0]), 6),
        thresholds=thresholds,
        calibration=reliability(cv_probs, y),
        training=TrainingSummary(
            queries=len(top_p),
            pairs=len(y),
            positives=int(y.sum()),
            cv_top1=round(float(np.mean(top_ok)), 4),
            cv_top5=round(float(np.mean([r <= 5 for r in ranks])), 4),
            high_precision=None if precision is None else round(precision, 4),
            high_coverage=round(coverage, 4),
            absent_answer_suppressed=(
                round(float(np.mean(np.array(absent) < thresholds.low)), 4) if absent else None
            ),
            regularization_c=chosen_c,
            threshold_method=method,
        ),
        dataset_releases=releases,
        semantic_method=matcher.semantic.method,
        disabled_stages=sorted(matcher.options.disabled),
        trained_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
