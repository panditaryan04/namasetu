"""Gold NAMASTE -> TM2 mappings with a fixed, seeded 80/20 train/test split."""

from __future__ import annotations

import csv
import io
import random
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from app.ingest.schemas import IngestReport, RowIssue

SPLIT_SEED = 20251026
TEST_FRACTION = 0.2
RELATIONSHIPS = {"equivalent", "wider", "narrower", "relatedto"}


class GoldIn(BaseModel):
    namaste_code: str
    tm2_code: str
    relationship: Literal["equivalent", "wider", "narrower", "relatedto"]
    split: Literal["train", "test"]


def assign_splits(
    codes: Iterable[str], seed: int = SPLIT_SEED, test_fraction: float = TEST_FRACTION
) -> dict[str, str]:
    """Deterministic split by NAMASTE code: same codes + seed always give the same split."""
    ordered = sorted(set(codes))
    rng = random.Random(seed)  # noqa: S311 - reproducible split, not security
    shuffled = ordered[:]
    rng.shuffle(shuffled)
    n_test = round(len(shuffled) * test_fraction)
    test = set(shuffled[:n_test])
    return {c: ("test" if c in test else "train") for c in ordered}


def parse_gold(rows: Iterable[Mapping[str, Any]]) -> tuple[list[GoldIn], IngestReport]:
    report = IngestReport(source="gold")
    raw: list[tuple[int, str, str, str, str | None]] = []
    for idx, row in enumerate(rows, start=2):
        report.total_rows += 1
        src = (row.get("namaste_code") or "").strip()
        tgt = (row.get("tm2_code") or "").strip()
        rel = (row.get("relationship") or "equivalent").strip().lower()
        split = (row.get("split") or "").strip().lower() or None
        if not src or not tgt:
            report.rejected.append(RowIssue(row=idx, code=src or None, problem="missing code"))
            continue
        if rel not in RELATIONSHIPS:
            report.rejected.append(RowIssue(row=idx, code=src, problem=f"bad relationship '{rel}'"))
            continue
        if split not in (None, "train", "test"):
            report.rejected.append(RowIssue(row=idx, code=src, problem=f"bad split '{split}'"))
            continue
        raw.append((idx, src, tgt, rel, split))

    computed = assign_splits(r[1] for r in raw)
    if any(r[4] is None for r in raw):
        report.warnings.append(f"split column empty for some rows; assigned with seed {SPLIT_SEED}")
    gold = [
        GoldIn(namaste_code=src, tm2_code=tgt, relationship=rel, split=split or computed[src])
        for _, src, tgt, rel, split in raw
    ]
    report.accepted = len(gold)
    return gold, report


def load_gold_file(path: Path) -> tuple[list[GoldIn], IngestReport]:
    return parse_gold(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
