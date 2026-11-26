"""Run the vision model over a document (page batches, schema validation, repair retry)."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from pydantic import ValidationError

from app.config import Settings

from ..imaging import encode_jpeg, resize_max
from ..ocr.base import Token
from .prompt import SYSTEM_PROMPT, repair_prompt, user_prompt
from .providers import VisionProvider
from .schema import SECTIONS, LLMExtraction

log = logging.getLogger(__name__)


@dataclass
class VisionPage:
    page: int
    image: np.ndarray
    tokens: list[Token]
    width: float
    height: float


@dataclass
class VisionRun:
    result: LLMExtraction
    calls: int
    repaired: bool
    pages_sent: int
    pages_skipped: int


def _ask(provider: VisionProvider, text: str, images: list[bytes], schema: dict | None, name: str,
         gate: threading.BoundedSemaphore) -> tuple[LLMExtraction, int, bool]:
    """One request (+ one repair retry if the JSON doesn't validate)."""
    messages: list[tuple[str, str]] = [("user", text)]
    extra = (schema, name) if schema is not None else ()  # the default schema is the providers' own default
    with gate:
        raw = provider.complete(SYSTEM_PROMPT, messages, images, *extra)
    try:
        return LLMExtraction.model_validate(raw), 1, False
    except ValidationError as e:
        log.warning("vision output failed validation, retrying with repair prompt")
        messages += [("assistant", json.dumps(raw)[:30000]), ("user", repair_prompt(str(e)))]
        with gate:
            raw2 = provider.complete(SYSTEM_PROMPT, messages, images, *extra)
        return LLMExtraction.model_validate(raw2), 2, True


def _call(provider: VisionProvider, batch: list[VisionPage], total_pages: int, max_side: int,
          split: bool, gate: threading.BoundedSemaphore) -> tuple[LLMExtraction, int, bool]:
    images = [encode_jpeg(resize_max(p.image, max_side), 88) for p in batch]
    pages = [(p.page, p.tokens, p.width, p.height) for p in batch]
    if not split:
        return _ask(provider, user_prompt(pages, total_pages), images, None, "document_extraction", gate)
    # Same page, same prompt rules, same fields -- requested as three parts at once. The wait is dominated by
    # how many tokens the model writes, so three small answers arrive in the time of the largest one.
    with ThreadPoolExecutor(max_workers=len(SECTIONS)) as pool:
        futures = {scope: pool.submit(_ask, provider, user_prompt(pages, total_pages, scope), images, dsl, name, gate)
                   for scope, dsl, name in SECTIONS}
        got = {scope: f.result() for scope, f in futures.items()}
    return _combine({k: v[0] for k, v in got.items()}), sum(v[1] for v in got.values()), any(v[2] for v in got.values())


def _combine(parts: dict[str, LLMExtraction]) -> LLMExtraction:
    out, totals, items = parts["header"], parts["totals"], parts["items"]
    out.currency_code, out.currency_evidence, out.prices_include_tax = totals.currency_code, totals.currency_evidence, totals.prices_include_tax
    out.taxes, out.summary = totals.taxes, totals.summary
    out.line_items = items.line_items
    out.observations = [*totals.observations, *items.observations]
    return out


def _merge(parts: list[LLMExtraction]) -> LLMExtraction:
    if len(parts) == 1:
        return parts[0]
    out = LLMExtraction(document_type=parts[0].document_type)
    for p in parts:
        out.language = out.language or p.language
        out.currency_code = out.currency_code or p.currency_code
        out.currency_evidence = out.currency_evidence or p.currency_evidence
        if out.prices_include_tax is None:
            out.prices_include_tax = p.prices_include_tax
        for k, v in p.header.items():
            if v is not None and v.text and not (out.header.get(k) and out.header[k].text):
                out.header[k] = v
        out.line_items.extend(p.line_items)
        out.taxes.extend(p.taxes)
        for k, v in p.summary.items():  # totals usually sit on the last page: later pages win
            if v is not None and v.text:
                out.summary[k] = v
        out.observations.extend(p.observations)
    return out


def run_vision(provider: VisionProvider, pages: list[VisionPage], settings: Settings) -> VisionRun:
    use = pages[: settings.llm_max_pages]
    per = max(1, provider.pages_per_call)
    batches = [use[i : i + per] for i in range(0, len(use), per)]
    split = bool(getattr(settings, "llm_split_call", False))
    gate = threading.BoundedSemaphore(max(1, int(getattr(settings, "llm_parallel_calls", 1))))

    def one(batch: list[VisionPage]) -> tuple[LLMExtraction, int, bool]:
        return _call(provider, batch, len(pages), settings.llm_image_max_side, split, gate)

    if len(batches) > 1:  # page batches are independent: run them together instead of one after another
        with ThreadPoolExecutor(max_workers=min(len(batches), 4)) as pool:
            results = list(pool.map(one, batches))
    else:
        results = [one(batches[0])]

    parts: list[LLMExtraction] = []
    calls = 0
    repaired = False
    for batch, (res, n, rep) in zip(batches, results):
        for li in res.line_items:
            if not li.page or li.page < batch[0].page or li.page > batch[-1].page:
                li.page = batch[0].page
        parts.append(res)
        calls += n
        repaired = repaired or rep
    return VisionRun(_merge(parts), calls, repaired, len(use), max(0, len(pages) - len(use)))
