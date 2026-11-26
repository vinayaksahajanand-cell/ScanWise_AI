"""Concrete OCR engines.

* ``rapidocr`` (default): PaddleOCR PP-OCRv6 detection + recognition models on
  ONNX Runtime. Same model family as PaddleOCR, much lighter to deploy.
* ``paddleocr``: the official PaddleOCR pipeline (optional, heavy install).
* ``tesseract``: Tesseract via pytesseract (optional, needs the binary).

Engines are created lazily and kept per thread (ONNX sessions are reused).
"""

from __future__ import annotations

import logging
import shutil
import threading

import numpy as np

from .base import OCREngine, RawLine

log = logging.getLogger(__name__)


class RapidOCREngine(OCREngine):
    name = "rapidocr"

    def __init__(self, model_size: str = "small", max_side: int = 2400, min_score: float = 0.3, lang: str | None = None):
        self.model_size = model_size
        self.max_side = max_side
        self.min_score = min_score
        self.lang = lang
        self._local = threading.local()

    def available(self) -> bool:
        try:
            import rapidocr  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def describe(self) -> str:
        return f"rapidocr (PP-OCRv6 {self.model_size}, ONNX Runtime)"

    def _engine(self):
        eng = getattr(self._local, "engine", None)
        if eng is None:
            from rapidocr import ModelType, OCRVersion, RapidOCR

            params = {
                "Global.log_level": "warning",
                "Global.text_score": self.min_score,
                "Global.use_cls": False,
                "Global.return_word_box": True,
                "Global.max_side_len": self.max_side,
                "Det.ocr_version": OCRVersion.PPOCRV6,
                "Rec.ocr_version": OCRVersion.PPOCRV6,
                "Det.model_type": ModelType(self.model_size),
                "Rec.model_type": ModelType(self.model_size),
            }
            if self.lang:
                from rapidocr import LangRec

                params["Rec.lang_type"] = LangRec(self.lang)
            eng = RapidOCR(params=params)
            self._local.engine = eng
        return eng

    def recognize_crop(self, crop_bgr: np.ndarray) -> tuple[str, float] | None:
        """Recognition only (no detection) on a single text-line crop."""
        res = self._engine()(crop_bgr, use_det=False, use_cls=False, use_rec=True)
        if res is None or not getattr(res, "txts", None):
            return None
        return str(res.txts[0]), float(res.scores[0])

    def run(self, image_bgr: np.ndarray) -> list[RawLine]:
        # flags are passed explicitly: RapidOCR keeps per-call flags as instance state
        res = self._engine()(image_bgr, use_det=True, use_cls=False, use_rec=True)
        if res is None or getattr(res, "txts", None) is None or getattr(res, "boxes", None) is None:
            return []
        out: list[RawLine] = []
        word_results = res.word_results if isinstance(res.word_results, tuple) else ()
        for i, (box, txt, score) in enumerate(zip(res.boxes, res.txts, res.scores)):
            words = []
            if i < len(word_results) and word_results[i]:
                for item in word_results[i]:
                    if isinstance(item, (tuple, list)) and len(item) == 3:
                        wt, wc, wpoly = item
                        poly = [(float(p[0]), float(p[1])) for p in wpoly] if wpoly is not None else None
                        words.append((str(wt), float(wc), poly))
            out.append(RawLine([(float(p[0]), float(p[1])) for p in box], str(txt), float(score), words))
        return out


class PaddleOCREngine(OCREngine):
    name = "paddleocr"

    def __init__(self, lang: str = "en"):
        self.lang = lang
        self._local = threading.local()

    def available(self) -> bool:
        try:
            import paddleocr  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def describe(self) -> str:
        return "paddleocr (official pipeline)"

    def run(self, image_bgr: np.ndarray) -> list[RawLine]:
        eng = getattr(self._local, "engine", None)
        if eng is None:
            from paddleocr import PaddleOCR

            eng = PaddleOCR(lang=self.lang, use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
            self._local.engine = eng
        out: list[RawLine] = []
        for res in eng.predict(image_bgr):
            data = res.json.get("res", res.json) if hasattr(res, "json") else res
            texts = data.get("rec_texts", [])
            scores = data.get("rec_scores", [])
            polys = data.get("rec_polys", data.get("dt_polys", []))
            for txt, sc, poly in zip(texts, scores, polys):
                pts = [(float(p[0]), float(p[1])) for p in poly]
                out.append(RawLine(pts, str(txt), float(sc), []))
        return out


class TesseractEngine(OCREngine):
    name = "tesseract"

    def __init__(self, lang: str = "eng"):
        self.lang = lang

    def available(self) -> bool:
        try:
            import pytesseract  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return shutil.which("tesseract") is not None

    def run(self, image_bgr: np.ndarray) -> list[RawLine]:
        import pytesseract

        data = pytesseract.image_to_data(image_bgr[:, :, ::-1], lang=self.lang, output_type=pytesseract.Output.DICT, config="--psm 3")
        groups: dict[tuple, list[int]] = {}
        for i, txt in enumerate(data["text"]):
            if not str(txt).strip() or float(data["conf"][i]) < 0:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            groups.setdefault(key, []).append(i)
        out: list[RawLine] = []
        for idxs in groups.values():
            # split a line into segments at large gaps (table columns)
            idxs.sort(key=lambda i: data["left"][i])
            seg: list[int] = []
            segments: list[list[int]] = []
            for i in idxs:
                if seg:
                    prev = seg[-1]
                    gap = data["left"][i] - (data["left"][prev] + data["width"][prev])
                    if gap > 1.5 * max(data["height"][prev], 1):
                        segments.append(seg)
                        seg = []
                seg.append(i)
            if seg:
                segments.append(seg)
            for sg in segments:
                x0 = min(data["left"][i] for i in sg)
                y0 = min(data["top"][i] for i in sg)
                x1 = max(data["left"][i] + data["width"][i] for i in sg)
                y1 = max(data["top"][i] + data["height"][i] for i in sg)
                words = []
                for i in sg:
                    wx0, wy0 = data["left"][i], data["top"][i]
                    wx1, wy1 = wx0 + data["width"][i], wy0 + data["height"][i]
                    words.append((str(data["text"][i]), float(data["conf"][i]) / 100, [(wx0, wy0), (wx1, wy0), (wx1, wy1), (wx0, wy1)]))
                text = " ".join(w[0] for w in words)
                score = sum(w[1] for w in words) / len(words)
                out.append(RawLine([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], text, score, words))
        return out


_ENGINES: dict[str, OCREngine] = {}
_LOCK = threading.Lock()


def get_engine(name: str, **kwargs) -> OCREngine:
    """Return the configured engine, falling back to RapidOCR if unavailable."""
    with _LOCK:
        key = name
        if key in _ENGINES:
            return _ENGINES[key]
        engine: OCREngine
        if name == "paddleocr":
            engine = PaddleOCREngine()
        elif name == "tesseract":
            engine = TesseractEngine()
        else:
            engine = RapidOCREngine(**kwargs)
        if not engine.available():
            log.warning("OCR engine %s unavailable, falling back to rapidocr", name)
            engine = RapidOCREngine(**kwargs)
        _ENGINES[key] = engine
        return engine


def engines_status() -> list[dict]:
    return [
        {"name": "rapidocr", "available": RapidOCREngine().available(), "description": "PP-OCRv6 on ONNX Runtime (default)"},
        {"name": "paddleocr", "available": PaddleOCREngine().available(), "description": "Official PaddleOCR pipeline (optional)"},
        {"name": "tesseract", "available": TesseractEngine().available(), "description": "Tesseract OCR (optional)"},
    ]
