"""Price book endpoints: CRUD, search, duplicate checks, staging, import, export, history."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import exports
from app.db import get_db
from app.models import AuditEvent, Document, PriceBookItem, iso_utc
from app.pipeline.normalize.money import parse_number, parse_percent, to_canonical
from app.pipeline.normalize.units import normalize_unit, unit_display
from app.schemas.extraction import ExtractionResult
from app.services import pricebook as pb

router = APIRouter(prefix="/api/pricebook", tags=["price book"])


class ItemIn(BaseModel):
    name: str
    description: str | None = None
    sku: str | None = None
    rate: str | None = None
    currency: str | None = None
    unit: str | None = None
    category: str = "Services"
    tax_mode: str = "default"
    tax_rate: str | None = None
    vendor: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Item name is required")
        return v[:255]

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("Currency must be an ISO 4217 code")
        return v

    @field_validator("tax_mode")
    @classmethod
    def _tm(cls, v: str) -> str:
        if v not in ("default", "rate", "not_taxable"):
            raise ValueError("tax_mode must be default, rate or not_taxable")
        return v

    def to_data(self) -> pb.ItemData:
        rate = None
        if self.rate not in (None, ""):
            p = parse_number(self.rate)
            if p.value is None:
                raise HTTPException(422, f"'{self.rate}' is not a valid rate")
            rate = to_canonical(p.value, 2)
        tax_rate = None
        if self.tax_mode == "rate":
            t = parse_percent(self.tax_rate or "") if self.tax_rate else None
            if t is None and self.tax_rate:
                pn = parse_number(self.tax_rate)
                t = pn.value
            if t is None:
                raise HTTPException(422, "A tax rate is required when tax mode is 'rate'")
            tax_rate = to_canonical(t, 0)
        unit = unit_label = None
        if self.unit:
            ui = normalize_unit(self.unit)
            if ui:
                unit, unit_label = (ui.code, None) if ui.known else ("custom", ui.label)
        return pb.ItemData(name=self.name, description=(self.description or None), sku=(self.sku or None), rate=rate,
                           currency=self.currency, unit=unit, unit_label=unit_label, category=(self.category or "Services").strip()[:40],
                           tax_mode=self.tax_mode, tax_rate=tax_rate, vendor=self.vendor)


def item_out(i: PriceBookItem, with_history: bool = False) -> dict:
    d = {
        "id": i.id, "name": i.name, "description": i.description, "sku": i.sku, "rate": i.rate, "currency": i.currency,
        "unit": i.unit, "unit_label": i.unit_label, "unit_display": unit_display(i.unit, i.unit_label) if i.unit != "custom" else i.unit_label,
        "category": i.category, "tax_mode": i.tax_mode, "tax_rate": i.tax_rate, "vendor": i.vendor,
        "source_document_id": i.source_document_id, "created_at": iso_utc(i.created_at), "updated_at": iso_utc(i.updated_at),
    }
    if with_history:
        d["history"] = [{"rate": h.rate, "currency": h.currency, "unit": h.unit, "note": h.note,
                         "source_document_id": h.source_document_id, "recorded_at": iso_utc(h.recorded_at)} for h in i.history]
    return d


@router.get("", summary="List / search price-book items")
def list_items(q: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    query = db.query(PriceBookItem)
    if category and category.lower() != "all":
        query = query.filter(PriceBookItem.category == category)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(PriceBookItem.name.ilike(like), PriceBookItem.description.ilike(like), PriceBookItem.sku.ilike(like)))
    items = query.order_by(PriceBookItem.category.desc(), PriceBookItem.name.asc()).all()
    cats = [r[0] for r in db.query(PriceBookItem.category).distinct().all()]
    return {"items": [item_out(i) for i in items], "categories": sorted(set(cats) | {"Services", "Materials"})}


class DupCheck(BaseModel):
    name: str
    sku: str | None = None
    unit: str | None = None
    exclude_id: str | None = None


@router.post("/check-duplicates", summary="Find existing items similar to a name/SKU")
def check_duplicates(body: DupCheck, db: Session = Depends(get_db)):
    ui = normalize_unit(body.unit) if body.unit else None
    return [m.model_dump() for m in pb.find_matches(db, body.name, body.sku, ui.code if ui else None, body.exclude_id)]


@router.post("", status_code=201, summary="Create a price-book item")
def create_item(body: ItemIn, db: Session = Depends(get_db)):
    item = pb.create_item(db, body.to_data(), "created manually")
    db.commit()
    return item_out(item, True)


@router.get("/export", summary="Export the price book (csv | xlsx | json | pdf)")
def export_items(format: str = Query("csv", pattern="^(csv|xlsx|json|pdf)$"), category: str | None = None, db: Session = Depends(get_db)):
    query = db.query(PriceBookItem)
    if category and category.lower() != "all":
        query = query.filter(PriceBookItem.category == category)
    items = query.order_by(PriceBookItem.category.desc(), PriceBookItem.name.asc()).all()
    headers = ["Item", "Description", "SKU", "Rate", "Currency", "Unit", "Category", "Tax", "Vendor"]

    def tax(i: PriceBookItem) -> str:
        return f"{i.tax_rate}%" if i.tax_mode == "rate" else ("Not taxable" if i.tax_mode == "not_taxable" else "Default")

    rows = [[i.name, i.description, i.sku, i.rate, i.currency, unit_display(i.unit, i.unit_label) if i.unit != "custom" else i.unit_label,
             i.category, tax(i), i.vendor] for i in items]
    if format == "json":
        data, media = json.dumps([item_out(i) for i in items], ensure_ascii=False, indent=2).encode(), "application/json"
    elif format == "csv":
        data, media = exports.rows_csv(headers, rows), "text/csv; charset=utf-8"
    elif format == "xlsx":
        data, media = exports.rows_xlsx("Price book", headers, rows, {3}), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        pdf_rows = [[i.name, i.category, f"{i.currency or ''} {i.rate or '—'}".strip(),
                     (unit_display(i.unit, i.unit_label) if i.unit != "custom" else i.unit_label) or "", tax(i), i.sku or ""] for i in items]
        data, media = exports.pricebook_pdf(pdf_rows), "application/pdf"
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="price-book.{format}"'})


@router.get("/{item_id}", summary="Get one item with its price history")
def get_item(item_id: str, db: Session = Depends(get_db)):
    item = db.get(PriceBookItem, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    return item_out(item, True)


@router.patch("/{item_id}", summary="Update an item (rate changes are kept in price history)")
def update_item(item_id: str, body: ItemIn, db: Session = Depends(get_db)):
    item = db.get(PriceBookItem, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    data = body.to_data()
    data.source_document_id = item.source_document_id
    pb.update_item(db, item, data, "edited")
    db.commit()
    db.refresh(item)
    return item_out(item, True)


@router.delete("/{item_id}", status_code=204, summary="Delete an item")
def delete_item(item_id: str, db: Session = Depends(get_db)):
    item = db.get(PriceBookItem, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    db.add(AuditEvent(entity_type="pricebook", entity_id=item.id, action="deleted", old_value={"name": item.name, "rate": item.rate}))
    db.delete(item)
    db.commit()
    return Response(status_code=204)


class BulkIds(BaseModel):
    ids: list[str]
    category: str | None = None


@router.post("/bulk-delete", summary="Delete several items")
def bulk_delete(body: BulkIds, db: Session = Depends(get_db)):
    n = 0
    for i in db.query(PriceBookItem).filter(PriceBookItem.id.in_(body.ids)).all():
        db.add(AuditEvent(entity_type="pricebook", entity_id=i.id, action="deleted", old_value={"name": i.name, "rate": i.rate}))
        db.delete(i)
        n += 1
    db.commit()
    return {"deleted": n}


@router.post("/bulk-category", summary="Set the category of several items")
def bulk_category(body: BulkIds, db: Session = Depends(get_db)):
    if not body.category:
        raise HTTPException(422, "category is required")
    n = 0
    for i in db.query(PriceBookItem).filter(PriceBookItem.id.in_(body.ids)).all():
        i.category = body.category.strip()[:40]
        n += 1
    db.commit()
    return {"updated": n}


class StageBody(BaseModel):
    document_id: str
    item_ids: list[str] | None = None


@router.post("/stage", summary="Prepare extracted line items for the price book (with duplicate matches)")
def stage_from_document(body: StageBody, db: Session = Depends(get_db)):
    doc = db.get(Document, body.document_id)
    if doc is None or not doc.extraction:
        raise HTTPException(404, "Document or extraction not found")
    ex = ExtractionResult.model_validate(doc.extraction)
    vendor = ex.header.get("vendor_name").value if ex.header.get("vendor_name") else None
    items = [li for li in ex.line_items if body.item_ids is None or li.id in body.item_ids]
    datas = []
    for li in items:
        data, warnings = pb.item_from_line(li, ex.currency.code, vendor, doc.id)
        if ex.currency.status == "needs_review" or ex.currency.ambiguous:
            warnings.append(f"Currency {ex.currency.code or '?'} is unconfirmed")
        datas.append((data, warnings, li.id))
    return [s.model_dump() for s in pb.stage(db, datas)]


@router.post("/import", summary="Bulk import from CSV/XLSX (returns staged rows for review)")
def import_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = file.file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Import file too large (10 MB max)")
    try:
        datas = pb.parse_import(file.filename or "import.csv", data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if not datas:
        raise HTTPException(422, "No rows found")
    return [s.model_dump() for s in pb.stage(db, datas)]


class CommitBody(BaseModel):
    decisions: list[pb.CommitDecision]


@router.post("/commit", summary="Apply reviewed staging decisions (create / update / skip)")
def commit(body: CommitBody, db: Session = Depends(get_db)):
    return pb.commit(db, body.decisions)
