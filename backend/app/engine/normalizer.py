"""Query/term normalisation and Sanskrit compound (samāsa) splitting.

Every surface form gets: the original text, an NFC lower-case display form, a folded phonetic key,
a compact key (spaces/hyphens removed), a consonant skeleton, and the root terms it splits into.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.engine.transliterator import Script, compact, detect_script, fold, skeleton

_SPACES = re.compile(r"\s+")

# English function words and generic classifier words that carry no matching signal.
STOPWORDS = frozenset(
    """a an and as at by for from in into is it of on or the to with without due like type
    which whose where when that this these those its their his her be are was were
    disorder disorders disease diseases tm2 sample condition conditions group groups""".split()
)
# Tokens are compared after phonetic folding ("which" -> "vhich"), so the stop list is folded too.
FOLDED_STOPWORDS = frozenset(fold(w) for w in STOPWORDS) | STOPWORDS


def clean_text(text: str) -> str:
    """NFC, lowercase, punctuation stripped, whitespace collapsed (script preserved)."""
    s = unicodedata.normalize("NFC", text).lower()
    # Keep letters, digits and combining marks (Devanagari vowel signs are marks, not punctuation).
    s = "".join(ch if unicodedata.category(ch)[0] in "LNM" else " " for ch in s)
    return _SPACES.sub(" ", s).strip()


class RootSplitter:
    """Greedy longest-match splitter over a dictionary of folded Sanskrit roots."""

    def __init__(self, roots: Iterable[str], min_len: int = 3) -> None:
        folded = {compact(fold(r)) for r in roots}
        self.roots = frozenset(r for r in folded if len(r) >= min_len)
        self._max = max((len(r) for r in self.roots), default=0)

    def split(self, word: str) -> tuple[list[str], bool]:
        """Split one compact folded word. Returns (roots found, fully covered by roots)."""
        found: list[str] = []
        i, covered = 0, 0
        while i < len(word):
            match = self._longest_at(word, i)
            # Vowel sandhi: "kasta" + "artava" -> "kastartava" shares one "a"; retry one char back.
            if match is None and found and found[-1].endswith("a"):
                back = self._longest_at(word, i - 1)
                if back is not None and back.startswith("a") and len(back) > 3:
                    found.append(back)
                    covered += len(back) - 1
                    i += len(back) - 1
                    continue
            if match is None:
                i += 1
                continue
            found.append(match)
            covered += len(match)
            i += len(match)
        return found, covered == len(word) and bool(found)

    def _longest_at(self, word: str, i: int) -> str | None:
        for size in range(min(self._max, len(word) - i), 2, -1):
            if word[i : i + size] in self.roots:
                return word[i : i + size]
        return None

    def token_roots(self, token: str, min_cover: float = 0.6) -> list[str]:
        """Roots of one word, only if the word *starts* with a root and roots cover most of it.
        This stops incidental substrings ("ama" in "tamak" or "dama") from counting as root terms."""
        if not token or self._longest_at(token, 0) is None:
            return []
        found, _ = self.split(token)
        covered = sum(len(r) for r in found)
        return found if covered / len(token) >= min_cover else []

    def covers(self, key: str) -> bool:
        """True when every word of the key is fully made of root terms ("pitta", "amla pitta")."""
        return bool(key) and all(self.split(t)[1] for t in key.split())

    def roots_in(self, key: str) -> list[str]:
        """Root terms inside a folded key (each token split independently), in order, de-duplicated."""
        out: list[str] = []
        for token in key.split():
            for r in self.token_roots(token):
                if r not in out:
                    out.append(r)
        return out


@dataclass(frozen=True)
class Normalized:
    original: str
    script: Script
    clean: str
    key: str  # folded phonetic key, tokens separated by spaces
    compact: str
    skeleton: str
    tokens: tuple[str, ...]
    content_tokens: tuple[str, ...]
    roots: tuple[str, ...] = field(default=())


def normalize(text: str, splitter: RootSplitter | None = None) -> Normalized:
    key = fold(text)
    tokens = tuple(key.split())
    return Normalized(
        original=text,
        script=detect_script(text),
        clean=clean_text(text),
        key=key,
        compact=compact(key),
        skeleton=skeleton(key),
        tokens=tokens,
        content_tokens=tuple(t for t in tokens if t not in FOLDED_STOPWORDS and len(t) > 1),
        roots=tuple(splitter.roots_in(key)) if splitter else (),
    )
