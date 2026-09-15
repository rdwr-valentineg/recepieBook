"""Tests for units.py — parsing, conversion and scaling."""
from fractions import Fraction

import pytest
from units import (
    analyze_ingredients,
    apply_gram_conversion,
    density_for,
    format_quantity,
    get_unit,
    normalize_for_display,
    parse_ingredient_line,
    scale_ingredients,
    to_grams,
)

# ---------------------------------------------------------------------------
# Unit lookup and conversion
# ---------------------------------------------------------------------------


def test_lookup_by_hebrew_and_english_aliases():
    assert get_unit("כוס").id == "cup"
    assert get_unit("כוסות").id == "cup"
    assert get_unit("cups").id == "cup"
    assert get_unit('ק"ג').id == "kg"
    assert get_unit("קילו").id == "kg"
    assert get_unit("nonsense") is None


def test_teaspoon_beats_tablespoon_on_prefix():
    # "כפית" starts with "כף" — the longest alias must win.
    assert get_unit("כפית").id == "tsp"
    assert get_unit("כף").id == "tbsp"


def test_conversion_within_dimension():
    cup, tbsp = get_unit("כוס"), get_unit("כף")
    assert cup.convert_to(1, tbsp) == 16.0
    assert tbsp.convert_to(16, cup) == 1.0


def test_conversion_across_dimensions_is_refused():
    # Volume→mass needs per-ingredient density; guessing corrupts recipes.
    cup, gram = get_unit("כוס"), get_unit("גרם")
    with pytest.raises(ValueError):
        cup.convert_to(1, gram)


def test_hebrew_plural_agreement():
    cup = get_unit("כוס")
    assert cup.label(1) == "כוס"
    assert cup.label(0.5) == "כוס"      # חצי כוס, not חצי כוסות
    assert cup.label(2) == "כוסות"


# ---------------------------------------------------------------------------
# Quantity parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line,qty,unit_id", [
    ("2 כוסות קמח", Fraction(2), "cup"),
    ("חצי כוס סוכר", Fraction(1, 2), "cup"),
    ("1/4 כוס שמן", Fraction(1, 4), "cup"),
    ("1½ כפית מלח", Fraction(3, 2), "tsp"),
    ("250 גרם חמאה", Fraction(250), "gram"),
    ("שתי כוסות גבינה", Fraction(2), "cup"),
    ("רבע כפית וניל", Fraction(1, 4), "tsp"),
    ("1.5 ליטר מים", Fraction(3, 2), "liter"),
    ("3 ביצים", Fraction(3), None),
])
def test_parses_quantity_and_unit(line, qty, unit_id):
    p = parse_ingredient_line(line)
    assert p.quantity == qty
    assert (p.unit.id if p.unit else None) == unit_id


def test_bare_fraction_is_not_read_as_integer():
    # Regression: "1/4" must not parse as 1.
    assert parse_ingredient_line("1/4 כוס שמן").quantity == Fraction(1, 4)


def test_mixed_number():
    assert parse_ingredient_line("1 1/2 כוסות קמח").quantity == Fraction(3, 2)


def test_section_header_detected():
    p = parse_ingredient_line("לבצק:")
    assert p.is_section and not p.scalable


def test_pinch_is_unscalable_not_unparseable():
    p = parse_ingredient_line("קורט מלח")
    assert not p.scalable
    assert not p.needs_review


def test_unparseable_line_is_flagged_and_preserved():
    p = parse_ingredient_line("• מלח ופלפל שחור גרוס טרי")
    assert p.needs_review
    assert p.raw == "• מלח ופלפל שחור גרוס טרי"


def test_range_is_flagged_rather_than_mangled():
    p = parse_ingredient_line("2-3 כפות מים")
    assert p.needs_review
    assert p.review_reason == "טווח כמויות"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_fractions_render_as_glyphs():
    assert format_quantity(Fraction(1, 2)) == "½"
    assert format_quantity(Fraction(3, 2)) == "1½"
    assert format_quantity(Fraction(2)) == "2"


def test_grams_never_render_as_fractions():
    gram = get_unit("גרם")
    assert format_quantity(Fraction(125, 2), gram) == "63"   # not "62½"


def test_step_down_to_measurable_unit():
    cup = get_unit("כוס")
    # ⅛ cup is not a thing you can measure; 2 tablespoons is.
    qty, unit = normalize_for_display(Fraction(1, 8), cup)
    assert unit.id == "tbsp" and qty == 2


def test_step_up_only_when_clean():
    ml = get_unit('מ"ל')
    # 1000ml promotes to 1 liter
    qty, unit = normalize_for_display(Fraction(1000), ml)
    assert unit.id == "liter" and qty == 1
    # 250ml must stay in ml — not become 16⅔ tablespoons
    qty, unit = normalize_for_display(Fraction(250), ml)
    assert unit.id == "ml" and qty == 250


def test_four_teaspoons_stays_teaspoons():
    tsp = get_unit("כפית")
    qty, unit = normalize_for_display(Fraction(4), tsp)
    assert unit.id == "tsp" and qty == 4
    # but 3 teaspoons is exactly 1 tablespoon
    qty, unit = normalize_for_display(Fraction(3), tsp)
    assert unit.id == "tbsp" and qty == 1


# ---------------------------------------------------------------------------
# End-to-end scaling
# ---------------------------------------------------------------------------

SAMPLE = """לבצק:
• 2 כוסות קמח
• חצי כוס סוכר
• 250 גרם חמאה
• קורט מלח
• 2-3 כפות מים"""


def test_halving():
    r = scale_ingredients(SAMPLE, 0.5)
    assert "1 כוס קמח" in r.text
    assert "¼ כוס סוכר" in r.text
    assert "125 גרם חמאה" in r.text


def test_pinch_and_range_survive_untouched():
    r = scale_ingredients(SAMPLE, 0.5)
    assert "קורט מלח" in r.text
    assert "2-3 כפות מים" in r.text          # range passed through verbatim
    assert r.review_count == 1               # only the range is flagged


def test_section_headers_preserved():
    assert scale_ingredients(SAMPLE, 0.25).text.startswith("לבצק:")


def test_fractional_egg_is_flagged():
    r = scale_ingredients("• 3 ביצים", 0.5)
    assert r.lines[0]["needs_review"]


def test_whole_eggs_are_not_flagged():
    r = scale_ingredients("• 3 ביצים", 2)
    assert not r.lines[0]["needs_review"]
    assert "6 ביצים" in r.text


def test_scaling_by_one_is_identity_for_parsed_lines():
    r = scale_ingredients("• 2 כוסות קמח", 1)
    assert r.text.strip() == "• 2 כוסות קמח"


def test_empty_input():
    r = scale_ingredients("", 0.5)
    assert r.text == "" and r.review_count == 0


# ---------------------------------------------------------------------------
# Cup → gram conversion
# ---------------------------------------------------------------------------


def test_density_lookup_prefers_longest_match():
    # "קמח שקדים" must not match the generic "קמח" entry.
    assert density_for("קמח שקדים")[0] == "קמח שקדים"
    assert density_for("קמח")[0] == "קמח"
    assert density_for("שמן זית")[0] == "שמן זית"


def test_unknown_ingredient_has_no_density():
    assert density_for("פטרוזיליה קצוצה") is None


def test_flour_and_honey_differ():
    # The whole reason this needs a density table: same volume, different mass.
    flour = to_grams(parse_ingredient_line("1 כוס קמח"))
    honey = to_grams(parse_ingredient_line("1 כוס דבש"))
    assert float(honey) > float(flour) * 2


def test_conversion_requires_volume_unit():
    # Already in grams — nothing to convert.
    assert to_grams(parse_ingredient_line("250 גרם חמאה")) is None
    # No unit at all.
    assert to_grams(parse_ingredient_line("3 ביצים")) is None


def test_unknown_ingredients_are_left_alone():
    text = "• 1 כוס קמח\n• 1 כוס פטרוזיליה קצוצה"
    out = apply_gram_conversion(text)
    assert "גרם קמח" in out
    assert "1 כוס פטרוזיליה קצוצה" in out     # untouched, not guessed


def test_conversion_can_target_specific_lines():
    text = "• 1 כוס קמח\n• 1 כוס סוכר"
    out = apply_gram_conversion(text, only_indices=[0])
    assert "גרם קמח" in out
    assert "1 כוס סוכר" in out


def test_sections_survive_conversion():
    assert apply_gram_conversion("לבצק:\n• 1 כוס קמח").startswith("לבצק:")


# ---------------------------------------------------------------------------
# Review analysis
# ---------------------------------------------------------------------------


def test_analyze_flags_only_real_problems():
    a = analyze_ingredients("• 2 כוסות קמח\n• קורט מלח\n• 2-3 כפות מים")
    by_line = {line["original"]: line for line in a["lines"]}
    assert not by_line["• 2 כוסות קמח"]["needs_review"]
    assert not by_line["• קורט מלח"]["needs_review"]      # unscalable ≠ broken
    assert by_line["• 2-3 כפות מים"]["needs_review"]
    assert a["review_count"] == 1


def test_range_offers_both_ends_and_as_is():
    a = analyze_ingredients("• 2-3 כפות מים")
    ids = [o["id"] for o in a["lines"][0]["options"]]
    assert ids == ["low", "high", "as_is"]


def test_unrecognised_quantity_offers_manual_entry():
    a = analyze_ingredients("• קצת פטרוזיליה")
    options = a["lines"][0]["options"]
    assert [o["id"] for o in options] == ["manual", "as_is"]
    assert options[0]["needs_input"] is True


def test_as_is_option_is_always_offered():
    for text in ("• 2-3 כפות מים", "• קצת פטרוזיליה"):
        options = analyze_ingredients(text)["lines"][0]["options"]
        assert options[-1]["id"] == "as_is"
        assert options[-1]["value"] == text


def test_analyze_reports_convertible_lines():
    a = analyze_ingredients("• 1 כוס קמח\n• 1 כוס פטרוזיליה")
    assert a["lines"][0]["convertible_to_grams"]
    assert "גרם" in a["lines"][0]["grams_preview"]
    assert not a["lines"][1]["convertible_to_grams"]
    assert a["convertible_count"] == 1


def test_analyze_handles_empty_input():
    a = analyze_ingredients("")
    assert a["review_count"] == 0 and a["convertible_count"] == 0
