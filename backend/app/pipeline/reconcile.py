"""Cross-validation between the vision model, OCR evidence and layout rules.

* Every vision value is checked against the OCR tokens it cites. Agreement adds
  the OCR source; disagreement keeps both readings (``alternatives``) and routes
  the field to review.
* Vision rows are aligned with layout rows (order-preserving DP). Values found
  only by one method are kept and flagged — nothing is silently dropped.
"""

from __future__ import annotations

import re
from decimal import Decimal

from rapidfuzz import fuzz

from app.schemas.extraction import (
    DOCUMENT_TYPES,
    HEADER_KEYS,
    HEADER_KINDS,
    LINE_FIELD_KEYS,
    SUMMARY_KEYS,
    Alternative,
    ExtractedField,
    ExtractionResult,
    LineItem,
    TaxLine,
)

from .layout import Evidence, money_of, norm_text
from .llm.schema import LLMExtraction, LLMField
from .normalize.category import canonical_category, classify_item
from .normalize.currency import detect_currency
from .normalize.dates import parse_date
from .normalize.money import dec, parse_number, parse_percent, parse_quantity, to_canonical
from .normalize.tax import parse_tax_label
from .normalize.units import normalize_unit
from .ocr.base import Token, Word
from .rules import new_id

KIND_FOR_LINE = {
    "name": "text", "description": "text", "sku": "code", "quantity": "number", "unit": "unit", "rate": "money",
    "discount": "money", "tax_rate": "percent", "tax_amount": "money", "amount": "money", "line_total": "money",
}


class Reconciler:
    def __init__(self, ev: Evidence, dec_hint: str | None, date_order: str | None, minor_units: int = 2):
        self.ev = ev
        self.tokens: dict[str, Token] = ev.tokens
        self.dec_hint = dec_hint
        self.date_order = date_order
        self.mu = minor_units

    # ------------------------------------------------------------ normalise
    def normalize(self, kind: str, raw: str | None) -> tuple[str | None, list[str], str]:
        """Return (value, issues, kind) for a printed string."""
        if raw is None or not str(raw).strip():
            return None, [], kind
        raw = str(raw).strip()
        if kind == "money":
            if "%" in raw:
                pct = parse_percent(raw)
                return (to_canonical(pct, 0) if pct is not None else None), [], "percent"
            p = parse_number(raw, self.dec_hint)
            issues = []
            if p.value is None:
                issues.append(f"Unreadable amount '{raw}'")
            elif p.ambiguous:
                issues.append("Ambiguous number format")
            return (to_canonical(p.value, self.mu) if p.value is not None else None), issues, kind
        if kind == "number":
            q = parse_quantity(raw, self.dec_hint)
            if q.value is None:
                return None, [f"Unreadable quantity '{raw}'"], kind
            return to_canonical(q.value, 0), [], kind
        if kind == "percent":
            pct = parse_percent(raw)
            if pct is None:
                if re.search(r"(?i)exempt|nil|n/?a|free|not\s*taxable", raw):
                    return "0", ["Exempt / not taxable"], kind
                return None, [f"Unreadable percentage '{raw}'"], kind
            return to_canonical(pct, 0), [], kind
        if kind == "date":
            pd = parse_date(raw, self.date_order)
            return pd.iso, list(pd.issues), kind
        if kind == "unit":
            ui = normalize_unit(raw)
            if ui is None:
                return None, [], kind
            return (ui.code if ui.known else ui.label), ([] if ui.known else [f"Custom unit '{raw}'"]), kind
        return raw, [], kind

    # ------------------------------------------------------------ comparison
    def _num_equal(self, kind: str, value: str | None, text: str) -> bool:
        if value is None:
            return False
        v = dec(value)
        if v is None:
            return False
        if kind == "percent":
            p = parse_percent(text)
            return p is not None and p == v
        if kind == "number":
            q = parse_quantity(text, self.dec_hint)
            if q.value is not None and q.value == v:
                return True
        p = money_of(text, self.dec_hint)
        return p is not None and p.value is not None and abs(p.value) == abs(v)

    def compare(self, kind: str, raw: str, value: str | None, toks: list[Token]) -> list[Word] | None:
        """Return the matching words if the tokens support the value, else None."""
        words = [w for t in toks for w in t.words] or []
        if kind in ("money", "number", "percent"):
            for span in (1, 2, 3):
                for i in range(len(words) - span + 1):
                    txt = " ".join(w.text for w in words[i : i + span])
                    if self._num_equal(kind, value, txt):
                        return words[i : i + span]
            for t in toks:
                if self._num_equal(kind, value, t.text):
                    return t.words or [Word(t.text, t.conf, t.x0, t.y0, t.x1, t.y1)]
            return None
        if kind == "date" and value:
            for t in toks:
                pd = parse_date(t.text, self.date_order)
                if pd.iso == value:
                    return self.ev.match_words([t], raw) or t.words
        target = norm_text(raw)
        joined = norm_text(" ".join(t.text for t in toks))
        if target and target in joined:
            return self.ev.match_words(toks, raw) or [w for t in toks for w in t.words]
        return None

    def search(self, kind: str, raw: str, value: str | None, page: int | None, near: list[Token]) -> tuple[list[Token], list[Word]] | None:
        cands: list[tuple[Token, list[Word]]] = []
        for t in self.tokens.values():
            if t.noise or (page is not None and t.page != page):
                continue
            w = self.compare(kind, raw, value, [t])
            if w:
                cands.append((t, w))
        if not cands:
            return None
        if len(cands) == 1:
            return [cands[0][0]], cands[0][1]
        if near:
            cy = sum(t.cy for t in near) / len(near)
            cx = sum(t.cx for t in near) / len(near)
            best = min(cands, key=lambda c: abs(c[0].cy - cy) * 3 + abs(c[0].cx - cx) * 0.2)
            return [best[0]], best[1]
        return None

    # ------------------------------------------------------------ grounding
    def ground(self, kind: str, lf: LLMField | None, page: int | None = None, near: list[Token] | None = None) -> ExtractedField:
        if lf is None or (lf.text is None and not lf.tokens):
            return ExtractedField(kind=kind)  # type: ignore[arg-type]
        raw = lf.text.strip() if lf.text else None
        value, issues, kind2 = self.normalize(kind, raw)
        toks = [self.tokens[i] for i in lf.tokens if i in self.tokens]
        f = ExtractedField(kind=kind2, raw=raw, value=value, extracted_value=value,  # type: ignore[arg-type]
                           extraction_confidence=lf.confidence, sources=["vision"], issues=issues)
        if raw is None:
            f.issues.append("Vision model could not read this value")
            if toks:
                self._set_evidence(f, toks, None)
            return f
        words = self.compare(kind2, raw, value, toks) if toks else None
        if words:
            self._set_evidence(f, toks, words)
            f.sources = ["vision"] + sorted({("pdf_text" if t.source == "pdf_text" else "layout") for t in toks})
            return f
        found = self.search(kind2, raw, value, page if page is not None else (toks[0].page if toks else None), near or toks)
        if found:
            ftoks, fwords = found
            self._set_evidence(f, ftoks, fwords)
            f.sources = ["vision"] + sorted({("pdf_text" if t.source == "pdf_text" else "layout") for t in ftoks})
            return f
        if toks:
            ocr_txt = " ".join(t.text for t in toks)
            self._set_evidence(f, toks, None)
            alt_val, _, _ = self.normalize(kind2, ocr_txt) if kind2 in ("money", "percent", "number", "date") else (ocr_txt, [], kind2)
            f.alternatives.append(Alternative(source="ocr", value=alt_val, raw=ocr_txt))
            f.issues.append(f"Vision model read '{raw}' but OCR read '{ocr_txt}'")
        else:
            f.issues.append("No OCR evidence for this value (vision-only reading)")
        return f

    def _set_evidence(self, f: ExtractedField, toks: list[Token], words: list[Word] | None) -> None:
        f.page = toks[0].page
        f.token_ids = [t.id for t in toks]
        f.bbox = self.ev.bbox(toks, words)
        f.ocr_text = " ".join(t.text for t in toks)
        confs = [w.conf for w in words] if words else [t.conf for t in toks]
        f.ocr_confidence = round(min(confs), 4) if confs else None
        for t in toks:
            if t.alt_text:
                f.alternatives.append(Alternative(source="ocr_pass_b", raw=t.alt_text, note=f"conf {t.alt_conf}"))
                f.issues.append(f"OCR passes disagree: '{t.text}' vs '{t.alt_text}'")

    # ------------------------------------------------------------ build
    def from_llm(self, llm: LLMExtraction) -> ExtractionResult:
        ex = ExtractionResult()
        dt = (llm.document_type or "other").lower()
        ex.document_type = ExtractedField(kind="enum", value=dt if dt in DOCUMENT_TYPES else "other", raw=llm.document_type,
                                          extracted_value=dt, sources=["vision"], extraction_confidence="high")
        ex.language = llm.language
        for key in HEADER_KEYS:
            ex.header[key] = self.ground(HEADER_KINDS.get(key, "text"), llm.header.get(key))
        for i, li in enumerate(llm.line_items):
            item = LineItem(id=new_id("li"), index=i, page=li.page or 1, sources=["vision"])
            row_toks: list[Token] = []
            for key in KIND_FOR_LINE:
                lf = getattr(li, key)
                fld = self.ground(KIND_FOR_LINE[key], lf, li.page, row_toks)
                if key == "quantity" and lf is not None and lf.text and fld.value is not None:
                    q = parse_quantity(lf.text, self.dec_hint)
                    if q.unit_text and (li.unit is None or not li.unit.text):
                        ui = normalize_unit(q.unit_text)
                        if ui:
                            item.unit = ExtractedField(kind="unit", raw=q.unit_text, value=ui.code if ui.known else ui.label,
                                                       extracted_value=ui.code if ui.known else ui.label, sources=fld.sources,
                                                       page=fld.page, bbox=fld.bbox, token_ids=fld.token_ids,
                                                       ocr_confidence=fld.ocr_confidence, extraction_confidence=fld.extraction_confidence)
                if key == "unit" and fld.value is None and item.unit.value is not None:
                    continue
                setattr(item, key, fld)
                row_toks.extend(self.tokens[t] for t in fld.token_ids if t in self.tokens)
            cat = canonical_category(li.category)
            guess = classify_item(item.name.value, item.description.value, item.unit.value)
            if cat:
                conf = "high" if guess.category == cat or guess.confidence == "low" else "medium"
                item.category = ExtractedField(kind="category", value=cat, raw=li.category, extracted_value=cat,
                                               sources=["vision"], extraction_confidence=conf)  # type: ignore[arg-type]
                if guess.category != cat and guess.confidence != "low":
                    item.category.alternatives.append(Alternative(source="classifier", value=guess.category, note=guess.reason))
            else:
                item.category = ExtractedField(kind="category", value=guess.category, extracted_value=guess.category,
                                               sources=["classifier"], extraction_confidence=guess.confidence)  # type: ignore[arg-type]
            if row_toks:
                item.bbox = self.ev.bbox(row_toks)
                item.original_text = " | ".join(dict.fromkeys(t.text for t in row_toks))
            else:
                item.original_text = item.name.raw
            ex.line_items.append(item)
        for tx in llm.taxes:
            tl = TaxLine(id=new_id("tx"))
            tl.label = self.ground("text", tx.label)
            tl.amount = self.ground("money", tx.amount)
            tl.base = self.ground("money", tx.base)
            tl.rate = self.ground("percent", tx.rate)
            info = parse_tax_label(tl.label.raw)
            tl.tax_type = info.tax_type
            if tl.rate.value is None and info.rate is not None:
                tl.rate = ExtractedField(kind="percent", raw=tl.label.raw, value=to_canonical(info.rate, 0), extracted_value=to_canonical(info.rate, 0),
                                         sources=tl.label.sources, page=tl.label.page, bbox=tl.label.bbox, token_ids=tl.label.token_ids,
                                         ocr_confidence=tl.label.ocr_confidence, extraction_confidence=tl.label.extraction_confidence)
            tl.inclusive = info.inclusive
            ex.taxes.append(tl)
        for key in SUMMARY_KEYS:
            ex.summary[key] = self.ground("money", llm.summary.get(key))
        ex.prices_include_tax = llm.prices_include_tax
        ex.notes.extend(f"Vision model: {o}" for o in llm.observations if o)
        return ex.ensure_keys()


# ---------------------------------------------------------------- merging
def _same(a: ExtractedField, b: ExtractedField) -> bool:
    if a.value is None or b.value is None:
        return False
    if a.kind in ("money", "number", "percent") or b.kind in ("money", "number", "percent"):
        da, db = dec(a.value), dec(b.value)
        return da is not None and db is not None and abs(da) == abs(db)
    return norm_text(str(a.value)) == norm_text(str(b.value))


def merge_field(v: ExtractedField, r: ExtractedField, label: str = "Layout analysis") -> ExtractedField:
    if not v.present and not r.present:
        return v if v.raw else r
    if v.present and r.present:
        if _same(v, r):
            for s in r.sources:
                if s not in v.sources:
                    v.sources.append(s)
            if v.bbox is None and r.bbox is not None:
                v.bbox, v.page, v.token_ids, v.ocr_text, v.ocr_confidence = r.bbox, r.page, r.token_ids, r.ocr_text, r.ocr_confidence
            return v
        v.alternatives.append(Alternative(source="layout", value=r.value, raw=r.raw))
        v.issues.append(f"{label} read '{r.raw}'")
        return v
    if r.present:
        r.issues.append("Not reported by the vision model")
        return r
    return v


def _item_sim(v: LineItem, r: LineItem) -> float:
    s = 0.0
    for key, w in (("amount", 3.0), ("rate", 2.0), ("quantity", 1.0)):
        a, b = getattr(v, key), getattr(r, key)
        if a.present and b.present and _same(a, b):
            s += w
    if v.name.present and r.name.present:
        s += 3.0 * fuzz.token_set_ratio(norm_text(v.name.value or ""), norm_text(r.name.value or "")) / 100
    return s


def align(vs: list[LineItem], rs: list[LineItem], threshold: float = 3.0) -> list[tuple[int | None, int | None]]:
    n, m = len(vs), len(rs)
    sim = [[_item_sim(vs[i], rs[j]) for j in range(m)] for i in range(n)]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best = max(dp[i + 1][j], dp[i][j + 1])
            if sim[i][j] >= threshold:
                best = max(best, sim[i][j] + dp[i + 1][j + 1])
            dp[i][j] = best
    pairs: list[tuple[int | None, int | None]] = []
    i = j = 0
    while i < n and j < m:
        if sim[i][j] >= threshold and abs(dp[i][j] - (sim[i][j] + dp[i + 1][j + 1])) < 1e-9:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            pairs.append((i, None))
            i += 1
        else:
            pairs.append((None, j))
            j += 1
    pairs.extend((k, None) for k in range(i, n))
    pairs.extend((None, k) for k in range(j, m))
    return pairs


def merge_results(v: ExtractionResult, r: ExtractionResult, all_texts: list[str], llm_currency: str | None, llm_currency_evidence: str | None) -> ExtractionResult:
    out = v
    for key in HEADER_KEYS:
        out.header[key] = merge_field(v.header.get(key) or ExtractedField(), r.header.get(key) or ExtractedField())
    for key in SUMMARY_KEYS:
        out.summary[key] = merge_field(v.summary.get(key) or ExtractedField(kind="money"), r.summary.get(key) or ExtractedField(kind="money"))
    merged_items: list[LineItem] = []
    for vi, ri in align(v.line_items, r.line_items):
        if vi is not None and ri is not None:
            a, b = v.line_items[vi], r.line_items[ri]
            for key in LINE_FIELD_KEYS:
                if key == "category":
                    continue
                setattr(a, key, merge_field(getattr(a, key), getattr(b, key)))
            if "layout" in b.sources and "layout" not in a.sources:
                a.sources.append("layout")
            if a.bbox is None:
                a.bbox = b.bbox
            if b.original_text:
                a.original_text = b.original_text
            merged_items.append(a)
        elif vi is not None:
            item = v.line_items[vi]
            if r.line_items:
                item.issues.append("Row not detected by layout analysis")
            merged_items.append(item)
        else:
            item = r.line_items[ri]  # type: ignore[index]
            item.issues.append("Row found by layout analysis only — not reported by the vision model")
            merged_items.append(item)
    for i, it in enumerate(merged_items):
        it.index = i
    out.line_items = merged_items
    # taxes
    used: set[int] = set()
    for tl in out.taxes:
        for j, rt in enumerate(r.taxes):
            if j in used:
                continue
            same_amount = tl.amount.present and rt.amount.present and _same(tl.amount, rt.amount)
            same_type = tl.tax_type == rt.tax_type and (not tl.rate.present or not rt.rate.present or _same(tl.rate, rt.rate))
            if same_amount and same_type:
                used.add(j)
                tl.amount = merge_field(tl.amount, rt.amount)
                tl.rate = merge_field(tl.rate, rt.rate)
                tl.label = merge_field(tl.label, rt.label)
                break
    for j, rt in enumerate(r.taxes):
        if j not in used:
            rt.issues.append("Tax line found by layout analysis only")
            out.taxes.append(rt)
    if out.document_type.value in (None, "other") and r.document_type.value:
        out.document_type = r.document_type
    elif r.document_type.value and r.document_type.value != out.document_type.value:
        out.document_type.alternatives.append(Alternative(source="layout", value=r.document_type.value))
    if out.prices_include_tax is None:
        out.prices_include_tax = r.prices_include_tax
    out.notes = r.notes + out.notes
    # currency: OCR text + the vision model's transcriptions (it sees symbols OCR may drop)
    vision_money = [f.raw for f in out.summary.values() if f.raw] + [
        li.rate.raw or "" for li in out.line_items] + [li.amount.raw or "" for li in out.line_items]
    det = detect_currency(all_texts + [s for s in vision_money if s])
    out.currency.code, out.currency.symbol = det.code, det.symbol
    out.currency.confidence = det.confidence  # type: ignore[assignment]
    out.currency.ambiguous = det.ambiguous
    out.currency.evidence = det.evidence
    out.currency.candidates = det.candidates
    out.currency.issues = list(det.issues)
    out.currency.minor_units = det.minor_units
    if llm_currency and det.code and llm_currency.upper() != det.code:
        out.currency.ambiguous = True
        out.currency.issues.append(f"Vision model suggests {llm_currency.upper()} ({llm_currency_evidence or 'no reason given'})")
    elif llm_currency and not det.code:
        out.currency.code = llm_currency.upper()
        out.currency.confidence = "low"
        out.currency.ambiguous = True
        out.currency.issues.append(f"Currency from vision model only: {llm_currency_evidence or ''}".strip())
    if llm_currency_evidence and llm_currency and det.code == llm_currency.upper():
        out.currency.evidence.append(f"vision: {llm_currency_evidence}")
    return out


def total_abs(f: ExtractedField) -> Decimal | None:
    return abs(dec(f.value)) if f.value is not None and dec(f.value) is not None else None
