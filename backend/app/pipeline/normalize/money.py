"""Deterministic money / number / percent / quantity parsing.

All arithmetic uses :class:`decimal.Decimal`. Nothing here guesses: when a
string cannot be parsed unambiguously the result carries ``ambiguous=True``
and/or ``issues`` so the field is routed to human review.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ISO_CODES = (
    "USD INR EUR GBP AED CAD AUD JPY SGD NZD HKD CHF CNY SAR QAR KWD BHD OMR JOD ZAR MXN BRL "
    "SEK NOK DKK PLN CZK HUF TRY RUB KRW THB MYR IDR PHP VND PKR LKR BDT NPR EGP NGN KES ILS UAH"
).split()

_ISO_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(ISO_CODES) + r")(?![A-Za-z])")
_RS_RE = re.compile(r"(?i)(?<![A-Za-z])rs\.?(?=\s*[\d(\-−])")

# Longest markers first so that "US$" wins over "$".
CURRENCY_MARKERS: tuple[tuple[str, str], ...] = (
    ("US$", "USD"),
    ("CA$", "CAD"),
    ("C$", "CAD"),
    ("AU$", "AUD"),
    ("A$", "AUD"),
    ("NZ$", "NZD"),
    ("SG$", "SGD"),
    ("S$", "SGD"),
    ("HK$", "HKD"),
    ("MX$", "MXN"),
    ("R$", "BRL"),
    ("د.إ", "AED"),
    ("₹", "INR"),
    ("€", "EUR"),
    ("£", "GBP"),
    ("¥", "¥"),
    ("￥", "¥"),
    ("₩", "KRW"),
    ("₽", "RUB"),
    ("₺", "TRY"),
    ("₫", "VND"),
    ("₱", "PHP"),
    ("฿", "THB"),
    ("₦", "NGN"),
    ("₪", "ILS"),
    ("₴", "UAH"),
    ("$", "$"),
)

_SPACES = "      "
_MINUS = "-−–—‒﹣－"

ZERO_DECIMAL_CURRENCIES = {"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "XAF", "XOF", "PYG"}
THREE_DECIMAL_CURRENCIES = {"BHD", "KWD", "OMR", "JOD", "TND", "IQD", "LYD"}


def minor_units(currency: str | None) -> int:
    if not currency:
        return 2
    if currency in ZERO_DECIMAL_CURRENCIES:
        return 0
    if currency in THREE_DECIMAL_CURRENCIES:
        return 3
    return 2


@dataclass
class ParsedNumber:
    value: Decimal | None
    raw: str
    currency_hint: str | None = None
    negative: bool = False
    decimals: int = 0
    decimal_sep: str | None = None
    group_sep: str | None = None
    ambiguous: bool = False
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.value is not None


def _clean(raw: str) -> str:
    s = raw
    for ch in _SPACES:
        s = s.replace(ch, " ")
    return s.strip()


def strip_currency(text: str) -> tuple[str, str | None]:
    """Remove currency markers from ``text``; return (rest, hint).

    ``hint`` is an ISO code when unambiguous, or the raw symbol ("$", "¥", "Rs")
    when the symbol maps to several currencies.
    """
    hint: str | None = None
    s = text
    m = _ISO_RE.search(s)
    if m:
        hint = m.group(1)
        s = s[: m.start()] + " " + s[m.end() :]
    if _RS_RE.search(s):
        hint = hint or "Rs"
        s = _RS_RE.sub(" ", s)
    for marker, h in CURRENCY_MARKERS:
        if marker in s:
            hint = hint or h
            s = s.replace(marker, " ")
    return s, hint


def _valid_grouping(groups: list[str]) -> bool:
    """Western (1,234,567) or Indian (12,34,567) digit grouping."""
    if len(groups) < 2:
        return True
    first, rest = groups[0], groups[1:]
    if not (1 <= len(first) <= 3) or not first.isdigit():
        return False
    if all(len(g) == 3 and g.isdigit() for g in rest):
        return True
    # Indian lakh/crore grouping: last group 3 digits, middle groups 2 digits.
    if len(rest[-1]) == 3 and all(len(g) == 2 and g.isdigit() for g in rest[:-1]) and len(first) <= 2:
        return True
    return False


def parse_number(raw: str | None, decimal_sep_hint: str | None = None) -> ParsedNumber:
    """Parse a printed amount such as ``"-₹6,726.00"`` or ``"1.234,56 €"``."""
    result = ParsedNumber(value=None, raw=raw or "")
    if raw is None:
        return result
    s = _clean(raw)
    if not s:
        return result

    # "500/-" (Indian notation: no paise) is NOT a negative sign.
    s = re.sub(r"/\s*-\s*$", "", s)
    s, hint = strip_currency(s)
    result.currency_hint = hint
    s = s.strip()

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s and s[0] in _MINUS:
        negative, s = True, s[1:].strip()
    if s and s[-1] in _MINUS:
        negative, s = True, s[:-1].strip()
    # currency marker may have been between sign and digits: "- 500"
    if s and s[0] in _MINUS:
        negative, s = True, s[1:].strip()
    if re.fullmatch(r"(?i)(cr|dr)", s[-2:] if len(s) > 2 else ""):
        result.issues.append("credit_debit_suffix")
        s = s[:-2].strip()

    if not s or not re.search(r"\d", s):
        result.issues.append("no_digits")
        return result
    if not re.fullmatch(r"[0-9.,' ]+", s):
        # One stray leading glyph before an otherwise well-formed amount is almost always a
        # currency symbol the OCR could not read (₹ -> 'e', 'R', 'Z'). Accept it, but flag it.
        m = re.fullmatch(r"([A-Za-z])\s?(\d{1,3}(?:[,.' ]\d{2,3})*(?:[.,]\d{1,3})?)", s)
        if m:
            result.issues.append(f"unrecognised_leading_symbol:{m.group(1)}")
            result.ambiguous = True
            s = m.group(2)
        else:
            result.issues.append("non_numeric_characters")
            return result

    if " " in s:
        parts = s.split()
        joined_ok = len(parts) > 1 and 1 <= len(parts[0]) <= 3 and all(
            re.fullmatch(r"\d{3}([.,]\d+)?", p) for p in parts[1:]
        )
        if not joined_ok:
            result.issues.append("multiple_numbers")
            return result
        result.group_sep = " "
        s = "".join(parts)
    if "'" in s:
        result.group_sep = "'"
        s = s.replace("'", "")

    n_dot, n_com = s.count("."), s.count(",")
    dec: str | None = None
    grp: str | None = None
    if n_dot and n_com:
        last = max(s.rfind("."), s.rfind(","))
        dec = s[last]
        grp = "," if dec == "." else "."
        if s.count(dec) > 1 or s.rfind(grp) > last:
            result.issues.append("invalid_separators")
            return result
    elif n_dot or n_com:
        sep = "." if n_dot else ","
        if (n_dot or n_com) > 1:
            grp = sep
        else:
            after = len(s) - s.rfind(sep) - 1
            if after == 3 and s.rfind(sep) > 0:
                if decimal_sep_hint == sep:
                    dec = sep
                elif decimal_sep_hint is not None:
                    grp = sep
                else:
                    # No document-level evidence: thousands for ',' (1,500) and
                    # decimal for '.' (1.500). Either way: flag for review.
                    if sep == ",":
                        grp = sep
                    else:
                        dec = sep
                    result.ambiguous = True
                    result.issues.append("ambiguous_separator")
            else:
                dec = sep

    if dec:
        int_part, frac_part = s.split(dec, 1)
    else:
        int_part, frac_part = s, ""
    if grp:
        groups = int_part.split(grp)
        if not _valid_grouping(groups):
            result.ambiguous = True
            result.issues.append("unusual_digit_grouping")
        int_part = "".join(groups)
    if int_part == "":
        int_part = "0"
    if not int_part.isdigit() or (frac_part and not frac_part.isdigit()):
        result.issues.append("invalid_number")
        return result
    try:
        value = Decimal(f"{int_part}.{frac_part}" if frac_part else int_part)
    except InvalidOperation:
        result.issues.append("invalid_number")
        return result

    result.value = -value if negative else value
    result.negative = negative
    result.decimals = len(frac_part)
    result.decimal_sep = dec
    result.group_sep = result.group_sep or grp
    return result


def parse_money(raw: str | None, decimal_sep_hint: str | None = None) -> ParsedNumber:
    return parse_number(raw, decimal_sep_hint)


_PERCENT_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*(?:%|pct\b|percent\b|per\s*cent\b)", re.I)


def parse_percent(raw: str | None) -> Decimal | None:
    """``"CGST 9%"`` -> 9, ``"@ 18.00 %"`` -> 18. A bare number is accepted too."""
    if not raw:
        return None
    s = _clean(raw)
    m = _PERCENT_RE.search(s)
    if m:
        try:
            return Decimal(m.group(1).replace(",", "."))
        except InvalidOperation:
            return None
    m = re.fullmatch(r"@?\s*(\d+(?:[.,]\d+)?)", s)
    if m:
        try:
            return Decimal(m.group(1).replace(",", "."))
        except InvalidOperation:
            return None
    return None


_QTY_RE = re.compile(
    r"^\s*(?:[x×]\s*)?(?P<num>\d[\d.,']*)\s*(?P<unit>[A-Za-z\"'²³/.\-][A-Za-z\"'²³/. \-]*)?\s*$"
)
_UNIT_FIRST_RE = re.compile(r"^\s*(?P<unit>[A-Za-z][A-Za-z.]*)\s+(?P<num>\d[\d.,']*)\s*$")


@dataclass
class ParsedQuantity:
    value: Decimal | None
    unit_text: str | None
    raw: str
    issues: list[str] = field(default_factory=list)


def parse_quantity(raw: str | None, decimal_sep_hint: str | None = None) -> ParsedQuantity:
    """``"2 h"`` -> (2, "h"); ``"1 job"`` -> (1, "job"); ``"1,000 pcs"`` -> (1000, "pcs")."""
    if not raw or not raw.strip():
        return ParsedQuantity(None, None, raw or "")
    s = _clean(raw)
    m = _QTY_RE.match(s)
    unit = None
    num_text = None
    if m:
        num_text = m.group("num")
        unit = (m.group("unit") or "").strip(" .-") or None
    else:
        m2 = _UNIT_FIRST_RE.match(s)
        if m2:
            num_text, unit = m2.group("num"), m2.group("unit")
    if not num_text:
        return ParsedQuantity(None, None, raw, ["unparseable_quantity"])
    parsed = parse_number(num_text, decimal_sep_hint)
    if unit and unit.lower() in {"x", "×"}:
        unit = None
    issues = list(parsed.issues)
    return ParsedQuantity(parsed.value, unit, raw, issues)


def infer_decimal_separator(texts: list[str]) -> str | None:
    """Infer the document's decimal separator from all numeric strings on it."""
    votes: Counter[str] = Counter()
    for t in texts:
        s = _clean(t)
        for m in re.finditer(r"\d[\d.,' ]*\d", s):
            num = m.group(0).replace(" ", "")
            if "." in num and "," in num:
                votes["." if num.rfind(".") > num.rfind(",") else ","] += 2
                continue
            m2 = re.search(r"\d([.,])(\d{2})$", num)
            if m2:
                votes[m2.group(1)] += 1
    if not votes:
        return None
    sep, count = votes.most_common(1)[0]
    total = sum(votes.values())
    if count / total >= 0.75:
        return sep
    return None


def quantize(value: Decimal, places: int) -> Decimal:
    q = Decimal(1).scaleb(-places)
    return value.quantize(q, rounding=ROUND_HALF_UP)


def to_canonical(value: Decimal | None, min_places: int = 2) -> str | None:
    """Canonical decimal string preserving extra printed precision."""
    if value is None:
        return None
    exp = -value.as_tuple().exponent if isinstance(value.as_tuple().exponent, int) else 0
    places = max(min_places, exp)
    return f"{quantize(value, places):f}"


def dec(value: str | None) -> Decimal | None:
    """Parse a canonical decimal string (as stored in ExtractedField.value)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        parsed = parse_number(s)
        return parsed.value


def format_amount(value: Decimal | None, currency: str | None = None) -> str:
    if value is None:
        return "—"
    places = minor_units(currency)
    q = quantize(value, places)
    sign = "-" if q < 0 else ""
    return f"{sign}{abs(q):,.{places}f}"
