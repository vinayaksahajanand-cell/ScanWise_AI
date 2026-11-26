# ScanWise AI — Intelligent Invoice & Financial Document Reader

Scan invoices, bills, receipts, quotations, purchase orders and price lists with a **live camera**, or upload **images and PDFs**. ScanWise turns them into structured, validated financial data:

- Every value is linked to the exact words on the page.
- Every total is recomputed with exact decimal arithmetic.
- Anything uncertain is flagged for review instead of guessed.

---

## Contents

1. [What it does](#what-it-does)
2. [How accuracy works](#how-accuracy-works)
3. [Quick start](#quick-start)
4. [Scanning with a phone](#scanning-with-a-phone)
5. [Configuration](#configuration)
6. [Using the app](#using-the-app)
7. [REST API](#rest-api)
8. [Testing & benchmark](#testing--benchmark)
9. [Deployment (Docker)](#deployment-docker)
10. [Project structure](#project-structure)
11. [Troubleshooting](#troubleshooting)
12. [Scope decisions, security & roadmap](#scope-decisions-security--roadmap)

---

## What it does

| Area | Highlights |
|---|---|
| **Capture** | Live camera with real-time page detection and a steady/sharp/large-enough check before auto-capture. Also: torch, zoom, camera switch, full-resolution stills where supported, and multi-page sessions. The corner editor has a magnifier loupe and a live perspective preview. You can also upload JPG, PNG, WEBP, HEIC, TIFF or PDF by drag & drop or paste, as a batch or as one multi-page document. |
| **Reading** | **PP-OCRv6** OCR (RapidOCR on ONNX Runtime) with word boxes. A second consensus pass re-reads the page when confidence is low, and dropped word spaces are repaired. Digital PDFs use their text layer, so nothing is re-OCR'd. An optional **vision model** (OpenAI, Gemini, Anthropic, NVIDIA NIM or any OpenAI-compatible endpoint) cross-checks every value. |
| **Understanding** | **Header fields:** invoice/PO/reference numbers, issue and due dates, payment terms, place of supply. **Parties:** vendor and customer name, address, phone, email and tax ID. **Line items:** name, description, SKU, quantity, unit, rate, discount, tax and amount. **Taxes:** GST/CGST/SGST/IGST, VAT, sales tax, consumption tax and exempt items. **Totals:** subtotal, discount, shipping, rounding, grand total, amount paid and balance. **Categories:** Services, Materials or custom. **Units:** normalised (h → hour, ea → each, sq ft …), keeping the printed text. |
| **Currency** | The ISO 4217 code is worked out from symbols, codes and regional context such as GSTIN, +91, postcodes, ABN or VAT numbers. An ambiguous `$`, `¥` or `Rs` is flagged for confirmation. When a symbol conflicts with the regional context (for example `₹` misread as `€`), the conflict is flagged too. Amounts are **never converted**. |
| **Validation** | Python `Decimal` recomputes qty × rate, line taxes, Σ lines = subtotal, each tax line, grand total and total − paid = balance. It recognises tax-inclusive pricing and **taxes that apply to only some items**, and adjusts tolerances for rounding and currency (e.g. 0 decimals for JPY, 3 for KWD). When arithmetic fails, it proposes OCR-aware fixes, for example "leading 7 may be a misread ₹ symbol", and a person applies them. |
| **Review** | Two-panel workspace: click any value to jump to it on the page, or click a box on the page to select its value. The inspector shows the printed text, the original machine reading, sources, OCR confidence, issues and competing readings. A **pick-from-document** button fills a field from any word. "Next issue" walks through flagged values. Approval is blocked until open issues are acknowledged, and everything is audited. |
| **Price book** | Search; All/Services/Materials and custom categories; scan a bill or price list; add, edit and delete items (unit chips, tax presets); bulk select; CSV/XLSX import; export. Duplicates are detected by name, SKU and unit, with old → new rate shown. Price history is kept. |
| **Exports** | Excel (summary, items, taxes, validation and field-review sheets), CSV, JSON (with evidence, the original reading and the audit trail) and a PDF report. Available per document, for all extracted line items, or for the price book. |
| **Clients & business cards** | A **Clients** page with a New Client form (name, company, job title, website, email, phone, billing address, tax ID). **Scan Business Card** reads the front and optionally the back of a card (a camera photo, or an uploaded JPG, PNG, WEBP, HEIC, TIFF, BMP, GIF or PDF) with OCR, text rules and your vision model, then fills the form for you to review: uncertain fields are highlighted and nothing is saved until you press Add. Also: import from the browser's contact picker or a `.vcf` file. Separate from invoice scanning. |
| **API** | REST with OpenAPI docs at `/docs`. |

## How accuracy works

```
capture ─► geometry ─► OCR (pass A) ─► orientation / deskew ─► OCR (pass B, enhanced)  ─► space repair
             │            │                                         consensus merge
             │            └─ PDF text layer when reliable (exact)
             ▼
   layout extraction (always) ─────────────┐
   vision model (always | fallback | off) ─┼─► cross-validation ─► Decimal validation ─► arbitration ─► field status
   price-book matching ────────────────────┘     (vision value vs     (recompute every     (competing readings,     verified / high confidence /
                                                  cited OCR tokens)    relationship)        kept flagged)            needs review / unreadable / mismatch
```

The design rules the project is built around:

1. **Models transcribe, code computes.** The vision model returns values *exactly as printed*, together with the OCR token IDs that contain them. Parsing and arithmetic are deterministic.
2. **Evidence for every value.** Each value carries its page, bounding box, OCR tokens, OCR confidence and the methods that agree on it.
3. **No silent corrections.** Disagreements keep both readings, failed arithmetic produces *suggestions*, and nothing changes without a person.
4. **Separate signals.** OCR confidence, extractor confidence and arithmetic validation are shown side by side, never blended into one score.
5. **The metric that matters is the silent error rate**: wrong values that were *not* flagged for review. See [benchmark](#testing--benchmark).

## Quick start

**Prerequisites:** Python **3.12** (3.11–3.13 work), Node.js **20.9+**, and about 1 GB of disk. The first OCR run downloads the PP-OCRv6 models (~30 MB) automatically.

### 1. Backend (FastAPI, port 8000)

```bash
cd backend
python -m venv .venv                 # Windows: py -3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                 # optional: add a vision-model API key
python -m uvicorn app.main:app --reload --port 8000
```

The database (SQLite in `backend/data/`) is created and migrated automatically on start. API docs are at http://localhost:8000/docs.

### 2. Frontend (Next.js, port 3000)

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:3000**. The frontend proxies `/api/*` to the backend, so both are on one origin. Set `BACKEND_URL` if the backend runs elsewhere.

**Production:** run `npm run build && npm start` for the frontend, and `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` for the backend.

## Scanning with a phone

Browsers only allow live camera access on **HTTPS** (or `localhost`). To use your phone on the same Wi-Fi:

```bash
cd frontend
ALLOWED_DEV_ORIGINS=192.168.1.20 npm run dev:https     # your computer's LAN IP; Windows: set ALLOWED_DEV_ORIGINS=… first
```

Open `https://192.168.1.20:3000` on the phone and accept the self-signed certificate. On HTTP, or if camera permission is denied, **Use the phone's camera app** still works: it opens the native camera, and edges are detected on the photo.

**Tips for best accuracy:**
- Fill most of the frame with the page; the scanner says *"Move closer"* when text would be too small.
- Use even light; turn on the torch in dark rooms.
- Place the page on a contrasting surface.
- Let auto-capture fire when the outline turns green.

## Configuration

All configuration is via environment variables or `backend/.env` (see [`backend/.env.example`](backend/.env.example)). **API keys are only ever read from the environment.** They are never shown in the UI, returned by the API or logged.

### Vision model (optional, recommended)

Without a key, documents are read by OCR plus layout analysis, which works well on clean documents (see the benchmark). A vision model adds a second, independent reading, and its biggest gains are on photos.

| Provider | Variables | Notes |
|---|---|---|
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-4.1`), `OPENAI_REASONING_EFFORT` (default `low`) | Uses strict JSON-schema structured outputs. The effort setting applies to reasoning models (o-series, gpt-5, gpt-6…) and makes them far faster; unsupported values are replaced by a supported one automatically |
| Google Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-2.5-flash`) | Uses `responseSchema` |
| Anthropic | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (default `claude-opus-5-5`) | Forced tool call for structured output |
| NVIDIA NIM | `NVIDIA_API_KEY`, `NVIDIA_MODEL` (default `meta/llama-3.2-90b-vision-instruct`), `NVIDIA_PAGES_PER_CALL` | OpenAI-compatible endpoint at `integrate.api.nvidia.com` |
| Any OpenAI-compatible | `COMPATIBLE_BASE_URL`, `COMPATIBLE_MODEL`, `COMPATIBLE_API_KEY` | Ollama, vLLM, LM Studio, OpenRouter … |

**Speed.** The wait for a vision model is the time it takes to *write* the answer. ScanWise asks for the header, the totals and taxes, and the line items as three parallel requests (`LLM_SPLIT_CALL=true`, the default; set it to `false` for a single request). It extracts the same fields, and the benchmark scores are identical, but the scan finishes in roughly the time of the longest part. Reasoning models (o-series, gpt-5, gpt-6…) are run at low effort, and what an endpoint rejects (for example `temperature`) is remembered, so it costs one wasted request per server start instead of one per scan. An unreachable API falls back to OCR within seconds.

`LLM_PROVIDER=auto` picks the first provider that has a key. Under **Settings → Vision AI**, choose **Always** (most accurate), **When uncertain** (only when the OCR result is weak), or **Off** (no data leaves the server). Provider failures fall back to OCR and show a warning on the document.

### Other settings

| Variable | Default | Purpose |
|---|---|---|
| `OCR_ENGINE` | `rapidocr` | `rapidocr`, `paddleocr` (install `paddleocr`) or `tesseract` (install `pytesseract` and the binary) |
| `OCR_MODEL_SIZE` | `small` | PP-OCRv6 size. `small` is the best CPU trade-off; `medium`/`server` are much slower |
| `REVIEW_CONF_THRESHOLD` / `REVIEW_TEXT_CONF_THRESHOLD` | `0.90` / `0.80` | Below these OCR confidences a value needs review (unless arithmetic confirms it) |
| `DATABASE_URL` | SQLite in `DATA_DIR` | e.g. `postgresql+psycopg://user:pass@host/db` (`pip install -r requirements-postgres.txt`) |
| `DATA_DIR` | `backend/data` | Uploads, page images, SQLite database |
| `MAX_UPLOAD_MB`, `MAX_FILES_PER_REQUEST`, `MAX_PAGES_PER_DOCUMENT` | 40, 30, 40 | Intake limits |
| `WORKER_CONCURRENCY` | 2 | Documents processed in parallel |

The UI's **Settings** page holds non-secret preferences: vision mode, date order, the default currency for manual items, auto-crop, tax presets, categories and custom units.

## Using the app

1. **Scan** (camera) or **Upload**. Multiple pages become one document; with several files you can choose "Separate documents" (batch).
2. The document opens in the **review workspace** while it processes. When it finishes:
   - the summary says how many values need checking;
   - **Review issues** jumps through them one by one, zooming the page to each;
   - edit inline; every change is re-validated immediately and recorded in the audit log;
   - **Pick from document** lets you fill a field by clicking a word on the page.
3. **Validation** lists every check, showing the document value, the recalculated value and the difference. Suggestions have an **Apply** button.
4. **Approve**, then **Export** (Excel, CSV, JSON, PDF) or **Add to price book**. In the price-book step, likely duplicates show old → new rates, and you choose add, update or skip per item.
5. **Price Book** → *Scan Bill or Price List* turns a printed rate card (e.g. `$40.00 / visit`) into price-book items in one pass.

## REST API

Interactive docs are served at `http://localhost:8000/docs`. The main endpoints:

| Method & path | Purpose |
|---|---|
| `POST /api/documents` | Upload files (`files[]`, `mode=single|batch|auto`, `source`, `pages_meta` with crop corners/rotation, `llm_mode`) |
| `GET /api/documents` · `GET /api/documents/{id}` · `GET …/status` | List, detail (extraction + pipeline log), lightweight status |
| `GET …/pages/{n}/image?variant=processed|original|thumb` · `GET …/pages/{n}/tokens` | Page images and OCR tokens (normalised boxes) |
| `PATCH /api/documents/{id}/extraction` | `{updates:[{path,value}], verify:[path]}` → re-validated extraction |
| `POST|DELETE …/line-items`, `POST|DELETE …/taxes` | Add or remove rows (audited) |
| `POST …/recalculate` · `POST …/approve` · `POST …/reprocess` · `DELETE …` | Lifecycle |
| `GET …/export?format=json|csv|xlsx|pdf` · `GET …/audit` | Exports, audit trail |
| `GET /api/line-items` · `GET /api/line-items/export` | Extracted data across documents |
| `GET|POST /api/pricebook` · `GET|PATCH|DELETE /api/pricebook/{id}` | Price book CRUD (with history) |
| `POST /api/pricebook/stage` · `POST /api/pricebook/import` · `POST /api/pricebook/commit` | Review-then-commit from documents or CSV/XLSX, with duplicate matching |
| `POST /api/business-cards/extract` | Read a business card (`files[]` = front, optional back; JPG, PNG, WEBP, HEIC, TIFF, BMP, GIF or PDF; a 2-page PDF is front + back; `llm_mode`) → structured contact JSON with per-field confidence, `missing_fields`, `uncertain_fields` and `warnings`. Saves nothing |
| `GET|POST /api/clients` · `GET|PATCH|DELETE /api/clients/{id}` | Client CRUD and search (`q`); `name` is required |
| `GET /api/dashboard` · `GET /api/system` · `GET|PUT /api/settings` · `GET /api/options` | Stats, engine status (no secrets), preferences, pickers |

Field paths look like `header.vendor_name`, `line_items.<id>.rate`, `taxes.<id>.amount`, `summary.grand_total`, `document_type` and `currency`.

## Testing & benchmark

```bash
cd backend && python -m pytest          # 135 tests: parsing, currency, units, tax, dates, Decimal engine, business cards, clients,
                                        # real-sample extraction, PDF text layer, vision path (scripted provider), REST API
cd frontend && npm test                 # live document detector incl. a real camera frame on a textured desk
cd frontend && npm run typecheck
```

### Extraction benchmark

```bash
cd backend
python -m benchmark.generate --count 18          # synthetic invoices with exact ground truth
python -m benchmark.run --variants pdf,scan,photo --verbose   # add --llm always to include your vision model
```

The generator builds varied invoices across 6 currencies with their native number formats (`1.234,56 €`, Indian lakh grouping `₹1,45,339.42`, `¥262,515`). They use CGST/SGST, VAT/MwSt, sales tax and consumption tax, and mix services and materials. Each invoice is rendered three ways: **digital PDF**, **flat scan** (200 dpi) and **phone photo** (perspective, desk background, uneven light, blur and noise). Measured results without a vision model (`benchmark/results/latest.md`):

| Metric | pdf | scan | photo |
|---|---:|---:|---:|
| documents | 18 | 18 | 18 |
| cer | 0.00% | 0.11% | 0.63% |
| wer | 0.00% | 0.64% | 3.75% |
| header | 100.0% | 100.0% | 100.0% |
| currency | 100.0% | 100.0% | 100.0% |
| rows found | 100.0% | 100.0% | 100.0% |
| item name | 100.0% | 100.0% | 100.0% |
| quantity | 100.0% | 100.0% | 100.0% |
| rate | 100.0% | 98.9% | 100.0% |
| unit | 100.0% | 100.0% | 100.0% |
| amount | 100.0% | 98.9% | 100.0% |
| category | 100.0% | 100.0% | 100.0% |
| row association | 100.0% | 97.8% | 100.0% |
| tax | 100.0% | 100.0% | 100.0% |
| totals | 100.0% | 100.0% | 100.0% |
| reconciled | 100.0% | 100.0% | 100.0% |
| false positive rows | 0.0% | 0.0% | 0.0% |
| review rate | 7.0% | 13.0% | 9.7% |
| silent error rate | 0.00% | 0.00% | 0.00% |
| errors caught | 0/0 | 2/2 | 0/0 |
| avg seconds | 0.1 | 2.4 | 3.0 |

*Silent error rate* is the share of values that were wrong **and** not flagged; *review rate* is the share a person is asked to confirm. Treat these as measurements on a synthetic set: real-world documents (handwriting, stamps, crumpled paper, unusual layouts) are harder. Run the benchmark with `--llm always` to measure your vision model, and add your own documents with a `truth.json` to extend it.

## Deployment (Docker)

```bash
cp backend/.env.example backend/.env     # optional: API keys
docker compose up --build                # http://localhost:3000  (API on :8000)
```

The stack:
- **PostgreSQL 16:** database, with migrations applied on start.
- **Backend:** OCR models baked into the image; runs as a non-root user.
- **Frontend:** a standalone Next.js server.

Uploaded files and data persist in named volumes. For a public deployment, put both services behind HTTPS (needed for the camera) and an authenticating reverse proxy (see below).

## Project structure

```
backend/
  app/
    main.py, config.py, db.py, models.py      FastAPI app, settings, SQLAlchemy models
    api/                                      documents, pricebook, clients, business_cards, system routers
    cards/                                    business-card reader (OCR rules, vision prompt/schema, merge), independent of invoices
    schemas/extraction.py                     evidence-carrying extraction schema
    pipeline/
      imaging.py, page_ocr.py, pdf.py         preprocessing, multi-pass OCR, PDF text/OCR
      ocr/                                    engine abstraction (RapidOCR, PaddleOCR, Tesseract)
      layout.py, rules.py                     layout analysis + deterministic extractor
      llm/                                    vision providers, prompt, schema, runner
      reconcile.py                            vision ⟷ OCR ⟷ layout cross-validation
      finance.py                              Decimal validation engine + suggestions
      assess.py, orchestrator.py              statuses, arbitration, conditional pipeline
      normalize/                              money, currency, units, tax, dates, categories
    services/                                 intake, corrections, jobs, price book, storage
    exports/                                  CSV / XLSX / JSON / PDF
  alembic/                                    migrations
  benchmark/                                  generator + runner
  tests/                                      pytest suite + fixtures (your sample images)
frontend/
  src/app/                                    dashboard, scan, upload, documents, review, extracted, price-book, settings
  src/components/                             scanner (camera, corner editor), review workspace, price book, UI kit
  src/lib/                                    API client, types, document detector, perspective warp
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ImportError: DLL load failed while importing cv2: An Application Control policy has blocked this file` (Windows) | Windows Smart App Control blocks some new native wheels. The requirements pin `opencv-python-headless<5`; if it still happens, `pip install "opencv-python-headless==4.12.0.88"`. |
| `alembic.exe` / `uvicorn.exe` "blocked by Application Control" | Use the module form: `python -m uvicorn …`, `python -m alembic …` (migrations also run automatically on start). |
| "Cannot reach the ScanWise API" banner | Start the backend on port 8000, or set `BACKEND_URL` for the frontend. |
| Camera shows "Camera unavailable" | Use `https://` or `localhost`, allow camera permission, close other apps using the camera, or use **Use the phone's camera app**. |
| Page stays on a spinner in `npm run dev` when opened via an IP | Next.js blocks dev resources from unknown origins: use `localhost`, or set `ALLOWED_DEV_ORIGINS=<ip>`. |
| First document is slow | The first OCR call downloads/loads models (once). Typical processing afterwards: ~2–4 s per photo page on a laptop CPU, ~0.1 s for digital PDFs. |
| Photos read poorly | Make the page fill the frame (look for the "Move closer" hint), add light, avoid glare, or configure a vision model. |
| Vision model errors | The document still processes with OCR and shows the provider error as a warning. Check the key and model name; for NVIDIA NIM models that accept one image, keep `NVIDIA_PAGES_PER_CALL=1`. |

## Scope decisions, security & roadmap

Deliberately left out of v1:

- **Celery/Redis:** replaced by an in-process worker pool with database-backed job state and crash recovery.
- **LangGraph:** replaced by a plain, audited conditional pipeline.
- **Multi-user authentication and RBAC.**
- **S3 storage.**

**Security & privacy:**
- **Uploads are validated:** magic bytes, decodability, and size and page limits. Storage paths are confined to the data directory.
- **Secrets stay in the environment.** Security headers are set, and the system endpoint never exposes keys.
- **Document images reach a vision provider only when one is configured and the mode isn't Off.**
- **Deleting a document removes its files.** The audit log keeps a record that it existed.
- **There is no login.** Keep the backend bound to `localhost`, or put it behind your SSO or reverse proxy before exposing it.

**Roadmap ideas:**
- Authentication/RBAC and per-user document ownership.
- S3 storage.
- Celery for multi-node processing.
- Handwriting-specific OCR models.
- Tiling very dense pages for the vision model.
- Learning per-vendor layouts from corrections.
