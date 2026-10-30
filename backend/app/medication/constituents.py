"""Constituent (herb -> phytochemical / mineral) and interaction datasets, ingested as one versioned release.

Every row must cite a source and reference known agents; otherwise it is rejected. Nothing is inferred:
an interaction exists only if a cited row says so.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import SOURCE_INTERACTIONS, Constituent, Ingredient, InteractionRule
from app.ingest.release import ReleaseResult, _close_release, _open_release, checksum_of, latest_release
from app.ingest.schemas import IngestReport, RowIssue
from app.medication.loaders import IngredientDictionary, IngredientIn, slug

SEVERITIES = {"major", "moderate", "minor"}
EVIDENCE = {"established", "probable", "theoretical", "case-report"}
AGENT_TYPES = {"salt", "herb", "constituent"}


def constituent_key(name: str) -> str:
    return f"constituent:{slug(name)}"


def _s(row: Mapping[str, Any], k: str) -> str | None:
    v = row.get(k)
    v = str(v).strip() if v is not None else ""
    return v or None


def db_dictionary(session: Session) -> tuple[IngredientDictionary, dict[str, Ingredient]]:
    rows = list(session.scalars(select(Ingredient)))
    ins = [
        IngredientIn(
            key=i.key,
            kind=i.kind,
            canonical_name=i.canonical_name,
            botanical_name=i.botanical_name,
            iast=i.iast,
            devanagari=i.devanagari,
            synonyms=list(i.synonyms or []),
        )
        for i in rows
    ]
    return IngredientDictionary(ins), {i.key: i for i in rows}


def ingest_interaction_data(
    session: Session,
    constituent_rows: Iterable[Mapping[str, Any]],
    rule_rows: Iterable[Mapping[str, Any]],
    *,
    version: str,
    is_sample: bool,
    actor: str = "system",
) -> ReleaseResult:
    dictionary, by_key = db_dictionary(session)
    c_report, r_report = IngestReport(source="constituents"), IngestReport(source="interactions")

    constituents: list[dict[str, Any]] = []
    for idx, row in enumerate(constituent_rows, start=2):
        c_report.total_rows += 1
        ing_name, name, source = _s(row, "ingredient"), _s(row, "constituent"), _s(row, "source_title")
        if not (ing_name and name):
            c_report.rejected.append(RowIssue(row=idx, problem="missing ingredient or constituent"))
            continue
        if not source:
            c_report.rejected.append(RowIssue(row=idx, code=name, problem="no source citation"))
            continue
        key = dictionary.resolve(ing_name)
        if key is None or by_key[key].kind not in {"herb", "mineral"}:
            c_report.rejected.append(RowIssue(row=idx, code=ing_name, problem="unknown Ayush ingredient"))
            continue
        constituents.append(
            {
                "ingredient_key": key,
                "key": constituent_key(name),
                "name": name,
                "constituent_class": _s(row, "constituent_class"),
                "source_title": source,
                "source_edition": _s(row, "source_edition"),
                "source_page": _s(row, "source_page"),
            }
        )
        c_report.accepted += 1
    known_constituents = {c["key"]: c["name"] for c in constituents}

    def agent(kind: str | None, name: str | None) -> tuple[str, str] | None:
        if not name or kind not in AGENT_TYPES:
            return None
        if kind == "constituent":
            k = constituent_key(name)
            return (k, known_constituents[k]) if k in known_constituents else None
        key = dictionary.resolve(name, "salt" if kind == "salt" else "herb")
        if key is None or (by_key[key].kind == "salt") != (kind == "salt"):
            return None
        return key, by_key[key].canonical_name

    rules: list[dict[str, Any]] = []
    for idx, row in enumerate(rule_rows, start=2):
        r_report.total_rows += 1
        a = agent(_s(row, "agent_a_type"), _s(row, "agent_a"))
        b = agent(_s(row, "agent_b_type"), _s(row, "agent_b"))
        severity, evidence = _s(row, "severity"), _s(row, "evidence_level")
        citation, mechanism = _s(row, "citation"), _s(row, "mechanism_summary")
        problem = (
            "unknown agent"
            if a is None or b is None
            else (
                "no citation"
                if not citation
                else (
                    f"bad severity '{severity}'"
                    if severity not in SEVERITIES
                    else (
                        f"bad evidence level '{evidence}'"
                        if evidence not in EVIDENCE
                        else "no mechanism summary" if not mechanism else None
                    )
                )
            )
        )
        if problem:
            r_report.rejected.append(
                RowIssue(row=idx, code=f"{_s(row, 'agent_a')} x {_s(row, 'agent_b')}", problem=problem)
            )
            continue
        assert a and b
        rules.append(
            {
                "agent_a_type": _s(row, "agent_a_type"),
                "agent_a_key": a[0],
                "agent_a_name": a[1],
                "agent_b_type": _s(row, "agent_b_type"),
                "agent_b_key": b[0],
                "agent_b_name": b[1],
                "severity": severity,
                "evidence_level": evidence,
                "mechanism_summary": mechanism,
                "management_note": _s(row, "management_note"),
                "citation": citation,
                "source_version": _s(row, "source_version") or version,
            }
        )
        r_report.accepted += 1

    payload = {
        "constituents": sorted(constituents, key=lambda c: (c["ingredient_key"], c["key"])),
        "rules": sorted(rules, key=lambda r: (r["agent_a_key"], r["agent_b_key"])),
    }
    release, prev = _open_release(
        session,
        source=SOURCE_INTERACTIONS,
        version=version,
        checksum=checksum_of(payload),
        row_count=len(rules),
        is_sample=is_sample,
        report={"tables": {"constituents": c_report.model_dump(), "interactions": r_report.model_dump()}},
        actor=actor,
    )
    if release is None:
        assert prev is not None
        return ReleaseResult(release=prev, skipped=True)
    old_rules = {(r.agent_a_key, r.agent_b_key) for r in session.scalars(select(InteractionRule))}
    session.execute(delete(InteractionRule))
    session.execute(delete(Constituent))
    session.add_all(
        Constituent(
            ingredient_id=by_key[c["ingredient_key"]].id,
            release_id=release.id,
            **{k: v for k, v in c.items() if k != "ingredient_key"},
        )
        for c in constituents
    )
    session.add_all(InteractionRule(**r, release_id=release.id) for r in rules)
    new_rules = {(r["agent_a_key"], r["agent_b_key"]) for r in rules}
    return _close_release(
        session,
        release,
        prev,
        {
            "added": sorted(f"{a}|{b}" for a, b in new_rules - old_rules),
            "removed": sorted(f"{a}|{b}" for a, b in old_rules - new_rules),
            "constituents": len(constituents),
            "rejected_rows": len(c_report.rejected) + len(r_report.rejected),
        },
        actor,
        "admin",
    )


def interaction_dataset_tag(session: Session) -> str | None:
    rel = latest_release(session, SOURCE_INTERACTIONS)
    return rel.tag if rel else None
