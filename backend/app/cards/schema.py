"""Output schema for the vision model when it reads a business card (rendered per provider)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# The twelve flat field keys the API and the New Client form share.
FIELD_KEYS = ("name", "company", "job_title", "email", "phone", "website",
              "street", "city", "state", "zip_code", "country", "tax_id")


def _s(nullable: bool = False, enum: tuple[str, ...] | None = None, desc: str | None = None) -> dict:
    return {"t": "string", "nullable": nullable, "enum": list(enum) if enum else None, "desc": desc}


def _a(items: dict, desc: str | None = None) -> dict:
    return {"t": "array", "items": items, "nullable": False, "desc": desc}


def _o(props: dict[str, dict], nullable: bool = False, desc: str | None = None) -> dict:
    return {"t": "object", "props": props, "nullable": nullable, "desc": desc}


_FIELD = _o(
    {
        "text": _s(nullable=True, desc="Value exactly as printed on the card, or null when it is not on the card"),
        "confidence": _s(enum=("high", "medium", "low"), desc="How sure you are that you read it correctly"),
    },
    nullable=True,
)

CARD_DSL = _o(
    {
        **{k: _FIELD for k in FIELD_KEYS},
        "other_emails": _a(_s(), desc="Further e-mail addresses printed on the card (not the primary one)"),
        "other_phones": _a(_s(), desc="Further phone/mobile numbers printed on the card (not the primary one, no fax)"),
        "language": _s(nullable=True, desc="Main language of the card, ISO 639-1"),
        "observations": _a(_s(), desc="Short notes, e.g. 'back side is in another language' or 'name is partly hidden'"),
    }
)

SCHEMA_NAME = "business_card"


class CardField(BaseModel):
    text: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"


class CardLLMResult(BaseModel):
    name: CardField | None = None
    company: CardField | None = None
    job_title: CardField | None = None
    email: CardField | None = None
    phone: CardField | None = None
    website: CardField | None = None
    street: CardField | None = None
    city: CardField | None = None
    state: CardField | None = None
    zip_code: CardField | None = None
    country: CardField | None = None
    tax_id: CardField | None = None
    other_emails: list[str] = Field(default_factory=list)
    other_phones: list[str] = Field(default_factory=list)
    language: str | None = None
    observations: list[str] = Field(default_factory=list)
