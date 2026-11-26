"""Business-card scanner endpoint. Separate from the invoice/document endpoints on purpose."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.cards.service import MAX_CARD_MB, CardError, extract_card
from app.config import get_settings
from app.db import get_db
from app.services.app_settings import load_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/business-cards", tags=["business cards"])


@router.post("/extract")
def extract(
    files: list[UploadFile] = File(..., description="1-2 files: front first, then optionally the back. JPG, PNG, WEBP, HEIC, TIFF, BMP, GIF or PDF (a 2-page PDF = front + back)"),
    llm_mode: str | None = Form(None, pattern="^(always|fallback|off)$"),
    db: Session = Depends(get_db),
):
    """Read a business card and return structured contact details. Nothing is saved:
    the caller reviews/edits the result and then creates the client with POST /api/clients."""
    limit = min(get_settings().max_upload_mb, MAX_CARD_MB) * 1024 * 1024
    incoming: list[tuple[str, bytes]] = []
    for f in files[:5]:
        data = f.file.read(limit + 1)
        incoming.append((f.filename or "image", data))
    mode = llm_mode or load_settings(db).llm_mode
    try:
        return extract_card(incoming, mode)
    except CardError as e:
        raise HTTPException(e.status, e.detail) from None
