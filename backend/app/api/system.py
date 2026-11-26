"""System status, user settings, dashboard and cross-document extracted data."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session, defer

from app import __version__, exports
from app.config import get_settings
from app.db import DATABASE_URL, get_db
from app.models import Document, PriceBookItem
from app.pipeline.llm.providers import build_provider, configured_providers
from app.pipeline.normalize.currency import SYMBOL_FOR
from app.pipeline.normalize.money import ISO_CODES, dec, minor_units
from app.pipeline.normalize.units import unit_options
from app.pipeline.ocr.engines import engines_status
from app.schemas.extraction import DOCUMENT_TYPES
from app.services.app_settings import UserSettings, load_settings, save_settings
from app.services.documents import doc_summary

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", summary="Liveness check")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/system", summary="Engines, providers and limits (never exposes secrets)")
def system(db: Session = Depends(get_db)):
    s = get_settings()
    user = load_settings(db)
    active = build_provider(s)
    return {
        "version": __version__,
        "ocr": {"engine": s.ocr_engine, "model_size": s.ocr_model_size, "engines": engines_status()},
        "llm": {
            "mode": user.llm_mode,
            "active": active.label if active else None,
            "providers": [{"name": p.name, "model": p.model, "configured": p.configured} for p in configured_providers(s)],
        },
        "limits": {"max_upload_mb": s.max_upload_mb, "max_files_per_request": s.max_files_per_request,
                   "max_pages_per_document": s.max_pages_per_document, "worker_concurrency": s.worker_concurrency},
        "database": "postgresql" if DATABASE_URL.startswith("postgres") else "sqlite",
        "review_thresholds": {"numeric": s.review_conf_threshold, "text": s.review_text_conf_threshold},
    }


@router.get("/settings", summary="User-adjustable settings")
def get_user_settings(db: Session = Depends(get_db)):
    return load_settings(db).model_dump()


@router.put("/settings", summary="Update user-adjustable settings")
def put_user_settings(body: UserSettings, db: Session = Depends(get_db)):
    return save_settings(db, body).model_dump()


@router.get("/options", summary="Units, currencies and document types for pickers")
def options():
    return {
        "units": unit_options(),
        "currencies": [{"code": c, "symbol": SYMBOL_FOR.get(c, c), "minor_units": minor_units(c)} for c in ISO_CODES],
        "document_types": list(DOCUMENT_TYPES),
    }


@router.get("/dashboard", summary="Dashboard statistics")
def dashboard(db: Session = Depends(get_db)):
    counts = dict(db.query(Document.status, func.count(Document.id)).group_by(Document.status).all())
    total = sum(counts.values())
    items = db.query(func.coalesce(func.sum(Document.line_item_count), 0)).scalar() or 0
    recent = (db.query(Document).options(defer(Document.extraction), defer(Document.extraction_original), defer(Document.pipeline_log))
              .order_by(Document.created_at.desc()).limit(8).all())
    review = (db.query(Document).options(defer(Document.extraction), defer(Document.extraction_original), defer(Document.pipeline_log))
              .filter(Document.status == "review").order_by(Document.created_at.desc()).limit(6).all())
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for cur, gt in db.query(Document.currency, Document.grand_total).filter(Document.status.in_(["approved", "ready"])).all():
        d = dec(gt)
        if cur and d is not None:
            totals[cur] += d
    avg_ms = db.query(func.avg(Document.processing_ms)).filter(Document.processing_ms.isnot(None)).scalar()
    return {
        "total_documents": total,
        "processed": counts.get("ready", 0) + counts.get("review", 0) + counts.get("approved", 0),
        "successful": counts.get("ready", 0) + counts.get("approved", 0),
        "needs_review": counts.get("review", 0),
        "failed": counts.get("failed", 0),
        "in_progress": counts.get("queued", 0) + counts.get("processing", 0),
        "approved": counts.get("approved", 0),
        "line_items": int(items),
        "price_book_items": db.query(func.count(PriceBookItem.id)).scalar() or 0,
        "avg_processing_ms": int(avg_ms) if avg_ms else None,
        "totals_by_currency": [{"currency": c, "total": f"{v:.2f}"} for c, v in sorted(totals.items())],
        "recent": [doc_summary(d) for d in recent],
        "review_queue": [doc_summary(d) for d in review],
    }


def _line_rows(db: Session, q: str | None, category: str | None, currency: str | None, status: str | None) -> list[dict]:
    rows = []
    docs = db.query(Document).filter(Document.extraction.isnot(None), Document.status.in_(["ready", "review", "approved"])) \
        .order_by(Document.created_at.desc()).limit(500).all()
    for d in docs:
        ex = d.extraction or {}
        cur = (ex.get("currency") or {}).get("code")
        if currency and cur != currency:
            continue
        for li in ex.get("line_items", []):
            def v(k: str):
                return (li.get(k) or {}).get("value")

            row = {
                "document_id": d.id, "document_number": d.document_number, "vendor": d.vendor_name, "document_date": d.document_date,
                "document_status": d.status, "currency": cur, "item_id": li.get("id"), "name": v("name"), "description": v("description"),
                "sku": v("sku"), "quantity": v("quantity"), "unit": v("unit"), "rate": v("rate"), "tax_rate": v("tax_rate"),
                "amount": v("amount"), "category": v("category"), "status": li.get("status"),
            }
            if category and (row["category"] or "") != category:
                continue
            if status and row["status"] != status:
                continue
            if q and q.lower() not in f"{row['name'] or ''} {row['description'] or ''} {row['vendor'] or ''}".lower():
                continue
            rows.append(row)
    return rows


@router.get("/line-items", summary="All extracted line items across documents")
def line_items(q: str | None = None, category: str | None = None, currency: str | None = None, status: str | None = None,
               db: Session = Depends(get_db)):
    rows = _line_rows(db, q, category, currency, status)
    return {"total": len(rows), "items": rows[:2000]}


@router.get("/line-items/export", summary="Export extracted line items (csv | xlsx | json)")
def line_items_export(format: str = Query("csv", pattern="^(csv|xlsx|json)$"), q: str | None = None, category: str | None = None,
                      currency: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    import json

    rows = _line_rows(db, q, category, currency, status)
    headers = ["Document", "Vendor", "Date", "Item", "Description", "SKU", "Qty", "Unit", "Rate", "Tax %", "Amount", "Currency", "Category", "Status"]
    table = [[r["document_number"], r["vendor"], exports.dmy(r["document_date"]), r["name"], r["description"], r["sku"], r["quantity"], r["unit"],
              r["rate"], r["tax_rate"], r["amount"], r["currency"], r["category"], r["status"]] for r in rows]
    if format == "json":
        return Response(json.dumps(rows, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="line-items.json"'})
    if format == "csv":
        return Response(exports.rows_csv(headers, table), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="line-items.csv"'})
    return Response(exports.rows_xlsx("Line items", headers, table, {8, 10}),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="line-items.xlsx"'})
