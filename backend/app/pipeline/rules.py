"""Deterministic layout-based extractor.

Works on OCR / PDF-text tokens only, so every value it returns is grounded in
specific words on the page. It is the always-available baseline and the
cross-check for the vision model.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.schemas.extraction import (
    HEADER_KINDS,
    ExtractedField,
    ExtractionResult,
    LineItem,
    TaxLine,
)

from .layout import Evidence, Line, PageData, build_lines, money_of, norm_text
from .normalize.category import canonical_category, classify_item
from .normalize.currency import date_order_for_currency, detect_currency
from .normalize.dates import parse_date
from .normalize.money import (
    infer_decimal_separator,
    parse_number,
    parse_percent,
    parse_quantity,
    to_canonical,
)
from .normalize.tax import TAX_LABEL_RE, parse_tax_label
from .normalize.units import normalize_unit
from .ocr.base import Token, Word


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- vocab
HEADER_LABELS: tuple[tuple[str, re.Pattern], ...] = (
    ("document_number", re.compile(
        r"(?i)^(?:tax\s+)?(?:invoice|inv|bill|receipt|quotation|quote|estimate|credit\s+note|debit\s+note|document|doc|order)\.?"
        r"\s*(?:#|no\.?|number|num\.?)(?=\s|:|$)\s*:?")),
    ("po_number", re.compile(r"(?i)^(?:p\.?\s?o\.?|purchase\s+order)(?:\s*(?:#|no\.?|number))?(?=\s|:|$)\s*:?")),
    ("reference_number", re.compile(r"(?i)^(?:ref(?:erence)?\.?|your\s+ref\.?)(?:\s*(?:#|no\.?|number))?(?=\s|:|$)\s*:?")),
    ("due_date", re.compile(r"(?i)^(?:due\s+date|due\s+on|due|payment\s+due(?:\s+date)?|pay\s+by)(?=\s|:|$)\s*:?")),
    ("document_date", re.compile(
        r"(?i)^(?:(?:invoice|bill|issue|document|receipt|quote|order|tax\s+point)\s+date|date\s+of\s+issue|date\s+issued|"
        r"issued(?:\s+on)?|dated|date)(?=\s|:|$)\s*:?")),
    ("payment_terms", re.compile(r"(?i)^(?:payment\s+terms|terms(?:\s+of\s+payment)?)(?=\s|:|$)\s*:?")),
    ("place_of_supply", re.compile(r"(?i)^place\s+of\s+supply(?=\s|:|$)\s*:?")),
)

VENDOR_LABEL = re.compile(r"(?i)^(from|seller|vendor|supplier|billed\s+by|bill\s+from|sold\s+by|issued\s+by|service\s+provider|payee)\s*:?\s*(.*)$")
CUSTOMER_LABEL = re.compile(r"(?i)^(bill(?:ed)?\s+to|invoice\s+to|sold\s+to|customer|client|buyer|recipient|to)\s*:\s*(.*)$|^(bill(?:ed)?\s+to|invoice\s+to|sold\s+to|customer|client|buyer|recipient)\s*$")
SHIP_LABEL = re.compile(r"(?i)^(ship(?:ped)?\s+to|deliver(?:y)?\s+to|delivery\s+address|site\s+address)\s*:?\s*(.*)$")

COLUMN_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("index", re.compile(r"^(#|s\.?\s?no\.?|sr\.?\s?no\.?|sl\.?\s?no\.?|no\.?|item\s*#|line)$")),
    ("sku", re.compile(r"^(sku|item\s*code|code|part\s*(no\.?|#|number)|hsn(/sac)?|sac|hsn\s*code|model|article\s*no\.?)$")),
    ("quantity", re.compile(r"^(qty\.?|quantity|qnty|hrs\.?|hours|nos\.?|pcs|count|no\.?\s*of\s*units|units)$")),
    ("unit", re.compile(r"^(unit|uom|u/m|unit\s*of\s*measure)$")),
    ("rate", re.compile(r"^(rate|price|unit\s*price|unit\s*cost|cost|price\s*/\s*unit|rate\s*/\s*unit|unit\s*rate|each|mrp|list\s*price|price\s*per\s*unit|rate\s*\(.*\)|price\s*\(.*\))$")),
    ("discount", re.compile(r"^(disc\.?|discount|disc\.?\s*%|discount\s*%|disc\.?\s*amt\.?)$")),
    ("tax_rate", re.compile(r"^(tax\s*%|tax\s*rate|gst\s*%|gst\s*rate|vat\s*%|vat\s*rate|igst\s*%|cgst\s*%|sgst\s*%|tax|gst|vat|igst|cgst|sgst)$")),
    ("tax_amount", re.compile(r"^(tax\s*amount|tax\s*amt\.?|gst\s*amount|gst\s*amt\.?|vat\s*amount|vat\s*amt\.?)$")),
    ("amount", re.compile(r"^(amount|total|line\s*total|amt\.?|net\s*amount|value|total\s*amount|amount\s*\(.*\)|total\s*\(.*\)|ext\.?\s*price|extended|subtotal)$")),
    ("description", re.compile(r"^(items?|description|item\s*description|particulars|products?|services?|details|name|item\s*name|goods|work\s*description|task|activity|description\s*of\s*goods(/services)?)$")),
)

SUMMARY_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("balance_due", re.compile(r"(?i)^(balance\s*due|balance\s*payable|balance\s*amount|balance|amount\s*outstanding|outstanding(\s*balance)?|remaining\s*balance|net\s*due|due\s*amount)\b")),
    ("amount_paid", re.compile(r"(?i)^(amount\s*paid|paid(\s*amount)?|payments?(\s*(received|made))?|less\s*:?\s*(amount\s*)?paid|deposit(\s*paid)?|advance(\s*paid)?|amount\s*received)\b(?!\s*(terms|method|mode|details|info|instructions|by))")),
    ("tax_total", re.compile(r"(?i)^(total\s*(tax|gst|vat)(\s*amount)?|tax\s*total|(gst|vat)\s*total)\b")),
    ("taxable_amount", re.compile(r"(?i)^(taxable\s*(amount|value)|net\s*taxable)\b")),
    ("subtotal", re.compile(r"(?i)^(sub[\s-]*total|total\s*before\s*tax|amount\s*before\s*tax|total\s*excl\.?|total\s*\(excl)")),
    ("discount_total", re.compile(r"(?i)^(total\s*discount|discount|less\s*:?\s*discount|disc\.?)\b")),
    ("shipping", re.compile(r"(?i)^(shipping(\s*(&|and)\s*handling)?|freight|delivery(\s*charges?)?|postage|carriage)\b")),
    ("rounding", re.compile(r"(?i)^(round(ing|ed)?[\s-]*(off|adjustment)?|round\s*off)\b")),
    ("other_charges", re.compile(r"(?i)^(service\s*charge|surcharge|other\s*charges?|additional\s*charges?|packing(\s*charges?)?|convenience\s*fee|handling(\s*charges?)?)\b")),
    ("grand_total", re.compile(r"(?i)^(grand\s*total|total\s*(amount\s*)?(due|payable)|amount\s*due|invoice\s*total|total\s*amount|net\s*payable|total\s*\(incl|total\s*incl|total)\b")),
)
GRAND_TOTAL_STRENGTH = (
    (re.compile(r"(?i)^grand\s*total"), 5),
    (re.compile(r"(?i)^(invoice\s*total|total\s*amount|net\s*payable|total\s*(amount\s*)?(due|payable))"), 4),
    (re.compile(r"(?i)^total"), 3),
    (re.compile(r"(?i)^amount\s*due"), 2),
)

FOOTER_RE = re.compile(r"(?i)^(notes?|terms\s*(&|and)\s*conditions|thank\s*you|amount\s*in\s*words|rupees|bank\s*details|how\s*to\s*pay|payment\s*(details|instructions|info)|declaration|authori[sz]ed\s*signatory|signature)\b")
SECTION_RE = re.compile(r"(?i)^(services?|labou?r|materials?|parts|equipment|supplies|products?|fees|travel)\s*:?$")
DOC_TYPES: tuple[tuple[str, re.Pattern], ...] = (
    ("tax_invoice", re.compile(r"(?i)\btax\s+invoice\b")),
    ("proforma_invoice", re.compile(r"(?i)\bpro\s*-?\s*forma\b")),
    ("credit_note", re.compile(r"(?i)\bcredit\s+(note|memo)\b")),
    ("purchase_order", re.compile(r"(?i)\bpurchase\s+order\b")),
    ("quotation", re.compile(r"(?i)\bquotation\b|^quote$")),
    ("estimate", re.compile(r"(?i)\bestimate\b")),
    ("receipt", re.compile(r"(?i)\breceipt\b|\bcash\s+memo\b")),
    ("price_list", re.compile(r"(?i)\bprice\s*(list|book)\b|\brate\s*(card|list)\b|\btariff\b")),
    ("invoice", re.compile(r"(?i)\binvoice\b")),
    ("bill", re.compile(r"(?i)^bill$|\bbill\s+of\s+supply\b")),
)
UI_WORDS = re.compile(r"(?i)^(preview|back|done|cancel|edit|search.*|share|download|close|menu|settings|\d{1,2}:\d{2}(\s*[ap]m)?)$")

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A line break is only allowed right after the country code ("+91\n98250 41872").
PHONE_RE = re.compile(
    r"(?:(?:ph(?:one)?|tel|mob(?:ile)?|cell|contact)\.?\s*(?:no\.?)?\s*:?\s*)?"
    r"(\+\d{1,3}[\s.-]?\(?\d{1,5}\)?(?:[ \t.-]?\d{2,5}){1,4}|\(\d{3}\)[ \t]?\d{3}[ \t.-]\d{4}|\b\d{3}[.-]\d{3}[.-]\d{4}\b)",
    re.I,
)
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
TAXID_RE = re.compile(
    r"(?i)\b(GSTIN|GST\s*(?:No\.?|Number|Reg(?:istration)?\.?(?:\s*No\.?)?)|VAT\s*(?:No\.?|Number|Reg(?:istration)?\.?(?:\s*No\.?)?)|TRN|ABN|EIN|Tax\s*ID|TIN|UEN)"
    r"\s*[:#]?\s*([A-Z]{0,3}[ \t]?\d[\d \t-]{3,20}\d|[A-Z0-9-]{5,24})"
)
MONEY_UNIT_RE = re.compile(
    r"^\s*(?P<money>[^\s/]{0,4}\s?-?\d[\d.,' ]*\d|[^\s/]{0,4}\s?\d)\s*(?:/|per\s|each\s*/)\s*(?P<unit>[A-Za-z][A-Za-z .²]{0,15})\s*$",
    re.I,
)


@dataclass
class Column:
    type: str
    x0: float
    x1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Part:
    """A piece of a token (split at large gaps) assigned to one column."""

    token: Token
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)


@dataclass
class RowDraft:
    page: int
    cells: dict[str, list[Part]] = field(default_factory=dict)
    lines: list[Line] = field(default_factory=list)
    desc_parts: list[Part] = field(default_factory=list)
    section: str | None = None


LABEL_VOCAB = (
    "invoice", "number", "date", "issued", "due", "terms", "payment", "reference", "purchase", "order", "place",
    "supply", "bill", "billed", "receipt", "quotation", "estimate", "description", "particulars", "quantity", "price",
    "amount", "total", "subtotal", "balance", "discount", "shipping", "freight", "delivery", "paid", "tax", "rate",
    "unit", "items", "item", "customer", "vendor", "supplier", "seller", "outstanding", "taxable", "rounding", "grand",
)
_CONFUSE = str.maketrans({"0": "o", "1": "l", "|": "l", "5": "s"})


def canon_words(text: str, max_words: int = 4) -> str:
    """Canonicalise OCR-damaged label words ('Descrigtion', 'Subtota1', 'lnvoice', 'Arnount')
    so layout anchors survive noisy OCR. Values are untouched (only vocabulary look-alikes change)."""
    from rapidfuzz import fuzz, process

    words = text.split()
    out: list[str] = []
    for i, w in enumerate(words):
        if i >= max_words:
            out.extend(words[i:])
            break
        core = re.sub(r"[^\w|]", "", w)
        letters = sum(c.isalpha() for c in core)
        if len(core) >= 4 and letters >= 0.6 * len(core):
            low = core.lower()
            if low in LABEL_VOCAB:
                out.append(w)
                continue
            cands = {low.translate(_CONFUSE), low.translate(_CONFUSE).replace("rn", "m"), low.replace("rn", "m")}
            best = None
            for c in cands:
                hit = process.extractOne(c, LABEL_VOCAB, scorer=fuzz.ratio, score_cutoff=84)
                if hit and (best is None or hit[1] > best[1]):
                    best = hit
            if best:
                trail = w[len(w.rstrip(":#.")):]
                out.append(best[0] + trail)
                continue
        out.append(w)
    return " ".join(out)


def _label_match(text: str) -> tuple[str, re.Match, str] | None:
    """Return (field key, match, the text the match refers to)."""
    t = text.strip()
    for candidate in (t, canon_words(t)):
        for key, rx in HEADER_LABELS:
            m = rx.match(candidate)
            if m:
                return key, m, candidate
    return None


def _is_any_label(text: str) -> bool:
    t = text.strip()
    return bool(
        _label_match(t) or VENDOR_LABEL.match(t) or CUSTOMER_LABEL.match(t) or SHIP_LABEL.match(t)
        or _summary_key(t) or TAX_LABEL_RE.match(t)
    )


def _summary_key(text: str) -> str | None:
    t = text.strip()
    for candidate in (t, canon_words(t, 3)):
        for key, rx in SUMMARY_PATTERNS:
            if rx.match(candidate):
                return key
    return None


def _classify_header(text: str) -> str | None:
    t = re.sub(r"\s+", " ", text.strip().lower()).strip(" :.")
    for candidate in (t, canon_words(t).lower()):
        for col, rx in COLUMN_PATTERNS:
            if rx.fullmatch(candidate):
                return col
    return None


class RuleExtractor:
    def __init__(self, pages: list[PageData], date_order: str | None = None):
        self.pages = pages
        self.ev = Evidence(pages)
        self.lines: dict[int, list[Line]] = {p.page: build_lines(p.tokens) for p in pages}
        self.dims = {p.page: (p.width, p.height) for p in pages}
        live = [t for p in pages for t in p.tokens if not t.noise]
        all_texts = [t.text for t in live]
        self.dec_hint = infer_decimal_separator(all_texts)
        self.currency = detect_currency(all_texts, confidences=[t.conf for t in live])
        self.date_order = date_order or date_order_for_currency(self.currency.code)
        self.used_tokens: set[str] = set()
        self.notes: list[str] = []

    # ----------------------------------------------------------------- utils
    def _money_field(self, raw: str | None, tokens: list[Token], confidence: str = "high", match_raw: str | None = None) -> ExtractedField:
        if raw is None:
            return ExtractedField(kind="money")
        p = parse_number(raw, self.dec_hint)
        issues = []
        if p.value is None:
            issues.append(f"Unreadable amount '{raw}'")
        if p.ambiguous:
            issues.append("Ambiguous number format (" + ", ".join(p.issues) + ")")
        value = to_canonical(p.value, self.currency.minor_units) if p.value is not None else None
        return self.ev.field("money", raw.strip(), value, tokens, confidence, issues, match_raw)

    def _parts(self, tok: Token) -> list[Part]:
        words = tok.words or [Word(tok.text, tok.conf, tok.x0, tok.y0, tok.x1, tok.y1)]
        parts: list[Part] = [Part(tok, [words[0]])]
        for w in words[1:]:
            prev = parts[-1].words[-1]
            if w.x0 - prev.x1 > 1.0 * max(tok.h, 1):
                parts.append(Part(tok, [w]))
            else:
                parts[-1].words.append(w)
        return parts

    # ----------------------------------------------------------------- doc type
    def detect_doc_type(self) -> tuple[str, list[Token]]:
        first = self.pages[0]
        cands = sorted((t for t in first.tokens if not t.noise and t.y0 < first.height * 0.45), key=lambda t: -t.h)
        for t in cands[:25]:
            for name, rx in DOC_TYPES:
                if rx.search(t.text):
                    return name, [t]
        for p in self.pages:
            for t in p.tokens:
                for name, rx in DOC_TYPES[:-1]:
                    if rx.search(t.text) and name != "bill":
                        return name, [t]
        return "invoice", []

    # ----------------------------------------------------------------- header
    def _value_right_or_below(self, ln: Line, idx: int, label_tok: Token, lines: list[Line]) -> list[Token] | None:
        if idx + 1 < len(ln.tokens):
            nxt = ln.tokens[idx + 1]
            if not _is_any_label(nxt.text) and nxt.x0 - label_tok.x1 < self.dims[label_tok.page][0] * 0.6:
                return [nxt]
        # below: next line with a token starting near the label's x
        for other in lines:
            if other.y0 <= label_tok.y1 - 0.2 * label_tok.h:
                continue
            if other.y0 - label_tok.y1 > 1.6 * label_tok.h:
                break
            for t in other.tokens:
                if abs(t.x0 - label_tok.x0) < 2 * label_tok.h and not _is_any_label(t.text):
                    return [t]
        return None

    def extract_header(self, region_bottom: float) -> dict[str, ExtractedField]:
        out: dict[str, ExtractedField] = {}
        page = self.pages[0].page
        lines = self.lines[page]
        for ln in lines:
            if ln.y0 > region_bottom:
                break
            for i, t in enumerate(ln.tokens):
                lm = _label_match(t.text)
                if not lm:
                    continue
                key, m, used = lm
                if key in out:
                    continue
                rest = used[m.end():].strip(" :#")
                if rest:
                    vtoks, raw = [t], rest
                else:
                    vt = self._value_right_or_below(ln, i, t, lines)
                    if not vt:
                        continue
                    vtoks, raw = vt, vt[0].text.strip()
                fld = self._header_value(key, raw, vtoks)
                if fld is not None:
                    out[key] = fld
                    self.used_tokens.update(x.id for x in vtoks)
                    self.used_tokens.add(t.id)
        return out

    def _header_value(self, key: str, raw: str, toks: list[Token]) -> ExtractedField | None:
        kind = HEADER_KINDS.get(key, "text")
        if key in ("document_number", "po_number", "reference_number"):
            if not re.search(r"\d", raw) or len(raw) > 40 or re.match(r"(?i)^box\b", raw):
                return None
            return self.ev.field(kind, raw, raw.strip(), toks, "high")
        if key in ("document_date", "due_date"):
            pd = parse_date(raw, self.date_order)
            if pd.iso is None and not re.search(r"\d", raw):
                return None
            issues = list(pd.issues)
            conf = "medium" if pd.ambiguous else "high"
            return self.ev.field("date", raw, pd.iso, toks, conf, issues)
        if key == "payment_terms":
            if len(raw) > 60:
                return None
            return self.ev.field("text", raw, raw, toks, "high")
        return self.ev.field(kind, raw, raw, toks, "high")

    # ----------------------------------------------------------------- parties
    def _collect_block(self, label: Token, lines: list[Line], stop_y: float, right_limit: float, first: list[Token] | None) -> list[list[Token]]:
        block: list[list[Token]] = [first] if first else []
        tol = max(label.h * 1.5, 10)
        prev_y1 = label.y1
        for ln in lines:
            if ln.y0 < label.y1 - 0.3 * label.h or not ln.tokens:
                continue
            if ln.y0 > stop_y:
                break
            aligned = [t for t in ln.tokens if abs(t.x0 - label.x0) <= tol and t.id not in self.used_tokens]
            if not aligned:
                if ln.y0 - prev_y1 > 2.2 * max(label.h, ln.h):
                    break
                continue
            if ln.y0 - prev_y1 > 1.8 * max(ln.h, label.h * 0.8):
                break
            head = aligned[0]
            if _is_any_label(head.text) or _classify_header(head.text) or FOOTER_RE.match(head.text):
                break
            row = [head] + [t for t in ln.tokens if t.x0 > head.x1 and t.x1 <= right_limit and t.x0 - head.x1 < 3 * head.h and not _is_any_label(t.text)]
            block.append(row)
            prev_y1 = ln.y1
            if len(block) >= 8:
                break
        return block

    def _parse_party(self, prefix: str, block: list[list[Token]], out: dict[str, ExtractedField]) -> None:
        if not block:
            return
        name_row = block[0]
        name_text = " ".join(t.text for t in name_row).strip()
        rest_rows = block[1:]
        if GSTIN_RE.search(name_text) or EMAIL_RE.search(name_text) or TAXID_RE.search(name_text):
            rest_rows = block
            name_row = []
        if name_row:
            out[f"{prefix}_name"] = self.ev.field("text", name_text, name_text, name_row, "high")
        address_parts: list[str] = []
        address_tokens: list[Token] = []
        joined = "\n".join(" ".join(t.text for t in r) for r in rest_rows)
        joined_tokens = [t for r in rest_rows for t in r]
        phone_chunks: list[str] = []

        def tok_for(fragment: str) -> list[Token]:
            frag = norm_text(fragment)
            hits = [t for t in joined_tokens if frag and (frag in norm_text(t.text) or norm_text(t.text) in frag and len(norm_text(t.text)) > 3)]
            return hits or joined_tokens[:1]

        m = EMAIL_RE.search(joined)
        if m:
            out[f"{prefix}_email"] = self.ev.field("text", m.group(0), m.group(0), tok_for(m.group(0)), "high")
        pm = PHONE_RE.search(joined)
        if pm and len(re.sub(r"\D", "", pm.group(1))) >= 8:
            phone = re.sub(r"\s+", " ", pm.group(1)).strip()
            phone_chunks = phone.split()
            ptoks = [t for t in joined_tokens if any(c in t.text for c in phone_chunks[-1:])] or joined_tokens[:1]
            out[f"{prefix}_phone"] = self.ev.field("text", phone, phone, ptoks, "high", match_raw=phone_chunks[-1])
        tm = TAXID_RE.search(joined)
        gm = GSTIN_RE.search(joined)
        tax_raw = gm.group(0) if gm else (tm.group(2).strip() if tm else None)
        if tax_raw:
            out[f"{prefix}_tax_id"] = self.ev.field("code", tax_raw, tax_raw.replace(" ", ""), tok_for(tax_raw), "high")
        contact: str | None = None
        for row in rest_rows:
            text = " ".join(t.text for t in row)
            resid = text
            for rx in (EMAIL_RE, TAXID_RE, GSTIN_RE):
                resid = rx.sub(" ", resid)
            resid = PHONE_RE.sub(" ", resid)
            for chunk in phone_chunks:  # phone split across lines
                resid = re.sub(r"(?<![\w+])" + re.escape(chunk) + r"(?!\w)", " ", resid)
            resid = re.sub(r"(?i)\b(GSTIN|GST\s*No\.?|VAT\s*No\.?|TRN|ABN|EIN|email|e-mail|phone|tel|mob(ile)?)\s*[:.]?", " ", resid)
            resid = re.sub(r"\s*[·|•]\s*", ", ", resid)
            resid = re.sub(r"\s+", " ", resid)
            resid = re.sub(r"(,\s*)+", ", ", resid).strip(" ,;-")
            if not resid or resid == "+" or re.fullmatch(r"\+?\d{1,3}", resid):
                continue
            if contact is None and not address_parts and re.fullmatch(r"(?:(?:Mr|Ms|Mrs|Dr)\.?\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}", resid) and (EMAIL_RE.search(text) or PHONE_RE.search(text)):
                contact = resid
                if prefix == "customer":
                    out["customer_contact"] = self.ev.field("text", resid, resid, row, "high", match_raw=resid)
                continue
            address_parts.append(resid)
            address_tokens.extend(row)
        if address_parts:
            addr = re.sub(r"\s*\+\d{1,3}$", "", ", ".join(address_parts)).strip(" ,")
            out[f"{prefix}_address"] = self.ev.field("text", addr, addr, address_tokens, "high", match_raw="")

    def extract_parties(self, region_bottom: float) -> dict[str, ExtractedField]:
        out: dict[str, ExtractedField] = {}
        page = self.pages[0].page
        lines = self.lines[page]
        labels: list[tuple[str, Token, str | None]] = []
        for ln in lines:
            if ln.y0 > region_bottom:
                break
            for t in ln.tokens:
                txt = t.text.strip()
                vm, cm, sm = VENDOR_LABEL.match(txt), CUSTOMER_LABEL.match(txt), SHIP_LABEL.match(txt)
                if vm:
                    labels.append(("vendor", t, (vm.group(2) or "").strip() or None))
                elif cm:
                    rest = (cm.group(2) or "").strip() if cm.group(2) is not None else None
                    labels.append(("customer", t, rest or None))
                elif sm:
                    labels.append(("ship", t, (sm.group(2) or "").strip() or None))
        page_w = self.dims[page][0]
        for kind, tok, inline in labels:
            if kind == "ship" and any(k == "customer" for k, _, _ in labels):
                continue
            prefix = "vendor" if kind == "vendor" else "customer"
            if f"{prefix}_name" in out:
                continue
            others = [o for k, o, _ in labels if o is not tok and o.x0 > tok.x0 + tok.h and abs(o.cy - tok.cy) < 2 * tok.h]
            right_limit = min([o.x0 - tok.h for o in others], default=page_w)
            first = None
            if inline:
                first = [tok]
            self.used_tokens.add(tok.id)
            block = self._collect_block(tok, lines, region_bottom, right_limit, first)
            if inline:
                # "Bill To: Acme Ltd" -> the name is inside the label token
                out[f"{prefix}_name"] = self.ev.field("text", inline, inline, [tok], "high", match_raw=inline)
                rest = block[1:] if block and block[0] == [tok] else block
                if rest:
                    tmp: dict[str, ExtractedField] = {}
                    self._parse_party(prefix, [[]] + rest, tmp)
                    tmp.pop(f"{prefix}_name", None)
                    out.update(tmp)
            else:
                self._parse_party(prefix, block, out)
            for row in block:
                self.used_tokens.update(t.id for t in row if t)
        return out

    def guess_vendor(self) -> dict[str, ExtractedField]:
        first = self.pages[0]
        cands = [
            t for t in first.tokens
            if not t.noise and t.y0 < first.height * 0.3 and t.id not in self.used_tokens and len(t.text.strip()) >= 3
            and not UI_WORDS.match(t.text.strip()) and not any(rx.search(t.text) for _, rx in DOC_TYPES)
            and sum(c.isdigit() for c in t.text) / max(1, len(t.text)) < 0.3 and not _is_any_label(t.text)
            and not EMAIL_RE.search(t.text)
        ]
        if not cands:
            return {}
        best = max(cands, key=lambda t: (round(t.h / 4), -t.y0))
        return {"vendor_name": self.ev.field("text", best.text, best.text, [best], "low",
                                             ["Vendor guessed from the most prominent text — no 'From'/'Seller' label found"])}

    # ----------------------------------------------------------------- table
    def _header_columns(self, ln: Line) -> list[Column] | None:
        cols: list[Column] = []
        unmatched = 0
        for tok in ln.tokens:
            c = _classify_header(tok.text)
            if c:
                cols.append(Column(c, tok.x0, tok.x1))
                continue
            words = tok.words or []
            i, sub, ok = 0, [], True
            while i < len(words):
                for j in range(min(len(words), i + 4), i, -1):
                    c2 = _classify_header(" ".join(w.text for w in words[i:j]))
                    if c2:
                        sub.append(Column(c2, words[i].x0, words[j - 1].x1))
                        i = j
                        break
                else:
                    ok = False
                    break
            if ok and sub:
                cols.extend(sub)
            else:
                unmatched += 1
        types = [c.type for c in cols]
        has_desc = "description" in types
        numeric = {"amount", "rate", "quantity"} & set(types)
        if has_desc and numeric and len(set(types)) >= 2 and len(cols) >= max(2, unmatched):
            cols.sort(key=lambda c: c.x0)
            # disambiguate duplicates: two description columns -> name + description,
            # two amount columns -> amount + line_total
            seen_desc = seen_amt = 0
            for c in cols:
                if c.type == "description":
                    seen_desc += 1
                    if seen_desc == 2:
                        c.type = "item_description"
                elif c.type == "amount":
                    seen_amt += 1
                    if seen_amt == 2:
                        c.type = "line_total"
            return cols
        return None

    def _assign(self, part: Part, cols: list[Column]) -> Column:
        x0, x1 = part.x0, part.x1
        cx = (x0 + x1) / 2
        has_digit = bool(re.search(r"\d", part.text))

        def dist(c: Column) -> float:
            d = min(abs(x1 - c.x1), abs(cx - c.cx), abs(x0 - c.x0))
            if min(x1, c.x1) - max(x0, c.x0) > 0:
                d *= 0.5
            return d

        best = min(cols, key=dist)
        if not has_digit and best.type in ("amount", "rate", "line_total", "tax_amount", "discount"):
            text_cols = [c for c in cols if c.type in ("description", "item_description", "unit", "sku")]
            if text_cols:
                best = min(text_cols, key=dist)
        return best

    def _table_rows(self, page: int, cols: list[Column], start_y: float) -> tuple[list[RowDraft], float]:
        """Group table lines into item rows; returns rows and the y where the table ended."""
        lines = [ln for ln in self.lines[page] if ln.y0 > start_y]
        rows: list[RowDraft] = []
        pending: list[tuple[Line, list[Part]]] = []
        section: str | None = None
        end_y = self.dims[page][1]
        hs = sorted(ln.h for ln in lines) or [12.0]
        line_h = hs[len(hs) // 2]
        last_y1 = start_y
        numeric_types = {"quantity", "rate", "amount", "line_total", "tax_amount"}
        for ln in lines:
            texts = [t.text.strip() for t in ln.tokens]
            if any(_summary_key(x) or TAX_LABEL_RE.match(x) for x in texts) or FOOTER_RE.match(texts[0]):
                end_y = ln.y0
                break
            if self._header_columns(ln):
                end_y = ln.y0
                break
            if ln.y0 - last_y1 > 6 * line_h and rows:
                # big vertical gap: only continue if this line clearly is another item row
                pass
            cells: dict[str, list[Part]] = {}
            for tok in ln.tokens:
                for part in self._parts(tok):
                    col = self._assign(part, cols)
                    cells.setdefault(col.type, []).append(part)
            cells.pop("index", None)
            has_numeric = any(
                any(re.search(r"\d", p.text) for p in cells.get(ct, [])) for ct in numeric_types
            )
            desc_parts = cells.get("description", []) + cells.get("item_description", [])
            desc_text = " ".join(p.text for p in desc_parts).strip()
            if has_numeric:
                r = RowDraft(page=page, cells=cells, lines=[ln], section=section)
                if pending:
                    gap = ln.y0 - pending[-1][0].y1
                    if gap < 0.8 * line_h and not cells.get("description"):
                        r.cells["description"] = [p for _, ps in pending for p in ps]
                    elif gap < 0.8 * line_h:
                        r.cells["description"] = [p for _, ps in pending for p in ps] + cells.get("description", [])
                    pending = []
                rows.append(r)
                last_y1 = ln.y1
            elif desc_text:
                if SECTION_RE.match(desc_text) and len(desc_parts) == 1:
                    section = canonical_category(desc_text.rstrip(":"))
                    pending = []
                elif rows and ln.y0 - rows[-1].lines[-1].y1 < 1.4 * line_h and not pending:
                    rows[-1].desc_parts.extend(desc_parts)
                    rows[-1].lines.append(ln)
                    last_y1 = ln.y1
                else:
                    pending.append((ln, desc_parts))
            if ln.y0 - last_y1 > 10 * line_h and rows:
                end_y = ln.y0
                break
        return rows, end_y

    def _row_to_item(self, r: RowDraft, index: int) -> LineItem:
        li = LineItem(id=new_id("li"), index=index, page=r.page)
        tokens = [t for ln in r.lines for t in ln.tokens]
        li.original_text = " | ".join(ln.text for ln in r.lines)
        li.bbox = self.ev.bbox(tokens)
        li.sources = sorted({"pdf_text" if t.source == "pdf_text" else "layout" for t in tokens})

        def ptoks(parts: list[Part]) -> list[Token]:
            seen, out = set(), []
            for p in parts:
                if p.token.id not in seen:
                    seen.add(p.token.id)
                    out.append(p.token)
            return out

        def ptext(parts: list[Part]) -> str:
            return " ".join(p.text for p in parts).strip()

        name_parts = r.cells.get("description", [])
        if name_parts:
            nm = ptext(name_parts)
            li.name = self.ev.field("text", nm, nm, ptoks(name_parts), "high", match_raw=nm)
        desc_parts = r.cells.get("item_description", []) + r.desc_parts
        if desc_parts:
            if r.desc_parts and name_parts:
                # continuation text in the same font size -> wrapped name, smaller -> description
                name_h = max(p.token.h for p in name_parts)
                cont_h = max(p.token.h for p in r.desc_parts)
                if cont_h >= 0.92 * name_h and not r.cells.get("item_description"):
                    nm = ptext(name_parts + r.desc_parts)
                    li.name = self.ev.field("text", nm, nm, ptoks(name_parts + r.desc_parts), "high", match_raw="")
                    desc_parts = []
            if desc_parts:
                ds = ptext(desc_parts)
                li.description = self.ev.field("text", ds, ds, ptoks(desc_parts), "high", match_raw=ds)
        if r.cells.get("sku"):
            sk = ptext(r.cells["sku"])
            li.sku = self.ev.field("code", sk, sk, ptoks(r.cells["sku"]), "high")
        unit_text = None
        unit_tokens: list[Token] = []
        if r.cells.get("quantity"):
            qraw = ptext(r.cells["quantity"])
            pq = parse_quantity(qraw, self.dec_hint)
            qtoks = ptoks(r.cells["quantity"])
            if pq.value is not None:
                num_txt = re.match(r"\s*[x×]?\s*([\d.,']+)", qraw)
                num_raw = num_txt.group(1) if num_txt else qraw
                li.quantity = self.ev.field("number", num_raw, to_canonical(pq.value, 0), qtoks, "high", match_raw=num_raw)
                if pq.unit_text:
                    unit_text, unit_tokens = pq.unit_text, qtoks
            else:
                li.quantity = self.ev.field("number", qraw, None, qtoks, "low", [f"Unreadable quantity '{qraw}'"])
        if r.cells.get("unit"):
            unit_text = ptext(r.cells["unit"])
            unit_tokens = ptoks(r.cells["unit"])
        if unit_text:
            ui = normalize_unit(unit_text)
            if ui:
                issues = [] if ui.known else [f"Custom unit '{unit_text}'"]
                li.unit = self.ev.field("unit", unit_text, ui.code if ui.known else ui.label, unit_tokens, "high" if ui.known else "medium", issues, match_raw=unit_text)
        if r.cells.get("rate"):
            raw = ptext(r.cells["rate"])
            mu = MONEY_UNIT_RE.match(raw)
            if mu and not unit_text:
                ui = normalize_unit(mu.group("unit"))
                if ui:
                    li.unit = self.ev.field("unit", mu.group("unit"), ui.code if ui.known else ui.label, ptoks(r.cells["rate"]), "high", match_raw=mu.group("unit"))
                raw = mu.group("money")
            li.rate = self._money_field(raw, ptoks(r.cells["rate"]), match_raw=raw)
        if r.cells.get("amount"):
            raw = ptext(r.cells["amount"])
            li.amount = self._money_field(raw, ptoks(r.cells["amount"]), match_raw=raw)
        if r.cells.get("line_total"):
            raw = ptext(r.cells["line_total"])
            li.line_total = self._money_field(raw, ptoks(r.cells["line_total"]), match_raw=raw)
        if r.cells.get("tax_amount"):
            raw = ptext(r.cells["tax_amount"])
            li.tax_amount = self._money_field(raw, ptoks(r.cells["tax_amount"]), match_raw=raw)
        if r.cells.get("tax_rate"):
            raw = ptext(r.cells["tax_rate"])
            pct = parse_percent(raw) if "%" in raw else None
            if pct is not None:
                li.tax_rate = self.ev.field("percent", raw, to_canonical(pct, 0), ptoks(r.cells["tax_rate"]), "high")
            elif re.search(r"(?i)exempt|nil|n/?a|free", raw):
                li.tax_rate = self.ev.field("percent", raw, "0", ptoks(r.cells["tax_rate"]), "medium", ["Exempt / nil tax"])
            else:
                p = parse_number(raw, self.dec_hint)
                if p.value is not None and p.value <= 30 and p.decimals == 0:
                    li.tax_rate = self.ev.field("percent", raw, to_canonical(p.value, 0), ptoks(r.cells["tax_rate"]), "medium",
                                                ["Tax column value interpreted as a percentage"])
                else:
                    li.tax_amount = self._money_field(raw, ptoks(r.cells["tax_rate"]), "medium")
        if r.cells.get("discount"):
            raw = ptext(r.cells["discount"])
            if "%" in raw:
                pct = parse_percent(raw)
                li.discount = self.ev.field("percent", raw, to_canonical(pct, 0) if pct is not None else None, ptoks(r.cells["discount"]), "high")
            else:
                li.discount = self._money_field(raw, ptoks(r.cells["discount"]))
        self._categorize(li, r.section)
        return li

    def _categorize(self, li: LineItem, section: str | None) -> None:
        if section:
            li.category = ExtractedField(kind="category", value=section, raw=section, extracted_value=section,
                                         sources=["layout"], extraction_confidence="high")
            return
        g = classify_item(li.name.value, li.description.value, li.unit.value)
        li.category = ExtractedField(
            kind="category", value=g.category, extracted_value=g.category, sources=["classifier"],
            extraction_confidence=g.confidence,  # type: ignore[arg-type]
            issues=[] if g.confidence != "low" else [f"Category uncertain ({g.reason})"],
        )

    def extract_table_items(self) -> tuple[list[LineItem], float | None, int | None, float | None]:
        """Returns (items, header_y on first table page, last page with table, table end y)."""
        items: list[LineItem] = []
        first_header_y: float | None = None
        last_page: int | None = None
        end_y: float | None = None
        prev_cols_norm: list[tuple[str, float, float]] | None = None
        for p in self.pages:
            cols = None
            header_y = None
            for ln in self.lines[p.page]:
                c = self._header_columns(ln)
                if c:
                    cols, header_y = c, ln.y1
                    break
            if cols is None and prev_cols_norm is not None:
                w = p.width
                cols = [Column(t, x0 * w, x1 * w) for t, x0, x1 in prev_cols_norm]
                header_y = 0.0
            if cols is None:
                continue
            rows, e = self._table_rows(p.page, cols, header_y or 0.0)
            if first_header_y is None and header_y:
                first_header_y = header_y
            for r in rows:
                items.append(self._row_to_item(r, len(items)))
                for ln in r.lines:
                    self.used_tokens.update(t.id for t in ln.tokens)
            last_page, end_y = p.page, e
            ended_by_summary = e < p.height - 1
            prev_cols_norm = None if ended_by_summary else [(c.type, c.x0 / p.width, c.x1 / p.width) for c in cols]
        return items, first_header_y, last_page, end_y

    def extract_price_list(self) -> list[LineItem]:
        items: list[LineItem] = []
        for p in self.pages:
            toks = [t for t in p.tokens if not t.noise]
            anchors = [t for t in toks if MONEY_UNIT_RE.match(t.text) and money_of(MONEY_UNIT_RE.match(t.text).group("money"), self.dec_hint)]
            for a in anchors:
                m = MONEY_UNIT_RE.match(a.text)
                assert m is not None
                left = [t for t in toks if t.x1 <= a.x0 + 2 and t is not a and not money_of(t.text, self.dec_hint)
                        and abs(t.cy - a.cy) <= 1.3 * max(a.h, t.h) and t.cy <= a.cy + 0.35 * a.h and not _is_any_label(t.text)]
                if not left:
                    continue
                name_tok = min(left, key=lambda t: (abs(t.cy - a.cy) + 0.3 * abs(a.x0 - t.x1) / max(a.h, 1) * 0.1))
                li = LineItem(id=new_id("li"), index=len(items), page=p.page)
                li.name = self.ev.field("text", name_tok.text, name_tok.text, [name_tok], "high")
                money_raw = m.group("money").strip()
                li.rate = self._money_field(money_raw, [a], match_raw=money_raw)
                unit_raw = m.group("unit").strip()
                ui = normalize_unit(unit_raw)
                if ui:
                    li.unit = self.ev.field("unit", unit_raw, ui.code if ui.known else ui.label, [a], "high" if ui.known else "medium",
                                            [] if ui.known else [f"Custom unit '{unit_raw}'"], match_raw=unit_raw)
                below = [t for t in toks if t is not name_tok and t.y0 >= name_tok.y1 - 0.3 * name_tok.h
                         and t.y0 - name_tok.y1 < 1.0 * name_tok.h and abs(t.x0 - name_tok.x0) < 0.6 * name_tok.h
                         and not money_of(t.text, self.dec_hint)]
                section = None
                tokens = [name_tok, a]
                if below:
                    b = min(below, key=lambda t: t.y0)
                    if SECTION_RE.match(b.text.strip()):
                        section = canonical_category(b.text.strip())
                        li.category = self.ev.field("category", b.text.strip(), section, [b], "high")
                    elif len(b.text.split()) <= 12:
                        li.description = self.ev.field("text", b.text, b.text, [b], "medium")
                    tokens.append(b)
                if section is None:
                    self._categorize(li, None)
                li.original_text = " | ".join(t.text for t in tokens)
                li.bbox = self.ev.bbox(tokens)
                li.sources = ["layout"]
                self.used_tokens.update(t.id for t in tokens)
                items.append(li)
        items.sort(key=lambda li: (li.page or 0, li.bbox.y0 if li.bbox else 0))
        for i, li in enumerate(items):
            li.index = i
        return items

    def extract_loose_items(self, start_y: float, end_label_y: float | None) -> list[LineItem]:
        items: list[LineItem] = []
        page = self.pages[0].page
        for ln in self.lines[page]:
            if ln.y0 <= start_y or (end_label_y is not None and ln.y0 >= end_label_y):
                continue
            toks = [t for t in ln.tokens if t.id not in self.used_tokens]
            if len(toks) < 2:
                continue
            last = toks[-1]
            mon = money_of(last.text, self.dec_hint)
            text_toks = [t for t in toks[:-1] if not money_of(t.text, self.dec_hint)]
            if not mon or not text_toks or _is_any_label(text_toks[0].text) or FOOTER_RE.match(text_toks[0].text):
                continue
            name = " ".join(t.text for t in text_toks)
            if len(name) < 2:
                continue
            li = LineItem(id=new_id("li"), index=len(items), page=page)
            li.name = self.ev.field("text", name, name, text_toks, "medium")
            li.amount = self._money_field(last.text, [last], "medium")
            middle = [t for t in toks[:-1] if money_of(t.text, self.dec_hint)]
            if middle:
                pq = parse_quantity(middle[0].text, self.dec_hint)
                if len(middle) >= 2:
                    li.quantity = self.ev.field("number", middle[0].text, to_canonical(pq.value, 0) if pq.value is not None else None, [middle[0]], "low")
                    li.rate = self._money_field(middle[1].text, [middle[1]], "low")
                else:
                    li.rate = self._money_field(middle[0].text, [middle[0]], "low")
            li.original_text = ln.text
            li.bbox = self.ev.bbox(toks)
            li.sources = ["layout"]
            li.issues.append("Row detected without a table header — verify columns")
            self._categorize(li, None)
            items.append(li)
        return items

    # ----------------------------------------------------------------- summary
    def extract_summary(self, start: tuple[int, float] | None) -> tuple[dict[str, ExtractedField], list[TaxLine], bool | None]:
        summary: dict[str, ExtractedField] = {}
        taxes: list[TaxLine] = []
        grand_candidates: list[tuple[int, int, float, ExtractedField, str]] = []
        prices_incl: bool | None = None
        for p in self.pages:
            for ln in self.lines[p.page]:
                if start and (p.page < start[0] or (p.page == start[0] and ln.y1 < start[1])):
                    continue
                for i, t in enumerate(ln.tokens):
                    if t.id in self.used_tokens:
                        continue
                    txt = t.text.strip()
                    is_tax = bool(TAX_LABEL_RE.match(txt)) and not re.match(r"(?i)^total\s*(tax|gst|vat)", txt)
                    key = None if is_tax else _summary_key(txt)
                    if not key and not is_tax:
                        continue
                    if re.search(r"(?i)\bincl", txt):
                        prices_incl = True
                    # value inside the label token?
                    label_txt, inline_val = txt, None
                    m = re.search(r"([\-−(]?\s*[^\w\s]{0,3}\s*\d[\d.,' ]*\d\)?|\d)\s*$", txt)
                    if m and money_of(m.group(1), self.dec_hint) and not re.search(r"%\s*$", txt):
                        cand = m.group(1)
                        before = txt[: m.start()].strip()
                        if before and (TAX_LABEL_RE.match(before) or _summary_key(before)):
                            inline_val, label_txt = cand, before
                    right = [x for x in ln.tokens[i + 1:] if x.x0 >= t.x1 - 2]
                    money_right = [x for x in right if money_of(x.text, self.dec_hint)]
                    if inline_val is None and not money_right:
                        continue
                    value_tok = money_right[-1] if money_right else t
                    if inline_val is None and self._label_between(ln.tokens, t, value_tok):
                        continue  # another label sits between this one and the number: the number is that label's
                    raw = inline_val if inline_val is not None else value_tok.text.strip()
                    fld = self._money_field(raw, [value_tok], match_raw=raw)
                    self.used_tokens.update({t.id, value_tok.id})
                    if is_tax:
                        info = parse_tax_label(label_txt)
                        tl = TaxLine(id=new_id("tx"), tax_type=info.tax_type)
                        tl.label = self.ev.field("text", label_txt, label_txt, [t], "high", match_raw=label_txt)
                        rate_tok = None
                        rate = info.rate
                        if rate is None:
                            for x in right:
                                if "%" in x.text and parse_percent(x.text) is not None:
                                    rate, rate_tok = parse_percent(x.text), x
                                    break
                        if rate is not None:
                            rt = rate_tok or t
                            rt_raw = rate_tok.text if rate_tok else (re.search(r"\d+(?:[.,]\d+)?\s*%", label_txt) or [label_txt])[0]
                            tl.rate = self.ev.field("percent", rt_raw, to_canonical(rate, 0), [rt], "high", match_raw=rt_raw)
                        if len(money_right) >= 2:
                            base_tok = money_right[-2]
                            tl.base = self._money_field(base_tok.text, [base_tok])
                        tl.amount = fld
                        if info.inclusive:
                            tl.inclusive = True
                            prices_incl = True
                        taxes.append(tl)
                        continue
                    if key == "grand_total":
                        strength = next((s for rx, s in GRAND_TOTAL_STRENGTH if rx.match(label_txt)), 1)
                        grand_candidates.append((strength, p.page, ln.y0, fld, label_txt))
                        continue
                    if key not in summary:
                        summary[key] = fld
        if grand_candidates:
            grand_candidates.sort(key=lambda g: (g[0], g[1], g[2]))
            best = self._arithmetic_grand_total(grand_candidates, summary, taxes, prices_incl) or grand_candidates[-1]
            summary["grand_total"] = best[3]
            for g in grand_candidates:
                if g is not best and re.match(r"(?i)^amount\s*due", g[4]) and "balance_due" not in summary:
                    summary["balance_due"] = g[3]
        return summary, taxes, prices_incl

    @staticmethod
    def _label_between(tokens: list[Token], left: Token, right: Token) -> bool:
        """True when a tax/summary label token lies horizontally between `left` and `right`."""
        for x in tokens:
            if x.id in (left.id, right.id) or x.x0 < left.x1 - 2 or x.x1 > right.x0 + 2:
                continue
            txt = x.text.strip()
            if TAX_LABEL_RE.match(txt) or _summary_key(txt):
                return True
        return False

    @staticmethod
    def _dec(fld: ExtractedField | None) -> Decimal | None:
        try:
            return Decimal(str(fld.value)) if fld is not None and fld.value not in (None, "") else None
        except InvalidOperation:
            return None

    def _arithmetic_grand_total(self, cands: list, summary: dict[str, ExtractedField], taxes: list[TaxLine], prices_incl: bool | None):
        """Among labelled total candidates, the one equal to subtotal + tax + charges - discount is the grand total.
        Label strength alone is fooled by lines such as "Total Account Charges" (really the subtotal)
        sitting above "Amount Due". Returns None when the arithmetic can't single one out."""
        sub = self._dec(summary.get("subtotal")) or self._dec(summary.get("taxable_amount"))
        if sub is None:
            return None
        tax = self._dec(summary.get("tax_total"))
        if tax is None:
            tax = sum((a for a in (self._dec(t.amount) for t in taxes if not t.inclusive) if a is not None), Decimal(0))
        extra = sum((self._dec(summary.get(k)) or Decimal(0) for k in ("shipping", "other_charges", "rounding")), Decimal(0))
        disc = abs(self._dec(summary.get("discount_total")) or Decimal(0))
        expected = sub + (Decimal(0) if prices_incl else tax) + extra - disc
        hits = [c for c in cands if (v := self._dec(c[3])) is not None and abs(v - expected) <= Decimal("0.011")]
        return hits[-1] if hits else None

    # ----------------------------------------------------------------- run
    def extract(self) -> ExtractionResult:
        ex = ExtractionResult()
        doc_type, dt_tokens = self.detect_doc_type()
        items, header_y, last_page, end_y = self.extract_table_items()
        first_page = self.pages[0]
        header_region_bottom = header_y if header_y else first_page.height * 0.6
        header = self.extract_header(header_region_bottom)
        parties = self.extract_parties(header_region_bottom)
        mode = "table"
        if not items:
            pl = self.extract_price_list()
            if len(pl) >= 2:
                items, mode = pl, "price_list"
                if doc_type in ("invoice", "bill"):
                    doc_type = "price_list"
        if doc_type == "price_list" and mode != "price_list":
            pl = self.extract_price_list()
            if pl:
                items, mode = pl, "price_list"
        summary_start = (last_page, end_y) if (mode == "table" and last_page is not None and end_y is not None) else None
        summary, taxes, prices_incl = self.extract_summary(summary_start)
        if not items:
            first_label_y = None
            for ln in self.lines[first_page.page]:
                if any(_summary_key(t.text) for t in ln.tokens):
                    first_label_y = ln.y0
                    break
            items = self.extract_loose_items(header_region_bottom * 0.5 if header_y is None else header_y, first_label_y)
            mode = "loose" if items else "none"
        if "vendor_name" not in parties and doc_type != "price_list":
            parties.update(self.guess_vendor())

        ex.document_type = ExtractedField(kind="enum", value=doc_type, raw=dt_tokens[0].text if dt_tokens else None,
                                          extracted_value=doc_type, sources=["layout"],
                                          extraction_confidence="high" if dt_tokens else "low")
        if dt_tokens:
            ex.document_type.bbox = self.ev.bbox(dt_tokens)
            ex.document_type.page = dt_tokens[0].page
            ex.document_type.token_ids = [dt_tokens[0].id]
        ex.header.update(header)
        ex.header.update(parties)
        ex.line_items = items
        ex.taxes = taxes
        ex.summary.update(summary)
        ex.prices_include_tax = prices_incl
        c = self.currency
        ex.currency.code = c.code
        ex.currency.symbol = c.symbol
        ex.currency.confidence = c.confidence  # type: ignore[assignment]
        ex.currency.ambiguous = c.ambiguous
        ex.currency.evidence = c.evidence
        ex.currency.candidates = c.candidates
        ex.currency.issues = c.issues
        ex.currency.minor_units = c.minor_units
        ex.extractors = ["layout"]
        ex.notes.extend(self.notes)
        ex.notes.append(f"Layout extractor mode: {mode}; decimal separator: {self.dec_hint or 'unknown'}; date order: {self.date_order or 'unknown'}")
        return ex.ensure_keys()
