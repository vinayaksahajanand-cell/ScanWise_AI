"""Per-page image preparation + multi-pass OCR.

Flow for one image page::

    original ─► (user corners | conservative auto-detect) perspective warp
             ─► user rotation ─► OCR pass A (gentle)
             ─► orientation search if text looks rotated / unreadable
             ─► deskew from text-line angles (re-OCR if rotated)
             ─► OCR pass B on an enhanced/upscaled variant when pass A is weak
             ─► consensus merge (agreeing readings keep max confidence,
                 disagreeing readings keep both -> flagged for review)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .imaging import (
    ImageQuality,
    detect_document,
    enhance,
    measure_quality,
    resize_max,
    rotate90,
    rotate_small,
    warp_quad,
)
from .ocr.base import OCREngine, Token, flag_noise, raw_to_tokens, split_words

log = logging.getLogger(__name__)

SPARSE_TOKENS = 4  # fewer text lines than this on a page = the read most likely failed (cards have >= 4)
BLURRY_LAPLACIAN = 60  # same threshold imaging.measure_quality uses for its "looks blurry" warning


@dataclass
class PageOCRResult:
    image: np.ndarray
    tokens: list[Token]
    engine: str
    rotation: int = 0
    deskew: float = 0.0
    crop: str = "none"  # none | user | auto
    quality: ImageQuality | None = None
    passes: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def token_stats(tokens: list[Token]) -> dict:
    real = [t for t in tokens if len(t.text.strip()) >= 2]
    if not real:
        return {"n": 0, "mean_conf": 0.0, "low_ratio": 1.0, "median_h": 0.0, "vertical_ratio": 0.0}
    confs = [t.conf for t in real]
    hs = sorted(t.h for t in real)
    vertical = sum(1 for t in real if len(t.text) >= 3 and t.h > 1.3 * t.w)
    return {
        "n": len(real),
        "mean_conf": float(np.mean(confs)),
        "low_ratio": sum(1 for c in confs if c < 0.85) / len(confs),
        "median_h": float(hs[len(hs) // 2]),
        "vertical_ratio": vertical / len(real),
    }


def _iou(a: Token, b: Token) -> float:
    ix0, iy0 = max(a.x0, b.x0), max(a.y0, b.y0)
    ix1, iy1 = min(a.x1, b.x1), min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def merge_passes(a: list[Token], b: list[Token]) -> tuple[list[Token], dict]:
    used: set[int] = set()
    out: list[Token] = []
    agree = disagree = added = 0
    for ta in a:
        best_j, best_iou = -1, 0.0
        for j, tb in enumerate(b):
            if j in used:
                continue
            iou = _iou(ta, tb)
            if iou > best_iou:
                best_j, best_iou = j, iou
        if best_j >= 0 and best_iou >= 0.5:
            tb = b[best_j]
            used.add(best_j)
            if _norm(ta.text) == _norm(tb.text):
                # Same characters; recognisers drop inter-word spaces far more often than they
                # invent them, so the reading with more word breaks is the better one.
                keep = tb if tb.text.count(" ") > ta.text.count(" ") else ta
                keep.conf = max(ta.conf, tb.conf)
                agree += 1
                out.append(keep)
            else:
                winner, loser = (ta, tb) if ta.conf >= tb.conf else (tb, ta)
                winner.alt_text, winner.alt_conf = loser.text, loser.conf
                disagree += 1
                out.append(winner)
        else:
            out.append(ta)
    for j, tb in enumerate(b):
        if j in used or tb.conf < 0.6:
            continue
        if any(_iou(tb, t) > 0.1 for t in out):
            continue
        out.append(tb)
        added += 1
    return out, {"agree": agree, "disagree": disagree, "added_from_b": added}


RUN_ON = re.compile(r"[A-Za-z]{11,}|[a-z][A-Z][a-z]|[A-Z]{2,}[A-Z][a-z]{2,}")


def respace(engine: OCREngine, img: np.ndarray, tokens: list[Token]) -> int:
    """Repair dropped word spaces ('Installationlabor', 'ElectricWorks').

    Full-page recognition sometimes merges words; re-recognising just the padded line
    crop usually restores the space. A reading is adopted only when it has exactly the
    same characters plus extra spaces — the recogniser's own evidence, never a guess.
    """
    fixed = 0
    h_img, w_img = img.shape[:2]
    for t in tokens:
        if t.noise or t.source != "ocr" or not RUN_ON.search(t.text):
            continue
        pad = max(2, int(t.h * 0.25))
        crop = img[max(0, int(t.y0) - pad): min(h_img, int(t.y1) + pad), max(0, int(t.x0) - pad): min(w_img, int(t.x1) + pad)]
        if crop.size == 0:
            continue
        got = engine.recognize_crop(crop)
        if not got:
            continue
        text, conf = got
        if _norm(text) == _norm(t.text) and text.strip().count(" ") > t.text.count(" "):
            t.text = text.strip()
            t.words = split_words(t.text, max(t.conf, conf), t.x0, t.y0, t.x1, t.y1)
            fixed += 1
    return fixed


def _run(engine: OCREngine, img: np.ndarray, page: int, scale_back: float = 1.0) -> list[Token]:
    lines = engine.run(img)
    tokens = raw_to_tokens(lines, page)
    if scale_back != 1.0:
        for t in tokens:
            t.x0 /= scale_back
            t.y0 /= scale_back
            t.x1 /= scale_back
            t.y1 /= scale_back
            for w in t.words:
                w.x0 /= scale_back
                w.y0 /= scale_back
                w.x1 /= scale_back
                w.y1 /= scale_back
    return tokens


def _search_rotation(engine: OCREngine, img: np.ndarray) -> tuple[int, dict]:
    small = resize_max(img, 1400)
    scores: dict[int, float] = {}
    for k in (0, 1, 2, 3):
        lines = engine.run(rotate90(small, k))
        s = 0.0
        for ln in lines:
            xs = [p[0] for p in ln.poly]
            ys = [p[1] for p in ln.poly]
            if len(ln.text.strip()) >= 3 and (max(xs) - min(xs)) > (max(ys) - min(ys)):
                s += ln.score * len(ln.text.strip())
        scores[k] = s
    best = max(scores, key=lambda k: scores[k])
    if best != 0 and scores[best] > 1.3 * scores[0] + 5:
        return best, scores
    return 0, scores


def _quad_long_side(quad: np.ndarray) -> int:
    q = quad.reshape(4, 2)
    sides = [float(np.linalg.norm(q[i] - q[(i + 1) % 4])) for i in range(4)]
    return int(max(sides))


def prepare_geometry(
    original: np.ndarray, corners: list[list[float]] | None, auto_crop: bool, rotation: int
) -> tuple[np.ndarray, str, int]:
    """Returns (page image, crop mode, long side of the page in *captured* pixels)."""
    img = original
    crop = "none"
    h, w = img.shape[:2]
    source_px = max(h, w)
    if corners and len(corners) == 4:
        quad = np.array([[float(x) * w, float(y) * h] for x, y in corners], dtype=np.float32)
        # ignore a "full frame" quad
        if not np.allclose(quad, [[0, 0], [w, 0], [w, h], [0, h]], atol=max(w, h) * 0.01):
            source_px = _quad_long_side(quad)
            img = warp_quad(img, quad)
            crop = "user"
    elif auto_crop:
        det = detect_document(img)
        if det is not None:
            source_px = _quad_long_side(det.quad)
            img = warp_quad(img, det.quad)
            crop = "auto"
    if rotation:
        img = rotate90(img, (rotation // 90) % 4)
    # very small images (thumbnails, low-res webcams): upscale once before OCR
    long_side = max(img.shape[:2])
    if long_side < 1200:
        f = 1600 / long_side
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    return img, crop, source_px


def ocr_page_image(
    engine: OCREngine,
    original: np.ndarray,
    page: int,
    corners: list[list[float]] | None = None,
    rotation: int = 0,
    auto_crop: bool = True,
    allow_retry: bool = True,
    allow_geometry: bool = True,
) -> PageOCRResult:
    img, crop, source_px = prepare_geometry(original, corners, auto_crop, rotation)
    quality = measure_quality(img)
    result = PageOCRResult(image=img, tokens=[], engine=engine.describe(), crop=crop, quality=quality)

    def set_image_warnings(q: ImageQuality, px: int) -> None:
        result.warnings = [w for w in q.warnings if not w.startswith("Low resolution")]
        # judge resolution on the pixels that were actually captured, not the upsampled page
        if px < 1300:
            result.warnings.append(
                f"The page was captured at only ~{px}px — move the camera closer or use a higher-resolution camera; small text may be misread"
            )

    set_image_warnings(quality, source_px)

    t0 = time.perf_counter()
    tokens = _run(engine, enhance(img, "standard"), page)
    stats = token_stats(tokens)
    result.passes.append({"pass": "A", "ms": int((time.perf_counter() - t0) * 1000), **stats})

    # --- bad auto-crop guard --------------------------------------------------
    # On a soft/blurry photo the paper edges are weak and the detector can lock onto the wrong
    # region, leaving almost no text. Re-read the uncropped photo and keep whichever read better.
    if crop == "auto" and stats["n"] < SPARSE_TOKENS:
        full, _, full_px = prepare_geometry(original, None, False, rotation)
        t0 = time.perf_counter()
        tokens_f = _run(engine, enhance(full, "standard"), page)
        stats_f = token_stats(tokens_f)
        result.passes.append({"pass": "A-uncropped", "ms": int((time.perf_counter() - t0) * 1000), **stats_f})
        if stats_f["n"] > stats["n"]:
            img, tokens, stats, crop, source_px = full, tokens_f, stats_f, "none", full_px
            result.crop = "none"
            quality = measure_quality(img)
            result.quality = quality
            set_image_warnings(quality, source_px)

    # --- orientation -----------------------------------------------------
    if allow_geometry and (stats["n"] < 3 or stats["vertical_ratio"] > 0.4 or stats["mean_conf"] < 0.7):
        k, scores = _search_rotation(engine, img)
        result.passes.append({"pass": "orientation", "scores": {str(a * 90): round(b, 1) for a, b in scores.items()}, "chosen": k * 90})
        if k:
            img = rotate90(img, k)
            result.rotation = k * 90
            t0 = time.perf_counter()
            tokens = _run(engine, enhance(img, "standard"), page)
            stats = token_stats(tokens)
            result.passes.append({"pass": "A-rotated", "ms": int((time.perf_counter() - t0) * 1000), **stats})

    # --- deskew ----------------------------------------------------------
    angles = [t.angle for t in tokens if t.w > 4 * t.h and len(t.text) >= 4 and abs(t.angle) < 20]
    if allow_geometry and len(angles) >= 4:
        skew = float(np.median(angles))
        if 0.5 <= abs(skew) <= 15:
            img = rotate_small(img, skew)
            result.deskew = round(skew, 2)
            t0 = time.perf_counter()
            tokens = _run(engine, enhance(img, "standard"), page)
            stats = token_stats(tokens)
            result.passes.append({"pass": "A-deskewed", "angle": round(skew, 2), "ms": int((time.perf_counter() - t0) * 1000), **stats})

    # --- consensus pass ----------------------------------------------------
    # Small glyphs alone no longer trigger the (slow) second pass when pass A is already very
    # confident: the enhanced pass then agrees word-for-word and only costs seconds.
    small_text = 0 < stats["median_h"] < 18
    very_confident = stats["mean_conf"] >= 0.97 and stats["low_ratio"] <= 0.03
    # A near-empty read is never "confident" (1 token at 100% is a failure, not a success), and a blurry
    # photo that isn't already read with very high confidence deserves the sharpen+upscale pass.
    blurry = quality.blur < BLURRY_LAPLACIAN
    sparse = stats["n"] < SPARSE_TOKENS
    weak = (stats["mean_conf"] < 0.93 or stats["low_ratio"] > 0.08 or (small_text and not very_confident)
            or sparse or (blurry and not very_confident))
    if allow_retry and weak and (stats["n"] > 0 or blurry):
        mh = stats["median_h"] or 24
        # aim for ~28 px tall glyphs — the recogniser's sweet spot
        up = min(3.0, max(1.0, 28.0 / mh)) if mh < 22 else 1.0
        if max(img.shape[:2]) * up > 4600:
            up = max(1.0, 4600 / max(img.shape[:2]))
        t0 = time.perf_counter()
        tokens_b = _run(engine, enhance(img, "strong", upscale=up), page, scale_back=up)
        stats_b = token_stats(tokens_b)
        tokens, merge_info = merge_passes(tokens, tokens_b)
        result.passes.append({"pass": "B-enhanced", "upscale": up, "ms": int((time.perf_counter() - t0) * 1000), **stats_b, **merge_info})

    t0 = time.perf_counter()
    n_fixed = respace(engine, img, tokens)
    if n_fixed:
        result.passes.append({"pass": "respace", "fixed": n_fixed, "ms": int((time.perf_counter() - t0) * 1000)})

    tokens.sort(key=lambda t: (t.y0, t.x0))
    for i, t in enumerate(tokens):
        t.id = f"p{page}:t{i}"
    flag_noise(tokens)
    result.image = img
    result.tokens = tokens
    final = token_stats([t for t in tokens if not t.noise])
    if final["n"] and final["mean_conf"] < 0.8:
        result.warnings.append("Low OCR confidence on this page — values will need review")
    if final["n"] == 0:
        result.warnings.append("No readable text found on this page")
    elif final["n"] < SPARSE_TOKENS and allow_retry:
        result.warnings.append("Very little text could be read from this page — retake the photo (steady, well lit, whole page in frame)")
    return result
