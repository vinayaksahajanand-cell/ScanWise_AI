"""Date parsing with explicit day/month ambiguity handling.

``03/04/2026`` is never silently resolved: unless one component is > 12 or the
document's region implies an order, the result is flagged ambiguous.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


@dataclass
class ParsedDate:
    iso: str | None
    raw: str
    ambiguous: bool = False
    issues: list[str] = field(default_factory=list)


def _year(y: str) -> int:
    v = int(y)
    if len(y) == 2:
        v += 2000 if v < 70 else 1900
    return v


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(raw: str | None, order_hint: str | None = None) -> ParsedDate:
    """Parse a printed date. ``order_hint`` is DMY / MDY / YMD (from region/settings)."""
    if not raw or not raw.strip():
        return ParsedDate(None, raw or "")
    s = raw.strip()
    s_clean = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s, flags=re.I)
    s_clean = s_clean.replace(",", " ")
    s_clean = re.sub(r"\s+", " ", s_clean)

    # 24 Sep 2026 / 24-Sep-26 / 24 September, 2026
    m = re.search(r"(?i)\b(\d{1,2})[\s\-./]*" + _MONTH_RE + r"[a-z]*\.?[\s\-./]*(\d{4}|\d{2})\b", s_clean)
    if m:
        d = _mk(_year(m.group(3)), MONTHS[m.group(2).lower()[:3] if m.group(2).lower()[:4] != "sept" else "sep"], int(m.group(1)))
        if d:
            return ParsedDate(d.isoformat(), raw)
    # Sep 24 2026 / September 24, 2026
    m = re.search(r"(?i)\b" + _MONTH_RE + r"[a-z]*\.?[\s\-./]*(\d{1,2})[\s\-./]+(\d{4}|\d{2})\b", s_clean)
    if m:
        d = _mk(_year(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
        if d:
            return ParsedDate(d.isoformat(), raw)
    # 2026-09-24 / 2026/09/24 (ISO-like, unambiguous)
    m = re.search(r"\b(\d{4})[\-/.](\d{1,2})[\-/.](\d{1,2})\b", s_clean)
    if m:
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return ParsedDate(d.isoformat(), raw)
    # 24/09/2026, 09/24/2026, 24.09.26
    m = re.search(r"\b(\d{1,2})[\-/.](\d{1,2})[\-/.](\d{4}|\d{2})\b", s_clean)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        dmy = _mk(y, b, a)
        mdy = _mk(y, a, b)
        if dmy and not mdy:
            return ParsedDate(dmy.isoformat(), raw)
        if mdy and not dmy:
            return ParsedDate(mdy.isoformat(), raw)
        if dmy and mdy:
            if dmy == mdy:
                return ParsedDate(dmy.isoformat(), raw)
            if order_hint == "MDY":
                return ParsedDate(mdy.isoformat(), raw, ambiguous=True, issues=["Day/month order assumed MM/DD from document region"])
            if order_hint == "DMY":
                return ParsedDate(dmy.isoformat(), raw, ambiguous=True, issues=["Day/month order assumed DD/MM from document region"])
            return ParsedDate(dmy.isoformat(), raw, ambiguous=True, issues=["Ambiguous day/month order — please confirm"])
        return ParsedDate(None, raw, issues=["Invalid calendar date"])
    return ParsedDate(None, raw, issues=["Unrecognised date format"])
