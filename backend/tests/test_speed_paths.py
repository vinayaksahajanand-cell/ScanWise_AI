"""Latency-related behaviour: fail fast on exhausted credit, reasoning effort, no wasted OCR pass."""

import httpx
import numpy as np
import pytest

from app.pipeline import page_ocr
from app.pipeline.llm import providers
from app.pipeline.llm.providers import OpenAICompatibleProvider, ProviderError
from app.pipeline.ocr.base import OCREngine, RawLine


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self.text, self.headers, self._body = status, str(body), {}, body

    def json(self):
        return self._body


def _provider(model="gpt-5", effort="low", retries=2):
    return OpenAICompatibleProvider("openai", "http://x/v1", "k", model, 5, retries, 4, reasoning_effort=effort)


def _patch_post(monkeypatch, responses, seen):
    it = iter(responses)

    class Client:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None, json=None):
            seen.append(json)
            return next(it)

    monkeypatch.setattr(providers.httpx, "Client", Client)
    monkeypatch.setattr(providers.time, "sleep", lambda s: seen.append(("sleep", s)))


OK = {"choices": [{"message": {"content": "{}"}}]}


def test_exhausted_credit_fails_without_retrying(monkeypatch):
    seen = []
    quota = FakeResponse(429, {"error": {"code": "credit_balance_exhausted", "type": "insufficient_quota"}})
    _patch_post(monkeypatch, [quota, quota, quota], seen)
    with pytest.raises(ProviderError, match="429"):
        _provider().complete("s", [("user", "u")], [])
    assert len(seen) == 1, "a quota error must not be retried or slept on"


def test_ordinary_rate_limit_is_still_retried(monkeypatch):
    seen = []
    _patch_post(monkeypatch, [FakeResponse(429, "slow down"), FakeResponse(200, OK)], seen)
    assert _provider().complete("s", [("user", "u")], []) == {}
    assert any(isinstance(x, tuple) and x[0] == "sleep" for x in seen)


def test_reasoning_effort_sent_only_to_reasoning_models(monkeypatch):
    for model, expected in (("gpt-5", "low"), ("o3", "low"), ("gpt-4.1", None)):
        seen = []
        _patch_post(monkeypatch, [FakeResponse(200, OK)], seen)
        _provider(model).complete("s", [("user", "u")], [])
        assert seen[0].get("reasoning_effort") == expected, model


def test_reasoning_effort_dropped_if_endpoint_rejects_it(monkeypatch):
    seen = []
    bad = FakeResponse(400, "Unsupported parameter: 'reasoning_effort' is not supported with this model")
    _patch_post(monkeypatch, [bad, FakeResponse(200, OK)], seen)
    p = _provider()
    assert p.complete("s", [("user", "u")], []) == {}
    assert "reasoning_effort" in seen[0] and "reasoning_effort" not in seen[1]
    assert p._send_reasoning_effort is False


def test_newer_reasoning_models_get_effort_and_no_temperature(monkeypatch):
    """gpt-6.x was not recognised as a reasoning model: it was sent temperature=0 (HTTP 400, a wasted round trip)
    and then ran at the model's slow default effort."""
    for model in ("gpt-5", "gpt-5-mini", "gpt-6.1-terra", "gpt-7", "o4-mini"):
        seen = []
        _patch_post(monkeypatch, [FakeResponse(200, OK)], seen)
        _provider(model).complete("s", [("user", "u")], [])
        assert len(seen) == 1 and "temperature" not in seen[0] and seen[0]["reasoning_effort"] == "low", model
    seen = []
    _patch_post(monkeypatch, [FakeResponse(200, OK)], seen)
    _provider("gpt-4.1").complete("s", [("user", "u")], [])
    assert seen[0]["temperature"] == 0 and "reasoning_effort" not in seen[0]


def test_unsupported_effort_value_switches_to_a_supported_one(monkeypatch):
    seen = []
    bad = FakeResponse(400, "Unsupported value: 'reasoning_effort' does not support 'minimal' with this model. "
                            "Supported values are: 'none', 'low', 'medium', 'high', and 'xhigh'.")
    _patch_post(monkeypatch, [bad, FakeResponse(200, OK)], seen)
    p = _provider("gpt-6.1-terra", effort="minimal")
    assert p.complete("s", [("user", "u")], []) == {}
    assert seen[0]["reasoning_effort"] == "minimal" and seen[1]["reasoning_effort"] == "low"


def test_what_an_endpoint_rejected_is_remembered_across_scans(monkeypatch):
    """Providers are rebuilt for every scan; a rejected parameter must cost one wasted request per server, not per scan."""
    seen = []
    bad = FakeResponse(400, "Unsupported parameter: 'temperature' is not supported with this model")
    _patch_post(monkeypatch, [bad, FakeResponse(200, OK), FakeResponse(200, OK)], seen)
    _provider("some-new-model").complete("s", [("user", "u")], [])
    assert len(seen) == 2
    _provider("some-new-model").complete("s", [("user", "u")], [])  # the next scan
    assert len(seen) == 3 and "temperature" not in seen[2]


class CountingEngine(OCREngine):
    """A page of large, confident text; counts how many OCR passes the page costs."""
    name = "counting"

    def __init__(self, conf, height):
        self.conf, self.height, self.calls = conf, height, 0

    def describe(self): return "counting"
    def available(self): return True

    def run(self, image_bgr):
        self.calls += 1
        h = self.height
        return [RawLine([(10, 20 + i * 40), (300, 20 + i * 40), (300, 20 + i * 40 + h), (10, 20 + i * 40 + h)], f"Line {i} 123.45", self.conf, [])
                for i in range(12)]


def _passes(conf, height):
    eng = CountingEngine(conf, height)
    page_ocr.ocr_page_image(eng, np.full((1700, 1300, 3), 255, np.uint8), 1, allow_geometry=False, auto_crop=False)
    return eng.calls


def test_small_but_confident_text_skips_the_second_pass():
    assert _passes(conf=0.99, height=16) == 1


def test_small_and_less_confident_text_still_gets_the_second_pass():
    assert _passes(conf=0.95, height=16) == 2


def test_low_confidence_always_gets_the_second_pass():
    assert _passes(conf=0.85, height=30) == 2


# --- failed reads must not look "confident" ----------------------------------------------------------
class SparseEngine(OCREngine):
    """Returns one confident line on the first pass and a full page once the image has been enhanced+upscaled."""
    name = "sparse"

    def __init__(self):
        self.calls = 0

    def describe(self): return "sparse"
    def available(self): return True

    def run(self, image_bgr):
        self.calls += 1
        n = 1 if self.calls == 1 else 12
        return [RawLine([(10, 20 + i * 40), (300, 20 + i * 40), (300, 50 + i * 40), (10, 50 + i * 40)], f"Line {i} 123.45", 1.0, []) for i in range(n)]


def test_one_confident_token_is_not_a_good_read():
    eng = SparseEngine()
    res = page_ocr.ocr_page_image(eng, np.full((1700, 1300, 3), 255, np.uint8), 1, allow_geometry=False, auto_crop=False)
    assert [p["pass"] for p in res.passes] == ["A", "B-enhanced"]
    assert len([t for t in res.tokens if not t.noise]) >= 12


def test_unreadable_page_says_so():
    class Nothing(SparseEngine):
        def run(self, image_bgr):
            self.calls += 1
            return [RawLine([(10, 20), (300, 20), (300, 50), (10, 50)], "Only line", 1.0, [])]

    res = page_ocr.ocr_page_image(Nothing(), np.full((1700, 1300, 3), 255, np.uint8), 1, allow_geometry=False, auto_crop=False)
    assert any("Very little text" in w for w in res.warnings)


def test_bad_auto_crop_falls_back_to_the_full_photo(monkeypatch):
    """If the detected 'document' region yields almost no text, the uncropped photo is read instead."""
    full = np.full((1700, 1300, 3), 255, np.uint8)
    cropped = np.full((1500, 1100, 3), 255, np.uint8)  # smaller than the uncropped photo

    def fake_geometry(original, corners, auto_crop, rotation):
        return (cropped, "auto", 1700) if auto_crop else (full, "none", 1700)

    class ByImage(OCREngine):
        name = "byimage"
        def describe(self): return "byimage"
        def available(self): return True

        def run(self, image_bgr):
            n = 12 if image_bgr.shape[0] >= 1700 else 1  # only the uncropped photo is readable
            return [RawLine([(10, 20 + i * 40), (300, 20 + i * 40), (300, 50 + i * 40), (10, 50 + i * 40)], f"Line {i} 99.00", 1.0, []) for i in range(n)]

    monkeypatch.setattr(page_ocr, "prepare_geometry", fake_geometry)
    res = page_ocr.ocr_page_image(ByImage(), full, 1, allow_geometry=False, auto_crop=True)
    assert res.crop == "none"
    assert "A-uncropped" in [p["pass"] for p in res.passes]
    assert len([t for t in res.tokens if not t.noise]) >= 12
