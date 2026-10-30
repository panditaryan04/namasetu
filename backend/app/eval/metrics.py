"""Ranking metrics over (query, ranked candidate codes, gold code)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryOutcome:
    query: str
    gold: str
    ranked: list[str]  # candidate codes, best first (every candidate the system scored above 0)
    top_confidence: float | None = None
    top_band: str | None = None
    relationship_ok: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def rank(self) -> int | None:
        return self.ranked.index(self.gold) + 1 if self.gold in self.ranked else None


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95% Wilson score interval: honest error bars for small labelled sets."""
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def rank_metrics(outcomes: list[QueryOutcome]) -> dict[str, Any]:
    n = len(outcomes)
    if n == 0:
        return {"queries": 0}
    ranks = [o.rank for o in outcomes]
    out: dict[str, Any] = {
        "queries": n,
        "top1": round(sum(r == 1 for r in ranks) / n, 4),
        "top1_ci95": wilson(sum(r == 1 for r in ranks), n),
        "top5": round(sum(r is not None and r <= 5 for r in ranks) / n, 4),
        "mrr": round(sum(1 / r for r in ranks if r) / n, 4),
        "coverage": round(sum(bool(o.ranked) for o in outcomes) / n, 4),
    }
    bands = [o.top_band for o in outcomes]
    if any(b is not None for b in bands):
        high = [o for o in outcomes if o.top_band == "high"]
        out["high_band_precision"] = round(sum(o.rank == 1 for o in high) / len(high), 4) if high else None
        out["high_band_rate"] = round(len(high) / n, 4)
        out["review_queue_rate"] = round(sum(b == "medium" for b in bands) / n, 4)
        out["no_match_rate"] = round(sum(b in (None, "low") for b in bands) / n, 4)
        # correct AND surfaced to the user (high or medium band) - what a clinician actually sees first
        out["top1_shown"] = round(
            sum(o.rank == 1 and o.top_band in ("high", "medium") for o in outcomes) / n, 4
        )
    rel = [o.relationship_ok for o in outcomes if o.relationship_ok is not None]
    if rel:
        out["relationship_accuracy"] = round(sum(rel) / len(rel), 4)
    return out


def failures(outcomes: list[QueryOutcome], limit: int = 50) -> list[dict[str, Any]]:
    out = []
    for o in outcomes:
        if o.rank != 1:
            out.append({"query": o.query, "gold": o.gold, "rank": o.rank, "top": o.ranked[:3], **o.extra})
    return out[:limit]
