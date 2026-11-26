"""Price book: duplicate detection, staging from documents/imports, commits with history.

Duplicates are only *suggested*. Items are never merged automatically; when a
rate differs the user decides to update the existing item or create a new one.
"""

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal

from pydantic import BaseModel
from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models import AuditEvent, PriceBookItem, PriceHistory
from app.pipeline.normalize.category import canonical_category, classify_item
from app.pipeline.normalize.money import dec, parse_number, parse_percent, to_canonical
from app.pipeline.normalize.tax import tax_mode_from_rate
from app.pipeline.normalize.units import normalize_unit, unit_display
from app.schemas.extraction import LineItem, PriceBookMatch

ABBREVIATIONS = {
    "inst": "installation", "install": "installation", "insp": "inspection", "maint": "maintenance",
    "labour": "labor", "hr": "hour", "hrs": "hour", "a c": "ac", "a/c": "ac", "svc": "service", "repl": "replacement",
}


def normalize_name(name: str | None) -> str:
    s = (name or "").lower().replace("&", " and ").replace("a/c", "ac")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = []
    for w in s.split():
        w = ABBREVIATIONS.get(w, w)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


class ItemData(BaseModel):
    name: str
    description: str | None = None
    sku: str | None = None
    rate: str | None = None
    currency: str | None = None
    unit: str | None = None
    unit_label: str | None = None
    category: str = "Services"
    tax_mode: str = "default"
    tax_rate: str | None = None
    vendor: str | None = None
    source_document_id: str | None = None


class StagedItem(BaseModel):
    key: str
    data: ItemData
    source_item_id: str | None = None
    warnings: list[str] = []
    matches: list[PriceBookMatch] = []
    suggested_action: str = "create"  # create | update | skip
    suggested_target: str | None = None


class CommitDecision(BaseModel):
    data: ItemData
    action: str  # create | update | skip
    target_id: str | None = None


def find_matches(db: Session, name: str, sku: str | None = None, unit: str | None = None,
                 exclude_id: str | None = None, limit: int = 3) -> list[PriceBookMatch]:
    norm = normalize_name(name)
    if not norm:
        return []
    out: list[PriceBookMatch] = []
    for item in db.query(PriceBookItem).all():
        if item.id == exclude_id:
            continue
        reason = None
        score = 0.0
        if sku and item.sku and sku.strip().lower() == item.sku.strip().lower():
            score, reason = 1.0, "Same SKU"
        elif item.normalized_name == norm:
            score, reason = 0.98, "Same name"
        else:
            r = fuzz.token_set_ratio(norm, item.normalized_name) / 100
            r2 = fuzz.ratio(norm, item.normalized_name) / 100
            sc = max(r * 0.9, r2)
            if sc >= 0.86:
                score, reason = sc, "Similar name"
        if reason is None:
            continue
        if unit and item.unit and unit != item.unit:
            reason += f" · different unit ({unit_display(item.unit, item.unit_label)})"
            score -= 0.1
        out.append(PriceBookMatch(item_id=item.id, name=item.name, rate=item.rate, currency=item.currency,
                                  unit=item.unit, score=round(score, 3), reason=reason))
    out.sort(key=lambda m: -m.score)
    return out[:limit]


def make_matcher(session_factory):
    def matcher(items: list[LineItem], currency: str | None) -> None:
        with session_factory() as db:
            for li in items:
                if li.name.value:
                    li.pricebook_matches = find_matches(db, li.name.value, li.sku.value, li.unit.value)
    return matcher


def item_from_line(li: LineItem, currency: str | None, vendor: str | None, document_id: str) -> tuple[ItemData, list[str]]:
    warnings: list[str] = []
    rate = li.rate.value
    if rate is None and li.amount.value and li.quantity.value:
        q, a = dec(li.quantity.value), dec(li.amount.value)
        if q and a is not None and q != 0:
            warnings.append(f"No unit rate printed — amount ÷ quantity = {to_canonical(a / q, 2)} (please confirm)")
    elif rate is None and li.amount.value and not li.quantity.value:
        warnings.append("No unit rate printed — only a line amount was found")
    if li.status not in ("verified", "high_confidence"):
        warnings.append("Source line item still needs review")
    mode, tax_rate = tax_mode_from_rate(li.tax_rate.raw or li.tax_rate.value)
    if li.tax_rate.value is not None and mode == "default":
        mode, tax_rate = "rate", dec(li.tax_rate.value)
    unit = li.unit.value
    ui = normalize_unit(li.unit.raw or unit) if (li.unit.raw or unit) else None
    category = canonical_category(li.category.value) or classify_item(li.name.value, li.description.value, unit).category
    return ItemData(
        name=(li.name.value or "").strip() or "Unnamed item",
        description=li.description.value,
        sku=li.sku.value,
        rate=rate,
        currency=currency,
        unit=(ui.code if ui and ui.known else ("custom" if ui else None)),
        unit_label=(ui.label if ui and not ui.known else None),
        category=category,
        tax_mode=mode,
        tax_rate=to_canonical(tax_rate, 0) if tax_rate is not None else None,
        vendor=vendor,
        source_document_id=document_id,
    ), warnings


def stage(db: Session, datas: list[tuple[ItemData, list[str], str | None]]) -> list[StagedItem]:
    staged = []
    for i, (data, warnings, src_id) in enumerate(datas):
        matches = find_matches(db, data.name, data.sku, data.unit)
        action, target = "create", None
        if matches and matches[0].score >= 0.97 and (not data.unit or not matches[0].unit or matches[0].unit == data.unit):
            m = matches[0]
            same_rate = m.rate is not None and data.rate is not None and dec(m.rate) == dec(data.rate) and (m.currency or data.currency) == (data.currency or m.currency)
            action, target = ("skip", m.item_id) if same_rate else ("update", m.item_id)
            if not same_rate and m.rate and data.rate:
                warnings = warnings + [f"Existing rate {m.currency or ''} {m.rate} → new rate {data.currency or ''} {data.rate}"]
        staged.append(StagedItem(key=f"s{i}", data=data, source_item_id=src_id, warnings=warnings, matches=matches,
                                 suggested_action=action, suggested_target=target))
    return staged


def _record_history(db: Session, item: PriceBookItem, note: str) -> None:
    db.add(PriceHistory(item_id=item.id, rate=item.rate, currency=item.currency, unit=item.unit,
                        source_document_id=item.source_document_id, note=note))


def create_item(db: Session, data: ItemData, note: str = "created") -> PriceBookItem:
    item = PriceBookItem(**data.model_dump(), normalized_name=normalize_name(data.name))
    db.add(item)
    db.flush()
    _record_history(db, item, note)
    db.add(AuditEvent(entity_type="pricebook", entity_id=item.id, action="created", new_value=data.model_dump()))
    return item


def update_item(db: Session, item: PriceBookItem, data: ItemData, note: str = "updated") -> PriceBookItem:
    old = {k: getattr(item, k) for k in ItemData.model_fields}
    rate_changed = (dec(old["rate"]) != dec(data.rate)) or old["currency"] != data.currency or old["unit"] != data.unit
    for k, v in data.model_dump().items():
        if k == "source_document_id" and v is None:
            continue
        setattr(item, k, v)
    item.normalized_name = normalize_name(item.name)
    if rate_changed:
        _record_history(db, item, note)
    db.add(AuditEvent(entity_type="pricebook", entity_id=item.id, action="updated", old_value=old, new_value=data.model_dump()))
    return item


def commit(db: Session, decisions: list[CommitDecision]) -> dict:
    created = updated = skipped = 0
    for d in decisions:
        if d.action == "skip":
            skipped += 1
            continue
        if d.action == "update" and d.target_id:
            item = db.get(PriceBookItem, d.target_id)
            if item is not None:
                data = d.data
                # keep fields the new source did not provide
                merged = ItemData(**{**{k: getattr(item, k) for k in ItemData.model_fields},
                                     **{k: v for k, v in data.model_dump().items() if v not in (None, "")}})
                update_item(db, item, merged, note="updated from document" if data.source_document_id else "updated")
                updated += 1
                continue
        create_item(db, d.data, note="added from document" if d.data.source_document_id else "created")
        created += 1
    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------- import
HEADER_MAP = {
    "name": ("name", "item", "item name", "description", "service", "product"),
    "description": ("details", "notes", "item description", "long description"),
    "sku": ("sku", "code", "item code", "part number", "part no"),
    "rate": ("rate", "price", "unit price", "cost", "amount"),
    "currency": ("currency", "ccy"),
    "unit": ("unit", "uom", "per"),
    "category": ("category", "type", "group"),
    "tax": ("tax", "tax rate", "gst", "vat", "tax %"),
}


def parse_import(filename: str, data: bytes) -> list[tuple[ItemData, list[str], str | None]]:
    rows: list[list[str]] = []
    if filename.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        for r in ws.iter_rows(values_only=True):
            rows.append(["" if v is None else str(v) for v in r])
    else:
        text = data.decode("utf-8-sig", errors="replace")
        rows = [r for r in csv.reader(io.StringIO(text))]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    col: dict[str, int] = {}
    for key, aliases in HEADER_MAP.items():
        for i, h in enumerate(header):
            if h in aliases and key not in col and i not in col.values():
                col[key] = i
    if "name" not in col:
        raise ValueError("Import file needs a 'name' (or 'item') column")
    out = []
    for r in rows[1:]:
        def cell(k: str) -> str:
            i = col.get(k)
            return r[i].strip() if i is not None and i < len(r) else ""

        name = cell("name")
        if not name:
            continue
        warnings: list[str] = []
        rate_raw = cell("rate")
        p = parse_number(rate_raw) if rate_raw else None
        rate = to_canonical(p.value, 2) if p and p.value is not None else None
        if rate_raw and rate is None:
            warnings.append(f"Unreadable rate '{rate_raw}'")
        currency = (cell("currency") or (p.currency_hint if p and p.currency_hint and len(p.currency_hint) == 3 else "")).upper() or None
        ui = normalize_unit(cell("unit")) if cell("unit") else None
        tax_raw = cell("tax")
        mode, tr = tax_mode_from_rate(tax_raw if "%" in tax_raw or not tax_raw else tax_raw + "%")
        if tax_raw and mode == "default" and parse_percent(tax_raw) is not None:
            mode, tr = "rate", parse_percent(tax_raw)
        cat = canonical_category(cell("category")) or classify_item(name, None, ui.code if ui else None).category
        out.append((ItemData(name=name, description=cell("description") or None, sku=cell("sku") or None, rate=rate,
                             currency=currency, unit=(ui.code if ui and ui.known else ("custom" if ui else None)),
                             unit_label=(ui.label if ui and not ui.known else None), category=cat, tax_mode=mode,
                             tax_rate=to_canonical(tr, 0) if isinstance(tr, Decimal) else None), warnings, None))
    return out
