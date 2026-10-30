"""Script detection, transliteration to IAST, and phonetic folding to a script-independent key.

"Amavata", "Āmavāta", "aamavaata", "AmavAta" (Harvard-Kyoto) and "आमवात" all fold to "amavata".
Hinglish spellings that drop the inherent vowel ("aamvaat") share the consonant skeleton "amvt".
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Literal

from indic_transliteration import sanscript

Script = Literal["devanagari", "iast", "harvard-kyoto", "latin"]

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_IAST_MARKS = set("āīūṛṝḷḹṅñṭḍṇśṣḥṃṁēō")
# Harvard-Kyoto marks long vowels / retroflexes with capitals inside a word: "AmavAta", "kRmi", "zItapitta".
# Only an internal capital after a lowercase letter counts, so "Amavata", "GERD" or "eczema" are not HK.
_HK = re.compile(r"(?<=[a-z])[AIURLMHTDNSG]")

# Diacritic letters -> plain ASCII (after lowercasing).
_DIACRITICS = {
    "ā": "a", "ī": "i", "ū": "u", "ṛ": "ri", "ṝ": "ri", "ḷ": "li", "ḹ": "li",
    "ṅ": "n", "ñ": "n", "ṭ": "t", "ḍ": "d", "ṇ": "n", "ś": "sh", "ṣ": "sh",
    "ḥ": "", "ē": "e", "ō": "o",
}  # fmt: skip
_ANUSVARA = re.compile(r"[ṃṁ](?=[pbm])")

# Phonetic folds applied in order to plain ASCII.
_FOLDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), r)
    for p, r in (
        (r"chh", "ch"),
        (r"chch", "ch"),  # "krichchhra" (after chh->ch) ~ IAST "kṛcchra"
        (r"(?<=[aeiou])hsh", "sh"),  # romanised visarga before a sibilant: "shirahshula" ~ "shirashula"
        (r"sh", "s"),
        (r"w", "v"),
        (r"f", "ph"),
        (r"aa+", "a"),
        (r"ee+|ii+", "i"),
        (r"oo+|uu+", "u"),
        (r"([bcdghjklmnpqrstvxyz])\1+", r"\1"),  # double consonants
    )
)
_NON_WORD = re.compile(r"[^a-z0-9]+")
_VOWELS = re.compile(r"[aeiou]")


def detect_script(text: str) -> Script:
    if _DEVANAGARI.search(text):
        return "devanagari"
    if any(ch in _IAST_MARKS for ch in unicodedata.normalize("NFC", text).lower()):
        return "iast"
    if _HK.search(text):
        return "harvard-kyoto"
    return "latin"


def to_iast(text: str, script: Script | None = None) -> str:
    """Romanise Devanagari / Harvard-Kyoto input to IAST; other text is returned unchanged."""
    script = script or detect_script(text)
    if script == "devanagari":
        return str(sanscript.transliterate(text, sanscript.DEVANAGARI, sanscript.IAST))
    if script == "harvard-kyoto":
        return str(sanscript.transliterate(text, sanscript.HK, sanscript.IAST))
    return text


def to_devanagari(iast: str) -> str:
    return str(sanscript.transliterate(iast, sanscript.IAST, sanscript.DEVANAGARI))


@lru_cache(maxsize=65536)
def fold(text: str) -> str:
    """Script-independent phonetic key: lowercase ASCII words separated by single spaces."""
    s = unicodedata.normalize("NFC", to_iast(unicodedata.normalize("NFC", text))).lower()
    s = _ANUSVARA.sub("m", s)
    s = s.replace("ṃ", "n").replace("ṁ", "n")
    s = "".join(_DIACRITICS.get(ch, ch) for ch in s)
    # Strip any remaining combining marks (e.g. "é" -> "e").
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if not unicodedata.combining(ch))
    s = _NON_WORD.sub(" ", s).strip()
    for pattern, repl in _FOLDS:
        s = pattern.sub(repl, s)
    return s


def compact(key: str) -> str:
    """Space/hyphen-insensitive form of a folded key: "amla pita" -> "amlapita"."""
    return key.replace(" ", "")


def skeleton(key: str) -> str:
    """Consonant skeleton (keeps a leading vowel): "amavata" and "amvat" -> "amvt"."""
    word = compact(key)
    if not word:
        return ""
    return word[0] + _VOWELS.sub("", word[1:])
