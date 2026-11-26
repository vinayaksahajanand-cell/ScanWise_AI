"""Structured extraction schema.

Design rules (anti-hallucination):
* Every value keeps the text exactly as printed (`raw`) and a normalised `value`.
* Every value keeps its evidence: page, bounding box, OCR tokens, OCR text and
  OCR confidence, plus which extractors produced or agreed with it (`sources`).
* Confidence signals are kept separate (OCR confidence, extractor confidence,
  arithmetic validation) and are never blended into a single score.
* `extracted_value` is the immutable machine reading; user corrections only
  change `value` and set `corrected=True` (the audit log keeps the history).
* Money is serialised as plain decimal strings, never floats.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FieldStatus = Literal["verified", "high_confidence", "needs_review", "unreadable", "mismatch", "missing"]
FieldKind = Literal["text", "money", "number", "percent", "date", "unit", "category", "code", "enum"]
Confidence = Literal["high", "medium", "low"]

HEADER_KEYS: tuple[str, ...] = (
    "document_number",
    "po_number",
    "reference_number",
    "document_date",
    "due_date",
    "payment_terms",
    "vendor_name",
    "vendor_address",
    "vendor_phone",
    "vendor_email",
    "vendor_tax_id",
    "customer_name",
    "customer_contact",
    "customer_address",
    "customer_phone",
    "customer_email",
    "customer_tax_id",
    "place_of_supply",
)

HEADER_KINDS: dict[str, FieldKind] = {
    "document_date": "date",
    "due_date": "date",
    "document_number": "code",
    "po_number": "code",
    "reference_number": "code",
    "vendor_tax_id": "code",
    "customer_tax_id": "code",
}

SUMMARY_KEYS: tuple[str, ...] = (
    "subtotal",
    "discount_total",
    "taxable_amount",
    "tax_total",
    "shipping",
    "other_charges",
    "rounding",
    "grand_total",
    "amount_paid",
    "balance_due",
)

LINE_FIELD_KEYS: tuple[str, ...] = (
    "name",
    "description",
    "sku",
    "quantity",
    "unit",
    "rate",
    "discount",
    "tax_rate",
    "tax_amount",
    "amount",
    "line_total",
    "category",
)

LINE_FIELD_KINDS: dict[str, FieldKind] = {
    "name": "text",
    "description": "text",
    "sku": "code",
    "quantity": "number",
    "unit": "unit",
    "rate": "money",
    "discount": "money",
    "tax_rate": "percent",
    "tax_amount": "money",
    "amount": "money",
    "line_total": "money",
    "category": "category",
}

DOCUMENT_TYPES: tuple[str, ...] = (
    "invoice",
    "tax_invoice",
    "proforma_invoice",
    "receipt",
    "bill",
    "quotation",
    "estimate",
    "purchase_order",
    "credit_note",
    "price_list",
    "other",
)


class BBox(BaseModel):
    """Axis-aligned box in normalised page coordinates (0..1)."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class Alternative(BaseModel):
    source: str
    value: str | None = None
    raw: str | None = None
    note: str | None = None


class ExtractedField(BaseModel):
    value: str | None = None
    raw: str | None = None
    extracted_value: str | None = None
    kind: FieldKind = "text"
    page: int | None = None
    bbox: BBox | None = None
    token_ids: list[str] = Field(default_factory=list)
    ocr_text: str | None = None
    ocr_confidence: float | None = None
    extraction_confidence: Confidence | None = None
    sources: list[str] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    validated: bool | None = None
    status: FieldStatus = "missing"
    issues: list[str] = Field(default_factory=list)
    corrected: bool = False
    verified_by_user: bool = False

    @property
    def present(self) -> bool:
        return self.value is not None and str(self.value).strip() != ""


class PriceBookMatch(BaseModel):
    item_id: str
    name: str
    rate: str | None = None
    currency: str | None = None
    unit: str | None = None
    score: float
    reason: str


class LineItem(BaseModel):
    id: str
    index: int
    name: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="text"))
    description: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="text"))
    sku: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="code"))
    quantity: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="number"))
    unit: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="unit"))
    rate: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="money"))
    discount: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="money"))
    tax_rate: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="percent"))
    tax_amount: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="money"))
    amount: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="money"))
    line_total: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="money"))
    category: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="category"))
    page: int | None = None
    bbox: BBox | None = None
    original_text: str | None = None
    sources: list[str] = Field(default_factory=list)
    status: FieldStatus = "needs_review"
    issues: list[str] = Field(default_factory=list)
    computed_amount: str | None = None
    pricebook_matches: list[PriceBookMatch] = Field(default_factory=list)
    user_added: bool = False

    def fields(self) -> dict[str, ExtractedField]:
        return {k: getattr(self, k) for k in LINE_FIELD_KEYS}


class TaxLine(BaseModel):
    id: str
    label: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="text"))
    tax_type: str | None = None
    rate: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="percent"))
    base: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="money"))
    amount: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="money"))
    applies_to: list[str] | None = None
    inclusive: bool | None = None
    status: FieldStatus = "needs_review"
    issues: list[str] = Field(default_factory=list)

    def fields(self) -> dict[str, ExtractedField]:
        return {"label": self.label, "rate": self.rate, "base": self.base, "amount": self.amount}


class CurrencyInfo(BaseModel):
    code: str | None = None
    symbol: str | None = None
    confidence: Confidence | None = None
    ambiguous: bool = False
    evidence: list[str] = Field(default_factory=list)
    candidates: list[dict] = Field(default_factory=list)
    minor_units: int = 2
    status: FieldStatus = "missing"
    overridden: bool = False
    issues: list[str] = Field(default_factory=list)


class Suggestion(BaseModel):
    path: str
    current: str | None
    suggested: str
    reason: str


class ValidationCheck(BaseModel):
    id: str
    category: Literal["line", "subtotal", "tax", "total", "balance", "consistency", "format"]
    label: str
    expected: str | None = None
    extracted: str | None = None
    difference: str | None = None
    status: Literal["pass", "explained", "warning", "fail", "skipped"]
    message: str
    fields: list[str] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)


class ValidationReport(BaseModel):
    status: Literal["pass", "warning", "fail", "incomplete", "not_applicable"]
    extracted_total: str | None = None
    recalculated_total: str | None = None
    difference: str | None = None
    prices_include_tax: bool | None = None
    computed: dict[str, str | None] = Field(default_factory=dict)
    checks: list[ValidationCheck] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class StageLog(BaseModel):
    stage: str
    status: Literal["ok", "skipped", "retry", "warning", "failed"]
    started_at: datetime
    duration_ms: int
    detail: str = ""
    data: dict | None = None


class ReviewSummary(BaseModel):
    total_fields: int = 0
    high_confidence: int = 0
    verified: int = 0
    needs_review: int = 0
    unreadable: int = 0
    mismatch: int = 0


class PageQuality(BaseModel):
    page: int
    text_source: Literal["ocr", "pdf_text", "mixed"]
    ocr_engine: str | None = None
    token_count: int = 0
    mean_confidence: float | None = None
    low_confidence_ratio: float | None = None
    blur_score: float | None = None
    warnings: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    schema_version: int = 1
    document_type: ExtractedField = Field(default_factory=lambda: ExtractedField(kind="enum"))
    language: str | None = None
    currency: CurrencyInfo = Field(default_factory=CurrencyInfo)
    header: dict[str, ExtractedField] = Field(default_factory=dict)
    line_items: list[LineItem] = Field(default_factory=list)
    taxes: list[TaxLine] = Field(default_factory=list)
    summary: dict[str, ExtractedField] = Field(default_factory=dict)
    prices_include_tax: bool | None = None
    validation: ValidationReport | None = None
    review: ReviewSummary = Field(default_factory=ReviewSummary)
    extractors: list[str] = Field(default_factory=list)
    page_quality: list[PageQuality] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    approved: bool = False

    def ensure_keys(self) -> "ExtractionResult":
        for key in HEADER_KEYS:
            if key not in self.header:
                self.header[key] = ExtractedField(kind=HEADER_KINDS.get(key, "text"))
        for key in SUMMARY_KEYS:
            if key not in self.summary:
                self.summary[key] = ExtractedField(kind="money")
        return self
