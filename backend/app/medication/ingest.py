"""Medication reference release: synonyms + Ayush formulations + biomedical drugs + brands + condition links.

All five tables are ingested together as one DatasetRelease (source "medications") so cross references are
validated atomically. The raw bundle is kept in the release report, which lets an admin replace one table
(e.g. upload a new brand list) and re-run the whole ingest with a fresh diff.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    SOURCE_MEDICATIONS,
    Brand,
    Concept,
    Ingredient,
    Medication,
    MedicationIngredient,
    MedicationLink,
)
from app.engine.transliterator import fold
from app.fhir.audit_provenance import record_provenance
from app.ingest.release import ReleaseResult, _close_release, _open_release, checksum_of, latest_release
from app.ingest.schemas import IngestReport, RowIssue
from app.medication.loaders import (
    IngredientDictionary,
    MedicationIn,
    parse_brands,
    parse_drugs,
    parse_formulations,
    parse_links,
    parse_medication_synonyms,
)

BUNDLE_KEYS = ("synonyms", "formulations", "drugs", "brands", "links")


def brand_key(name: str) -> str:
    return fold(name).replace(" ", "")


def current_bundle(session: Session) -> dict[str, Any] | None:
    rel = latest_release(session, SOURCE_MEDICATIONS)
    return (rel.report or {}).get("bundle") if rel else None


def ingest_medications(
    session: Session,
    bundle: dict[str, Any],
    *,
    version: str,
    is_sample: bool,
    actor: str = "system",
    role: str = "admin",
) -> ReleaseResult:
    ingredients, syn_report = parse_medication_synonyms(bundle["synonyms"])
    dictionary = IngredientDictionary(ingredients)
    ayush, unlisted, f_report = parse_formulations(bundle["formulations"], dictionary)
    drugs, new_salts, d_report = parse_drugs(bundle["drugs"], dictionary)
    brands, b_report = parse_brands(bundle["brands"])
    links, l_report = parse_links(bundle["links"])
    meds: list[MedicationIn] = ayush + drugs

    # Cross-reference validation (never silently keep a dangling reference).
    codes = {m.code for m in meds}
    dupes = {m.code for m in ayush} & {m.code for m in drugs}
    if dupes:
        f_report.rejected.append(
            RowIssue(row=0, problem=f"codes in both formulations and drugs: {sorted(dupes)}")
        )
    known_brands = []
    for b in brands:
        if b.medication_code in codes:
            known_brands.append(b)
        else:
            b_report.rejected.append(
                RowIssue(row=0, code=b.brand_name, problem=f"unknown medication {b.medication_code}")
            )
    brands = known_brands
    known_conditions = {
        (c.system, c.code) for c in session.scalars(select(Concept).where(Concept.active.is_(True)))
    }
    valid_links = []
    for lk in links:
        if lk.medication_code not in codes:
            l_report.rejected.append(RowIssue(row=0, code=lk.medication_code, problem="unknown medication"))
        elif (lk.condition_system, lk.condition_code) not in known_conditions:
            l_report.rejected.append(
                RowIssue(row=0, code=lk.condition_code, problem="unknown condition code")
            )
        else:
            valid_links.append(lk)

    reports = {r.source: r.model_dump() for r in (syn_report, f_report, d_report, b_report, l_report)}
    all_ingredients = ingredients + new_salts + unlisted
    payload = {
        "ingredients": sorted((i.model_dump() for i in all_ingredients), key=lambda d: d["key"]),
        "medications": sorted((m.model_dump() for m in meds), key=lambda d: d["code"]),
        "brands": sorted(
            (b.model_dump() for b in brands), key=lambda d: (d["brand_name"], d["medication_code"])
        ),
        "links": sorted(
            (lk.model_dump() for lk in valid_links),
            key=lambda d: (d["condition_system"], d["condition_code"], d["medication_code"]),
        ),
    }
    release, prev = _open_release(
        session,
        source=SOURCE_MEDICATIONS,
        version=version,
        checksum=checksum_of(payload),
        row_count=len(meds),
        is_sample=is_sample,
        report={"tables": reports, "bundle": bundle},
        actor=actor,
    )
    if release is None:
        assert prev is not None
        return ReleaseResult(release=prev, skipped=True)

    # Ingredients: upsert by key.
    by_key = {i.key: i for i in session.scalars(select(Ingredient)).all()}
    for ing in all_ingredients:
        row = by_key.get(ing.key)
        if row is None:
            row = Ingredient(**ing.model_dump(), release_id=release.id)
            session.add(row)
            by_key[ing.key] = row
        else:
            for k, v in ing.model_dump().items():
                setattr(row, k, v)
            row.release_id = release.id
    session.flush()

    # Medications: upsert by code; composition replaced when content changes; removed ones deactivated.
    existing = {m.code: m for m in session.scalars(select(Medication)).all()}
    added, changed, removed = [], [], []
    for med in meds:
        digest = checksum_of(med.model_dump())
        med_row = existing.get(med.code)
        fields = med.model_dump(exclude={"composition"})
        if med_row is None:
            med_row = Medication(**fields, is_sample=is_sample, content_hash=digest, release_id=release.id)
            session.add(med_row)
            added.append(med.code)
        elif med_row.content_hash != digest or not med_row.active:
            for k, v in fields.items():
                setattr(med_row, k, v)
            med_row.content_hash, med_row.active, med_row.release_id = digest, True, release.id
            med_row.composition.clear()
            changed.append(med.code)
        else:
            continue
        session.flush()
        for pos, line in enumerate(med.composition):
            data = line.model_dump(exclude={"ingredient_key"})
            med_row.composition.append(
                MedicationIngredient(**data, position=pos, ingredient_id=by_key[line.ingredient_key].id)
            )
    for code, med_row in existing.items():
        if code not in codes and med_row.active:
            med_row.active = False
            removed.append(code)
    session.flush()
    med_id = {m.code: m.id for m in session.scalars(select(Medication)).all()}

    # Brands: replaced wholesale.
    session.execute(delete(Brand))
    session.add_all(
        Brand(
            brand_name=b.brand_name,
            key=brand_key(b.brand_name),
            medication_id=med_id[b.medication_code],
            source_title=b.source_title,
            release_id=release.id,
        )
        for b in brands
    )

    # Links: upsert; a clinician decision already recorded on an existing link is preserved.
    old_links = {
        (lk.condition_system, lk.condition_code, lk.medication_id): lk
        for lk in session.scalars(select(MedicationLink)).all()
    }
    links_added, links_removed = [], []
    seen = set()
    for lk in valid_links:
        key = (lk.condition_system, lk.condition_code, med_id[lk.medication_code])
        seen.add(key)
        link_row = old_links.get(key)
        if link_row is None:
            link_row = MedicationLink(
                condition_system=lk.condition_system,
                condition_code=lk.condition_code,
                medication_id=key[2],
                source_title=lk.source_title,
                source_edition=lk.source_edition,
                source_page=lk.source_page,
                status=lk.status,
                reviewer=lk.reviewer,
                note=lk.note,
                is_sample=is_sample,
                release_id=release.id,
            )
            session.add(link_row)
            session.flush()
            links_added.append(f"{lk.condition_code}->{lk.medication_code}")
            if link_row.status == "approved":
                record_provenance(
                    session,
                    target=f"MedicationLink/{link_row.id}",
                    activity="import",
                    agent=lk.reviewer or "source",
                    entities=[f"{lk.source_title} | {lk.source_edition or ''} | p. {lk.source_page or ''}"],
                    reason=f"{lk.condition_code} -> {lk.medication_code}",
                )
        else:
            link_row.source_title, link_row.source_edition, link_row.source_page = (
                lk.source_title,
                lk.source_edition,
                lk.source_page,
            )
            if link_row.decided_at is None:  # never overwrite a clinician decision
                link_row.status, link_row.reviewer = lk.status, lk.reviewer
    for key, link_row in old_links.items():
        if key not in seen:
            links_removed.append(f"{link_row.condition_code}->{link_row.medication_id}")
            session.delete(link_row)

    return _close_release(
        session,
        release,
        prev,
        {
            "added": added,
            "changed": changed,
            "removed": removed,
            "brands": len(brands),
            "links_added": links_added,
            "links_removed": links_removed,
            "rejected_rows": sum(len(r["rejected"]) for r in reports.values()),
        },
        actor,
        role,
    )


def report_of(result: ReleaseResult) -> dict[str, IngestReport]:
    tables = (result.release.report or {}).get("tables", {})
    return {k: IngestReport.model_validate(v) for k, v in tables.items()}
