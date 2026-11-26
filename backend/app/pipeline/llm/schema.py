"""Output schema for the vision model, defined once and rendered for each provider.

The model only *transcribes* (values exactly as printed + the OCR token ids that
contain them). All parsing, normalisation and arithmetic happen in code.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.extraction import DOCUMENT_TYPES, HEADER_KEYS, SUMMARY_KEYS

LINE_KEYS = ("name", "description", "sku", "quantity", "unit", "rate", "discount", "tax_rate", "tax_amount", "amount", "line_total")
TAX_KEYS = ("label", "rate", "base", "amount")


# ---- tiny schema DSL -------------------------------------------------------
def _s(nullable: bool = False, enum: tuple[str, ...] | None = None, desc: str | None = None) -> dict:
    return {"t": "string", "nullable": nullable, "enum": list(enum) if enum else None, "desc": desc}


def _b(nullable: bool = False, desc: str | None = None) -> dict:
    return {"t": "boolean", "nullable": nullable, "desc": desc}


def _i(desc: str | None = None) -> dict:
    return {"t": "integer", "nullable": False, "desc": desc}


def _a(items: dict, desc: str | None = None) -> dict:
    return {"t": "array", "items": items, "nullable": False, "desc": desc}


def _o(props: dict[str, dict], nullable: bool = False, desc: str | None = None) -> dict:
    return {"t": "object", "props": props, "nullable": nullable, "desc": desc}


FIELD = _o(
    {
        "text": _s(nullable=True, desc="Value exactly as printed (keep symbols, separators, signs)"),
        "tokens": _a(_s(), desc="OCR token ids that contain this value"),
        "confidence": _s(enum=("high", "medium", "low")),
    },
    nullable=True,
)

SCHEMA_DSL = _o(
    {
        "document_type": _s(enum=DOCUMENT_TYPES),
        "language": _s(nullable=True, desc="ISO 639-1 code"),
        "currency_code": _s(nullable=True, desc="ISO 4217 code"),
        "currency_evidence": _s(nullable=True),
        "prices_include_tax": _b(nullable=True),
        "header": _o({k: FIELD for k in HEADER_KEYS}),
        "line_items": _a(
            _o({"page": _i(), **{k: FIELD for k in LINE_KEYS}, "category": _s(desc="Services, Materials or a short category")})
        ),
        "taxes": _a(_o({k: FIELD for k in TAX_KEYS})),
        "summary": _o({k: FIELD for k in SUMMARY_KEYS}),
        "observations": _a(_s()),
    }
)


# The same schema in three parts so they can be requested in parallel: every part makes the model write a
# fraction of the tokens, and writing is what the wait is made of. Nothing is added or removed.
_P = SCHEMA_DSL["props"]
HEADER_DSL = _o({k: _P[k] for k in ("document_type", "language", "header")})
TOTALS_DSL = _o({k: _P[k] for k in ("currency_code", "currency_evidence", "prices_include_tax", "taxes", "summary", "observations")})
ITEMS_DSL = _o({k: _P[k] for k in ("line_items", "observations")})
SECTIONS = (  # (scope shown to the model, schema, name sent to the API)
    ("header", HEADER_DSL, "document_header"),
    ("totals", TOTALS_DSL, "document_totals"),
    ("items", ITEMS_DSL, "document_line_items"),
)


def to_json_schema(node: dict = SCHEMA_DSL) -> dict[str, Any]:
    """Standard JSON Schema compatible with OpenAI strict structured outputs."""
    t = node["t"]
    if t == "object":
        out: dict[str, Any] = {
            "type": "object",
            "properties": {k: to_json_schema(v) for k, v in node["props"].items()},
            "required": list(node["props"].keys()),
            "additionalProperties": False,
        }
        if node.get("nullable"):
            return {"anyOf": [out, {"type": "null"}]}
        return out
    if t == "array":
        out = {"type": "array", "items": to_json_schema(node["items"])}
    else:
        out = {"type": [t, "null"] if node.get("nullable") else t}
        if node.get("enum"):
            out["enum"] = node["enum"] + ([None] if node.get("nullable") else [])
    if node.get("desc"):
        out["description"] = node["desc"]
    return out


def to_gemini_schema(node: dict = SCHEMA_DSL) -> dict[str, Any]:
    """Gemini responseSchema (OpenAPI subset, upper-case types, `nullable`)."""
    t = node["t"]
    out: dict[str, Any] = {"type": t.upper()}
    if t == "object":
        out["properties"] = {k: to_gemini_schema(v) for k, v in node["props"].items()}
        out["required"] = list(node["props"].keys())
        out["propertyOrdering"] = list(node["props"].keys())
    elif t == "array":
        out["items"] = to_gemini_schema(node["items"])
    if node.get("enum"):
        out["enum"] = node["enum"]
    if node.get("nullable"):
        out["nullable"] = True
    if node.get("desc"):
        out["description"] = node["desc"]
    return out


# ---- validation models -----------------------------------------------------
class LLMField(BaseModel):
    text: str | None = None
    tokens: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"


class LLMLineItem(BaseModel):
    page: int = 1
    name: LLMField | None = None
    description: LLMField | None = None
    sku: LLMField | None = None
    quantity: LLMField | None = None
    unit: LLMField | None = None
    rate: LLMField | None = None
    discount: LLMField | None = None
    tax_rate: LLMField | None = None
    tax_amount: LLMField | None = None
    amount: LLMField | None = None
    line_total: LLMField | None = None
    category: str | None = None


class LLMTax(BaseModel):
    label: LLMField | None = None
    rate: LLMField | None = None
    base: LLMField | None = None
    amount: LLMField | None = None


class LLMExtraction(BaseModel):
    document_type: str = "other"
    language: str | None = None
    currency_code: str | None = None
    currency_evidence: str | None = None
    prices_include_tax: bool | None = None
    header: dict[str, LLMField | None] = Field(default_factory=dict)
    line_items: list[LLMLineItem] = Field(default_factory=list)
    taxes: list[LLMTax] = Field(default_factory=list)
    summary: dict[str, LLMField | None] = Field(default_factory=dict)
    observations: list[str] = Field(default_factory=list)
