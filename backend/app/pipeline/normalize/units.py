"""Billing-unit normalisation. The original unit text is always preserved."""

from __future__ import annotations

import re
from dataclasses import dataclass

# code, label, short display, aliases (lower-case, punctuation-insensitive)
UNIT_TABLE: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("hour", "Hour", "h", ("h", "hr", "hrs", "hour", "hours", "hourly", "manhour", "manhours", "mh", "man hour", "man hours")),
    ("visit", "Visit", "visit", ("visit", "visits", "call", "calls", "callout", "call out", "trip", "trips")),
    ("job", "Job", "job", ("job", "jobs", "project", "lump sum", "lumpsum", "ls")),
    ("unit", "Unit", "unit", ("unit", "units", "u")),
    ("each", "Each", "ea", ("ea", "each", "pc", "pcs", "piece", "pieces", "nos", "no", "number", "numbers", "item", "items")),
    ("ft", "Foot", "ft", ("ft", "foot", "feet", "lf", "lin ft", "linear ft", "linear foot", "linear feet", "rft", "running ft", "running feet")),
    ("lb", "Pound", "lb", ("lb", "lbs", "pound", "pounds")),
    ("kg", "Kilogram", "kg", ("kg", "kgs", "kilogram", "kilograms", "kilo", "kilos")),
    ("g", "Gram", "g", ("g", "gm", "gms", "gram", "grams")),
    ("t", "Tonne", "t", ("t", "ton", "tons", "tonne", "tonnes", "mt")),
    ("m", "Meter", "m", ("m", "meter", "meters", "metre", "metres", "mtr", "mtrs", "rmt", "running meter", "running metre")),
    ("cm", "Centimeter", "cm", ("cm", "centimeter", "centimeters", "centimetre", "centimetres")),
    ("mm", "Millimeter", "mm", ("mm", "millimeter", "millimeters", "millimetre", "millimetres")),
    ("km", "Kilometer", "km", ("km", "kms", "kilometer", "kilometers", "kilometre", "kilometres")),
    ("mi", "Mile", "mi", ("mi", "mile", "miles")),
    ("in", "Inch", "in", ("in", "inch", "inches")),
    ("yd", "Yard", "yd", ("yd", "yds", "yard", "yards")),
    ("sq_ft", "Square foot", "sq ft", ("sq ft", "sqft", "sq feet", "sq foot", "ft2", "ft²", "square foot", "square feet", "sf")),
    ("sq_m", "Square meter", "sq m", ("sq m", "sqm", "sq mtr", "m2", "m²", "square meter", "square meters", "square metre", "square metres")),
    ("sq_yd", "Square yard", "sq yd", ("sq yd", "sqyd", "square yard", "square yards")),
    ("cu_m", "Cubic meter", "m³", ("cu m", "cum", "m3", "m³", "cubic meter", "cubic meters", "cubic metre", "cubic metres", "cbm")),
    ("cu_ft", "Cubic foot", "cu ft", ("cu ft", "cuft", "ft3", "ft³", "cubic foot", "cubic feet", "cft")),
    ("l", "Liter", "L", ("l", "ltr", "ltrs", "lt", "liter", "liters", "litre", "litres")),
    ("ml", "Milliliter", "mL", ("ml", "milliliter", "milliliters", "millilitre", "millilitres")),
    ("gal", "Gallon", "gal", ("gal", "gals", "gallon", "gallons")),
    ("box", "Box", "box", ("box", "boxes", "bx")),
    ("pack", "Pack", "pack", ("pack", "packs", "pk", "pkt", "pkts", "packet", "packets")),
    ("set", "Set", "set", ("set", "sets")),
    ("pair", "Pair", "pr", ("pair", "pairs", "pr")),
    ("roll", "Roll", "roll", ("roll", "rolls")),
    ("bag", "Bag", "bag", ("bag", "bags")),
    ("sheet", "Sheet", "sheet", ("sheet", "sheets")),
    ("bottle", "Bottle", "btl", ("bottle", "bottles", "btl")),
    ("can", "Can", "can", ("can", "cans")),
    ("carton", "Carton", "ctn", ("carton", "cartons", "ctn", "ctns")),
    ("dozen", "Dozen", "doz", ("dozen", "dozens", "doz", "dz")),
    ("lot", "Lot", "lot", ("lot", "lots")),
    ("minute", "Minute", "min", ("min", "mins", "minute", "minutes")),
    ("day", "Day", "day", ("day", "days")),
    ("week", "Week", "wk", ("week", "weeks", "wk", "wks")),
    ("month", "Month", "mo", ("month", "months", "mo", "mon", "mth", "mths")),
    ("year", "Year", "yr", ("year", "years", "yr", "yrs", "annum", "pa")),
    ("license", "License", "license", ("license", "licenses", "licence", "licences", "seat", "seats", "user", "users")),
)

_ALIAS: dict[str, tuple[str, str, str]] = {}
for _code, _label, _short, _aliases in UNIT_TABLE:
    for _a in _aliases:
        _ALIAS[_a] = (_code, _label, _short)
    _ALIAS[_code.replace("_", " ")] = (_code, _label, _short)

# Units shown as quick chips in the price-book editor (matches the reference UI).
QUICK_UNITS = ("hour", "visit", "job", "unit", "each", "ft", "lb", "kg", "m", "sq_ft", "sq_m", "l", "gal", "box", "pack", "set", "day", "month")


@dataclass
class UnitInfo:
    code: str  # canonical code, or "custom"
    label: str
    short: str
    original: str
    known: bool


def _key(text: str) -> str:
    s = text.strip().lower()
    s = s.replace("²", "2").replace("³", "3")
    s = re.sub(r"^(per|/|@)\s*", "", s)
    s = re.sub(r"[.\-_]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_unit(text: str | None) -> UnitInfo | None:
    if text is None or not text.strip():
        return None
    original = text.strip()
    key = _key(original)
    if not key:
        return None
    hit = _ALIAS.get(key) or _ALIAS.get(key.replace(" ", ""))
    if hit is None and key.endswith("s"):
        hit = _ALIAS.get(key[:-1])
    if hit:
        code, label, short = hit
        return UnitInfo(code, label, short, original, True)
    label = original.strip(" /.")
    return UnitInfo("custom", label[:40], label[:16], original, False)


def unit_options() -> list[dict]:
    return [{"code": c, "label": l, "short": s, "quick": c in QUICK_UNITS} for c, l, s, _ in UNIT_TABLE]


def unit_display(code: str | None, original: str | None = None) -> str | None:
    if not code:
        return original
    for c, label, short, _ in UNIT_TABLE:
        if c == code:
            return short
    return original or code
