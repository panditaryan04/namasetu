from __future__ import annotations

from app.eval.intercomparison import typo
from app.eval.metrics import QueryOutcome, failures, rank_metrics, wilson
from app.eval.report import README_END, README_START, fmt, readme_block, replace_block


def test_rank_metrics_counts_ranks_bands_and_relationships() -> None:
    outcomes = [
        QueryOutcome("q1", "A", ["A", "B"], 0.9, "high", True),
        QueryOutcome("q2", "A", ["B", "A"], 0.5, "medium", None),
        QueryOutcome("q3", "A", ["B", "C"], 0.1, "low", None),
        QueryOutcome("q4", "A", [], None, None, None),
    ]
    m = rank_metrics(outcomes)
    assert m["queries"] == 4
    assert m["top1"] == 0.25
    assert m["top5"] == 0.5
    assert m["mrr"] == round((1 + 0.5) / 4, 4)
    assert m["coverage"] == 0.75
    assert m["high_band_precision"] == 1.0
    assert m["review_queue_rate"] == 0.25
    assert m["no_match_rate"] == 0.5  # low band and nothing ranked both count as "no match"
    assert m["top1_shown"] == 0.25
    assert m["relationship_accuracy"] == 1.0
    assert [f["query"] for f in failures(outcomes)] == ["q2", "q3", "q4"]


def test_rank_metrics_without_bands_omits_gate_metrics() -> None:
    m = rank_metrics([QueryOutcome("q", "A", ["A"])])
    assert m["top1"] == 1.0 and "high_band_precision" not in m
    assert rank_metrics([]) == {"queries": 0}


def test_wilson_interval_is_honest_for_small_samples() -> None:
    lo, hi = wilson(11, 11)  # type: ignore[misc]
    assert 0.7 < lo < 0.75 and hi == 1.0
    lo, hi = wilson(50, 100)  # type: ignore[misc]
    assert 0.40 < lo < 0.41 and 0.59 < hi < 0.60
    assert wilson(0, 0) is None


def test_typo_is_deterministic_and_keeps_first_letter() -> None:
    assert typo("Vatavyadhi", 1, "seed") == typo("Vatavyadhi", 1, "seed")
    for seed in map(str, range(30)):
        t = typo("Amlapitta", 2, seed)
        assert t[0] == "A" and t != "Amlapitta"
    assert typo("ab", 1, "x") == "ab"  # too short to edit safely


def test_report_formatting_and_readme_markers() -> None:
    assert fmt(1.0) == "1.0" and fmt(0.9091) == "0.909" and fmt(None) == "—"
    assert fmt(0.00022) == "0.00022" and fmt([0.7412, 1.0]) == "0.74–1.00"
    text = f"intro\n{README_START}\nold\n{README_END}\nend"
    out = replace_block(text, "NEW")
    assert out == "intro\nNEW\nend"
    _ = readme_block  # rendered end-to-end in tests/integration/test_evaluation.py
