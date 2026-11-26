"""Layout primitives: visual lines, word-level evidence and field construction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.extraction import BBox, ExtractedField

from .normalize.money import ParsedNumber, parse_number
from .ocr.base import Token, Word


@dataclass
class PageData:
    page: int
    width: float
    height: float
    tokens: list[Token]
    text_source: str = "ocr"


@dataclass
class Line:
    page: int
    tokens: list[Token] = field(default_factory=list)

    @property
    def y0(self) -> float:
        return min(t.y0 for t in self.tokens)

    @property
    def y1(self) -> float:
        return max(t.y1 for t in self.tokens)

    @property
    def cy(self) -> float:
        return sum(t.cy for t in self.tokens) / len(self.tokens)

    @property
    def h(self) -> float:
        hs = sorted(t.h for t in self.tokens)
        return hs[len(hs) // 2]

    @property
    def x0(self) -> float:
        return min(t.x0 for t in self.tokens)

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.tokens)


def _v_overlap(a: Token, b: Token) -> float:
    inter = min(a.y1, b.y1) - max(a.y0, b.y0)
    if inter <= 0:
        return 0.0
    return inter / max(1.0, min(a.h, b.h))


def build_lines(tokens: list[Token]) -> list[Line]:
    toks = sorted((t for t in tokens if not t.noise), key=lambda t: t.cy)
    lines: list[Line] = []
    for t in toks:
        best: Line | None = None
        best_score = 0.0
        for ln in lines[-10:]:
            ov = max(_v_overlap(t, m) for m in ln.tokens)
            if ov < 0.5:
                continue
            # do not merge horizontally overlapping tokens (stacked text)
            if any(min(t.x1, m.x1) - max(t.x0, m.x0) > 0.3 * min(t.w, m.w) for m in ln.tokens):
                continue
            score = ov - abs(t.cy - ln.cy) / max(t.h, 1.0) * 0.2
            if score > best_score:
                best, best_score = ln, score
        if best is None:
            lines.append(Line(t.page, [t]))
        else:
            best.tokens.append(t)
    for ln in lines:
        ln.tokens.sort(key=lambda t: t.x0)
    lines.sort(key=lambda ln: (ln.y0 + ln.y1) / 2)
    return lines


def norm_text(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


_MONEYISH = re.compile(r"^[^\w]*[\(\-−]?\s*[^\w\s]{0,3}\s*\d[\d.,' ]*\d?\s*\)?\s*[A-Z]{0,3}\s*(/-)?\s*$")


def money_of(text: str, dec_hint: str | None = None) -> ParsedNumber | None:
    """Parse ``text`` if the whole token is a monetary amount."""
    s = text.strip()
    if not s or not re.search(r"\d", s):
        return None
    if "%" in s:
        return None
    p = parse_number(s, dec_hint)
    if p.value is None:
        return None
    return p


def is_number_token(text: str) -> bool:
    return bool(re.fullmatch(r"[\s$€£₹¥\-−()]*\d[\d.,' ]*\s*", text.strip()))


class Evidence:
    """Builds ExtractedFields with page / bbox / token / OCR-confidence evidence."""

    def __init__(self, pages: list[PageData]):
        self.dims = {p.page: (p.width, p.height) for p in pages}
        self.tokens: dict[str, Token] = {t.id: t for p in pages for t in p.tokens}

    def match_words(self, tokens: list[Token], raw: str | None) -> list[Word]:
        if not raw:
            return []
        target = norm_text(raw)
        if not target:
            return []
        words = [w for t in tokens for w in t.words]
        n = len(words)
        best: list[Word] = []
        for i in range(n):
            acc = ""
            for j in range(i, n):
                acc += norm_text(words[j].text)
                if acc == target:
                    return words[i : j + 1]
                if target in acc:
                    best = best or words[i : j + 1]
                    break
                if not target.startswith(acc):
                    break
        return best

    def bbox(self, tokens: list[Token], words: list[Word] | None = None) -> BBox | None:
        items = words if words else tokens
        if not items:
            return None
        page = tokens[0].page
        w, h = self.dims.get(page, (1.0, 1.0))
        return BBox(
            page=page,
            x0=round(min(i.x0 for i in items) / w, 5),
            y0=round(min(i.y0 for i in items) / h, 5),
            x1=round(max(i.x1 for i in items) / w, 5),
            y1=round(max(i.y1 for i in items) / h, 5),
        )

    def field(
        self,
        kind: str,
        raw: str | None,
        value: str | None,
        tokens: list[Token],
        confidence: str = "high",
        issues: list[str] | None = None,
        match_raw: str | None = None,
    ) -> ExtractedField:
        tokens = [t for t in tokens if t is not None]
        words = self.match_words(tokens, match_raw if match_raw is not None else raw) if tokens else []
        if words:
            confs = [w.conf for w in words]
        else:
            confs = [t.conf for t in tokens]
        sources = sorted({("pdf_text" if t.source == "pdf_text" else "layout") for t in tokens}) or ["layout"]
        alt_issues = []
        for t in tokens:
            if t.alt_text and (not words or any(w in t.words for w in words)):
                alt_issues.append(f"OCR passes disagree: '{t.text}' vs '{t.alt_text}'")
        f = ExtractedField(
            kind=kind,  # type: ignore[arg-type]
            raw=raw,
            value=value,
            extracted_value=value,
            page=tokens[0].page if tokens else None,
            bbox=self.bbox(tokens, words) if tokens else None,
            token_ids=[t.id for t in tokens],
            ocr_text=" ".join(t.text for t in tokens) if tokens else None,
            ocr_confidence=round(min(confs), 4) if confs else None,
            extraction_confidence=confidence,  # type: ignore[arg-type]
            sources=sources,
            issues=list(issues or []) + alt_issues,
        )
        for t in tokens:
            if t.alt_text:
                from app.schemas.extraction import Alternative

                f.alternatives.append(Alternative(source="ocr_pass_b", value=None, raw=t.alt_text, note=f"conf {t.alt_conf}"))
        return f
