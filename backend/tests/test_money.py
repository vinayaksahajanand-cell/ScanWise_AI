from decimal import Decimal

import pytest

from app.pipeline.normalize.money import (
    infer_decimal_separator,
    parse_number,
    parse_percent,
    parse_quantity,
    to_canonical,
)


@pytest.mark.parametrize(
    "raw,expected,hint",
    [
        ("₹4,500.00", Decimal("4500.00"), "INR"),
        ("$40.00", Decimal("40.00"), "$"),
        ("1.234,56 €", Decimal("1234.56"), "EUR"),
        ("1 234,56", Decimal("1234.56"), None),
        ("₹1,23,456.78", Decimal("123456.78"), "INR"),
        ("-₹6,726.00", Decimal("-6726.00"), "INR"),
        ("−₹6,726.00", Decimal("-6726.00"), "INR"),  # unicode minus
        ("(1,250.50)", Decimal("-1250.50"), None),
        ("Rs. 500/-", Decimal("500"), "Rs"),  # '/-' means "no paise", not negative
        ("INR 12,000", Decimal("12000"), "INR"),
        ("US$ 99.99", Decimal("99.99"), "USD"),
        ("CHF 1'234.50", Decimal("1234.50"), "CHF"),
        ("0.00", Decimal("0.00"), None),
        ("¥12,000", Decimal("12000"), "¥"),
    ],
)
def test_parse_number_formats(raw, expected, hint):
    p = parse_number(raw)
    assert p.value == expected
    assert p.currency_hint == hint


def test_decimal_hint_resolves_ambiguity():
    assert parse_number("1.500", decimal_sep_hint=",").value == Decimal("1500")
    assert parse_number("1,500", decimal_sep_hint=",").value == Decimal("1.500")
    p = parse_number("1,500")
    assert p.value == Decimal("1500") and p.ambiguous


def test_never_guesses_garbage():
    for raw in ("4,5O0.00", "12abc", "N/A", "—", ""):
        assert parse_number(raw).value is None


def test_leading_unknown_glyph_is_flagged_not_dropped():
    p = parse_number("e5,700.00")  # ₹ misread as 'e'
    assert p.value == Decimal("5700.00")
    assert p.ambiguous and any("unrecognised_leading_symbol" in i for i in p.issues)


def test_unusual_grouping_is_ambiguous():
    p = parse_number("4,50.00")
    assert p.ambiguous


def test_percent_and_quantity():
    assert parse_percent("CGST 9%") == Decimal("9")
    assert parse_percent("@ 18.00 %") == Decimal("18.00")
    assert parse_percent("VAT (20%)") == Decimal("20")
    q = parse_quantity("2 h")
    assert q.value == Decimal("2") and q.unit_text == "h"
    q = parse_quantity("1 job")
    assert q.value == Decimal("1") and q.unit_text == "job"
    q = parse_quantity("1.5hrs")
    assert q.value == Decimal("1.5") and q.unit_text == "hrs"
    assert parse_quantity("x3").value == Decimal("3")


def test_infer_decimal_separator():
    assert infer_decimal_separator(["4,500.00", "1,200.00", "5,700.00"]) == "."
    assert infer_decimal_separator(["4.500,00", "1.200,00", "12,50"]) == ","
    assert infer_decimal_separator(["hello"]) is None


def test_canonical_keeps_precision():
    assert to_canonical(Decimal("40")) == "40.00"
    assert to_canonical(Decimal("0.125")) == "0.125"
    assert to_canonical(Decimal("1E+3")) == "1000.00"
