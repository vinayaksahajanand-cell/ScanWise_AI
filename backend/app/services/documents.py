"""Document lifecycle: secure intake, serialisation, human corrections, approval."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pymupdf
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditEvent, Document, Page, iso_utc
from app.pipeline import finance
from app.pipeline.assess import assess
from app.pipeline.imaging import load_image_bytes
from app.pipeline.normalize.category import canonical_category
from app.pipeline.normalize.currency import SYMBOL_FOR
from app.pipeline.normalize.dates import parse_date
from app.pipeline.normalize.money import ISO_CODES, minor_units, parse_number, parse_percent, to_canonical
from app.pipeline.normalize.units import normalize_unit
from app.pipeline.rules import new_id
from app.schemas.extraction import (
    DOCUMENT_TYPES,
    HEADER_KEYS,
    HEADER_KINDS,
    LINE_FIELD_KEYS,
    SUMMARY_KEYS,
    ExtractedField,
    ExtractionResult,
    LineItem,
    TaxLine,
)

from .storage import get_storage, safe_name

log = logging.getLogger(__name__)

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/tiff", "image/bmp"}


class IntakeError(ValueError):
    pass


def sniff_type(data: bytes) -> str | None:
    head = data[:16]
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if b"%PDF-" in data[:1024]:
        return "application/pdf"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heim", b"heis"):
        return "image/heic"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head[:2] == b"BM":
        return "image/bmp"
    return None


@dataclass
class IncomingFile:
    filename: str
    data: bytes
    mime: str
    pages: int
    sha256: str


def inspect_file(filename: str, data: bytes) -> IncomingFile:
    s = get_settings()
    if not data:
        raise IntakeError(f"{filename}: empty file")
    if len(data) > s.max_upload_bytes:
        raise IntakeError(f"{filename}: larger than {s.max_upload_mb} MB")
    mime = sniff_type(data)
    if mime is None:
        raise IntakeError(f"{filename}: unsupported file type (use JPG, PNG, WEBP, HEIC, TIFF or PDF)")
    pages = 1
    if mime == "application/pdf":
        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass:
                    raise IntakeError(f"{filename}: password-protected PDFs are not supported")
                pages = doc.page_count
        except IntakeError:
            raise
        except Exception as e:  # noqa: BLE001
            raise IntakeError(f"{filename}: not a readable PDF ({e})") from e
        if pages == 0:
            raise IntakeError(f"{filename}: PDF has no pages")
        if pages > s.max_pages_per_document:
            raise IntakeError(f"{filename}: {pages} pages exceeds the limit of {s.max_pages_per_document}")
    else:
        try:
            img = load_image_bytes(data)
        except Exception as e:  # noqa: BLE001
            raise IntakeError(f"{filename}: image could not be decoded ({e})") from e
        if min(img.shape[:2]) < 200:
            raise IntakeError(f"{filename}: image is too small ({img.shape[1]}×{img.shape[0]}) to read reliably")
    return IncomingFile(filename, data, mime, pages, hashlib.sha256(data).hexdigest())


def _ext(mime: str) -> str:
    return {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic", "image/tiff": "tif",
            "image/bmp": "bmp", "application/pdf": "pdf"}.get(mime, "bin")


def create_documents(db: Session, files: list[IncomingFile], mode: str, source: str, pages_meta: list[dict] | None,
                     llm_mode: str | None) -> list[Document]:
    storage = get_storage()
    s = get_settings()
    batch_id = uuid.uuid4().hex if len(files) > 1 else None
    groups: list[list[int]] = [list(range(len(files)))] if mode == "single" else [[i] for i in range(len(files))]
    docs: list[Document] = []
    for group in groups:
        total_pages = sum(files[i].pages for i in group)
        if total_pages > s.max_pages_per_document:
            raise IntakeError(f"Document would have {total_pages} pages (limit {s.max_pages_per_document})")
        first = files[group[0]]
        doc = Document(
            filename=first.filename if len(group) == 1 else f"{safe_name(first.filename)} (+{len(group) - 1} more)",
            source=source, mime_type=first.mime, batch_id=batch_id, status="queued",
            file_hash=first.sha256 if len(group) == 1 else None, page_count=total_pages,
            options={"llm_mode": llm_mode} if llm_mode else {},
        )
        db.add(doc)
        db.flush()
        page_no = 0
        for i in group:
            f = files[i]
            rel = storage.write(f"{doc.id}/source/{i:02d}_{safe_name(f.filename)}.{_ext(f.mime)}"
                                if not safe_name(f.filename).lower().endswith("." + _ext(f.mime))
                                else f"{doc.id}/source/{i:02d}_{safe_name(f.filename)}", f.data)
            meta = (pages_meta[i] if pages_meta and i < len(pages_meta) and isinstance(pages_meta[i], dict) else {}) or {}
            if f.mime == "application/pdf":
                for k in range(f.pages):
                    page_no += 1
                    db.add(Page(document_id=doc.id, page_no=page_no, source_file=rel, source_kind="pdf", pdf_index=k,
                                rotation=int(meta.get("rotation") or 0) % 360))
            else:
                page_no += 1
                corners = meta.get("corners")
                if not (isinstance(corners, list) and len(corners) == 4 and all(isinstance(c, list | tuple) and len(c) == 2 for c in corners)):
                    corners = None
                else:
                    corners = [[min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y)))] for x, y in corners]
                db.add(Page(document_id=doc.id, page_no=page_no, source_file=rel, source_kind="image", corners=corners,
                            rotation=int(meta.get("rotation") or 0) % 360, auto_crop=0 if (corners or meta.get("auto_crop") is False) else 1))
        db.add(AuditEvent(entity_type="document", entity_id=doc.id, action="created",
                          detail=f"{source} upload, {len(group)} file(s), {total_pages} page(s)"))
        docs.append(doc)
    db.commit()
    return docs


# ---------------------------------------------------------------- serialisation
def page_urls(doc: Document, p: Page) -> dict:
    v = int(doc.processed_at.timestamp()) if doc.processed_at else 0
    base = f"/api/documents/{doc.id}/pages/{p.page_no}"
    return {
        "image_url": f"{base}/image?variant=processed&v={v}" if p.image_path else None,
        "original_url": f"{base}/image?variant=original&v={v}",
        "thumb_url": f"{base}/image?variant=thumb&v={v}" if p.thumb_path else None,
        "tokens_url": f"{base}/tokens" if p.image_path else None,
    }


def doc_summary(doc: Document) -> dict:
    first = doc.pages[0] if doc.pages else None
    return {
        "id": doc.id, "filename": doc.filename, "source": doc.source, "status": doc.status,
        "progress": doc.progress, "progress_message": doc.progress_message, "error": doc.error,
        "page_count": doc.page_count, "document_type": doc.document_type, "vendor_name": doc.vendor_name,
        "document_number": doc.document_number, "document_date": doc.document_date, "currency": doc.currency,
        "grand_total": doc.grand_total, "line_item_count": doc.line_item_count, "review_count": doc.review_count,
        "validation_status": doc.validation_status, "extractors": doc.extractors or [],
        "created_at": iso_utc(doc.created_at),
        "processed_at": iso_utc(doc.processed_at),
        "approved_at": iso_utc(doc.approved_at),
        "processing_ms": doc.processing_ms, "batch_id": doc.batch_id,
        "thumb_url": page_urls(doc, first)["thumb_url"] if first else None,
    }


def doc_detail(doc: Document) -> dict:
    out = doc_summary(doc)
    out["pages"] = [
        {"page_no": p.page_no, "width": p.width, "height": p.height, "text_source": p.text_source, "source_kind": p.source_kind,
         "quality": p.quality, "rotation": p.rotation,
         "meta": {k: v for k, v in (p.meta or {}).items() if k in ("crop", "rotation", "deskew", "pdf_mode", "original_size")},
         **page_urls(doc, p)}
        for p in doc.pages
    ]
    out["extraction"] = doc.extraction
    out["pipeline_log"] = doc.pipeline_log or []
    out["options"] = doc.options or {}
    return out


def update_summary_columns(doc: Document, ex: ExtractionResult) -> None:
    def hv(k: str) -> str | None:
        f = ex.header.get(k)
        return f.value if f is not None else None

    doc.document_type = ex.document_type.value
    doc.vendor_name = (hv("vendor_name") or None) and hv("vendor_name")[:255]  # type: ignore[index]
    doc.document_number = (hv("document_number") or None) and hv("document_number")[:64]  # type: ignore[index]
    d = hv("document_date")
    doc.document_date = d if d and len(d) == 10 else None
    doc.currency = ex.currency.code
    gt = ex.summary.get("grand_total")
    doc.grand_total = gt.value if gt is not None else None
    doc.line_item_count = len(ex.line_items)
    r = ex.review
    doc.review_count = r.needs_review + r.unreadable + r.mismatch + (1 if ex.currency.status == "needs_review" else 0)
    doc.validation_status = ex.validation.status if ex.validation else None
    doc.extractors = ex.extractors


# ---------------------------------------------------------------- corrections
class FieldUpdate(BaseModel):
    path: str
    value: str | None = None


class ExtractionPatch(BaseModel):
    updates: list[FieldUpdate] = []
    verify: list[str] = []


class PatchError(ValueError):
    pass


def load_extraction(doc: Document) -> ExtractionResult:
    if not doc.extraction:
        raise PatchError("Document has no extraction yet")
    return ExtractionResult.model_validate(doc.extraction).ensure_keys()


def resolve(ex: ExtractionResult, path: str) -> tuple[ExtractedField, str]:
    parts = path.split(".")
    if path == "document_type":
        return ex.document_type, "enum"
    if parts[0] == "header" and len(parts) == 2 and parts[1] in HEADER_KEYS:
        f = ex.header.setdefault(parts[1], ExtractedField(kind=HEADER_KINDS.get(parts[1], "text")))
        return f, f.kind
    if parts[0] == "summary" and len(parts) == 2 and parts[1] in SUMMARY_KEYS:
        f = ex.summary.setdefault(parts[1], ExtractedField(kind="money"))
        return f, "money"
    if parts[0] == "line_items" and len(parts) == 3 and parts[2] in LINE_FIELD_KEYS:
        for li in ex.line_items:
            if li.id == parts[1]:
                f = getattr(li, parts[2])
                return f, f.kind
    if parts[0] == "taxes" and len(parts) == 3 and parts[2] in ("label", "rate", "base", "amount"):
        for t in ex.taxes:
            if t.id == parts[1]:
                f = getattr(t, parts[2])
                return f, f.kind
    raise PatchError(f"Unknown field path '{path}'")


def normalize_user_value(kind: str, text: str | None, path: str, mu: int) -> tuple[str | None, str]:
    if text is None or not str(text).strip():
        return None, kind
    t = str(text).strip()
    if kind in ("money", "percent") and path.endswith(".discount"):
        if "%" in t:
            p = parse_percent(t)
            if p is None:
                raise PatchError(f"'{t}' is not a valid percentage")
            return to_canonical(p, 0), "percent"
        kind = "money"
    if kind == "money":
        p = parse_number(t)
        if p.value is None:
            raise PatchError(f"'{t}' is not a valid amount")
        return to_canonical(p.value, mu), kind
    if kind == "number":
        p = parse_number(t)
        if p.value is None:
            raise PatchError(f"'{t}' is not a valid number")
        return to_canonical(p.value, 0), kind
    if kind == "percent":
        p = parse_percent(t)
        if p is None:
            pn = parse_number(t)
            p = pn.value
        if p is None:
            raise PatchError(f"'{t}' is not a valid percentage")
        return to_canonical(p, 0), kind
    if kind == "date":
        pd = parse_date(t, "YMD" if len(t) == 10 and t[4] == "-" else None)
        if pd.iso is None:
            raise PatchError(f"'{t}' is not a valid date")
        return pd.iso, kind
    if kind == "unit":
        ui = normalize_unit(t)
        return (ui.code if ui and ui.known else (ui.label if ui else None)), kind
    if kind == "category":
        return canonical_category(t), kind
    if kind == "enum":
        if t not in DOCUMENT_TYPES:
            raise PatchError(f"Unknown document type '{t}'")
        return t, kind
    return t, kind


def apply_patch(db: Session, doc: Document, patch: ExtractionPatch) -> ExtractionResult:
    ex = load_extraction(doc)
    mu = minor_units(ex.currency.code)
    events: list[AuditEvent] = []
    for upd in patch.updates:
        if upd.path == "currency":
            code = (upd.value or "").strip().upper()
            if code not in ISO_CODES and len(code) != 3:
                raise PatchError(f"'{upd.value}' is not an ISO 4217 currency code")
            old = ex.currency.code
            ex.currency.code, ex.currency.symbol = code, SYMBOL_FOR.get(code, code)
            ex.currency.minor_units = minor_units(code)
            ex.currency.overridden, ex.currency.ambiguous = True, False
            events.append(AuditEvent(entity_type="document", entity_id=doc.id, action="currency_set", field_path="currency", old_value=old, new_value=code))
            mu = minor_units(code)
            continue
        f, kind = resolve(ex, upd.path)
        new_value, new_kind = normalize_user_value(kind, upd.value, upd.path, mu)
        old_value = f.value
        f.kind = new_kind  # type: ignore[assignment]
        f.value = new_value
        f.verified_by_user = True
        f.corrected = new_value != f.extracted_value
        if new_value is not None and not f.raw:
            f.raw = upd.value
        if "user" not in f.sources and f.corrected:
            f.sources = f.sources + ["user"]
        action = "field_updated" if new_value != old_value else "field_verified"
        events.append(AuditEvent(entity_type="document", entity_id=doc.id, action=action, field_path=upd.path,
                                 old_value=old_value, new_value=new_value))
    for path in patch.verify:
        if path == "currency":
            ex.currency.overridden = True
            ex.currency.ambiguous = False
            events.append(AuditEvent(entity_type="document", entity_id=doc.id, action="field_verified", field_path="currency", new_value=ex.currency.code))
            continue
        f, _ = resolve(ex, path)
        f.verified_by_user = True
        events.append(AuditEvent(entity_type="document", entity_id=doc.id, action="field_verified", field_path=path, new_value=f.value))
    return _finish(db, doc, ex, events)


def _finish(db: Session, doc: Document, ex: ExtractionResult, events: list[AuditEvent]) -> ExtractionResult:
    finance.validate(ex)
    status = assess(ex, get_settings())
    if doc.status == "approved" and events:
        ex.approved = False
        doc.approved_at = None
        events.append(AuditEvent(entity_type="document", entity_id=doc.id, action="unapproved", detail="edited after approval"))
    if not ex.approved:
        doc.status = status
    doc.extraction = json.loads(ex.model_dump_json())
    update_summary_columns(doc, ex)
    for e in events:
        db.add(e)
    db.commit()
    return ex


def add_line_item(db: Session, doc: Document, after_id: str | None) -> tuple[ExtractionResult, str]:
    ex = load_extraction(doc)
    li = LineItem(id=new_id("li"), index=0, user_added=True, sources=["user"], page=1)
    li.category = ExtractedField(kind="category")
    pos = len(ex.line_items)
    if after_id:
        for i, x in enumerate(ex.line_items):
            if x.id == after_id:
                pos = i + 1
                li.page = x.page
    ex.line_items.insert(pos, li)
    for i, x in enumerate(ex.line_items):
        x.index = i
    _finish(db, doc, ex, [AuditEvent(entity_type="document", entity_id=doc.id, action="line_added", field_path=f"line_items.{li.id}")])
    return ex, li.id


def delete_line_item(db: Session, doc: Document, item_id: str) -> ExtractionResult:
    ex = load_extraction(doc)
    item = next((x for x in ex.line_items if x.id == item_id), None)
    if item is None:
        raise PatchError("Line item not found")
    ex.line_items = [x for x in ex.line_items if x.id != item_id]
    for i, x in enumerate(ex.line_items):
        x.index = i
    for t in ex.taxes:
        if t.applies_to:
            t.applies_to = [a for a in t.applies_to if a != item_id] or None
    snapshot = {"name": item.name.value, "quantity": item.quantity.value, "rate": item.rate.value, "amount": item.amount.value}
    return _finish(db, doc, ex, [AuditEvent(entity_type="document", entity_id=doc.id, action="line_deleted",
                                            field_path=f"line_items.{item_id}", old_value=snapshot)])


def add_tax_line(db: Session, doc: Document) -> tuple[ExtractionResult, str]:
    ex = load_extraction(doc)
    t = TaxLine(id=new_id("tx"))
    ex.taxes.append(t)
    _finish(db, doc, ex, [AuditEvent(entity_type="document", entity_id=doc.id, action="tax_added", field_path=f"taxes.{t.id}")])
    return ex, t.id


def delete_tax_line(db: Session, doc: Document, tax_id: str) -> ExtractionResult:
    ex = load_extraction(doc)
    t = next((x for x in ex.taxes if x.id == tax_id), None)
    if t is None:
        raise PatchError("Tax line not found")
    ex.taxes = [x for x in ex.taxes if x.id != tax_id]
    return _finish(db, doc, ex, [AuditEvent(entity_type="document", entity_id=doc.id, action="tax_deleted",
                                            field_path=f"taxes.{tax_id}", old_value={"label": t.label.value, "amount": t.amount.value})])


def recalculate(db: Session, doc: Document) -> ExtractionResult:
    ex = load_extraction(doc)
    return _finish(db, doc, ex, [])


class ApprovalBlocked(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def approve(db: Session, doc: Document, acknowledge: bool) -> ExtractionResult:
    ex = load_extraction(doc)
    finance.validate(ex)
    assess(ex, get_settings())
    reasons = []
    if ex.validation and ex.validation.status == "fail":
        reasons.append("Totals do not reconcile")
    if ex.review.needs_review or ex.review.unreadable or ex.review.mismatch:
        reasons.append(f"{ex.review.needs_review + ex.review.unreadable + ex.review.mismatch} field(s) still need review")
    if ex.currency.status == "needs_review":
        reasons.append(f"Currency {ex.currency.code or '?'} is unconfirmed")
    if reasons and not acknowledge:
        raise ApprovalBlocked(reasons)
    from app.pipeline.assess import iter_fields

    for _, f in iter_fields(ex):
        if f.present:
            f.verified_by_user = True
    if ex.currency.code:
        ex.currency.overridden = True
        ex.currency.ambiguous = False
    ex.approved = True
    assess(ex, get_settings())
    doc.status = "approved"
    doc.approved_at = datetime.now(timezone.utc)
    doc.extraction = json.loads(ex.model_dump_json())
    update_summary_columns(doc, ex)
    db.add(AuditEvent(entity_type="document", entity_id=doc.id, action="approved",
                      detail=("with acknowledged warnings: " + "; ".join(reasons)) if reasons else "all checks passed"))
    db.commit()
    return ex


def reset_for_reprocess(db: Session, doc: Document, llm_mode: str | None) -> None:
    opts = dict(doc.options or {})
    if llm_mode:
        opts["llm_mode"] = llm_mode
    doc.options = opts
    doc.status = "queued"
    doc.progress = 0.0
    doc.progress_message = "Queued"
    doc.error = None
    doc.extraction = None
    doc.extraction_original = None
    doc.approved_at = None
    db.add(AuditEvent(entity_type="document", entity_id=doc.id, action="reprocessed",
                      detail="Extraction discarded and re-run" + (f" (vision mode {llm_mode})" if llm_mode else "")))
    db.commit()


def clone_extraction(data: dict | None) -> dict | None:
    return copy.deepcopy(data) if data else None
