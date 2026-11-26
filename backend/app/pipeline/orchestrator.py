"""Conditional, stage-based document processing workflow with an audit trail.

    ingest
      └─ per page: PDF text-layer check ─► native text | render ─► OCR (retry/rotate/deskew)
    layout extraction (deterministic, always)
    vision extraction  (mode: always | fallback when layout result is weak | off)
    cross-validation   (vision ⟷ OCR evidence ⟷ layout)
    financial validation (Decimal) ─► arbitration of competing readings
    price-book matching
    confidence assessment ─► "ready" or "review"

Every stage is timed and logged (StageLog). Failures of optional stages (vision)
are recorded and surfaced as warnings — never silently discarded.
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from app.config import Settings
from app.schemas.extraction import ExtractionResult, LineItem, PageQuality, StageLog

from . import finance
from .assess import arbitrate, assess
from .imaging import load_image_bytes, measure_quality, resize_max
from .layout import Evidence, PageData
from .llm.extract import VisionPage, run_vision
from .llm.providers import build_provider
from .ocr.base import Token, flag_noise
from .ocr.engines import get_engine
from .page_ocr import ocr_page_image, token_stats
from .pdf import analyze_page, merge_native_and_ocr, native_tokens, open_pdf, render_page
from .reconcile import Reconciler, merge_results
from .rules import RuleExtractor

log = logging.getLogger(__name__)


@dataclass
class PageSource:
    page_no: int
    kind: str  # image | pdf
    file_path: Path
    pdf_index: int | None = None
    corners: list[list[float]] | None = None
    rotation: int = 0
    auto_crop: bool = True


@dataclass
class ProcessedPage:
    page_no: int
    image: np.ndarray
    tokens: list[Token]
    quality: PageQuality
    meta: dict = field(default_factory=dict)

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


@dataclass
class PipelineResult:
    extraction: ExtractionResult
    pages: list[ProcessedPage]
    log: list[StageLog]
    status: str  # ready | review


ProgressFn = Callable[[str, float], None]
MatcherFn = Callable[[list[LineItem], str | None], None]


class Pipeline:
    def __init__(self, settings: Settings, progress: ProgressFn | None = None, matcher: MatcherFn | None = None,
                 llm_mode: str | None = None, date_order: str | None = None):
        self.s = settings
        self.progress = progress or (lambda msg, frac: None)
        self.matcher = matcher
        self.llm_mode = (llm_mode or settings.llm_mode or "always").lower()
        self.date_order = date_order if date_order and date_order != "auto" else None
        self.log: list[StageLog] = []
        self.engine = get_engine(settings.ocr_engine, model_size=settings.ocr_model_size, max_side=settings.ocr_max_side,
                                 min_score=settings.ocr_min_token_score)

    @contextmanager
    def stage(self, name: str):
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        rec = {"status": "ok", "detail": "", "data": None}
        try:
            yield rec
        except Exception as e:  # noqa: BLE001
            rec["status"] = "failed"
            rec["detail"] = f"{type(e).__name__}: {e}"
            self.log.append(StageLog(stage=name, status="failed", started_at=started,
                                     duration_ms=int((time.perf_counter() - t0) * 1000), detail=rec["detail"]))
            raise
        self.log.append(StageLog(stage=name, status=rec["status"], started_at=started,  # type: ignore[arg-type]
                                 duration_ms=int((time.perf_counter() - t0) * 1000), detail=rec["detail"], data=rec["data"]))

    # ----------------------------------------------------------- pages
    def _image_page(self, src: PageSource) -> ProcessedPage:
        original = load_image_bytes(src.file_path.read_bytes())
        res = ocr_page_image(self.engine, original, src.page_no, corners=src.corners, rotation=src.rotation, auto_crop=src.auto_crop)
        q = res.quality
        st = token_stats([t for t in res.tokens if not t.noise])
        quality = PageQuality(page=src.page_no, text_source="ocr", ocr_engine=res.engine, token_count=st["n"],
                              mean_confidence=round(st["mean_conf"], 4) if st["n"] else None,
                              low_confidence_ratio=round(st["low_ratio"], 4) if st["n"] else None,
                              blur_score=round(q.blur, 1) if q else None, warnings=res.warnings)
        meta = {"crop": res.crop, "rotation": res.rotation, "deskew": res.deskew, "passes": res.passes,
                "original_size": [int(original.shape[1]), int(original.shape[0])]}
        return ProcessedPage(src.page_no, res.image, res.tokens, quality, meta)

    def _pdf_page(self, src: PageSource) -> ProcessedPage:
        doc = open_pdf(str(src.file_path))
        try:
            page = doc[src.pdf_index or 0]
            info = analyze_page(page)
            mode = info.mode
            meta: dict = {"pdf_mode": mode, "text_chars": info.text_chars, "image_coverage": round(info.image_coverage, 3)}
            if mode == "native":
                display = render_page(page, self.s.pdf_render_dpi)
                toks = native_tokens(page, src.page_no, self.s.pdf_render_dpi / 72.0)
                for i, t in enumerate(toks):
                    t.id = f"p{src.page_no}:t{i}"
                flag_noise(toks)
                q = measure_quality(display)
                quality = PageQuality(page=src.page_no, text_source="pdf_text", token_count=len(toks), mean_confidence=1.0,
                                      low_confidence_ratio=0.0, blur_score=round(q.blur, 1))
                return ProcessedPage(src.page_no, display, toks, quality, meta)
            ocr_img = render_page(page, self.s.pdf_ocr_dpi)
            scanned = mode == "ocr"  # mixed pages keep geometry so native text stays aligned
            res = ocr_page_image(self.engine, ocr_img, src.page_no, auto_crop=False,
                                 rotation=src.rotation if scanned else 0, allow_geometry=scanned)
            toks = res.tokens
            if mode in ("mixed_native", "mixed_ocr"):
                native = native_tokens(page, src.page_no, self.s.pdf_ocr_dpi / 72.0)
                toks = merge_native_and_ocr(native, toks) if mode == "mixed_native" else merge_native_and_ocr(toks, native)
                for i, t in enumerate(toks):
                    t.id = f"p{src.page_no}:t{i}"
                flag_noise(toks)
            st = token_stats([t for t in toks if not t.noise])
            quality = PageQuality(page=src.page_no, text_source="mixed" if mode.startswith("mixed") else "ocr",
                                  ocr_engine=res.engine, token_count=st["n"],
                                  mean_confidence=round(st["mean_conf"], 4) if st["n"] else None,
                                  low_confidence_ratio=round(st["low_ratio"], 4) if st["n"] else None,
                                  warnings=res.warnings)
            meta.update({"rotation": res.rotation, "deskew": res.deskew, "passes": res.passes})
            return ProcessedPage(src.page_no, res.image, toks, quality, meta)
        finally:
            doc.close()

    def process_pages(self, sources: list[PageSource]) -> list[ProcessedPage]:
        pages: list[ProcessedPage] = []
        n = len(sources)
        for i, src in enumerate(sources):
            self.progress(f"Reading page {src.page_no} of {n}", 0.05 + 0.55 * i / max(n, 1))
            with self.stage(f"page {src.page_no}: {'pdf' if src.kind == 'pdf' else 'image'} → text") as rec:
                p = self._pdf_page(src) if src.kind == "pdf" else self._image_page(src)
                q = p.quality
                rec["detail"] = (f"{q.text_source}, {q.token_count} tokens"
                                 + (f", mean conf {q.mean_confidence:.3f}" if q.mean_confidence is not None else "")
                                 + (f", crop={p.meta.get('crop')}" if p.meta.get("crop") not in (None, "none") else "")
                                 + (f", rotated {p.meta.get('rotation')}°" if p.meta.get("rotation") else "")
                                 + (f", deskew {p.meta.get('deskew')}°" if p.meta.get("deskew") else ""))
                rec["data"] = {"passes": p.meta.get("passes"), "pdf_mode": p.meta.get("pdf_mode")}
                if any(ps.get("pass", "").startswith("B") for ps in p.meta.get("passes") or []):
                    rec["status"] = "retry"
                if q.warnings:
                    rec["status"] = "warning" if rec["status"] == "ok" else rec["status"]
            pages.append(p)
        return pages

    # ----------------------------------------------------------- run
    def _layout_is_weak(self, ex: ExtractionResult) -> str | None:
        if not ex.line_items:
            return "no line items found"
        v = ex.validation
        if v and v.status in ("fail", "incomplete") and ex.document_type.value != "price_list":
            return f"validation {v.status}"
        low = sum(1 for li in ex.line_items for f in li.fields().values() if f.present and (f.ocr_confidence or 1) < 0.85)
        if low > 2:
            return f"{low} low-confidence values"
        if not ex.currency.code:
            return "currency unknown"
        return None

    def run(self, sources: list[PageSource]) -> PipelineResult:
        with self.stage("ingest") as rec:
            if not sources:
                raise ValueError("No pages to process")
            rec["detail"] = f"{len(sources)} page(s): " + ", ".join(sorted({s.kind for s in sources}))
        pages = self.process_pages(sources)
        page_data = [PageData(p.page_no, p.width, p.height, p.tokens, p.quality.text_source) for p in pages]

        self.progress("Analysing layout", 0.62)
        with self.stage("layout extraction") as rec:
            rules = RuleExtractor(page_data, self.date_order)
            r_ex = rules.extract()
            finance.validate(r_ex)
            rec["detail"] = (f"{len(r_ex.line_items)} items, {len(r_ex.taxes)} tax lines, "
                             f"currency {r_ex.currency.code or '?'}, validation {r_ex.validation.status if r_ex.validation else '-'}")
        ex = r_ex

        provider = build_provider(self.s) if self.llm_mode != "off" else None
        run_vision_now = False
        reason = ""
        if provider is None:
            reason = "vision model disabled" if self.llm_mode == "off" else "no vision provider configured (set an API key in backend/.env)"
        elif self.llm_mode == "always":
            run_vision_now, reason = True, "mode=always"
        else:
            weak = self._layout_is_weak(r_ex)
            run_vision_now, reason = (True, f"layout result weak: {weak}") if weak else (False, "layout result passed all checks")

        if not run_vision_now:
            self.log.append(StageLog(stage="vision extraction", status="skipped", started_at=datetime.now(timezone.utc), duration_ms=0, detail=reason))
        else:
            self.progress(f"Reading with {provider.label}", 0.70)  # type: ignore[union-attr]
            try:
                with self.stage("vision extraction") as rec:
                    vpages = [VisionPage(p.page_no, p.image, p.tokens, p.width, p.height) for p in pages]
                    vr = run_vision(provider, vpages, self.s)  # type: ignore[arg-type]
                    rec["detail"] = (f"{provider.label}: {len(vr.result.line_items)} items, {vr.calls} call(s)"  # type: ignore[union-attr]
                                     + (", repaired invalid JSON" if vr.repaired else "")
                                     + (f", {vr.pages_skipped} page(s) over limit" if vr.pages_skipped else "") + f" ({reason})")
                with self.stage("cross-validation") as rec:
                    rc = Reconciler(Evidence(page_data), rules.dec_hint, rules.date_order, rules.currency.minor_units)
                    v_ex = rc.from_llm(vr.result)
                    all_texts = [t.text for p in pages for t in p.tokens if not t.noise]
                    ex = merge_results(v_ex, r_ex, all_texts, vr.result.currency_code, vr.result.currency_evidence)
                    ex.extractors = ["layout", f"vision:{provider.label}"]  # type: ignore[union-attr]
                    disagreements = sum(1 for li in ex.line_items for f in li.fields().values() if f.alternatives)
                    rec["detail"] = f"{len(ex.line_items)} merged items, {disagreements} field disagreement(s)"
                    if vr.pages_skipped:
                        ex.warnings.append(f"Only the first {vr.pages_sent} pages were sent to the vision model")
            except Exception as e:  # noqa: BLE001
                log.warning("vision extraction failed: %s", e)
                log.debug(traceback.format_exc())
                ex = r_ex
                ex.warnings.append(f"Vision model failed ({type(e).__name__}: {str(e)[:200]}). Results come from OCR + layout analysis only.")

        self.progress("Validating totals", 0.88)
        with self.stage("financial validation") as rec:
            report = finance.validate(ex)
            notes = arbitrate(ex) if report.status == "fail" else []
            report = ex.validation or report
            fails = sum(1 for c in report.checks if c.status == "fail")
            rec["detail"] = f"{report.status}: {len(report.checks)} checks, {fails} failed" + (f"; arbitration: {'; '.join(notes)}" if notes else "")
            rec["status"] = "warning" if report.status in ("fail", "warning") else "ok"
            ex.notes.extend(notes)

        if self.matcher and ex.line_items:
            with self.stage("price-book matching") as rec:
                self.matcher(ex.line_items, ex.currency.code)
                n = sum(1 for li in ex.line_items if li.pricebook_matches)
                rec["detail"] = f"{n} item(s) resemble existing price-book entries"

        with self.stage("confidence assessment") as rec:
            for p in pages:
                ex.page_quality.append(p.quality)
                ex.warnings.extend(f"Page {p.page_no}: {w}" for w in p.quality.warnings)
            if ex.currency.issues:
                ex.warnings.extend(ex.currency.issues[:1])
            status = assess(ex, self.s)
            r = ex.review
            rec["detail"] = (f"{status}: {r.high_confidence} high-confidence, {r.needs_review} need review, "
                             f"{r.unreadable} unreadable, {r.mismatch} mismatched of {r.total_fields} fields")
        self.progress("Done", 1.0)
        return PipelineResult(ex, pages, self.log, status)


def display_jpeg(img: np.ndarray, max_side: int = 2400) -> bytes:
    ok, buf = cv2.imencode(".jpg", resize_max(img, max_side), [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise RuntimeError("encode failed")
    return buf.tobytes()
