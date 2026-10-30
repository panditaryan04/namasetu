"""Normaliser / transliterator: every variant in a row must fold to the same key (spec: >= 30 pairs)."""

from __future__ import annotations

from itertools import pairwise

import pytest

from app.core.config import SAMPLE_DIR
from app.engine.normalizer import RootSplitter, clean_text, normalize
from app.engine.transliterator import compact, detect_script, fold, skeleton, to_devanagari, to_iast

# Each row: variants that must share one compact key (English, IAST, Devanagari, Hinglish, HK, ITRANS-ish).
SAME_KEY = [
    ["Amavata", "Āmavāta", "aamavaata", "आमवात", "AmavAta", "AMAVATA", "ama-vata", "Ama Vata"],
    ["Amlapitta", "Amla Pitta", "Amla-pitta", "Āmlapitta", "अम्लपित्त", "amlapita"],
    ["Sandhivata", "संधिवात", "Sandhivāta", "sandhivaata"],
    ["Vatarakta", "Vātarakta", "वातरक्त", "vaatarakta"],
    ["Gridhrasi", "Gṛdhrasī", "गृध्रसी", "gridhrasee"],
    ["Katishula", "Kaṭīśūla", "कटीशूल", "katishoola"],
    ["Pakshaghata", "Pakṣāghāta", "पक्षाघात", "pakshaaghaata"],
    ["Grahani", "Grahaṇī", "ग्रहणी", "grahanee"],
    ["Atisara", "Atisāra", "अतिसार", "atisaara"],
    ["Ajirna", "Ajīrṇa", "अजीर्ण", "ajeerna"],
    ["Chhardi", "Chardi", "छर्दि"],
    ["Kasa", "Kāsa", "कास", "kaasa"],
    ["Tamaka Shvasa", "Tamaka Śvāsa", "तमक श्वास", "tamaka shwasa"],
    ["Pratishyaya", "Pratiśyāya", "प्रतिश्याय"],
    ["Hikka", "Hikkā", "हिक्का", "hika"],
    ["Rajayakshma", "Rājayakṣmā", "राजयक्ष्मा"],
    ["Mutrakrichchhra", "Mūtrakṛcchra", "मूत्रकृच्छ्र", "mootrakrichra"],
    ["Ashmari", "Aśmarī", "अश्मरी", "ashmaree"],
    ["Ekakushtha", "Ekakuṣṭha", "एककुष्ठ"],
    ["Shvitra", "Śvitra", "श्वित्र", "switra"],
    ["Shitapitta", "Śītapitta", "शीतपित्त", "sheetapitta"],
    ["Pandu", "Pāṇḍu", "पाण्डु", "paandu"],
    ["Kamala", "Kāmalā", "कामला", "kaamalaa"],
    ["Unmada", "Unmāda", "उन्माद", "unmaada"],
    ["Apasmara", "Apasmāra", "अपस्मार", "apasmaara"],
    ["Anidra", "Anidrā", "अनिद्रा"],
    ["Kashtartava", "Kaṣṭārtava", "कष्टार्तव"],
    ["Shvetapradara", "Śvetapradara", "श्वेतप्रदर", "shwetapradara"],
    ["Jvara", "ज्वर", "jwara"],
    ["Vataja Jvara", "Vātaja Jvara", "वातज ज्वर", "vaataja jwara"],
    ["Madhumeha", "मधुमेह", "madhumeha"],
    ["Shirahshula", "Śiraḥśūla", "शिरःशूल", "shirashula"],
    ["Agnimandya", "Agnimāndya", "अग्निमान्द्य"],
    ["Kaphavriddhi", "Kaphavṛddhi", "कफवृद्धि", "kafavriddhi"],
]


@pytest.mark.parametrize("variants", SAME_KEY, ids=[row[0] for row in SAME_KEY])
def test_variants_share_one_compact_key(variants: list[str]) -> None:
    keys = {compact(fold(v)) for v in variants}
    assert len(keys) == 1, {v: compact(fold(v)) for v in variants}


def test_at_least_thirty_cross_script_pairs() -> None:
    pairs = sum(len(row) - 1 for row in SAME_KEY)
    assert len(SAME_KEY) >= 30 and pairs >= 60


@pytest.mark.parametrize(
    ("hinglish", "canonical"),
    [("aamvaat", "Amavata"), ("sandhi vaat", "Sandhivata"), ("vaataj jwar", "Vataja Jvara"),
     ("tamak shwas", "Tamaka Shvasa"), ("amlpitt", "Amlapitta"), ("shirshool", "Shirahshula")],
)  # fmt: skip
def test_schwa_dropping_hinglish_shares_consonant_skeleton(hinglish: str, canonical: str) -> None:
    assert skeleton(fold(hinglish)) == skeleton(fold(canonical))


def test_amavata_key_and_skeleton() -> None:
    assert fold("आमवात") == "amavata"
    assert skeleton("amavata") == "amvt"


@pytest.mark.parametrize(
    ("text", "script"),
    [("आमवात", "devanagari"), ("Āmavāta", "iast"), ("AmavAta", "harvard-kyoto"), ("Amavata", "latin"),
     ("aamvaat", "latin"), ("GERD", "latin"), ("eczema", "latin"), ("kRmi", "harvard-kyoto")],
)  # fmt: skip
def test_script_detection(text: str, script: str) -> None:
    assert detect_script(text) == script


def test_transliteration_round_trip() -> None:
    assert to_iast("अम्लपित्त") == "amlapitta"
    assert to_iast("Amavata") == "Amavata"  # Latin passes through
    assert to_devanagari("āmavāta") == "आमवात"


def test_anusvara_folds_to_nasal() -> None:
    assert fold("संधिवात") == fold("सन्धिवात") == "sandhivata"
    assert fold("saṃdhi") == "sandhi"  # before a dental -> n
    assert fold("saṃpūrṇa") == "sampurna"  # before a labial -> m
    assert fold("saṃbandha") == "sambandha"


def test_clean_text_keeps_script() -> None:
    assert clean_text("  Āmavāta,  (आमवात)!  ") == "āmavāta आमवात"


@pytest.fixture(scope="module")
def splitter() -> RootSplitter:
    import yaml

    roots = yaml.safe_load((SAMPLE_DIR / "synonyms.yaml").read_text(encoding="utf-8"))["roots"]
    return RootSplitter(roots)


@pytest.mark.parametrize(
    ("word", "roots"),
    [("Amlapitta", ["amla", "pita"]), ("Amla Pitta", ["amla", "pita"]), ("अम्लपित्त", ["amla", "pita"]),
     ("Amavata", ["ama", "vata"]), ("Sandhivata", ["sandhi", "vata"]), ("Madhumeha", ["madhu", "meha"]),
     ("Kashtartava", ["kasta", "artava"]), ("Shvetapradara", ["sveta", "pradara"]), ("pitta", ["pita"])],
)  # fmt: skip
def test_compound_splitting(splitter: RootSplitter, word: str, roots: list[str]) -> None:
    assert normalize(word, splitter).roots == tuple(roots)


def test_incidental_substrings_are_not_roots(splitter: RootSplitter) -> None:
    # "ama" inside "tamak", "dama" or "kamala" must not count as the root term "ama".
    for word in ("tamak", "dama", "Kamala", "piliya"):
        assert normalize(word, splitter).roots == (), word


def test_amlapitta_variants_are_identical_for_matching(splitter: RootSplitter) -> None:
    forms = [
        normalize(v, splitter) for v in ("Amlapitta", "Amla Pitta", "Amla-pitta", "Āmlapitta", "अम्लपित्त")
    ]
    assert len({f.compact for f in forms}) == 1
    assert all(splitter.covers(f.key) for f in forms)
    for a, b in pairwise(forms):
        assert a.roots == b.roots
