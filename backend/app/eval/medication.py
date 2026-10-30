"""Medication metrics computed by running the code on labelled SAMPLE data (no hand-typed numbers).

- brand resolution: exact, case / dosage-form variants, seeded 1-edit typos, and false resolutions of
  names that are not in the catalogue (must be 0: unknown brands are never guessed)
- salt synonyms and Ayush ingredient names in every script resolve to the canonical ingredient
- free-text line resolution accuracy (medication_line_gold.csv)
- interaction detection precision / recall incl. multi-hop and time-window cases (interaction_gold_cases.csv)
- safety counters that must be zero: alerts without a citation, "no interaction" messages without the
  dataset caveat.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import SAMPLE_DIR
from app.db.models import Patient
from app.eval.intercomparison import typo
from app.medication.brand_resolver import max_edits, resolve_brand
from app.medication.catalog import MedicationCatalog
from app.medication.constituents import db_dictionary
from app.medication.history import resolve_line
from app.medication.interactions import check

# Names that are not brands in the catalogue (invented for this test). Any resolution is a false resolution.
NOT_IN_CATALOGUE = [
    "Zorvanta",
    "Neurocalm Forte",
    "Paxlorin",
    "Glucotrim XR",
    "Dermavex",
    "Ostenix 70",
    "Calmora",
]


def _rows(name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO((SAMPLE_DIR / name).read_text(encoding="utf-8"))))


def _rate(ok: int, n: int) -> float | None:
    return round(ok / n, 4) if n else None


def brand_resolution(catalog: MedicationCatalog) -> dict[str, Any]:
    by_id = {rec.row_id: code for code, rec in catalog.by_code.items()}
    variants: dict[str, list[tuple[str, str]]] = {"exact": [], "case / dosage form": [], "typo (1 edit)": []}
    for name, key, med_id in catalog.brands:
        expected = by_id[med_id]
        variants["exact"].append((name, expected))
        variants["case / dosage form"] += [(name.upper(), expected), (f"Tab {name} BD", expected)]
        # Only where the policy allows a typo: the strength-free letters must be long enough (max_edits).
        if max_edits(re.sub(r"[^a-z]", "", key)) >= 1:
            variants["typo (1 edit)"].append((typo(name, 1, name), expected))
    out: dict[str, Any] = {}
    failures = []
    for label, cases in variants.items():
        ok = 0
        for text, expected in cases:
            got = resolve_brand(catalog, text).medication_code
            ok += got == expected
            if got != expected:
                failures.append({"query": text, "expected": expected, "got": got})
        out[label] = {"cases": len(cases), "accuracy": _rate(ok, len(cases))}
    false = [n for n in NOT_IN_CATALOGUE if resolve_brand(catalog, n).found]
    out["not in catalogue"] = {
        "cases": len(NOT_IN_CATALOGUE),
        "false_resolutions": len(false),
        "examples": false,
    }
    out["failures"] = failures
    return out


def ingredient_names(session: Session, catalog: MedicationCatalog) -> dict[str, Any]:
    """Every recorded name of every ingredient resolves back to it, grouped by script / name kind."""
    dictionary, _ = db_dictionary(session)
    groups: dict[str, list[bool]] = {}
    failures = []
    owners: dict[str, set[str]] = {}  # a name recorded for several ingredients (e.g. a herb and its extract)
    for ing in catalog.ingredients.values():
        for n in [ing.canonical_name, ing.iast, ing.devanagari, *(x["term"] for x in ing.synonyms)]:
            if n:
                owners.setdefault(n.casefold(), set()).add(ing.key)

    def probe(group: str, text: str | None, key: str) -> None:
        if not text:
            return
        got = dictionary.resolve(text)
        ok = got in owners[text.casefold()]  # any ingredient that legitimately carries this name
        groups.setdefault(group, []).append(ok)
        if not ok:
            failures.append({"name": text, "group": group, "expected": key, "got": got})

    for ing in catalog.ingredients.values():
        prefix = "Salt" if ing.kind == "salt" else "Ayush ingredient"
        probe(f"{prefix}: canonical", ing.canonical_name, ing.key)
        probe(f"{prefix}: IAST", ing.iast, ing.key)
        probe(f"{prefix}: Devanagari", ing.devanagari, ing.key)
        for syn in ing.synonyms:
            kind = {
                "synonyms": "synonym",
                "common": "common / Hinglish",
                "botanical": "botanical",
                "devanagari": "Devanagari (variant)",
                "iast": "IAST (variant)",
            }.get(syn["kind"], syn["kind"])
            probe(f"{prefix}: {kind}", syn["term"], ing.key)
    return {
        "groups": {
            g: {"cases": len(v), "accuracy": _rate(sum(v), len(v))} for g, v in sorted(groups.items())
        },
        "shared_names": sorted(n for n, keys in owners.items() if len(keys) > 1),
        "failures": failures[:30],
    }


def line_resolution(session: Session, catalog: MedicationCatalog) -> dict[str, Any]:
    failures, correct = [], 0
    rows = _rows("medication_line_gold.csv")
    for row in rows:
        res = resolve_line(session, catalog, row["line"])
        got = res.medication_code or (
            catalog.ingredients[res.ingredient_ids[0]].canonical_name if res.ingredient_ids else ""
        )
        ok = res.kind == row["expected_kind"] and got == row["expected"]
        correct += ok
        if not ok:
            failures.append(
                {
                    "line": row["line"],
                    "expected": f"{row['expected_kind']}:{row['expected']}",
                    "got": f"{res.kind}:{got}",
                }
            )
    return {
        "cases": len(rows),
        "correct": correct,
        "accuracy": round(correct / len(rows), 4) if rows else None,
        "failures": failures,
    }


def _alert_key(kind: str, agents: list[str]) -> str:
    return f"duplicate:{agents[0]}" if kind == "duplicate" else "interaction:" + "|".join(sorted(agents))


def interaction_detection(session: Session, catalog: MedicationCatalog) -> dict[str, Any]:
    tp = fp = fn = 0
    by_kind = {k: [0, 0, 0] for k in ("duplicate", "interaction")}  # tp, fp, fn
    uncited = caveat_missing = 0
    failures = []
    rows = _rows("interaction_gold_cases.csv")
    for row in rows:
        patient = session.get(Patient, row["patient_id"])
        out = check(
            session,
            catalog,
            [row["candidate_code"]],
            patient=patient,
            as_of=date.fromisoformat(row["as_of"]),
            persist=False,
        )
        got = {_alert_key(a.kind, a.agents) for a in out.alerts}
        expected = set()
        for e in filter(None, row["expected_alerts"].split(";")):
            kind, rest = e.split(":", 1)
            expected.add(e if kind == "duplicate" else "interaction:" + "|".join(sorted(rest.split("|"))))
        tp, fp, fn = tp + len(got & expected), fp + len(got - expected), fn + len(expected - got)
        for k, row_counts in by_kind.items():
            got_k = {x for x in got if x.startswith(k)}
            exp_k = {x for x in expected if x.startswith(k)}
            row_counts[0] += len(got_k & exp_k)
            row_counts[1] += len(got_k - exp_k)
            row_counts[2] += len(exp_k - got_k)
        uncited += sum(1 for a in out.alerts if not a.citation)
        caveat_missing += int(not out.alerts and "loaded dataset" not in out.message)
        if got != expected:
            failures.append(
                {
                    "case": f"{row['patient_id']} + {row['candidate_code']}",
                    "missing": sorted(expected - got),
                    "unexpected": sorted(got - expected),
                }
            )
    return {
        "cases": len(rows),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "by_kind": {
            k: {
                "true_positives": a,
                "false_positives": b,
                "false_negatives": c,
                "precision": _rate(a, a + b),
                "recall": _rate(a, a + c),
            }
            for k, (a, b, c) in by_kind.items()
        },
        "safety": {
            "alerts_without_citation": uncited,
            "no_interaction_without_dataset_caveat": caveat_missing,
        },
        "failures": failures,
    }
