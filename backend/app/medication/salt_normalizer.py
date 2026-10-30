"""Salt names and strengths.

- Salt-form stripping for matching ("Metformin hydrochloride" -> "Metformin"); full salt kept for display.
  Names where the "salt" word is the active moiety ("Ferrous sulphate", "Folic acid") are never stripped.
- Strength parsing to (exact decimal text, canonical unit): mg, mcg, g, IU, %w/w, %w/v, mg/ml.
Nothing here invents or converts a dose; it only normalises what the source printed.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from app.engine.transliterator import fold

SALT_FORMS = frozenset(
    """hydrochloride hcl hydrobromide sodium potassium succinate sulphate sulfate maleate citrate phosphate
    propionate acetate besylate besilate mesylate tartrate fumarate dihydrate monohydrate trihydrate
    anhydrous dipropionate valerate""".split()
)
# First words that mark the mineral/ion itself as the active ingredient: never strip the rest.
MOIETY_FIRST_WORDS = frozenset({"ferrous", "ferric", "calcium", "magnesium", "zinc", "sodium", "potassium"})

UNITS = {
    "mg": "mg",
    "milligram": "mg",
    "milligrams": "mg",
    "mcg": "mcg",
    "µg": "mcg",
    "μg": "mcg",
    "ug": "mcg",
    "microgram": "mcg",
    "micrograms": "mcg",
    "g": "g",
    "gm": "g",
    "gram": "g",
    "grams": "g",
    "iu": "IU",
    "i.u.": "IU",
    "units": "IU",
    "%w/w": "%w/w",
    "%w/v": "%w/v",
    "%v/v": "%v/v",
    "%": "%w/w",
    "mg/ml": "mg/ml",
    "mg/5ml": "mg/5ml",
}
_STRENGTH = re.compile(
    r"^\s*(?P<value>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>%\s*[wv]\s*/\s*[wv]|%|mg\s*/\s*5\s*ml|mg\s*/\s*ml|[a-zµμ.]+)\s*$",
    re.IGNORECASE,
)


class Strength(BaseModel):
    text: str
    value: str  # exact decimal as printed (no float rounding)
    unit: str


def parse_strength(text: str) -> Strength | None:
    m = _STRENGTH.match(text or "")
    if not m:
        return None
    raw_unit = re.sub(r"\s+", "", m.group("unit").lower())
    unit = UNITS.get(raw_unit)
    if unit is None:
        return None
    try:
        value = Decimal(m.group("value").replace(",", ""))
    except InvalidOperation:
        return None
    return Strength(text=text.strip(), value=format(value.normalize(), "f"), unit=unit)


def salt_base(name: str) -> str:
    """Strip trailing salt-form words: "Sumatriptan succinate" -> "Sumatriptan"."""
    words = name.strip().split()
    if not words or words[0].lower() in MOIETY_FIRST_WORDS:
        return name.strip()
    while len(words) > 1 and words[-1].lower().strip(".,") in SALT_FORMS:
        words.pop()
    return " ".join(words)


def salt_key(name: str) -> str:
    """Matching key for a salt name (folded, salt form stripped)."""
    return fold(salt_base(name)).replace(" ", "")
