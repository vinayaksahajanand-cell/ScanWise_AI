"""Tax label understanding (GST/CGST/SGST/IGST/VAT/sales tax/exempt...)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from .money import parse_percent

_TYPES: tuple[tuple[str, str], ...] = (
    (r"\bCGST\b", "CGST"),
    (r"\bSGST\b", "SGST"),
    (r"\bUTGST\b", "UTGST"),
    (r"\bIGST\b", "IGST"),
    (r"\bCESS\b", "CESS"),
    (r"\bGST\s*/\s*HST\b|\bHST\b", "HST"),
    (r"\bPST\b", "PST"),
    (r"\bQST\b", "QST"),
    (r"\bGST\b", "GST"),
    (r"\bVAT\b|\bMwSt\b|\bUSt\b|\bTVA\b|\bIVA\b|\bBTW\b|\bMOMS\b", "VAT"),
    (r"\bsales\s*tax\b", "SALES_TAX"),
    (r"\bservice\s*tax\b", "SERVICE_TAX"),
    (r"\bconsumption\s*tax\b|消費税|\bJCT\b", "CONSUMPTION_TAX"),
    (r"\bexcise\b", "EXCISE"),
    (r"\bwithholding\b|\bTDS\b", "WITHHOLDING"),
    (r"\btax(es)?\b", "TAX"),
)

_EXEMPT = re.compile(r"(?i)\b(exempt(ed)?|tax[\s-]*free|non[\s-]*taxable|not\s*taxable|n/?t)\b")
_ZERO = re.compile(r"(?i)\bzero[\s-]*rated\b")
_INCLUSIVE = re.compile(r"(?i)\b(incl(\.|usive|uding|uded)?|inc\.)\b")
_EXCLUSIVE = re.compile(r"(?i)\b(excl(\.|usive|uding|uded)?|plus|\+)\b")

TAX_LABEL_RE = re.compile(
    r"(?i)^\s*(?:add\s*:?\s*|less\s*:?\s*)?(?:total\s+)?"
    r"(cgst|sgst|utgst|igst|cess|gst\s*/\s*hst|hst|pst|qst|gst|vat|mwst|ust|tva|iva|btw|sales\s*tax|service\s*tax|"
    r"consumption\s*tax|消費税|jct|(?:city|state|county|local|luxury|excise|tourism|withholding)\s*tax|tax(?:es)?)"
    r"(?:\b|(?<=税))"
)


@dataclass
class TaxInfo:
    tax_type: str | None
    rate: Decimal | None
    exempt: bool = False
    zero_rated: bool = False
    inclusive: bool | None = None


def parse_tax_label(text: str | None) -> TaxInfo:
    if not text:
        return TaxInfo(None, None)
    s = text.strip()
    tax_type = None
    for pattern, name in _TYPES:
        if re.search(pattern, s, re.I):
            tax_type = name
            break
    rate = parse_percent(s) if "%" in s or re.search(r"(?i)pct|percent", s) else None
    exempt = bool(_EXEMPT.search(s))
    zero = bool(_ZERO.search(s)) or (rate is not None and rate == 0)
    inclusive: bool | None = None
    if _INCLUSIVE.search(s):
        inclusive = True
    elif _EXCLUSIVE.search(s) and tax_type:
        inclusive = False
    if exempt and tax_type is None:
        tax_type = "EXEMPT"
    if zero and tax_type is None:
        tax_type = "ZERO_RATED"
    return TaxInfo(tax_type, rate, exempt, zero, inclusive)


def is_tax_label(text: str) -> bool:
    return bool(TAX_LABEL_RE.match(text.strip()))


def tax_mode_from_rate(rate_text: str | None) -> tuple[str, Decimal | None]:
    """Map an item's printed tax cell to a price-book tax mode."""
    if not rate_text:
        return "default", None
    info = parse_tax_label(rate_text)
    if info.exempt:
        return "not_taxable", None
    if info.rate is not None:
        return "rate", info.rate
    pct = parse_percent(rate_text)
    if pct is not None:
        return "rate", pct
    return "default", None
