from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from app.core.config import SAMPLE_DIR
from app.ingest.gold_loader import SPLIT_SEED, assign_splits, load_gold_file, parse_gold
from app.ingest.namaste_loader import NamasteColumnMap, load_namaste_file, parse_namaste, read_rows
from app.ingest.synonyms_loader import load_synonyms_file, parse_synonyms

# ------------------------------------------------------------------ NAMASTE


def test_sample_namaste_parses_cleanly() -> None:
    concepts, report = load_namaste_file(SAMPLE_DIR / "namaste_sample.csv")
    assert report.rejected == []
    assert report.warnings == []
    types = Counter(c.entity_type for c in concepts)
    assert types["disorder"] >= 40  # spec: at least 40 sample conditions
    assert types["pattern"] == 5
    assert all(c.code.startswith("SAMPLE-") for c in concepts)


def test_every_sample_condition_has_required_fields() -> None:
    concepts, _ = load_namaste_file(SAMPLE_DIR / "namaste_sample.csv")
    for c in concepts:
        if c.entity_type == "category":
            continue
        assert c.term_iast and c.term_devanagari and c.definition and c.term_en, c.code
        assert c.entity_type in {"disorder", "pattern"}
    amavata = next(c for c in concepts if c.display == "Amavata")
    assert amavata.term_devanagari == "आमवात"
    assert amavata.term_iast == "Āmavāta"
    assert {t.kind for t in amavata.terms} == {"primary", "iast", "devanagari", "english"}


def test_bad_rows_are_reported_not_fatal() -> None:
    rows = [
        {"NAMC_CODE": "X-1", "NAMC_term": "Good", "Entity_type": "disorder"},
        {"NAMC_CODE": "", "NAMC_term": "No code"},
        {"NAMC_CODE": "X-2", "NAMC_term": ""},
        {"NAMC_CODE": "X-1", "NAMC_term": "Duplicate"},
        {"NAMC_CODE": "X-3", "NAMC_term": "Weird", "Entity_type": "syndrome"},
        {"NAMC_CODE": "X-4", "NAMC_term": "Orphan", "Parent_code": "X-404"},
    ]
    concepts, report = parse_namaste(rows)
    assert [c.code for c in concepts] == ["X-1", "X-4"]
    assert report.total_rows == 6 and report.accepted == 2
    problems = {(i.row, i.problem.split(" ")[0]) for i in report.rejected}
    assert problems == {(3, "missing"), (4, "missing"), (5, "duplicate"), (6, "unknown")}
    assert report.warnings == ["X-4: parent X-404 not in this export"]


def test_custom_column_map_and_nfc_normalisation() -> None:
    cmap = NamasteColumnMap(
        code="Code", display="Term", term_iast="Diacritic", entity_type=None, term_en=None
    )
    decomposed = "Āmavāta"  # A + combining macron
    concepts, report = parse_namaste([{"Code": " AYU-9 ", "Term": "Amavata", "Diacritic": decomposed}], cmap)
    assert report.rejected == []
    assert concepts[0].code == "AYU-9"
    assert concepts[0].term_iast == "Āmavāta"  # precomposed NFC
    assert concepts[0].entity_type == "disorder"


def test_column_map_from_yaml(tmp_path: Path) -> None:
    f = tmp_path / "map.yaml"
    f.write_text("code: Code\ndisplay: Term\ndefault_discipline: siddha\n", encoding="utf-8")
    cmap = NamasteColumnMap.from_yaml(f)
    concepts, _ = parse_namaste([{"Code": "S-1", "Term": "Vali Azhal Keel Vayu"}], cmap)
    assert concepts[0].discipline == "siddha"


def test_xlsx_export_is_supported(tmp_path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["NAMC_CODE", "NAMC_term", "NAMC_term_DEVANAGARI"])
    ws.append(["X-1", "Jvara", "ज्वर"])
    ws.append([None, None, None])
    path = tmp_path / "export.xlsx"
    wb.save(path)
    rows = read_rows(path)
    assert rows == [{"NAMC_CODE": "X-1", "NAMC_term": "Jvara", "NAMC_term_DEVANAGARI": "ज्वर"}]


# ------------------------------------------------------------------ synonyms


def test_sample_synonyms_cover_every_condition() -> None:
    synonyms, report = load_synonyms_file(SAMPLE_DIR / "synonyms.yaml")
    concepts, _ = load_namaste_file(SAMPLE_DIR / "namaste_sample.csv")
    conditions = {c.code for c in concepts if c.entity_type != "category"}
    assert set(synonyms.entries) == conditions
    assert report.rejected == []
    for code, terms in synonyms.entries.items():
        assert {k for k, _ in terms} == {"english", "iast", "devanagari", "hinglish"}, code
    assert {"ama", "amla", "pitta", "vata", "sandhi", "shotha", "jvara"} <= set(synonyms.roots)


def test_synonyms_require_version_and_dedupe() -> None:
    with pytest.raises(ValueError, match="version"):
        parse_synonyms({"concepts": {}})
    syn, report = parse_synonyms(
        {"version": "v1", "concepts": {"A": {"english": ["x", "x"], "klingon": ["y"]}, "B": "oops"}}
    )
    assert syn.entries["A"] == [("english", "x")]
    assert report.warnings == ["A: ignored unknown keys ['klingon']"]
    assert report.rejected[0].code == "B"


# ------------------------------------------------------------------ gold set


def test_split_is_deterministic_and_80_20() -> None:
    codes = [f"C{i:03d}" for i in range(100)]
    first = assign_splits(codes)
    assert first == assign_splits(reversed(codes))
    assert Counter(first.values()) == {"train": 80, "test": 20}
    assert first != assign_splits(codes, seed=SPLIT_SEED + 1)


def test_sample_gold_file_matches_seeded_split() -> None:
    gold, report = load_gold_file(SAMPLE_DIR / "gold_mappings.csv")
    assert report.rejected == [] and report.warnings == []
    expected = assign_splits(g.namaste_code for g in gold)
    assert all(g.split == expected[g.namaste_code] for g in gold)
    assert Counter(g.relationship for g in gold) == {"equivalent": 50, "wider": 3}


def test_gold_rejects_bad_rows_and_fills_missing_split() -> None:
    gold, report = parse_gold(
        [
            {"namaste_code": "A", "tm2_code": "T", "relationship": "equivalent", "split": ""},
            {"namaste_code": "B", "tm2_code": "T", "relationship": "same-ish"},
            {"namaste_code": "", "tm2_code": "T"},
        ]
    )
    assert [g.namaste_code for g in gold] == ["A"]
    assert gold[0].split in {"train", "test"}
    assert len(report.rejected) == 2
    assert "assigned with seed" in report.warnings[0]
