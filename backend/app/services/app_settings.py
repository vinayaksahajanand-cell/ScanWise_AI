"""User-adjustable settings persisted in the database (never secrets)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models import AppSetting


class UserSettings(BaseModel):
    llm_mode: str = Field("always", pattern="^(always|fallback|off)$")
    date_order: str = Field("DMY", pattern="^(auto|DMY|MDY|YMD)$")  # how ambiguous 01/02/2023 is read: day first
    display_currency: str | None = None  # display preference only — never converts
    auto_crop: bool = True
    tax_presets: list[str] = Field(default_factory=lambda: ["10", "5"])
    custom_units: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=lambda: ["Services", "Materials"])
    default_currency_for_new_items: str | None = None


def load_settings(db: Session) -> UserSettings:
    rows = {r.key: r.value for r in db.query(AppSetting).all()}
    data: dict[str, Any] = {k: v for k, v in rows.items() if k in UserSettings.model_fields}
    try:
        return UserSettings(**data)
    except Exception:  # noqa: BLE001
        return UserSettings()


def save_settings(db: Session, new: UserSettings) -> UserSettings:
    for key, value in new.model_dump().items():
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    db.commit()
    return new
