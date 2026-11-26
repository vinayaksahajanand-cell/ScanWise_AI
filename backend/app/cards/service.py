"""Business-card extraction: validate -> OCR -> text rules -> (one vision call) -> cross-validated merge.

Independent of the invoice pipeline. It reuses only the OCR engine, image helpers and whichever vision
provider is configured (no keys or providers are hard-coded here).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
import pymupdf
from pydantic import ValidationError
from rapidfuzz import fuzz

from app.config import Settings, get_settings
from app.pipeline.imaging import encode_jpeg, load_image_bytes, resize_max
from app.pipeline.llm.providers import VisionProvider, build_provider
from app.pipeline.ocr.engines import get_engine
from app.pipeline.page_ocr import ocr_page_image
from app.pipeline.pdf import render_page
from app.services.documents import sniff_type

from .prompt import SYSTEM_PROMPT, repair_prompt, user_prompt
from .rules import EMAIL_RE, Line, Parsed, digits, parse_card
from .schema import CARD_DSL, FIELD_KEYS, SCHEMA_NAME, CardLLMResult

log = logging.getLogger(__name__)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/tiff", "image/bmp", "image/gif", "application/pdf"}
FORMATS_TEXT = "JPG, PNG, WEBP, HEIC, TIFF, BMP, GIF or PDF"
PDF_DPI = 220
PDF_MIN_SIDE_PX = 1800
PDF_MAX_SIDE_PX = 3200  # a page is never rendered larger than this
MAX_CARD_MB = 15
MIN_SIDE_PX = 300
MAX_SIDES = 2
STRUCTURED = {"email", "phone", "website", "zip_code", "tax_id"}
LEVEL = {"low": 0, "medium": 1, "high": 2}
NAMES = ("low", "medium", "high")


class CardError(Exception):
    """A problem the caller should see; carries the HTTP status and a user-readable message."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --------------------------------------------------------------------------- upload validation
@dataclass
class CardImage:
    filename: str
    data: bytes
    bgr: np.ndarray


def _detect_type(data: bytes) -> str | None:
    kind = sniff_type(data)
    if kind is None and data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return kind


def _pdf_sides(label: str, data: bytes, max_pages: int) -> tuple[list[np.ndarray], list[str]]:
    """Render the first pages of a PDF as card sides (a 2-page PDF is front + back)."""
    notes: list[str] = []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # noqa: BLE001
        raise CardError(422, f"{label}: this PDF could not be opened.") from None
    with doc:
        if doc.needs_pass:
            raise CardError(422, f"{label}: password-protected PDFs are not supported.")
        if doc.page_count == 0:
            raise CardError(422, f"{label}: the PDF has no pages.")
        if doc.page_count > max_pages:
            notes.append(f"Only the first page of {label} was read." if max_pages == 1 else f"Only the first {max_pages} pages of {label} were read.")
        out: list[np.ndarray] = []
        for page in list(doc)[:max_pages]:
            long_pts = max(page.rect.width, page.rect.height) or 1.0
            # small pages (a 3.5 in card) are rendered up to ~1800 px so small print stays legible; big pages are capped
            target_px = min(PDF_MAX_SIDE_PX, max(PDF_MIN_SIDE_PX, long_pts * PDF_DPI / 72))
            dpi = max(72, int(target_px * 72 / long_pts))
            try:
                out.append(render_page(page, dpi))
            except Exception:  # noqa: BLE001
                raise CardError(422, f"{label}: a page of the PDF could not be read.") from None
    return out, notes


def validate_uploads(files: list[tuple[str, bytes]]) -> tuple[list[CardImage], list[str]]:
    """Check every upload and turn it into card sides (images, or the first pages of a PDF).

    Returns (sides, notes). At most MAX_SIDES sides come back: one file may hold both sides as a
    2-page PDF; with two files, each contributes its first page.
    """
    if not files:
        raise CardError(400, "Add a photo of the business card (front side required).")
    if len(files) > MAX_SIDES:
        raise CardError(400, f"A business card has at most {MAX_SIDES} sides: send the front and, optionally, the back.")
    limit = min(get_settings().max_upload_mb, MAX_CARD_MB) * 1024 * 1024
    out: list[CardImage] = []
    notes: list[str] = []
    for name, data in files:
        label = name or "file"
        if not data:
            raise CardError(400, f"{label}: the file is empty.")
        if len(data) > limit:
            raise CardError(413, f"{label}: larger than {limit // (1024 * 1024)} MB. Use a smaller file.")
        kind = _detect_type(data)
        if kind not in ALLOWED_TYPES:
            raise CardError(415, f"{label}: unsupported file type. Use {FORMATS_TEXT}.")
        if kind == "application/pdf":
            room = MAX_SIDES - len(out) if len(files) == 1 else 1
            pages, pnotes = _pdf_sides(label, data, room)
            notes += pnotes
            out += [CardImage(label, data, bgr) for bgr in pages]
            continue
        try:
            bgr = load_image_bytes(data)
        except Exception:  # noqa: BLE001
            raise CardError(422, f"{label}: the image could not be opened. Try taking the photo again.") from None
        if min(bgr.shape[:2]) < MIN_SIDE_PX:
            raise CardError(422, f"{label}: the image is too small ({bgr.shape[1]}x{bgr.shape[0]}px). Use at least {MIN_SIDE_PX}px on the short side, and fill the frame with the card.")
        out.append(CardImage(label, data, bgr))
    return out, notes


# --------------------------------------------------------------------------- OCR
@dataclass
class SideOCR:
    image: np.ndarray
    lines: list[Line]
    warnings: list[str]
    mean_conf: float


def _ocr_side(img: np.ndarray, side: int, s: Settings) -> SideOCR:
    engine = get_engine(s.ocr_engine, model_size=s.ocr_model_size, max_side=s.ocr_max_side, min_score=s.ocr_min_token_score)
    res = ocr_page_image(engine, img, side + 1, auto_crop=True)
    toks = [t for t in res.tokens if not t.noise and t.text.strip()]
    lines = [Line(t.text, t.conf, t.h, t.y0, t.x0, side) for t in toks]
    conf = float(np.mean([t.conf for t in toks])) if toks else 0.0
    return SideOCR(res.image, lines, list(res.warnings), conf)


def _on_ocr_worker(fn):
    """Run on the job runner's (pre-warmed) worker threads instead of an arbitrary request thread."""
    from app.services.jobs import get_runner

    return get_runner().executor.submit(fn).result()


# --------------------------------------------------------------------------- vision (single call, cached)
_CACHE: "OrderedDict[str, tuple[float, CardLLMResult]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_S = 600
_CACHE_MAX = 64


def _cache_get(key: str) -> CardLLMResult | None:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < _CACHE_TTL_S:
            _CACHE.move_to_end(key)
            return hit[1]
        _CACHE.pop(key, None)
    return None


def _cache_put(key: str, value: CardLLMResult) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def run_vision(provider: VisionProvider, images: list[bytes], hint: list[str]) -> tuple[CardLLMResult, bool]:
    """One request covering every side. Returns (result, served_from_cache)."""
    text = user_prompt(len(images), hint)
    key = hashlib.sha256(b"|".join(images) + provider.label.encode() + text.encode()).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        return cached, True
    messages: list[tuple[str, str]] = [("user", text)]
    raw = provider.complete(SYSTEM_PROMPT, messages, images, schema=CARD_DSL, schema_name=SCHEMA_NAME)
    try:
        result = CardLLMResult.model_validate(raw)
    except ValidationError as e:
        log.warning("business-card vision output failed validation; retrying once with a repair prompt")
        messages += [("assistant", json.dumps(raw)[:20000]), ("user", repair_prompt(str(e)))]
        result = CardLLMResult.model_validate(provider.complete(SYSTEM_PROMPT, messages, images, schema=CARD_DSL, schema_name=SCHEMA_NAME))
    _cache_put(key, result)
    return result, False


# --------------------------------------------------------------------------- cross-validation
def _alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


def _words(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def _host(url: str) -> str:
    return re.sub(r"(?i)^https?://|^www\.", "", url.strip()).split("/")[0].lower()


def _equiv(key: str, a: str, b: str) -> bool:
    if key == "phone":
        return digits(a) == digits(b)
    if key == "email":
        return a.strip().lower() == b.strip().lower()
    if key == "website":
        return _host(a) == _host(b)
    if key in ("zip_code", "tax_id"):
        return _alnum(a) == _alnum(b)
    na, nb = _words(a), _words(b)
    return na == nb or fuzz.ratio(na, nb) >= 90


class Evidence:
    """What OCR actually read; used to check that a vision value is really on the card."""

    def __init__(self, lines: list[str]):
        self.lines = lines
        self.blob = " ".join(lines)
        self.compact = _alnum(self.blob.replace(" @ ", "@"))
        self.digits = digits(self.blob)
        self.words = _words(self.blob)

    def grounded(self, key: str, value: str) -> bool:
        if not value or not self.blob:
            return False
        if key == "phone":
            d = digits(value)
            return len(d) >= 7 and (d in self.digits)
        if key in ("email", "zip_code", "tax_id"):
            return _alnum(value) in self.compact
        if key == "website":
            return _alnum(_host(value)) in self.compact
        v = _words(value)
        if not v:
            return False
        if len(v) < 4:
            return re.search(rf"\b{re.escape(v)}\b", self.words) is not None
        return fuzz.partial_ratio(v, self.words) >= 88


def _cap(level: str, top: str) -> str:
    return NAMES[min(LEVEL[level], LEVEL[top])]


def _valid_shape(key: str, value: str) -> bool:
    if key == "email":
        return EMAIL_RE.fullmatch(value.strip()) is not None
    if key == "phone":
        return 7 <= len(digits(value)) <= 15
    return True


@dataclass
class Merged:
    values: dict[str, str]
    conf: dict[str, str]
    other_emails: list[str]
    other_phones: list[str]
    warnings: list[str]


def merge(rules: Parsed, vision: CardLLMResult | None, ev: Evidence) -> Merged:
    values: dict[str, str] = {}
    conf: dict[str, str] = {}
    warnings: list[str] = []
    others_e: list[str] = list(rules.other_emails)
    others_p: list[str] = list(rules.other_phones)

    for key in FIELD_KEYS:
        r = rules.values.get(key)
        rc = rules.conf.get(key, "medium")
        vf = getattr(vision, key, None) if vision else None
        v = (vf.text or "").strip() if vf and vf.text else None
        vc = vf.confidence if (vf and v) else None

        if v is None and r is None:
            continue
        if v is None:  # vision ran but returned nothing; OCR found something: keep it, flagged unless it is exact
            values[key] = r  # type: ignore[assignment]
            conf[key] = _cap(rc, "medium") if vision is not None else rc
            continue
        if r is None:
            ok_shape = _valid_shape(key, v)
            g = ev.grounded(key, v)
            if key in STRUCTURED:
                level = _cap(vc, "high" if g else "low")  # type: ignore[arg-type]
            else:
                level = vc if g else _cap(vc, "medium")  # type: ignore[arg-type,assignment]
            if not ok_shape:
                level = "low"
            values[key], conf[key] = v, level
            continue
        # both present
        if _equiv(key, v, r):
            values[key] = v
            conf[key] = "medium" if vc == "low" else "high"
            continue
        g = ev.grounded(key, v)
        if key in STRUCTURED:
            if g and _valid_shape(key, v):
                values[key], conf[key] = v, "medium"
                if key == "email":
                    others_e.append(r)
                elif key == "phone":
                    others_p.append(r)
            else:
                values[key], conf[key] = r, "medium"
                if key in ("email", "phone"):
                    (others_e if key == "email" else others_p).append(v)
            warnings.append(f"The {key.replace('_', ' ')} was read differently by OCR and AI: please check it.")
        else:
            values[key] = v
            conf[key] = vc if g else _cap(vc, "medium")  # type: ignore[arg-type,assignment]
            if not g and not fuzz.partial_ratio(_words(r), _words(v)) >= 70:
                conf[key] = "low"

    if vision is not None:
        others_e += [e for e in vision.other_emails if e and e.strip()]
        others_p += [p for p in vision.other_phones if p and p.strip()]

    primary_e = values.get("email", "").lower()
    seen_e = {primary_e}
    other_emails: list[str] = []
    for e in others_e:
        k = e.strip().lower()
        if k and k not in seen_e and EMAIL_RE.fullmatch(e.strip()):
            seen_e.add(k)
            other_emails.append(e.strip())
    primary_p = digits(values.get("phone", ""))
    seen_p = {primary_p}
    other_phones: list[str] = []
    for p in others_p:
        d = digits(p)
        if 7 <= len(d) <= 15 and d not in seen_p:
            seen_p.add(d)
            other_phones.append(re.sub(r"\s+", " ", p.strip()))
    return Merged(values, conf, other_emails, other_phones, warnings)


# --------------------------------------------------------------------------- orchestration
def _weak(values: dict[str, str], mean_conf: float, n_lines: int) -> bool:
    return "name" not in values or ("email" not in values and "phone" not in values) or mean_conf < 0.85 or n_lines < 4


def _friendly_ai_error(e: Exception) -> str:
    msg = str(e)
    if re.search(r"insufficient_quota|credit_balance|no credits", msg, re.I):
        return "The AI provider account has no credit left"
    if re.search(r"\b(401|403)\b", msg):
        return "The AI provider rejected the API key"
    if re.search(r"\b429\b", msg):
        return "The AI provider is rate-limiting requests"
    if re.search(r"timeout|timed out|network", msg, re.I):
        return "The AI provider did not respond in time"
    return "The AI provider returned an error"


def extract_card(files: list[tuple[str, bytes]], llm_mode: str = "always", settings: Settings | None = None) -> dict:
    t0 = time.perf_counter()
    s = settings or get_settings()
    cards, notes = validate_uploads(files)
    warnings: list[str] = list(notes)
    extractors: list[str] = []

    # 1. OCR every side (on the warmed worker pool)
    sides: list[SideOCR] = []
    for i, c in enumerate(cards):
        label = ("Front" if i == 0 else "Back") if len(cards) > 1 else ""
        try:
            so = _on_ocr_worker(lambda img=c.bgr, side=i: _ocr_side(img, side, s))
        except Exception:  # noqa: BLE001
            log.exception("business-card OCR failed")
            so = SideOCR(c.bgr, [], ["The text could not be read automatically"], 0.0)
        sides.append(so)
        warnings += [f"{label + ': ' if label else ''}{w}" for w in so.warnings]
    all_lines = [ln for so in sides for ln in so.lines]
    if all_lines:
        extractors.append("ocr")
    mean_conf = float(np.mean([ln.conf for ln in all_lines])) if all_lines else 0.0

    # 2. deterministic parse of the OCR text
    rules = parse_card(all_lines)

    # 3. at most ONE vision call, covering both sides
    vision: CardLLMResult | None = None
    mode = (llm_mode or "always").lower()
    provider = build_provider(s) if mode != "off" else None
    if mode != "off" and provider is None:
        warnings.append("No AI vision provider is configured, so this card was read with OCR and text rules only: please check each field.")
    elif provider is not None and (mode == "always" or _weak(rules.values, mean_conf, len(all_lines))):
        try:
            jpegs = [encode_jpeg(resize_max(so.image, s.llm_image_max_side), 88) for so in sides]
            vision, cached = run_vision(provider, jpegs, [ln.text for ln in all_lines])
            extractors.append(f"vision:{provider.label}" + (" (cached)" if cached else ""))
            warnings += [f"AI note: {o}" for o in vision.observations[:3] if o and o.strip()]
        except Exception as e:  # noqa: BLE001
            log.warning("business-card vision failed: %s", type(e).__name__)
            warnings.append(f"{_friendly_ai_error(e)}, so this card was read with OCR and text rules only: please check each field.")

    # 4. cross-validated merge
    merged = merge(rules, vision, Evidence([ln.text for ln in all_lines]))
    warnings += merged.warnings
    if not merged.values:
        hint = "The photo may be blurry, dark or too far away." if any("blur" in w.lower() or "dark" in w.lower() for w in warnings) else "Make sure the whole card is in the frame, well lit and in focus."
        raise CardError(422, f"No contact details could be read from this card. {hint}")

    v = merged.values
    out = {
        "name": v.get("name"),
        "company": v.get("company"),
        "email": v.get("email"),
        "phone": v.get("phone"),
        "address": {k: v.get(k) for k in ("street", "city", "state", "zip_code", "country")},
        "website": v.get("website"),
        "job_title": v.get("job_title"),
        "tax_id": v.get("tax_id"),
        "other_emails": merged.other_emails,
        "other_phones": merged.other_phones,
        "confidence": dict(merged.conf),
        "missing_fields": [k for k in FIELD_KEYS if k not in v],
        "uncertain_fields": [k for k in FIELD_KEYS if k in v and merged.conf.get(k) in ("medium", "low")],
        "warnings": list(dict.fromkeys(warnings)),
        "extractors": extractors,
        "sides_read": len(cards),
        "processing_ms": int((time.perf_counter() - t0) * 1000),
    }
    return out
