"""PDF handling with PyMuPDF.

* Digital PDFs: native text + word boxes are used directly (exact, no OCR).
* Scanned PDFs: pages are rendered and OCR'd.
* Mixed pages: native text is kept and OCR fills in image-only regions
  (or, for full-page scans with an invisible OCR layer, our OCR is primary and
  the existing layer only fills gaps).
Pages are never rasterised for OCR unless they need it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pymupdf

from .ocr.base import Token, Word

log = logging.getLogger(__name__)


@dataclass
class PdfPageInfo:
    index: int
    width_pt: float
    height_pt: float
    text_chars: int
    garbage_ratio: float
    image_coverage: float

    @property
    def text_reliable(self) -> bool:
        return self.text_chars >= 25 and self.garbage_ratio < 0.05

    @property
    def mode(self) -> str:
        """native | mixed_native | mixed_ocr | ocr"""
        if not self.text_reliable:
            return "ocr"
        if self.image_coverage > 0.8:
            return "mixed_ocr"
        if self.image_coverage > 0.3:
            return "mixed_native"
        return "native"


def open_pdf(path: str) -> pymupdf.Document:
    doc = pymupdf.open(path)
    if doc.needs_pass:
        raise ValueError("PDF is password-protected")
    return doc


def analyze_page(page: pymupdf.Page) -> PdfPageInfo:
    text = page.get_text("text") or ""
    stripped = "".join(text.split())
    bad = sum(1 for c in stripped if c == "�" or (0xE000 <= ord(c) <= 0xF8FF) or ord(c) < 32)
    garbage = bad / len(stripped) if stripped else 0.0
    area = abs(page.rect.width * page.rect.height) or 1.0
    covered = 0.0
    try:
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) & page.rect
            if not r.is_empty:
                covered += abs(r.width * r.height)
    except Exception:  # noqa: BLE001
        pass
    return PdfPageInfo(page.number, page.rect.width, page.rect.height, len(stripped), garbage, min(1.0, covered / area))


def render_page(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return arr[:, :, ::-1].copy()  # RGB -> BGR


def native_tokens(page: pymupdf.Page, page_no: int, scale: float) -> list[Token]:
    """Word boxes from the text layer, grouped into column-aware segments.

    Coordinates are converted into the rendered image's pixel space
    (``scale`` = dpi / 72) and account for page rotation.
    """
    words = page.get_text("words", sort=False)
    mat = page.rotation_matrix
    groups: dict[tuple[int, int], list[tuple]] = {}
    for w in words:
        x0, y0, x1, y1, text, block, line, _wn = w[:8]
        if not str(text).strip():
            continue
        r = pymupdf.Rect(x0, y0, x1, y1) * mat
        groups.setdefault((block, line), []).append((r.x0 * scale, r.y0 * scale, r.x1 * scale, r.y1 * scale, str(text)))
    tokens: list[Token] = []
    for items in groups.values():
        items.sort(key=lambda it: it[0])
        seg: list[tuple] = []
        segments: list[list[tuple]] = []
        for it in items:
            if seg:
                prev = seg[-1]
                h = max(prev[3] - prev[1], 1)
                if it[0] - prev[2] > 0.9 * h:
                    segments.append(seg)
                    seg = []
            seg.append(it)
        if seg:
            segments.append(seg)
        for sg in segments:
            tokens.append(
                Token(
                    id="",
                    page=page_no,
                    text=" ".join(it[4] for it in sg),
                    conf=1.0,
                    x0=min(it[0] for it in sg),
                    y0=min(it[1] for it in sg),
                    x1=max(it[2] for it in sg),
                    y1=max(it[3] for it in sg),
                    words=[Word(it[4], 1.0, it[0], it[1], it[2], it[3]) for it in sg],
                    source="pdf_text",
                )
            )
    tokens.sort(key=lambda t: (t.y0, t.x0))
    return tokens


def merge_native_and_ocr(primary: list[Token], secondary: list[Token]) -> list[Token]:
    """Add secondary tokens that do not overlap any primary token."""
    out = list(primary)
    for s in secondary:
        overlap = False
        for p in primary:
            ix = min(p.x1, s.x1) - max(p.x0, s.x0)
            iy = min(p.y1, s.y1) - max(p.y0, s.y0)
            if ix > 0 and iy > 0 and ix * iy > 0.3 * max(1.0, s.w * s.h):
                overlap = True
                break
        if not overlap:
            out.append(s)
    out.sort(key=lambda t: (t.y0, t.x0))
    return out
