"""Document endpoints: upload, status, review/corrections, approval, exports."""

from __future__ import annotations

import json
import mimetypes

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session, defer, selectinload

from app import exports
from app.config import get_settings
from app.db import get_db
from app.models import AuditEvent, Document, Page, iso_utc
from app.pipeline.imaging import encode_jpeg, load_image_bytes
from app.services import documents as svc
from app.services.jobs import get_runner
from app.services.storage import get_storage

router = APIRouter(prefix="/api/documents", tags=["documents"])
CACHE = {"Cache-Control": "private, max-age=86400"}


def _get_doc(db: Session, doc_id: str) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


def _editable(doc: Document) -> None:
    if doc.status in ("queued", "processing"):
        raise HTTPException(409, "Document is still being processed")
    if not doc.extraction:
        raise HTTPException(409, "Document has no extraction to edit")


def _ex_response(doc: Document, ex) -> dict:
    return {"document": svc.doc_summary(doc), "extraction": json.loads(ex.model_dump_json())}


@router.post("", status_code=201, summary="Upload images/PDFs (single multi-page document or batch)")
def upload_documents(
    files: list[UploadFile] = File(...),
    mode: str = Form("auto", description="single = all files are pages of one document; batch = one document per file"),
    source: str = Form("upload", pattern="^(upload|camera)$"),
    pages_meta: str | None = Form(None, description='JSON list aligned with files: [{"corners": [[x,y]×4] (0..1), "rotation": 0}]'),
    llm_mode: str | None = Form(None, pattern="^(always|fallback|off)$"),
    db: Session = Depends(get_db),
):
    s = get_settings()
    if not files:
        raise HTTPException(400, "No files uploaded")
    if len(files) > s.max_files_per_request:
        raise HTTPException(413, f"At most {s.max_files_per_request} files per upload")
    incoming, errors = [], []
    for f in files:
        data = f.file.read(s.max_upload_bytes + 1)
        try:
            incoming.append(svc.inspect_file(f.filename or "upload", data))
        except svc.IntakeError as e:
            errors.append(str(e))
    if errors:
        raise HTTPException(422, {"message": "Some files were rejected", "errors": errors})
    meta = None
    if pages_meta:
        try:
            meta = json.loads(pages_meta)
            if not isinstance(meta, list):
                raise ValueError
        except ValueError as e:
            raise HTTPException(422, "pages_meta must be a JSON list") from e
    if mode not in ("auto", "single", "batch"):
        raise HTTPException(422, "mode must be auto, single or batch")
    if mode == "auto":
        mode = "single" if (source == "camera" or len(incoming) == 1) else "batch"
    try:
        docs = svc.create_documents(db, incoming, mode, source, meta, llm_mode)
    except svc.IntakeError as e:
        raise HTTPException(422, str(e)) from e
    duplicates = {}
    for d in docs:
        if d.file_hash:
            prev = db.query(Document.id, Document.filename).filter(Document.file_hash == d.file_hash, Document.id != d.id).first()
            if prev:
                duplicates[d.id] = {"id": prev.id, "filename": prev.filename}
    runner = get_runner()
    for d in docs:
        runner.submit(d.id)
    return {"documents": [svc.doc_summary(d) for d in docs], "duplicates": duplicates}


@router.get("", summary="List documents")
def list_documents(status: str | None = None, q: str | None = None, limit: int = Query(50, le=200), offset: int = 0,
                   db: Session = Depends(get_db)):
    query = db.query(Document).options(
        defer(Document.extraction), defer(Document.extraction_original), defer(Document.pipeline_log),
        selectinload(Document.pages).load_only(Page.id, Page.page_no, Page.thumb_path, Page.image_path, Page.document_id),
    )
    if status:
        query = query.filter(Document.status.in_(status.split(",")))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Document.filename.ilike(like), Document.vendor_name.ilike(like), Document.document_number.ilike(like)))
    total = query.count()
    docs = query.order_by(Document.created_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [svc.doc_summary(d) for d in docs]}


@router.get("/{doc_id}", summary="Document detail with extraction and pipeline log")
def get_document(doc_id: str, db: Session = Depends(get_db)):
    return svc.doc_detail(_get_doc(db, doc_id))


@router.get("/{doc_id}/status", summary="Lightweight processing status (for polling)")
def get_status(doc_id: str, db: Session = Depends(get_db)):
    d = db.query(Document.id, Document.status, Document.progress, Document.progress_message, Document.error).filter(Document.id == doc_id).first()
    if d is None:
        raise HTTPException(404, "Document not found")
    return {"id": d.id, "status": d.status, "progress": d.progress, "progress_message": d.progress_message, "error": d.error}


def _page(db: Session, doc_id: str, page_no: int) -> Page:
    p = db.query(Page).filter(Page.document_id == doc_id, Page.page_no == page_no).first()
    if p is None:
        raise HTTPException(404, "Page not found")
    return p


@router.get("/{doc_id}/pages/{page_no}/image", summary="Page image (processed | original | thumb)")
def page_image(doc_id: str, page_no: int, variant: str = Query("processed", pattern="^(processed|original|thumb)$"), db: Session = Depends(get_db)):
    p = _page(db, doc_id, page_no)
    st = get_storage()
    if variant == "thumb" and p.thumb_path and st.exists(p.thumb_path):
        return FileResponse(st.path(p.thumb_path), media_type="image/jpeg", headers=CACHE)
    if variant == "processed" and p.image_path and st.exists(p.image_path):
        return FileResponse(st.path(p.image_path), media_type="image/jpeg", headers=CACHE)
    if p.source_kind == "image":
        src = st.path(p.source_file)
        mime = mimetypes.guess_type(src.name)[0] or ""
        if mime in ("image/jpeg", "image/png", "image/webp"):
            return FileResponse(src, media_type=mime, headers=CACHE)
        cache_rel = f"{doc_id}/pages/{page_no}_original.jpg"
        if not st.exists(cache_rel):
            st.write(cache_rel, encode_jpeg(load_image_bytes(src.read_bytes()), 90))
        return FileResponse(st.path(cache_rel), media_type="image/jpeg", headers=CACHE)
    if p.image_path and st.exists(p.image_path):
        return FileResponse(st.path(p.image_path), media_type="image/jpeg", headers=CACHE)
    raise HTTPException(404, "Image not available yet")


@router.get("/{doc_id}/pages/{page_no}/tokens", summary="OCR tokens with normalised boxes for a page")
def page_tokens(doc_id: str, page_no: int, db: Session = Depends(get_db)):
    p = _page(db, doc_id, page_no)
    return {"page_no": p.page_no, "width": p.width, "height": p.height, "text_source": p.text_source, "tokens": p.tokens or []}


@router.get("/{doc_id}/source", summary="Download the original uploaded file (first file)")
def source_file(doc_id: str, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    if not doc.pages:
        raise HTTPException(404, "No source file")
    path = get_storage().path(doc.pages[0].source_file)
    return FileResponse(path, filename=doc.filename if "." in doc.filename else path.name)


@router.patch("/{doc_id}/extraction", summary="Correct or verify fields (re-validates automatically)")
def patch_extraction(doc_id: str, body: svc.ExtractionPatch, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    _editable(doc)
    try:
        ex = svc.apply_patch(db, doc, body)
    except svc.PatchError as e:
        raise HTTPException(422, str(e)) from e
    return _ex_response(doc, ex)


class AddLine(BaseModel):
    after_id: str | None = None


@router.post("/{doc_id}/line-items", summary="Add an empty line item")
def add_line(doc_id: str, body: AddLine, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    _editable(doc)
    ex, new_id = svc.add_line_item(db, doc, body.after_id)
    return {**_ex_response(doc, ex), "new_id": new_id}


@router.delete("/{doc_id}/line-items/{item_id}", summary="Delete a line item (audited)")
def delete_line(doc_id: str, item_id: str, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    _editable(doc)
    try:
        ex = svc.delete_line_item(db, doc, item_id)
    except svc.PatchError as e:
        raise HTTPException(404, str(e)) from e
    return _ex_response(doc, ex)


@router.post("/{doc_id}/taxes", summary="Add an empty tax line")
def add_tax(doc_id: str, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    _editable(doc)
    ex, new_id = svc.add_tax_line(db, doc)
    return {**_ex_response(doc, ex), "new_id": new_id}


@router.delete("/{doc_id}/taxes/{tax_id}", summary="Delete a tax line (audited)")
def delete_tax(doc_id: str, tax_id: str, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    _editable(doc)
    try:
        ex = svc.delete_tax_line(db, doc, tax_id)
    except svc.PatchError as e:
        raise HTTPException(404, str(e)) from e
    return _ex_response(doc, ex)


@router.post("/{doc_id}/recalculate", summary="Re-run the Decimal validation engine")
def recalculate(doc_id: str, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    _editable(doc)
    return _ex_response(doc, svc.recalculate(db, doc))


class ApproveBody(BaseModel):
    acknowledge: bool = False


@router.post("/{doc_id}/approve", summary="Approve the extraction (409 lists blocking issues unless acknowledged)")
def approve(doc_id: str, body: ApproveBody, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    _editable(doc)
    try:
        ex = svc.approve(db, doc, body.acknowledge)
    except svc.ApprovalBlocked as e:
        raise HTTPException(409, {"message": "Review required before approval", "reasons": e.reasons}) from e
    return _ex_response(doc, ex)


class ReprocessBody(BaseModel):
    llm_mode: str | None = None


@router.post("/{doc_id}/reprocess", summary="Discard the extraction and process again")
def reprocess(doc_id: str, body: ReprocessBody, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    if doc.status in ("queued", "processing"):
        raise HTTPException(409, "Already processing")
    if body.llm_mode not in (None, "always", "fallback", "off"):
        raise HTTPException(422, "Invalid llm_mode")
    svc.reset_for_reprocess(db, doc, body.llm_mode)
    get_runner().submit(doc.id)
    return svc.doc_summary(doc)


@router.delete("/{doc_id}", status_code=204, summary="Delete a document and its files")
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    if doc.status == "processing":
        raise HTTPException(409, "Wait until processing finishes")
    db.add(AuditEvent(entity_type="document", entity_id=doc.id, action="deleted", detail=doc.filename))
    db.delete(doc)
    db.commit()
    get_storage().delete_dir(doc_id)
    return Response(status_code=204)


@router.get("/{doc_id}/audit", summary="Audit trail for a document")
def audit(doc_id: str, db: Session = Depends(get_db)):
    rows = db.query(AuditEvent).filter(AuditEvent.entity_id == doc_id).order_by(AuditEvent.created_at.asc()).all()
    return [{"action": r.action, "field_path": r.field_path, "old_value": r.old_value, "new_value": r.new_value,
             "detail": r.detail, "created_at": iso_utc(r.created_at)} for r in rows]


@router.get("/{doc_id}/export", summary="Export a document (json | csv | xlsx | pdf)")
def export_document(doc_id: str, format: str = Query("json", pattern="^(json|csv|xlsx|pdf)$"), db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    if not doc.extraction:
        raise HTTPException(409, "Nothing to export yet")
    ex = svc.load_extraction(doc)
    meta = svc.doc_summary(doc)
    stem = (doc.document_number or doc.filename.rsplit(".", 1)[0] or doc.id)
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:60] or doc.id
    if format == "json":
        rows = db.query(AuditEvent).filter(AuditEvent.entity_id == doc_id).order_by(AuditEvent.created_at.asc()).all()
        audit_rows = [{"action": r.action, "field_path": r.field_path, "old_value": r.old_value, "new_value": r.new_value,
                       "created_at": iso_utc(r.created_at)} for r in rows]
        data, media = exports.document_json(meta, ex, doc.extraction_original, audit_rows), "application/json"
    elif format == "csv":
        data, media = exports.document_csv(ex), "text/csv; charset=utf-8"
    elif format == "xlsx":
        data, media = exports.document_xlsx(meta, ex), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        data, media = exports.document_pdf(meta, ex), "application/pdf"
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{stem}.{format}"'})
