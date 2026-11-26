"""Image loading and preprocessing (OpenCV).

The original upload is always preserved on disk; everything here produces
derived images. Geometry (perspective / rotation / deskew) is applied to the
display image so OCR boxes line up with what the reviewer sees. Photometric
enhancement is only used for OCR passes.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

try:  # HEIC/HEIF support (iPhone photos)
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except Exception:  # noqa: BLE001
    HEIF_SUPPORTED = False


def load_image_bytes(data: bytes) -> np.ndarray:
    """Decode bytes to a BGR uint8 array, honouring EXIF orientation."""
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg
        im = im.convert("RGB")
        arr = np.asarray(im)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def encode_jpeg(img: np.ndarray, quality: int = 90) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
    if not ok:
        raise RuntimeError("PNG encode failed")
    return buf.tobytes()


def resize_max(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = max(h, w)
    if s <= max_side:
        return img
    f = max_side / s
    return cv2.resize(img, (int(round(w * f)), int(round(h * f))), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- geometry
def order_quad(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    out = np.zeros((4, 2), dtype=np.float32)
    out[0] = pts[np.argmin(s)]
    out[2] = pts[np.argmax(s)]
    out[1] = pts[np.argmin(d)]
    out[3] = pts[np.argmax(d)]
    return out


def warp_quad(img: np.ndarray, quad: np.ndarray, min_long_side: int = 1800, max_long_side: int = 3600) -> np.ndarray:
    """Perspective-correct the quad. Small captures are upsampled *during* the warp
    (one interpolation step from the original pixels) so OCR sees legible glyphs."""
    q = order_quad(quad)
    (tl, tr, br, bl) = q
    width = float(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = float(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    long_side = max(width, height, 1.0)
    scale = 1.0
    if long_side < min_long_side:
        scale = min_long_side / long_side
    elif long_side > max_long_side:
        scale = max_long_side / long_side
    width, height = max(int(width * scale), 16), max(int(height * scale), 16)
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(q, dst)
    return cv2.warpPerspective(img, m, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    v1, v2 = a - b, c - b
    cosang = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9))
    return float(np.degrees(np.arccos(np.clip(cosang, -1, 1))))


@dataclass
class QuadDetection:
    quad: np.ndarray  # 4x2 in original pixel coordinates (ordered)
    score: float
    area_ratio: float
    method: str


def detect_document(img: np.ndarray) -> QuadDetection | None:
    """Find a paper document on a contrasting background.

    Conservative by design: returns ``None`` unless the quadrilateral has strong
    edge support AND the paper is clearly separated from its surroundings, so
    screenshots and flat scans are never cropped.
    """
    h0, w0 = img.shape[:2]
    scale = 900.0 / max(h0, w0) if max(h0, w0) > 900 else 1.0
    small = cv2.resize(img, (int(w0 * scale), int(h0 * scale)), interpolation=cv2.INTER_AREA) if scale != 1.0 else img.copy()
    h, w = small.shape[:2]
    area_img = float(h * w)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    med = float(np.median(blur))
    edges = cv2.Canny(blur, int(max(10, 0.5 * med)), int(min(255, 1.2 * med + 20)))
    edges_d = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)

    contours: list[np.ndarray] = []
    cs, _ = cv2.findContours(edges_d, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours.extend(cs)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)
    cs2, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours.extend(cs2)

    best: QuadDetection | None = None
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        area = cv2.contourArea(c)
        if area < 0.18 * area_img:
            break
        hull = cv2.convexHull(c)
        peri = cv2.arcLength(hull, True)
        approx = None
        for eps in (0.015, 0.02, 0.03, 0.045):
            a = cv2.approxPolyDP(hull, eps * peri, True)
            if len(a) == 4:
                approx = a
                break
        if approx is None:
            continue
        q = order_quad(approx.reshape(4, 2))
        qa = cv2.contourArea(q.reshape(-1, 1, 2))
        ratio = qa / area_img
        if ratio < 0.18 or ratio > 0.96:
            continue
        angles = [_angle(q[i - 1], q[i], q[(i + 1) % 4]) for i in range(4)]
        if min(angles) < 55 or max(angles) > 125:
            continue
        # edge support along each side
        supports = []
        for i in range(4):
            p, r = q[i], q[(i + 1) % 4]
            n = 40
            hits = 0
            for t in np.linspace(0.05, 0.95, n):
                x, y = (p + (r - p) * t).astype(int)
                if 0 <= x < w and 0 <= y < h and edges_d[y, x] > 0:
                    hits += 1
            supports.append(hits / n)
        support = float(np.mean(sorted(supports)[1:]))  # tolerate one weak side
        # inside vs outside brightness contrast
        mask = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(mask, q.astype(np.int32), 255)
        inside = float(blur[mask > 0].mean())
        outside_px = blur[mask == 0]
        outside = float(outside_px.mean()) if outside_px.size else inside
        contrast = (inside - outside) / 255.0
        if support < 0.55 or contrast < 0.12:
            continue
        score = support + 0.5 * min(contrast, 0.5) + 0.2 * ratio
        if best is None or score > best.score:
            best = QuadDetection(q / scale, score, ratio, "contour")
    return best


def rotate90(img: np.ndarray, k: int) -> np.ndarray:
    k %= 4
    if k == 0:
        return img
    return np.ascontiguousarray(np.rot90(img, -k))  # k * 90 degrees clockwise


def rotate_small(img: np.ndarray, angle_deg: float) -> np.ndarray:
    """Rotate by a small angle (deskew), expanding the canvas with white."""
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


# ---------------------------------------------------------------- quality
@dataclass
class ImageQuality:
    width: int
    height: int
    blur: float  # variance of Laplacian on a normalised-size image (higher = sharper)
    brightness: float
    contrast: float
    glare_ratio: float
    warnings: list[str]


def measure_quality(img: np.ndarray) -> ImageQuality:
    h, w = img.shape[:2]
    g = cv2.cvtColor(resize_max(img, 1400), cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(g, cv2.CV_64F).var())
    brightness = float(g.mean())
    contrast = float(g.std())
    glare = float((g >= 250).mean())
    warnings = []
    if blur < 60:
        warnings.append("Image looks blurry — hold steady or retake for best accuracy")
    if brightness < 70:
        warnings.append("Image is dark — add light or enable the flash")
    if contrast < 25:
        warnings.append("Low contrast image")
    if max(h, w) < 1000:
        warnings.append("Low resolution — small text may be misread")
    return ImageQuality(w, h, blur, brightness, contrast, glare, warnings)


# ---------------------------------------------------------------- enhancement
def normalize_background(gray: np.ndarray) -> np.ndarray:
    """Remove shadows / uneven lighting by dividing by an estimated background."""
    k = max(15, (min(gray.shape[:2]) // 25) | 1)
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.medianBlur(bg, 21)
    norm = cv2.divide(gray, bg, scale=255)
    return norm


def enhance(img: np.ndarray, level: str = "standard", upscale: float = 1.0) -> np.ndarray:
    """Return an OCR-friendly BGR image.

    * ``standard``: gentle — CLAHE only if contrast is low.
    * ``strong``: shadow removal + CLAHE + unsharp mask (used for retry passes).
    """
    out = img
    if upscale and upscale > 1.01:
        out = cv2.resize(out, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    if level == "standard":
        if gray.std() < 40:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            gray = clahe.apply(gray)
            return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        return out
    norm = normalize_background(gray)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    norm = clahe.apply(norm)
    blur = cv2.GaussianBlur(norm, (0, 0), 1.2)
    sharp = cv2.addWeighted(norm, 1.6, blur, -0.6, 0)
    return cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)


def thumbnail(img: np.ndarray, max_side: int = 480) -> bytes:
    return encode_jpeg(resize_max(img, max_side), 82)
