"""OCR abstraction: engines return text segments with polygons, confidences and
word boxes in *pixel* coordinates of the image they were given."""

from __future__ import annotations

import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Word:
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Token:
    """A text segment (roughly one detector box)."""

    id: str
    page: int
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float
    words: list[Word] = field(default_factory=list)
    source: str = "ocr"  # ocr | pdf_text
    noise: bool = False
    alt_text: str | None = None  # disagreeing reading from another OCR pass
    alt_conf: float | None = None
    angle: float = 0.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def to_dict(self, width: float, height: float) -> dict:
        """Serialise with normalised (0..1) coordinates."""
        return {
            "id": self.id,
            "text": self.text,
            "conf": round(self.conf, 4),
            "bbox": [round(self.x0 / width, 5), round(self.y0 / height, 5), round(self.x1 / width, 5), round(self.y1 / height, 5)],
            "words": [
                [w.text, round(w.conf, 4), round(w.x0 / width, 5), round(w.y0 / height, 5), round(w.x1 / width, 5), round(w.y1 / height, 5)]
                for w in self.words
            ],
            "source": self.source,
            "noise": self.noise,
            "alt_text": self.alt_text,
            "alt_conf": round(self.alt_conf, 4) if self.alt_conf is not None else None,
        }

    @classmethod
    def from_dict(cls, d: dict, page: int, width: float, height: float) -> "Token":
        x0, y0, x1, y1 = d["bbox"]
        return cls(
            id=d["id"],
            page=page,
            text=d["text"],
            conf=d["conf"],
            x0=x0 * width,
            y0=y0 * height,
            x1=x1 * width,
            y1=y1 * height,
            words=[Word(w[0], w[1], w[2] * width, w[3] * height, w[4] * width, w[5] * height) for w in d.get("words", [])],
            source=d.get("source", "ocr"),
            noise=d.get("noise", False),
            alt_text=d.get("alt_text"),
            alt_conf=d.get("alt_conf"),
        )


@dataclass
class RawLine:
    poly: list[tuple[float, float]]
    text: str
    score: float
    words: list[tuple[str, float, list[tuple[float, float]] | None]] = field(default_factory=list)


class OCREngine(ABC):
    name: str = "base"

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def run(self, image_bgr: np.ndarray) -> list[RawLine]: ...

    def recognize_crop(self, crop_bgr: np.ndarray) -> tuple[str, float] | None:
        """Optional: recognise a single text-line crop (used to repair dropped spaces)."""
        return None

    def describe(self) -> str:
        return self.name


def _is_cjk(ch: str) -> bool:
    name = unicodedata.name(ch, "")
    return any(k in name for k in ("CJK", "HIRAGANA", "KATAKANA", "HANGUL"))


def raw_to_tokens(lines: list[RawLine], page: int, start_index: int = 0) -> list[Token]:
    tokens: list[Token] = []
    for i, ln in enumerate(lines):
        text = ln.text.strip()
        if not text:
            continue
        xs = [p[0] for p in ln.poly]
        ys = [p[1] for p in ln.poly]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        words: list[Word] = []
        for wt, wc, wpoly in ln.words:
            if not wt or not wt.strip():
                continue
            if wpoly:
                wx = [p[0] for p in wpoly]
                wy = [p[1] for p in wpoly]
                words.append(Word(wt, float(wc), min(wx), min(wy), max(wx), max(wy)))
        if not words or "".join(w.text for w in words).replace(" ", "") != text.replace(" ", ""):
            words = split_words(text, ln.score, x0, y0, x1, y1)
        # angle of the top edge (deg); polygons are clockwise from top-left
        angle = 0.0
        if len(ln.poly) == 4:
            (ax, ay), (bx, by) = ln.poly[0], ln.poly[1]
            if bx - ax > 1:
                angle = float(np.degrees(np.arctan2(by - ay, bx - ax)))
        tokens.append(
            Token(
                id=f"p{page}:t{start_index + len(tokens)}",
                page=page,
                text=text,
                conf=float(ln.score),
                x0=float(x0),
                y0=float(y0),
                x1=float(x1),
                y1=float(y1),
                words=words,
                angle=angle,
            )
        )
    return tokens


def split_words(text: str, conf: float, x0: float, y0: float, x1: float, y1: float) -> list[Word]:
    """Approximate word boxes by character proportion (used when the engine gives none)."""
    parts = text.split()
    if not parts:
        return []
    total = max(1, len(text))
    words = []
    cursor = 0
    width = x1 - x0
    for p in parts:
        idx = text.find(p, cursor)
        if idx < 0:
            idx = cursor
        wx0 = x0 + width * idx / total
        wx1 = x0 + width * (idx + len(p)) / total
        words.append(Word(p, conf, wx0, y0, wx1, y1))
        cursor = idx + len(p)
    return words


def flag_noise(tokens: list[Token]) -> None:
    """Mark icon glyphs / UI debris so extraction ignores them (they stay visible)."""
    if not tokens:
        return
    latin = sum(1 for t in tokens if any(c.isascii() and c.isalpha() for c in t.text))
    mostly_latin = latin / len(tokens) > 0.6
    heights = sorted(t.h for t in tokens)
    med_h = heights[len(heights) // 2] if heights else 0
    for t in tokens:
        s = t.text.strip()
        if not s:
            t.noise = True
            continue
        if len(s) <= 2 and t.conf < 0.65:
            t.noise = True
        elif mostly_latin and len(s) <= 2 and any(_is_cjk(c) for c in s):
            t.noise = True
        elif len(s) == 1 and not s.isalnum():
            t.noise = True
        elif med_h and t.h > 3.5 * med_h and len(s) <= 2:
            t.noise = True
