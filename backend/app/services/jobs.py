"""Background processing.

A small thread pool runs the CPU-bound pipeline outside the request cycle.
Job state lives in the database, so interrupted jobs are resumed on restart.
(For multi-node deployments swap this for Celery/RQ — the pipeline is pure.)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditEvent, Document
from app.pipeline.imaging import thumbnail
from app.pipeline.orchestrator import PageSource, Pipeline, display_jpeg
from app.schemas.extraction import StageLog

from .app_settings import load_settings
from .documents import update_summary_columns
from .pricebook import make_matcher
from .storage import get_storage

log = logging.getLogger(__name__)


def process_document(doc_id: str) -> None:
    settings = get_settings()
    storage = get_storage()
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            return
        doc.status = "processing"
        doc.progress = 0.02
        doc.progress_message = "Starting"
        doc.error = None
        db.commit()
        user = load_settings(db)
        sources = [
            PageSource(page_no=p.page_no, kind=p.source_kind, file_path=storage.path(p.source_file), pdf_index=p.pdf_index,
                       corners=p.corners, rotation=p.rotation or 0, auto_crop=bool(p.auto_crop) and user.auto_crop)
            for p in doc.pages
        ]
        llm_mode = (doc.options or {}).get("llm_mode") or user.llm_mode
        date_order = user.date_order

    last = [0.0]

    def progress(msg: str, frac: float) -> None:
        now = time.monotonic()
        if now - last[0] < 0.4 and frac < 1.0:
            return
        last[0] = now
        try:
            with SessionLocal() as db2:
                d = db2.get(Document, doc_id)
                if d is not None and d.status == "processing":
                    d.progress = round(min(max(frac, 0.0), 1.0), 3)
                    d.progress_message = msg[:250]
                    db2.commit()
        except Exception:  # noqa: BLE001
            log.debug("progress update failed", exc_info=True)

    pipeline = Pipeline(settings, progress, make_matcher(SessionLocal), llm_mode, date_order)
    t0 = time.perf_counter()
    try:
        result = pipeline.run(sources)
    except Exception as e:  # noqa: BLE001
        log.exception("processing failed for %s", doc_id)
        with SessionLocal() as db:
            doc = db.get(Document, doc_id)
            if doc is not None:
                doc.status = "failed"
                doc.error = f"{type(e).__name__}: {str(e)[:500]}"
                doc.progress_message = None
                doc.pipeline_log = [json.loads(s.model_dump_json()) for s in pipeline.log]
                db.add(AuditEvent(entity_type="document", entity_id=doc_id, action="failed", detail=doc.error))
                db.commit()
        return

    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            return
        by_no = {p.page_no: p for p in doc.pages}
        for pp in result.pages:
            row = by_no.get(pp.page_no)
            if row is None:
                continue
            row.image_path = storage.write(f"{doc_id}/pages/{pp.page_no}.jpg", display_jpeg(pp.image))
            row.thumb_path = storage.write(f"{doc_id}/pages/{pp.page_no}_thumb.jpg", thumbnail(pp.image))
            row.width, row.height = pp.width, pp.height
            row.text_source = pp.quality.text_source
            row.tokens = [t.to_dict(pp.width, pp.height) for t in pp.tokens]
            row.quality = json.loads(pp.quality.model_dump_json())
            row.meta = json.loads(json.dumps(pp.meta, default=float))
        data = json.loads(result.extraction.model_dump_json())
        doc.extraction_original = data
        doc.extraction = json.loads(json.dumps(data))
        doc.pipeline_log = [json.loads(s.model_dump_json()) for s in result.log]
        update_summary_columns(doc, result.extraction)
        doc.status = result.status
        doc.progress = 1.0
        doc.progress_message = None
        doc.processed_at = datetime.now(timezone.utc)
        doc.processing_ms = int((time.perf_counter() - t0) * 1000)
        db.add(AuditEvent(entity_type="document", entity_id=doc_id, action="processed",
                          detail=f"{result.status}; extractors: {', '.join(result.extraction.extractors)}"))
        db.commit()


class JobRunner:
    def __init__(self) -> None:
        s = get_settings()
        self.executor = ThreadPoolExecutor(max_workers=max(1, s.worker_concurrency), thread_name_prefix="scanwise-worker")
        self.active: set[str] = set()
        self.lock = threading.Lock()

    def submit(self, doc_id: str) -> None:
        with self.lock:
            if doc_id in self.active:
                return
            self.active.add(doc_id)
        self.executor.submit(self._run, doc_id)

    def _run(self, doc_id: str) -> None:
        try:
            process_document(doc_id)
        except Exception:  # noqa: BLE001
            log.exception("job crashed: %s", doc_id)
        finally:
            with self.lock:
                self.active.discard(doc_id)

    def warm_up(self) -> None:
        """Load and prime the OCR models on every worker thread (engines are per-thread).

        The first inference after a start is ~2 s slower than later ones; doing it here, in the
        background, means the first real scan doesn't pay for it.
        """
        n = max(1, get_settings().worker_concurrency)
        barrier = threading.Barrier(n)  # all n tasks must be running at once -> one per worker thread

        def _warm() -> None:
            try:
                barrier.wait(timeout=30)
            except threading.BrokenBarrierError:
                pass
            try:
                import cv2
                import numpy as np

                from app.pipeline.ocr.engines import get_engine

                s = get_settings()
                engine = get_engine(s.ocr_engine, model_size=s.ocr_model_size, max_side=s.ocr_max_side, min_score=s.ocr_min_token_score)
                img = np.full((96, 480, 3), 255, np.uint8)
                cv2.putText(img, "Invoice 1234.56", (10, 62), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 2)
                t0 = time.perf_counter()
                engine.run(img)  # contains text, so both detection and recognition sessions get primed
                log.info("OCR warm-up done in %.1fs", time.perf_counter() - t0)
            except Exception:  # noqa: BLE001
                log.warning("OCR warm-up failed (first scan will just be slower)", exc_info=True)

        for _ in range(n):
            self.executor.submit(_warm)

    def recover(self) -> int:
        with SessionLocal() as db:
            ids = [d.id for d in db.query(Document).filter(Document.status.in_(["queued", "processing"])).all()]
        for i in ids:
            self.submit(i)
        return len(ids)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


_runner: JobRunner | None = None


def get_runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
    return _runner


def failed_stage(stage: str, detail: str) -> StageLog:
    return StageLog(stage=stage, status="failed", started_at=datetime.now(timezone.utc), duration_ms=0, detail=detail)
