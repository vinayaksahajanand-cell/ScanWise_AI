"""Currency identification from symbols, ISO codes and document context.

Never converts currencies. When the evidence is weak or conflicting the result
is flagged ``ambiguous`` so the UI asks the user to confirm.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .money import CURRENCY_MARKERS, ISO_CODES, minor_units

SYMBOL_FOR: dict[str, str] = {
    "USD": "$", "CAD": "$", "AUD": "$", "SGD": "$", "NZD": "$", "HKD": "$", "MXN": "$",
    "INR": "₹", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "KRW": "₩", "RUB": "₽",
    "TRY": "₺", "VND": "₫", "PHP": "₱", "THB": "฿", "NGN": "₦", "ILS": "₪", "UAH": "₴",
    "AED": "د.إ", "BRL": "R$", "CHF": "CHF", "SAR": "SAR", "ZAR": "R", "PKR": "Rs",
    "LKR": "Rs", "NPR": "Rs", "MYR": "RM",
}

AMBIGUOUS_SYMBOLS: dict[str, tuple[str, ...]] = {
    "$": ("USD", "CAD", "AUD", "SGD", "NZD", "HKD", "MXN"),
    "¥": ("JPY", "CNY"),
    "Rs": ("INR", "PKR", "LKR", "NPR"),
}

# (pattern, currency, weight, human-readable evidence)
CONTEXT_HINTS: tuple[tuple[str, str, float, str], ...] = (
    (r"\bGSTIN\b|\bCGST\b|\bSGST\b|\bIGST\b|\bUTGST\b|\bHSN\b|\bSAC\b", "INR", 3.0, "Indian GST terminology"),
    (r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", "INR", 3.0, "GSTIN number pattern"),
    (r"\+91[\s-]?\d", "INR", 1.5, "+91 phone number"),
    (r"(?i)\bIndia\b|\bRupees?\b|\blakhs?\b|\bcrores?\b|\bIFSC\b|\bUPI\b|@(sbi|okaxis|okhdfcbank|oksbi|ybl|paytm|upi)\b",
     "INR", 1.5, "Indian address/banking context"),
    (r"(?i)\b(Gujarat|Maharashtra|Karnataka|Tamil Nadu|Kerala|Telangana|Andhra Pradesh|Delhi|Rajasthan|Punjab|Haryana|"
     r"Uttar Pradesh|West Bengal|Madhya Pradesh|Bihar|Odisha|Goa|Assam|Mumbai|Bengaluru|Bangalore|Chennai|Hyderabad|"
     r"Kolkata|Pune|Ahmedabad|Surat|Jaipur|Lucknow|Noida|Gurgaon|Gurugram)\b", "INR", 1.5, "Indian location"),
    (r"(?i)\bTRN\b|\bUAE\b|\bDubai\b|\bAbu Dhabi\b|\bSharjah\b|\+971|\bDirhams?\b|\bAED\b", "AED", 2.0, "UAE context"),
    (r"\bVAT\s*(Reg|No|Number)\b.*|\bUnited Kingdom\b|\bEngland\b|\bLondon\b|\+44\s?\d|\bSort\s*code\b", "GBP", 1.5, "UK context"),
    (r"\b[A-Z]{1,2}\d[A-Z\d]?\s\d[A-Z]{2}\b", "GBP", 1.0, "UK postcode"),
    (r"(?i)\bCanada\b|\bGST/HST\b|\bHST\b|\bQST\b|\b(Ontario|Quebec|Alberta|British Columbia|Manitoba|Nova Scotia)\b",
     "CAD", 2.0, "Canadian context"),
    (r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b", "CAD", 1.5, "Canadian postal code"),
    (r"(?i)\bABN\b|\bAustralia\b|\b(NSW|VIC|QLD|Queensland|Victoria|New South Wales|Tasmania)\b|\+61\s?\d|\bBSB\b",
     "AUD", 2.0, "Australian context"),
    (r"(?i)\bSingapore\b|\bUEN\b|\+65\s?\d", "SGD", 2.0, "Singapore context"),
    (r"(?i)\bNew Zealand\b|\+64\s?\d|\bIRD\b", "NZD", 2.0, "New Zealand context"),
    (r"(?i)\bHong Kong\b|\+852", "HKD", 2.0, "Hong Kong context"),
    (r"(?i)\bMexico\b|\bRFC\b|\+52\s?\d", "MXN", 2.0, "Mexico context"),
    (r"(?i)\bUSA\b|\bUnited States\b|\bU\.S\.A?\b|\bEIN\b|\+1[\s-]?\(?\d{3}", "USD", 1.5, "US context"),
    (r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|"
     r"OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\s\d{5}(-\d{4})?\b", "USD", 2.0, "US state + ZIP code"),
    (r"\bSales\s+Tax\b", "USD", 0.5, "sales tax wording"),
    (r"\b(MwSt|USt|TVA|IVA|BTW|Deutschland|Germany|France|Spain|Italy|Netherlands|Belgium|Austria|Ireland|Portugal)\b|"
     r"\bIBAN\s*(DE|FR|ES|IT|NL|BE|AT|IE|PT|FI)\d", "EUR", 2.0, "Eurozone context"),
    (r"\bJapan\b|日本|消費税|\+81\s?\d|円", "JPY", 2.5, "Japanese context"),
    (r"\bChina\b|中国|增值税|人民币|\+86\s?\d|元", "CNY", 2.5, "Chinese context"),
    (r"(?i)\bPakistan\b|\bNTN\b|\+92\s?\d", "PKR", 2.0, "Pakistan context"),
    (r"(?i)\bSri Lanka\b|\+94\s?\d", "LKR", 2.0, "Sri Lanka context"),
    (r"\bNepal\b|\bPAN\s*No\b.*Nepal|\+977", "NPR", 2.0, "Nepal context"),
    (r"(?i)\bSaudi\b|\bKSA\b|\bRiyadh\b|\+966", "SAR", 2.0, "Saudi context"),
    (r"(?i)\bSouth Africa\b|\+27\s?\d", "ZAR", 2.0, "South Africa context"),
    (r"(?i)\bMalaysia\b|\bSST\b|\+60\s?\d", "MYR", 2.0, "Malaysia context"),
)

_COMPILED_HINTS = [(re.compile(p), c, w, e) for p, c, w, e in CONTEXT_HINTS]


@dataclass
class CurrencyDetection:
    code: str | None
    symbol: str | None
    confidence: str | None  # high | medium | low
    ambiguous: bool
    evidence: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def minor_units(self) -> int:
        return minor_units(self.code)


def _symbol_hits(texts: list[str], confidences: list[float] | None = None) -> dict[str, float]:
    """Count currency markers; glyphs read with low OCR confidence count for less."""
    hits: dict[str, float] = defaultdict(float)
    iso_re = re.compile(r"(?<![A-Za-z])(" + "|".join(ISO_CODES) + r")(?![A-Za-z])")
    rs_re = re.compile(r"(?i)(?<![A-Za-z])rs\.?(?=\s*\d)")
    for i, text in enumerate(texts):
        conf = confidences[i] if confidences and i < len(confidences) else 1.0
        w = 1.0 if conf >= 0.9 else 0.5 if conf >= 0.75 else 0.25
        s = text
        for m in iso_re.finditer(s):
            hits["ISO:" + m.group(1)] += w
        if rs_re.search(s):
            hits["Rs"] += w * len(rs_re.findall(s))
            s = rs_re.sub(" ", s)
        for marker, hint in CURRENCY_MARKERS:
            n = s.count(marker)
            if n:
                hits[hint] += w * n
                s = s.replace(marker, " ")
    return hits


def detect_currency(texts: list[str], amount_hints: list[str] | None = None, confidences: list[float] | None = None) -> CurrencyDetection:
    """Score currency candidates.

    ``texts``: all text on the document (OCR tokens / PDF text).
    ``confidences``: optional OCR confidence per text (aligned with ``texts``).
    """
    full_text = "\n".join(texts)
    scores: dict[str, float] = defaultdict(float)
    context: dict[str, float] = defaultdict(float)
    evidence: dict[str, list[str]] = defaultdict(list)
    hits = _symbol_hits(texts, confidences)

    strong_symbols = 0
    ambiguous_symbol: str | None = None
    for key, count in hits.items():
        if count <= 0:
            continue
        if key.startswith("ISO:"):
            code = key[4:]
            scores[code] += 5.0 + min(count, 5)
            evidence[code].append(f"ISO code {code} printed ({count}×)")
            strong_symbols += count
        elif key in AMBIGUOUS_SYMBOLS:
            ambiguous_symbol = key
            for code in AMBIGUOUS_SYMBOLS[key]:
                scores[code] += 1.0
            # $ is most commonly USD; small prior so context decides.
            prior = {"$": "USD", "¥": "JPY", "Rs": "INR"}[key]
            scores[prior] += 0.5
            for code in AMBIGUOUS_SYMBOLS[key]:
                evidence[code].append(f"'{key}' symbol ({count}×)")
        else:
            code = key
            scores[code] += 4.0 + min(count, 6) * 0.5
            evidence[code].append(f"'{SYMBOL_FOR.get(code, code)}' symbol ({count}×)")
            strong_symbols += count

    for rx, code, weight, label in _COMPILED_HINTS:
        n = len(rx.findall(full_text))
        if n:
            scores[code] += weight * min(n, 3) ** 0.5
            context[code] += weight * min(n, 3) ** 0.5
            evidence[code].append(label)

    if not scores:
        return CurrencyDetection(None, None, None, True, [], [], ["No currency symbol, code or context found"])

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    candidates = [{"code": c, "score": round(s, 2)} for c, s in ranked[:5]]
    issues: list[str] = []

    explicit = any(k == "ISO:" + best for k in hits) or (best in hits and best not in AMBIGUOUS_SYMBOLS)
    distinct_explicit = {k[4:] if k.startswith("ISO:") else k for k in hits if k not in AMBIGUOUS_SYMBOLS}
    if len(distinct_explicit) > 1:
        issues.append(f"Multiple currencies printed: {', '.join(sorted(distinct_explicit))}")

    margin = best_score - second_score
    if explicit and margin >= 2:
        confidence, ambiguous = "high", False
    elif ambiguous_symbol and best in AMBIGUOUS_SYMBOLS[ambiguous_symbol]:
        context_score = best_score - 1.5  # minus symbol share
        if context_score >= 2.0 and margin >= 1.5:
            confidence, ambiguous = "high", False
        else:
            confidence, ambiguous = "medium", True
            issues.append(f"'{ambiguous_symbol}' is used by several currencies; assumed {best} — please confirm")
    elif not explicit and best_score >= 8.0 and margin >= 5.0:
        # No symbol read, but overwhelming regional context (e.g. GSTIN + CGST/SGST + Indian address).
        confidence, ambiguous = "high", False
        issues.append(f"No currency symbol was read; {best} inferred from strong document context")
    elif not explicit and best_score >= 3.0 and margin >= 1.5:
        confidence, ambiguous = "medium", True
        issues.append(f"No currency symbol was read; {best} inferred from document context — please confirm")
    elif explicit:
        confidence, ambiguous = "medium", True
        issues.append("Conflicting currency evidence")
    else:
        confidence, ambiguous = "low", True
        issues.append(f"Currency inferred only from weak context ({best})")

    if len(distinct_explicit) > 1:
        ambiguous = True

    # Context won, but symbols of another currency were printed: say so precisely.
    other_symbols = sorted(c for c in distinct_explicit if c != best)
    if not explicit and other_symbols:
        confidence, ambiguous = "low", True
        issues = [
            f"Printed symbol(s) suggest {', '.join(other_symbols)} but the document context ({', '.join(evidence[best][:2])}) "
            f"suggests {best} — the OCR may have misread the currency symbol; please confirm"
        ]

    # A printed symbol contradicted by strong regional context is a classic OCR failure
    # (₹ is often read as € / e / 2). Never resolve it silently.
    rival = max(((c, s) for c, s in context.items() if c != best), key=lambda kv: kv[1], default=None)
    if explicit and rival and rival[1] >= 3.0 and context.get(best, 0.0) < rival[1]:
        ambiguous = True
        confidence = "low"
        issues.append(
            f"The symbol reads as {best} but the document context ({', '.join(evidence[rival[0]][:2])}) suggests "
            f"{rival[0]} — the OCR may have misread the currency symbol; please confirm"
        )
        candidates = [{"code": rival[0], "score": round(scores[rival[0]], 2)}] + [c for c in candidates if c["code"] != rival[0]]

    return CurrencyDetection(
        code=best,
        symbol=SYMBOL_FOR.get(best),
        confidence=confidence,
        ambiguous=ambiguous,
        evidence=evidence[best][:6],
        candidates=candidates,
        issues=issues,
    )


def date_order_for_currency(code: str | None) -> str | None:
    """Default day/month order implied by the currency's region."""
    if code in {"USD"}:
        return "MDY"
    if code in {"INR", "GBP", "EUR", "AUD", "NZD", "AED", "SGD", "ZAR", "PKR", "LKR", "NPR", "SAR", "MYR", "HKD", "BRL", "MXN"}:
        return "DMY"
    if code in {"JPY", "CNY", "KRW"}:
        return "YMD"
    return None
