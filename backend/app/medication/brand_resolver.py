"""Brand -> salt resolution and "What's inside this medicine?" search (brands, generics, formulations, herbs).

Uses the same folding as the terminology engine, so Devanagari / IAST / Hinglish herb names work.
Unknown brands are NEVER guessed: below the fuzzy threshold the answer is "Brand not found".
Dosage-form words ("Tab", "Cap") and frequency codes ("BD", "TDS") are stripped for matching only; they are
never stored or shown as dosing information.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel
from rapidfuzz import fuzz
from rapidfuzz.distance import OSA

from app.engine.transliterator import fold
from app.medication.catalog import DISCLAIMER, MedicationCatalog
from app.medication.salt_normalizer import salt_key

FORM_WORDS = frozenset(
    """tab tabs tablet tablets cap caps capsule capsules syp syrup susp suspension inj injection oint ointment
    cream gel drops drop lotion churna churan vati powder""".split()
)
FREQUENCY_WORDS = frozenset("od bd bid tds tid qid qds hs sos stat prn ac pc daily nightly x".split())
# Administration instructions ("before food") are stripped for matching only; never stored or shown as advice.
INSTRUCTION_WORDS = frozenset(
    "before after with at in on food foods meal meals empty stomach morning night bedtime water milk"
    " once twice thrice times day".split()
)
# Tokens are compared after phonetic folding ("food" -> "phud"), so fold the word lists the same way.
_NOISE = frozenset(fold(w) for w in FORM_WORDS | FREQUENCY_WORDS | INSTRUCTION_WORDS)
_STRENGTH_TOKEN = re.compile(r"^\d+(\.\d+)?(mg|mcg|g|ml|iu|k)?$")


def max_edits(key: str) -> int:
    """Brand typo tolerance: 1 edit (incl. transposition) for 5-7 letters, 2 for 8+, none below."""
    return 0 if len(key) < 5 else 1 if len(key) < 8 else 2


class BrandResolution(BaseModel):
    query: str
    found: bool
    brand: str | None = None
    medication_code: str | None = None
    match: Literal["exact", "fuzzy"] | None = None
    score: float | None = None
    strength_note: str | None = None
    message: str


class SearchHit(BaseModel):
    type: Literal["brand", "medication", "ingredient"]
    ref: str  # medication code or ingredient id
    name: str
    matched: str
    kind: str
    score: float


class SearchResponse(BaseModel):
    query: str
    brand: BrandResolution
    hits: list[SearchHit]
    disclaimer: str = DISCLAIMER


def clean_line(text: str) -> tuple[list[str], list[str]]:
    """Folded tokens with form/frequency words removed; plus the numeric strength tokens found."""
    tokens = [t for t in re.split(r"[\s,;/()+\-]+", fold(text)) if t]
    kept = [t for t in tokens if t not in _NOISE]
    strengths = [t for t in kept if _STRENGTH_TOKEN.match(t)]
    return kept, strengths


def resolve_brand(catalog: MedicationCatalog, text: str) -> BrandResolution:
    tokens, strengths = clean_line(text)
    no_numbers = [t for t in tokens if t not in strengths and t not in {"mg", "mcg", "g", "ml", "iu"}]
    candidates = list(dict.fromkeys(["".join(tokens), "".join(no_numbers)]))
    candidates = [c for c in candidates if c]
    keys = {key: (name, med_id) for name, key, med_id in catalog.brands}
    hit: tuple[str, int, str, float] | None = None
    alpha: dict[str, list[str]] = {}
    for k in keys:  # "dolo650" -> "dolo": a brand named with its strength, queried without it
        alpha.setdefault(re.sub(r"\d+(\.\d+)?", "", k), []).append(k)
    for cand in candidates:
        if cand in keys:
            hit = (*keys[cand], "exact", 1.0)
            break
        if len(alpha.get(cand, [])) == 1:
            hit = (*keys[alpha[cand][0]], "exact", 1.0)
            break
    if hit is None:
        # Typo tolerance without guessing: within budget, same first letter, UNIQUE closest brand.
        for cand in candidates:
            close = sorted(
                (OSA.distance(cand, k), k) for k in keys if k[:1] == cand[:1] and abs(len(k) - len(cand)) <= 2
            )
            if (
                close
                and close[0][0] <= max_edits(close[0][1])
                and (len(close) == 1 or close[1][0] > close[0][0])
            ):
                d, k = close[0]
                hit = (*keys[k], "fuzzy", round(1 - d / max(len(k), 1), 3))
                break
    if hit is None:
        return BrandResolution(query=text, found=False, message="Brand not found")
    name, med_id, match, score = hit
    view = catalog.by_id[med_id].view
    note = None
    printed = {c.strength.replace(" ", "").lower() for c in view.composition if c.strength}
    numbers = {re.sub(r"[a-z]+$", "", s) for s in strengths}
    in_product = {re.sub(r"[^0-9.]", "", p) for p in printed}
    if numbers and not numbers & in_product and not any(n in name for n in numbers):
        said, product = ", ".join(sorted(numbers)), ", ".join(sorted(printed))
        note = f"Strength in the text ({said}) differs from the product ({product})"
    return BrandResolution(
        query=text,
        found=True,
        brand=name,
        medication_code=view.code,
        match=match,
        score=score,
        strength_note=note,
        message=f"{name} → {view.name}",
    )


def search(catalog: MedicationCatalog, text: str, limit: int = 20) -> SearchResponse:
    brand = resolve_brand(catalog, text)
    tokens, _ = clean_line(text)
    q = "".join(tokens)
    hits: dict[tuple[str, str], SearchHit] = {}

    def consider(
        kind_: Literal["brand", "medication", "ingredient"],
        ref: str,
        name: str,
        term: str,
        sub: str,
        key: str,
    ) -> None:
        if not q or not key:
            return
        if key == q:
            score = 1.0
        elif key.startswith(q) and len(q) >= 3:
            score = 0.9
        elif q in key and len(q) >= 4:
            score = 0.8
        else:
            ratio = fuzz.ratio(q, key)
            score = ratio / 100 * 0.9 if ratio >= 85 else 0.0
        if not score:
            return
        prev = hits.get((kind_, ref))
        if prev is None or score > prev.score:
            hits[(kind_, ref)] = SearchHit(
                type=kind_, ref=ref, name=name, matched=term, kind=sub, score=round(score, 3)
            )

    for brand_name, key, med_id in catalog.brands:
        view = catalog.by_id[med_id].view
        consider("brand", view.code, f"{brand_name} ({view.name})", brand_name, view.kind, key)
    for code, rec in catalog.by_code.items():
        v = rec.view
        if not v.active:
            continue
        for term in filter(None, [v.name, v.name_iast, v.name_devanagari]):
            consider("medication", code, v.name, term, v.kind, fold(term).replace(" ", ""))
    for ing in catalog.ingredients.values():
        terms = [
            ing.canonical_name,
            ing.botanical_name,
            ing.iast,
            ing.devanagari,
            *(s["term"] for s in ing.synonyms),
        ]
        for term in filter(None, terms):
            key = salt_key(term) if ing.kind == "salt" else fold(term).replace(" ", "")
            consider("ingredient", str(ing.id), ing.canonical_name, term, ing.kind, key)
    if brand.found and brand.medication_code:
        view = catalog.by_code[brand.medication_code].view
        hits[("brand", view.code)] = SearchHit(
            type="brand",
            ref=view.code,
            name=f"{brand.brand} ({view.name})",
            matched=brand.brand or "",
            kind=view.kind,
            score=brand.score or 1.0,
        )
    ranked = sorted(hits.values(), key=lambda h: (-h.score, h.type != "brand", h.name))
    return SearchResponse(query=text, brand=brand, hits=ranked[:limit])
