"""Cooking units, quantity parsing and recipe scaling — no LLM involved.

The recipe `ingredients` field is stored as free Hebrew text (one ingredient per
line, optionally grouped under section headers ending with ':'). This module
parses those lines into (quantity, unit, name), scales the quantity by a factor,
and renders the result back as kitchen-friendly Hebrew text.

Anything it cannot confidently parse is returned with needs_review=True and its
original text untouched — we never guess.

Design notes:
  * Deliberately no `pint` dependency. Pint is excellent for general dimensional
    analysis, but its unit registry is English-only and the conversion table
    cooking actually needs is ~10 entries. The parsing — Hebrew fraction words,
    unicode vulgar fractions, RTL text — is the real work, and pint does none
    of it.
  * Volume→mass conversion is NOT supported. It requires per-ingredient density
    (a cup of flour and a cup of honey differ by ~3×) and guessing it silently
    corrupts recipes. Scaling stays within a dimension.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from fractions import Fraction

# ---------------------------------------------------------------------------
# Unit definitions
# ---------------------------------------------------------------------------

VOLUME = "volume"
MASS = "mass"
COUNT = "count"


@dataclass(frozen=True)
class Unit:
    """A single cooking unit.

    `factor` is the size of one unit expressed in the dimension's base unit
    (ml for volume, grams for mass, 1 for count). That single number is all
    the conversion machinery we need: a → b is `qty * a.factor / b.factor`.
    """
    id: str
    dimension: str
    factor: float
    singular: str                       # Hebrew display form, e.g. "כוס"
    plural: str                         # Hebrew display form, e.g. "כוסות"
    aliases: tuple[str, ...] = ()       # everything we accept while parsing
    # Below this many base units, prefer a smaller unit when rendering.
    # None = never step down from this unit.
    step_down_below: float | None = None
    # At or above this many of THIS unit, prefer a larger one — but only if
    # the result comes out clean (whole or half). None = never step up.
    step_up_at: float | None = None

    # -- getters ------------------------------------------------------------

    @property
    def is_volume(self) -> bool:
        return self.dimension == VOLUME

    @property
    def is_mass(self) -> bool:
        return self.dimension == MASS

    @property
    def is_count(self) -> bool:
        return self.dimension == COUNT

    def label(self, quantity: float) -> str:
        """Hebrew label agreeing with the quantity.

        Hebrew pluralises from 2 up, and fractions below 1 take the singular:
        "חצי כוס", "כוס אחת", but "2 כוסות" and "1½ כוסות".
        """
        if quantity <= 1.0 + 1e-9:
            return self.singular
        return self.plural

    def to_base(self, quantity: float) -> float:
        return quantity * self.factor

    def from_base(self, base_quantity: float) -> float:
        return base_quantity / self.factor

    def convert_to(self, quantity: float, other: Unit) -> float:
        if self.dimension != other.dimension:
            raise ValueError(
                f"cannot convert {self.dimension} ({self.id}) to "
                f"{other.dimension} ({other.id}) without ingredient density"
            )
        return other.from_base(self.to_base(quantity))


# Volume — base unit is the millilitre. Israeli kitchen conventions:
# כוס = 240ml, כף = 15ml, כפית = 5ml.
UNITS: tuple[Unit, ...] = (
    Unit("liter", VOLUME, 1000.0, "ליטר", "ליטר",
         aliases=("ליטר", "ל'", "l", "liter", "litre", "liters"),
         step_down_below=1000.0),
    Unit("cup", VOLUME, 240.0, "כוס", "כוסות",
         aliases=("כוס", "כוסות", "cup", "cups", "c"),
         step_down_below=60.0),          # under 1/4 cup → tablespoons
    Unit("tbsp", VOLUME, 15.0, "כף", "כפות",
         aliases=("כף", "כפות", "כפית גדולה", "tbsp", "tablespoon",
                  "tablespoons", "T"),
         step_down_below=15.0,           # under 1 tbsp → teaspoons
         step_up_at=16.0),               # 16 כפות = 1 כוס
    Unit("tsp", VOLUME, 5.0, "כפית", "כפיות",
         aliases=("כפית", "כפיות", "tsp", "teaspoon", "teaspoons", "t"),
         step_down_below=None,           # smallest volume unit we render
         step_up_at=3.0),                # 3 כפיות = 1 כף
    Unit("ml", VOLUME, 1.0, 'מ"ל', 'מ"ל',
         aliases=('מ"ל', "מ״ל", "מל", "מיליליטר", "ml", "milliliter",
                  "millilitre"),
         step_down_below=None,
         step_up_at=1000.0),             # 1000 מ"ל = 1 ליטר

    # Mass — base unit is the gram.
    Unit("kg", MASS, 1000.0, 'ק"ג', 'ק"ג',
         aliases=('ק"ג', "ק״ג", "קג", "קילו", "קילוגרם", "kg", "kilo",
                  "kilogram", "kilograms"),
         step_down_below=1000.0),
    Unit("gram", MASS, 1.0, "גרם", "גרם",
         aliases=("גרם", "ג'", "גר'", "גר", "g", "gr", "gram", "grams"),
         step_down_below=None,
         step_up_at=1000.0),             # 1000 גרם = 1 ק"ג

    # Count — base unit is "one of the thing".
    Unit("unit", COUNT, 1.0, "יחידה", "יחידות",
         aliases=("יחידה", "יחידות", "יח'", "יח", "unit", "units", "pcs"),
         step_down_below=None),
    Unit("package", COUNT, 1.0, "חבילה", "חבילות",
         aliases=("חבילה", "חבילות", "package", "packages", "pkg"),
         step_down_below=None),
    Unit("clove", COUNT, 1.0, "שן", "שיני",
         aliases=("שן", "שיני", "שיניים", "clove", "cloves"),
         step_down_below=None),
)

_BY_ID: dict[str, Unit] = {u.id: u for u in UNITS}

# Longest alias first so "כפית" wins over "כף" when both could match.
_ALIAS_TO_UNIT: dict[str, Unit] = {}
for _u in UNITS:
    for _a in (_u.singular, _u.plural, *_u.aliases):
        _ALIAS_TO_UNIT.setdefault(_a.lower(), _u)

_SORTED_ALIASES: list[str] = sorted(_ALIAS_TO_UNIT, key=len, reverse=True)


def get_unit(name: str) -> Unit | None:
    """Look up a unit by any of its aliases. Returns None if unrecognised."""
    return _ALIAS_TO_UNIT.get(name.strip().lower())


def units_in_dimension(dimension: str) -> list[Unit]:
    """All units of one dimension, largest first."""
    return sorted((u for u in UNITS if u.dimension == dimension),
                  key=lambda u: u.factor, reverse=True)


# ---------------------------------------------------------------------------
# Quantity parsing
# ---------------------------------------------------------------------------

# Unicode vulgar fractions that show up in scraped recipes.
_VULGAR = {
    "½": Fraction(1, 2), "⅓": Fraction(1, 3), "⅔": Fraction(2, 3),
    "¼": Fraction(1, 4), "¾": Fraction(3, 4), "⅕": Fraction(1, 5),
    "⅖": Fraction(2, 5), "⅗": Fraction(3, 5), "⅘": Fraction(4, 5),
    "⅙": Fraction(1, 6), "⅚": Fraction(5, 6), "⅐": Fraction(1, 7),
    "⅛": Fraction(1, 8), "⅜": Fraction(3, 8), "⅝": Fraction(5, 8),
    "⅞": Fraction(7, 8),
}

# Hebrew fraction words. Order matters — multi-word forms are matched first.
_HEBREW_FRACTIONS: list[tuple[str, Fraction]] = [
    ("שלושת רבעי", Fraction(3, 4)),
    ("שלושה רבעי", Fraction(3, 4)),
    ("שני שלישי", Fraction(2, 3)),
    ("שני שליש", Fraction(2, 3)),
    ("שליש", Fraction(1, 3)),
    ("רבע", Fraction(1, 4)),
    ("חצי", Fraction(1, 2)),
    ("שמינית", Fraction(1, 8)),
]

# Hebrew number words for small counts (common in ingredient lists).
_HEBREW_NUMBERS: dict[str, Fraction] = {
    "אחד": Fraction(1), "אחת": Fraction(1),
    "שתי": Fraction(2), "שני": Fraction(2), "שניים": Fraction(2), "שתיים": Fraction(2),
    "שלוש": Fraction(3), "שלושה": Fraction(3),
    "ארבע": Fraction(4), "ארבעה": Fraction(4),
    "חמש": Fraction(5), "חמישה": Fraction(5),
    "שש": Fraction(6), "שישה": Fraction(6),
    "שבע": Fraction(7), "שבעה": Fraction(7),
    "שמונה": Fraction(8),
    "תשע": Fraction(9), "תשעה": Fraction(9),
    "עשר": Fraction(10), "עשרה": Fraction(10),
}

# Words that mean "an unmeasurable pinch" — valid ingredients, never scaled.
_UNSCALABLE_WORDS = (
    "קורט", "קמצוץ", "לפי הטעם", "לטעם", "כמות לפי", "מעט", "טיפה",
    "לפי הצורך", "להגשה", "לקישוט", "לזילוף", "לשימון", "לפיזור",
    "pinch", "to taste", "as needed", "for garnish",
)

# Bullet / list prefixes we strip before parsing and restore when rendering.
# A numbered prefix ("1.") must be followed by whitespace, otherwise "1.5 ליטר"
# gets read as list-item 1 containing "5 ליטר".
_BULLET_RE = re.compile(r"^\s*(?:[•\-–—*·]\s*|\d+[.)]\s+)")


def _fraction_from_text(text: str) -> Fraction | None:
    """Parse a leading numeric expression: 1, 1.5, 1/2, 1 1/2, ½, 1½."""
    text = text.strip()
    if not text:
        return None

    total = Fraction(0)
    matched = False

    # A bare "a/b" must be handled before the integer branch, otherwise the
    # integer matcher swallows the numerator and "1/4" parses as 1.
    m = re.match(r"^(\d+)\s*/\s*(\d+)(?!\d)", text)
    if m and int(m.group(2)) != 0:
        return Fraction(int(m.group(1)), int(m.group(2)))

    # Leading integer or decimal, e.g. "1", "1.5", "2"
    m = re.match(r"^(\d+(?:[.,]\d+)?)", text)
    if m:
        total += Fraction(m.group(1).replace(",", "."))
        text = text[m.end():].strip()
        matched = True

    # Mixed number: "1 1/2"
    m = re.match(r"^(\d+)\s*/\s*(\d+)", text)
    if m and int(m.group(2)) != 0:
        total += Fraction(int(m.group(1)), int(m.group(2)))
        text = text[m.end():].strip()
        matched = True
    elif text and text[0] in _VULGAR:
        total += _VULGAR[text[0]]
        text = text[1:].strip()
        matched = True

    return total if matched else None


@dataclass
class ParsedIngredient:
    """One ingredient line after parsing."""
    raw: str                            # original line, verbatim
    bullet: str = ""                    # stripped prefix, restored on render
    quantity: Fraction | None = None
    unit: Unit | None = None
    name: str = ""                      # the ingredient itself
    is_section: bool = False            # a header line like "לבצק:"
    scalable: bool = True               # False for "קורט מלח" etc.
    needs_review: bool = False          # we could not parse a quantity
    review_reason: str = ""

    @property
    def parsed(self) -> bool:
        return self.quantity is not None and not self.needs_review


def parse_ingredient_line(line: str) -> ParsedIngredient:
    """Parse a single ingredient line. Never raises — unparseable lines come
    back with needs_review=True and their raw text intact."""
    raw = line.rstrip()
    stripped = raw.strip()

    # Blank line
    if not stripped:
        return ParsedIngredient(raw=raw, is_section=True, scalable=False)

    # Section header: ends with ':' and has no digits, e.g. "לבצק:"
    if stripped.endswith(":") and not any(c.isdigit() for c in stripped):
        return ParsedIngredient(raw=raw, is_section=True, scalable=False,
                                name=stripped)

    bullet_match = _BULLET_RE.match(raw)
    bullet = bullet_match.group(0) if bullet_match else ""
    body = raw[len(bullet):].strip() if bullet else stripped

    lowered = body.lower()

    # Unscalable by nature — a pinch stays a pinch at any batch size.
    for word in _UNSCALABLE_WORDS:
        if word in lowered:
            return ParsedIngredient(raw=raw, bullet=bullet, name=body,
                                    scalable=False)

    quantity: Fraction | None = None
    rest = body

    # 1) Hebrew fraction word, optionally after a number ("חצי כוס", "2 וחצי")
    for word, frac in _HEBREW_FRACTIONS:
        m = re.match(rf"^{re.escape(word)}\b\s*", rest)
        if m:
            quantity = frac
            rest = rest[m.end():].strip()
            break

    # 2) Numeric expression
    if quantity is None:
        m = re.match(r"^([\d.,/\s½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅐⅛⅜⅝⅞]+)", rest)
        if m:
            candidate = _fraction_from_text(m.group(1))
            if candidate is not None:
                quantity = candidate
                rest = rest[m.end():].strip()
                # "2 וחצי כוסות" — trailing Hebrew fraction after the number
                for word, frac in _HEBREW_FRACTIONS:
                    m2 = re.match(rf"^ו?{re.escape(word)}\b\s*", rest)
                    if m2:
                        quantity += frac
                        rest = rest[m2.end():].strip()
                        break

    # 3) Hebrew number word ("שתי כוסות")
    if quantity is None:
        first_word = rest.split()[0] if rest.split() else ""
        if first_word in _HEBREW_NUMBERS:
            quantity = _HEBREW_NUMBERS[first_word]
            rest = rest[len(first_word):].strip()

    if quantity is None:
        return ParsedIngredient(
            raw=raw, bullet=bullet, name=body,
            needs_review=True, review_reason="לא זוהתה כמות",
        )

    # A range like "2-3 כפות" — we caught the 2, the '-3' is still in rest.
    # Scaling a range is ambiguous, so flag it rather than mangle it.
    if re.match(r"^[-–—]\s*\d", rest):
        return ParsedIngredient(
            raw=raw, bullet=bullet, name=body,
            needs_review=True, review_reason="טווח כמויות",
        )

    # Unit: match the longest alias at the start of the remainder.
    unit: Unit | None = None
    for alias in _SORTED_ALIASES:
        m = re.match(rf"^{re.escape(alias)}(?=\s|$|,)", rest, re.IGNORECASE)
        if m:
            unit = _ALIAS_TO_UNIT[alias.lower()]
            rest = rest[m.end():].strip()
            break

    # Strip a leading "של" / "of" left over after the unit.
    rest = re.sub(r"^(של|of)\s+", "", rest).strip()

    return ParsedIngredient(raw=raw, bullet=bullet, quantity=quantity,
                            unit=unit, name=rest or body)


# ---------------------------------------------------------------------------
# Ingredient densities — volume → mass
# ---------------------------------------------------------------------------

# Grams per millilitre. A cup of flour and a cup of honey differ by ~3×, so
# this conversion is ONLY possible for ingredients we actually know. Anything
# not listed here is never guessed — it comes back as "not convertible" and
# the user is asked rather than given a wrong number.
#
# Keys are Hebrew substrings matched against the ingredient name, longest
# first. Values are approximate but standard for Israeli kitchens.
INGREDIENT_DENSITIES: dict[str, float] = {
    # Flours & dry powders
    "קמח מלא": 0.55,
    "קמח תופח": 0.53,
    "קמח לחם": 0.55,
    "קמח קוקוס": 0.45,
    "קמח שקדים": 0.40,
    "קמח": 0.53,
    "קורנפלור": 0.50,
    "קמח תירס": 0.50,
    "אבקת קקאו": 0.42,
    "קקאו": 0.42,
    "אבקת סוכר": 0.50,
    "אבקת חלב": 0.50,
    "פירורי לחם": 0.45,
    "שיבולת שועל": 0.40,
    "קוואקר": 0.40,

    # Sugars & sweeteners
    "סוכר חום": 0.85,
    "סוכר דמררה": 0.80,
    "סוכר ונילי": 0.85,
    "סוכר": 0.85,
    "דבש": 1.42,
    "סילאן": 1.40,
    "סירופ מייפל": 1.32,
    "סירופ": 1.30,
    "ריבה": 1.30,

    # Fats & liquids
    "שמן זית": 0.92,
    "שמן": 0.92,
    "חמאה": 0.91,
    "מרגרינה": 0.91,
    "מים": 1.00,
    "חלב": 1.03,
    "שמנת חמוצה": 1.00,
    "שמנת מתוקה": 0.99,
    "שמנת": 1.00,
    "יוגורט": 1.03,
    "לבן": 1.03,
    "חומץ": 1.01,
    "יין": 0.99,
    "מיץ לימון": 1.03,
    "מיץ": 1.04,
    "טחינה גולמית": 1.05,
    "טחינה": 1.05,

    # Grains, nuts, misc
    "אורז": 0.85,
    "קוסקוס": 0.72,
    "בורגול": 0.75,
    "עדשים": 0.85,
    "קינואה": 0.77,
    "שקדים טחונים": 0.40,
    "שקדים": 0.60,
    "אגוזים": 0.50,
    "אגוזי מלך": 0.50,
    "צימוקים": 0.65,
    "קוקוס": 0.35,
    "שוקולד צ'יפס": 0.72,
    "שוקולד": 0.72,
    "גבינה מגורדת": 0.45,
    "גבינה לבנה": 1.02,
    "גבינת שמנת": 1.02,
    "מלח": 1.20,
    "סוכריות": 0.80,
}

_SORTED_DENSITY_KEYS: list[str] = sorted(INGREDIENT_DENSITIES, key=len, reverse=True)


def density_for(name: str) -> tuple[str, float] | None:
    """Find the density for an ingredient name.

    Returns (matched_key, grams_per_ml) or None when we don't know the
    ingredient — in which case the caller must ask rather than guess.
    """
    if not name:
        return None
    lowered = name.strip().lower()
    for key in _SORTED_DENSITY_KEYS:
        if key in lowered:
            return key, INGREDIENT_DENSITIES[key]
    return None


def to_grams(item: ParsedIngredient) -> Fraction | None:
    """Convert a parsed volume ingredient to grams. None if not possible."""
    if item.quantity is None or item.unit is None or not item.unit.is_volume:
        return None
    match = density_for(item.name)
    if match is None:
        return None
    _, grams_per_ml = match
    ml = item.quantity * Fraction(item.unit.factor).limit_denominator()
    return ml * Fraction(grams_per_ml).limit_denominator(1000)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Denominators a cook can actually measure.
_NICE_DENOMINATORS = (2, 3, 4, 8)
_FRACTION_GLYPHS = {
    Fraction(1, 2): "½", Fraction(1, 3): "⅓", Fraction(2, 3): "⅔",
    Fraction(1, 4): "¼", Fraction(3, 4): "¾", Fraction(1, 8): "⅛",
    Fraction(3, 8): "⅜", Fraction(5, 8): "⅝", Fraction(7, 8): "⅞",
}


def _snap(value: Fraction) -> Fraction:
    """Round to the nearest measurable fraction. 0.333… → ⅓, not 333/1000."""
    if value == 0:
        return value
    best = None
    best_error = None
    for denom in _NICE_DENOMINATORS:
        candidate = Fraction(round(value * denom), denom)
        error = abs(candidate - value)
        if best_error is None or error < best_error:
            best, best_error = candidate, error
    # Only snap if we're within 4% — otherwise the number is genuinely odd
    # (e.g. 250g) and should stay as it is.
    if best_error is not None and best_error <= abs(value) * Fraction(4, 100):
        return best
    return value


def format_quantity(value: Fraction, unit: Unit | None = None) -> str:
    """Render a quantity the way a recipe would write it: ½, 1½, 2, 250."""
    if value == 0:
        return "0"

    # Gram and millilitre are never written as fractions — nobody weighs
    # 62½ grams. Round to a whole number (or 0.5 for tiny amounts).
    if unit is not None and unit.id in ("gram", "ml"):
        as_float = float(value)
        if as_float < 5:
            rounded = math.floor(as_float * 2 + 0.5) / 2
            return f"{rounded:g}"
        # round() is banker's rounding — 62.5 would give 62, not 63.
        return str(math.floor(as_float + 0.5))

    snapped = _snap(value)
    whole = int(snapped)
    remainder = snapped - whole

    if remainder == 0:
        return str(whole)

    glyph = _FRACTION_GLYPHS.get(remainder)
    if glyph:
        return f"{whole}{glyph}" if whole else glyph

    # Non-glyph fraction: show as decimal if it's tidy, else as a/b.
    as_float = float(snapped)
    if abs(as_float - round(as_float, 2)) < 1e-9:
        text = f"{as_float:.2f}".rstrip("0").rstrip(".")
        return text
    return f"{snapped.numerator}/{snapped.denominator}"


def normalize_for_display(quantity: Fraction, unit: Unit | None
                          ) -> tuple[Fraction, Unit | None]:
    """Pick the unit a cook would actually use for this quantity.

    Steps down when the number gets too small to measure (¼ cup halved is
    ⅛ cup, which no one owns a measure for — 2 tablespoons is the same thing)
    and steps up when it gets clumsily large (1000 מ"ל → 1 ליטר).
    """
    if unit is None:
        return quantity, unit

    base = Fraction(unit.factor).limit_denominator() * quantity

    # --- step up: only when this unit's own threshold is reached, and only
    # if the promoted number is clean (whole or half). 250 מ"ל must stay
    # 250 מ"ל, not become 16⅔ כפות.
    if unit.step_up_at is not None and quantity >= Fraction(unit.step_up_at).limit_denominator():
        for candidate in units_in_dimension(unit.dimension):
            if candidate.factor <= unit.factor:
                continue
            converted = base / Fraction(candidate.factor).limit_denominator()
            if converted >= 1 and converted.limit_denominator(2) == converted:
                return normalize_for_display(converted, candidate)

    # --- step down: too small to measure in this unit? ---
    if unit.step_down_below is None:
        return quantity, unit
    if base >= Fraction(unit.step_down_below).limit_denominator():
        return quantity, unit

    for candidate in units_in_dimension(unit.dimension):
        if candidate.factor >= unit.factor:
            continue
        converted = base / Fraction(candidate.factor).limit_denominator()
        if converted >= 1:
            return normalize_for_display(converted, candidate)
    return quantity, unit


def render_ingredient(item: ParsedIngredient, factor: Fraction) -> tuple[str, bool, str]:
    """Render one parsed ingredient scaled by `factor`.

    Returns (text, needs_review, reason) — review can be decided here as well
    as at parse time, because some problems only appear after scaling (half
    an egg, for instance).
    """
    if item.is_section:
        return item.raw, False, ""
    if not item.scalable or item.needs_review or item.quantity is None:
        return item.raw, item.needs_review, item.review_reason

    scaled = item.quantity * factor
    scaled, unit = normalize_for_display(scaled, item.unit)

    # A quantity with no unit is a count of discrete things — eggs, lemons,
    # onions. Scaling to a fraction of one is not something you can act on,
    # so flag it rather than instructing someone to use ¾ of an egg.
    review, reason = False, ""
    if unit is None and scaled.denominator != 1:
        review, reason = True, "כמות לא שלמה של פריט בודד"

    parts = [format_quantity(scaled, unit)]
    if unit is not None:
        parts.append(unit.label(float(scaled)))
    if item.name:
        parts.append(item.name)
    return f"{item.bullet}{' '.join(parts)}", review, reason


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class ScaledIngredients:
    factor: float
    lines: list[dict] = field(default_factory=list)
    text: str = ""
    review_count: int = 0

    def to_dict(self) -> dict:
        return {
            "factor": self.factor,
            "lines": self.lines,
            "text": self.text,
            "review_count": self.review_count,
        }


def parse_ingredients(text: str) -> list[ParsedIngredient]:
    """Parse a full ingredients blob into per-line structures."""
    return [parse_ingredient_line(line) for line in (text or "").splitlines()]


def analyze_ingredients(text: str) -> dict:
    """Inspect an ingredients blob and report what can and cannot be scaled.

    This drives the review step shown when adding or refreshing a recipe.
    Every line the parser could not handle comes back with concrete options
    for the user to choose from — we never silently guess a quantity.
    """
    items = parse_ingredients(text)
    lines: list[dict] = []

    for index, item in enumerate(items):
        gram_value = to_grams(item)
        entry: dict = {
            "index": index,
            "original": item.raw,
            "is_section": item.is_section,
            "scalable": item.scalable and item.quantity is not None
                        and not item.needs_review,
            "needs_review": item.needs_review and not item.is_section,
            "reason": item.review_reason,
            "unit": item.unit.id if item.unit else None,
            "name": item.name,
            "options": [],
            # Cup→gram conversion availability, per line
            "convertible_to_grams": gram_value is not None,
            "grams_preview": (
                f"{item.bullet}{format_quantity(gram_value, _BY_ID['gram'])} "
                f"גרם {item.name}".strip()
                if gram_value is not None else None
            ),
        }

        if item.is_section:
            lines.append(entry)
            continue

        # Build the choices offered for a line we could not scale.
        if item.needs_review:
            entry["options"] = _review_options(item)
        elif not item.scalable:
            # "קורט מלח" and friends: intentionally unscalable, not a problem.
            entry["reason"] = "כמות לא מדידה — תישאר כפי שהיא"

        lines.append(entry)

    return {
        "lines": lines,
        "review_count": sum(1 for line_ in lines if line_["needs_review"]),
        "convertible_count": sum(1 for line_ in lines if line_["convertible_to_grams"]),
    }


def _review_options(item: ParsedIngredient) -> list[dict]:
    """Concrete choices for an unparseable line. Always includes 'as is'."""
    options: list[dict] = []

    if item.review_reason == "טווח כמויות":
        # "2-3 כפות" — offer each end of the range as a concrete value.
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)",
                          item.raw)
        if match:
            low, high = match.group(1), match.group(2)
            tail = item.raw[match.end():].strip()
            options.append({
                "id": "low",
                "label": f"השתמש בכמות הנמוכה ({low})",
                "value": f"{item.bullet}{low} {tail}".rstrip(),
            })
            options.append({
                "id": "high",
                "label": f"השתמש בכמות הגבוהה ({high})",
                "value": f"{item.bullet}{high} {tail}".rstrip(),
            })

    if item.review_reason == "לא זוהתה כמות":
        options.append({
            "id": "manual",
            "label": "הזן כמות ידנית",
            "value": None,          # UI collects free text
            "needs_input": True,
        })

    # Always available, always last: leave the line exactly as written.
    options.append({
        "id": "as_is",
        "label": "הוסף כמו שזה",
        "value": item.raw,
    })
    return options


def apply_gram_conversion(text: str, only_indices: list[int] | None = None) -> str:
    """Rewrite volume measurements as grams where the ingredient is known.

    Lines whose ingredient isn't in the density table are left untouched —
    a wrong weight is worse than a cup measurement.
    """
    items = parse_ingredients(text)
    out: list[str] = []
    for index, item in enumerate(items):
        if only_indices is not None and index not in only_indices:
            out.append(item.raw)
            continue
        grams = to_grams(item)
        if grams is None:
            out.append(item.raw)
            continue
        value = format_quantity(grams, _BY_ID["gram"])
        out.append(f"{item.bullet}{value} גרם {item.name}".rstrip())
    return "\n".join(out)


def scale_ingredients(text: str, factor: float | Fraction) -> ScaledIngredients:
    """Scale every recognised quantity in an ingredients blob.

    Lines we cannot parse are passed through unchanged and flagged with
    needs_review so the UI can mark them for manual attention.
    """
    frac = Fraction(factor).limit_denominator(1000)
    items = parse_ingredients(text)

    lines: list[dict] = []
    review_count = 0
    rendered: list[str] = []

    for item in items:
        out, post_review, post_reason = render_ingredient(item, frac)
        rendered.append(out)
        flagged = (item.needs_review or post_review) and not item.is_section
        reason = item.review_reason or post_reason
        if flagged:
            review_count += 1
        lines.append({
            "original": item.raw,
            "scaled": out,
            "is_section": item.is_section,
            "scalable": item.scalable,
            "needs_review": flagged,
            "review_reason": reason if flagged else "",
            "unit": item.unit.id if item.unit else None,
        })

    return ScaledIngredients(
        factor=float(frac),
        lines=lines,
        text="\n".join(rendered),
        review_count=review_count,
    )
