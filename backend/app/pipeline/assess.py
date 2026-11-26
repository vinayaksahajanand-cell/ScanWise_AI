"""Field-level status assignment, alternative arbitration and review summary.

Statuses:
* verified         — confirmed or corrected by a person
* high_confidence  — clean evidence, sources agree, arithmetic consistent
* needs_review     — disagreement, ambiguity, low OCR confidence, vision-only, ...
* unreadable       — present on the document but could not be read
* mismatch         — part of a failed arithmetic check
* missing          — not on the document
"""

from __future__ import annotations

from collections.abc import Iterator

from app.config import Settings
from app.schemas.extraction import (
    Alternative,
    ExtractedField,
    ExtractionResult,
    LineItem,
    ReviewSummary,
)

from . import finance

NUMERIC = {"money", "number", "percent"}
SEVERITY = {"verified": 0, "high_confidence": 1, "missing": 1, "needs_review": 2, "unreadable": 3, "mismatch": 4}
DERIVED_ITEM_ISSUES = ("Item name missing", "No price or amount found")


def iter_fields(ex: ExtractionResult) -> Iterator[tuple[str, ExtractedField]]:
    for k, f in ex.header.items():
        yield f"header.{k}", f
    for li in ex.line_items:
        for k, f in li.fields().items():
            yield f"line_items.{li.id}.{k}", f
    for t in ex.taxes:
        for k, f in t.fields().items():
            yield f"taxes.{t.id}.{k}", f
    for k, f in ex.summary.items():
        yield f"summary.{k}", f


def field_index(ex: ExtractionResult) -> dict[str, ExtractedField]:
    return dict(iter_fields(ex))


def _fails(ex: ExtractionResult) -> int:
    return sum(1 for c in (ex.validation.checks if ex.validation else []) if c.status == "fail")


def arbitrate(ex: ExtractionResult) -> list[str]:
    """For fields in failing checks, try the competing machine readings; adopt one
    only if it reduces failures. The field stays flagged for review either way."""
    finance.validate(ex)
    base = _fails(ex)
    if base == 0:
        return []
    idx = field_index(ex)
    notes: list[str] = []
    paths = [p for c in ex.validation.checks if c.status == "fail" for p in c.fields]  # type: ignore[union-attr]
    for path in dict.fromkeys(paths):
        f = idx.get(path)
        if f is None or not f.alternatives or f.corrected:
            continue
        for alt in list(f.alternatives):
            if alt.value is None or alt.value == f.value:
                continue
            old_value, old_raw, old_sources = f.value, f.raw, list(f.sources)
            f.value = alt.value
            finance.validate(ex)
            now = _fails(ex)
            if now < base:
                f.raw = alt.raw or alt.value
                f.extracted_value = alt.value
                f.sources = [alt.source]
                f.alternatives.remove(alt)
                f.alternatives.append(Alternative(source="+".join(old_sources), value=old_value, raw=old_raw, note="original reading — did not reconcile"))
                f.issues.append(f"Using the {alt.source} reading '{alt.raw}' because it reconciles with the totals — please verify")
                notes.append(f"{path}: switched to {alt.source} reading '{alt.raw}' (reconciles)")
                base = now
                break
            f.value = old_value
        if base == 0:
            break
    finance.validate(ex)
    return notes


def field_status(f: ExtractedField, s: Settings) -> str:
    if f.corrected or f.verified_by_user:
        return "verified"
    if not f.present:
        if f.raw or any(("Unreadable" in i or "could not read" in i) for i in f.issues):
            return "unreadable"
        return "missing"
    if f.validated is False:
        return "mismatch"
    if f.kind == "category":
        return "needs_review" if (f.extraction_confidence == "low" or f.issues) else "high_confidence"
    review = bool(f.issues) or any(a.source != "classifier" for a in f.alternatives)
    thr = s.review_conf_threshold if f.kind in NUMERIC or f.kind in ("date", "code") else s.review_text_conf_threshold
    if f.ocr_confidence is not None and f.ocr_confidence < thr:
        if not (f.validated and f.kind in NUMERIC and f.ocr_confidence >= 0.6):
            review = True
    if f.extraction_confidence == "low":
        review = True
    if f.sources == ["vision"] and not f.validated:
        review = True
    return "needs_review" if review else "high_confidence"


def _worst(statuses: list[str]) -> str:
    if not statuses:
        return "needs_review"
    return max(statuses, key=lambda s: SEVERITY.get(s, 2))


def assess(ex: ExtractionResult, s: Settings) -> str:
    """Assign statuses; return the document review state ("review" | "ready")."""
    if ex.validation is None:
        finance.validate(ex)
    summary = ReviewSummary()
    for _, f in iter_fields(ex):
        f.status = field_status(f, s)  # type: ignore[assignment]
        if f.present or f.status == "unreadable":
            summary.total_fields += 1
            if f.status == "high_confidence":
                summary.high_confidence += 1
            elif f.status == "verified":
                summary.verified += 1
            elif f.status == "needs_review":
                summary.needs_review += 1
            elif f.status == "unreadable":
                summary.unreadable += 1
            elif f.status == "mismatch":
                summary.mismatch += 1
    for li in ex.line_items:
        li.issues = [i for i in li.issues if i not in DERIVED_ITEM_ISSUES]
        if not li.name.present:
            li.issues.append("Item name missing")
        if not li.amount.present and not li.rate.present and not li.line_total.present:
            li.issues.append("No price or amount found")
        st = _worst([f.status for f in li.fields().values() if f.present or f.status == "unreadable"])
        user_ok = all(f.corrected for f in li.fields().values() if f.present) and any(f.present for f in li.fields().values())
        if li.issues and st in ("high_confidence", "verified") and not user_ok:
            st = "needs_review"
        li.status = st  # type: ignore[assignment]
    for t in ex.taxes:
        st = _worst([f.status for f in t.fields().values() if f.present or f.status == "unreadable"])
        if t.issues and st in ("high_confidence", "verified"):
            st = "needs_review"
        t.status = st  # type: ignore[assignment]
    c = ex.currency
    if c.overridden:
        c.status = "verified"
    elif not c.code:
        c.status = "missing"
    elif c.ambiguous or c.confidence in ("medium", "low"):
        c.status = "needs_review"
    else:
        c.status = "high_confidence"
    ex.review = summary
    doc_ok = (
        c.status in ("verified", "high_confidence")
        and (ex.validation is None or ex.validation.status in ("pass", "warning", "not_applicable"))
        and summary.needs_review == 0 and summary.unreadable == 0 and summary.mismatch == 0
        and all(li.status in ("verified", "high_confidence") for li in ex.line_items)
        and all(t.status in ("verified", "high_confidence") for t in ex.taxes)
        and (ex.line_items or ex.summary.get("grand_total", ExtractedField()).present)
    )
    return "ready" if doc_ok else "review"


def line_item_ok(li: LineItem) -> bool:
    return li.status in ("verified", "high_confidence")
