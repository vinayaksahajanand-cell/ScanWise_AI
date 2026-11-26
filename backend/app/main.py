"""FastAPI application entry point.

Run:  python -m uvicorn app.main:app --reload --port 8000   (from backend/)
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import business_cards, clients, documents, pricebook, system
from app.config import get_settings
from app.db import run_migrations
from app.services.jobs import get_runner

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("RapidOCR", "httpx", "multipart"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("scanwise")


@asynccontextmanager
async def lifespan(_: FastAPI):
    run_migrations()
    if settings.ocr_warmup:
        get_runner().warm_up()
    resumed = get_runner().recover()
    if resumed:
        log.info("Resumed %d interrupted job(s)", resumed)
    yield
    get_runner().shutdown()


app = FastAPI(
    title="ScanWise AI API",
    version=__version__,
    description="Intelligent invoice & financial document reader: OCR + vision-LLM extraction with evidence, "
                "Decimal validation, human review, price book and exports.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # noqa: ARG001
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(system.router)
app.include_router(documents.router)
app.include_router(pricebook.router)
app.include_router(clients.router)
app.include_router(business_cards.router)
