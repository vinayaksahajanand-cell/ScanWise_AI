"""People see day/month/year: exports print DD/MM/YYYY, ambiguous printed dates are read day-first by default."""

import io

import pytest

from app import exports
from app.pipeline.normalize.dates import parse_date
from app.pipeline.orchestrator import PageSource, Pipeline
from app.services.app_settings import UserSettings

from .conftest import FIXTURES


def test_dmy_helper():
    assert exports.dmy("2023-02-01") == "01/02/2023"
    assert exports.dmy("2026-12-25") == "25/12/2026"
    assert exports.dmy(None) is None
    assert exports.dmy("INV-0001") == "INV-0001"
    assert exports.header_text("due_date", "2023-02-01") == "01/02/2023"
    assert exports.header_text("document_number", "2023-02-01") == "2023-02-01"  # only date fields are touched


def test_default_reads_ambiguous_dates_day_first():
    assert UserSettings().date_order == "DMY"
    assert parse_date("01/02/2023", "DMY").iso == "2023-02-01"
    assert parse_date("01/02/2023", "MDY").iso == "2023-01-02"  # still selectable in Settings


@pytest.fixture(scope="module")
def invoice(settings):
    return Pipeline(settings, llm_mode="off").run([PageSource(1, "image", FIXTURES / "sample_invoice_inr.png")])


def test_excel_export_shows_dates_as_dmy(invoice):
    from openpyxl import load_workbook

    ex = invoice.extraction
    assert ex.header["document_date"].value == "2026-09-24"  # stored ISO
    ws = load_workbook(io.BytesIO(exports.document_xlsx({"filename": "x.png", "status": "ready"}, ex)))["Summary"]
    cells = {r[0].value: r[1].value for r in ws.iter_rows(min_row=2)}
    assert cells["Document date"] == "24/09/2026"
    assert cells["Due date"] == "08/10/2026"


def test_json_export_keeps_iso_for_machines(invoice):
    import json

    payload = json.loads(exports.document_json({}, invoice.extraction, None, []))
    assert payload["extraction"]["header"]["document_date"]["value"] == "2026-09-24"
