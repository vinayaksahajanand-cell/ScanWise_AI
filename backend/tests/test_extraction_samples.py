"""End-to-end extraction on real samples (OCR + layout, no vision model)."""

from decimal import Decimal

import pytest

from app.pipeline.orchestrator import PageSource, Pipeline

from .conftest import FIXTURES


def run(settings, name, kind="image", **kw):
    return Pipeline(settings, llm_mode="off").run([PageSource(1, kind, FIXTURES / name, **kw)])


@pytest.fixture(scope="module")
def invoice(settings):
    return run(settings, "sample_invoice_inr.png")


def hv(ex, key):
    return ex.header[key].value


def test_invoice_header(invoice):
    ex = invoice.extraction
    assert ex.document_type.value == "invoice"
    assert hv(ex, "document_number") == "INV-00001"
    assert hv(ex, "document_date") == "2026-09-24"
    assert hv(ex, "due_date") == "2026-10-08"
    assert hv(ex, "payment_terms") == "Net 14"
    assert hv(ex, "po_number") == "PO-7000"
    assert hv(ex, "vendor_name") == "Patel Electric Works"
    assert hv(ex, "vendor_tax_id") == "24AAAFP4821C1Z5"
    assert hv(ex, "vendor_email") == "hiren@patelelectric.in"
    assert hv(ex, "customer_name") == "Shree Krupa Developers"
    assert hv(ex, "customer_tax_id") == "24AAECS7731F1ZP"
    assert hv(ex, "vendor_address") == "Shop 14, Ring Road Market, Surat, Gujarat 395002"
    assert ex.currency.code == "INR"


def test_invoice_items_taxes_totals(invoice):
    ex = invoice.extraction
    items = [(li.name.value, li.quantity.value, li.unit.value, li.rate.value, li.amount.value, li.category.value) for li in ex.line_items]
    assert items == [
        ("Wiring and rewiring", "1", "job", "4500.00", "4500.00", "Services"),
        ("Labour charge", "2", "hour", "600.00", "1200.00", "Services"),
    ]
    assert ex.line_items[0].description.value == "Includes materials and travel"
    assert [(t.tax_type, t.rate.value, t.amount.value) for t in ex.taxes] == [("CGST", "9", "405.00"), ("SGST", "9", "405.00"), ("IGST", "18", "216.00")]
    assert ex.summary["subtotal"].value == "5700.00"
    assert ex.summary["grand_total"].value == "6726.00"
    assert ex.summary["balance_due"].value == "0.00"
    assert ex.validation.status in ("pass", "warning")
    assert ex.validation.recalculated_total == "6726.00"


def test_every_value_has_evidence(invoice):
    ex = invoice.extraction
    for li in ex.line_items:
        for f in (li.name, li.rate, li.amount):
            assert f.bbox is not None and f.token_ids and f.ocr_confidence is not None
            assert 0 <= f.bbox.x0 < f.bbox.x1 <= 1 and 0 <= f.bbox.y0 < f.bbox.y1 <= 1


def test_price_list(settings):
    ex = run(settings, "sample_price_list_usd.png").extraction
    assert ex.document_type.value == "price_list"
    got = {li.name.value: (li.rate.value, li.unit.value, li.category.value) for li in ex.line_items}
    assert len(got) == 11
    assert got["AC inspection"] == ("40.00", "visit", "Services")
    assert got["Repair labor"] == ("80.00", "hour", "Services")
    assert got["Duct cleaning"] == ("150.00", "job", "Services")
    assert got["Split AC install"] == ("450.00", "unit", "Services")
    assert got["Replacement filter"] == ("37.50", "each", "Materials")
    assert ex.currency.code == "USD" and ex.currency.ambiguous  # "$" alone must be confirmed


def test_camera_photo_is_cropped_and_read(settings):
    res = run(settings, "camera_photo_invoice_hd.jpg")
    ex = res.extraction
    assert res.pages[0].meta["crop"] == "auto"
    assert hv(ex, "document_number") == "INV-00001"
    assert ex.summary["grand_total"].value == "6726.00"
    names = [li.name.value for li in ex.line_items]
    assert names == ["Wiring and rewiring", "Labour charge"]
    paid = ex.summary["amount_paid"]
    if paid.value != "-6726.00":
        # an OCR misread of the ₹ glyph must be caught by arithmetic and come with the fix
        assert paid.status == "mismatch"
        sugg = [s for c in ex.validation.checks for s in c.suggestions if s.path == "summary.amount_paid"]
        assert sugg and sugg[0].suggested == "-6726.00"


def test_low_resolution_capture_is_warned(settings):
    res = run(settings, "camera_photo_invoice.jpg")
    assert any("move the camera closer" in w for w in res.pages[0].quality.warnings)


def test_digital_pdf_uses_text_layer(settings, tmp_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "invoice.pdf"
    c = canvas.Canvas(str(pdf), pagesize=A4)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, 790, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(380, 790, "Invoice #")
    c.drawRightString(545, 790, "UK-2041")
    c.drawString(380, 775, "Date")
    c.drawRightString(545, 775, "14 Nov 2026")
    c.drawString(50, 750, "From")
    c.drawString(50, 736, "Thames Heating Ltd")
    c.drawString(50, 722, "12 Baker Street, London W1U 3BW")
    y = 680
    for x, h in ((50, "Description"), (330, "Qty"), (400, "Rate"), (500, "Amount")):
        c.drawString(x, y, h)
    rows = [("Boiler service", "1", "£95.00", "£95.00"), ("Radiator valve", "4", "£12.50", "£50.00"), ("Labour", "3", "£45.00", "£135.00")]
    for i, (d, q, r, a) in enumerate(rows):
        yy = y - 22 * (i + 1)
        c.drawString(50, yy, d)
        c.drawRightString(345, yy, q)
        c.drawRightString(440, yy, r)
        c.drawRightString(545, yy, a)
    c.drawString(400, 560, "Subtotal")
    c.drawRightString(545, 560, "£280.00")
    c.drawString(400, 545, "VAT 20%")
    c.drawRightString(545, 545, "£56.00")
    c.drawString(400, 530, "Total")
    c.drawRightString(545, 530, "£336.00")
    c.save()
    res = Pipeline(settings, llm_mode="off").run([PageSource(1, "pdf", pdf, pdf_index=0)])
    ex = res.extraction
    assert res.pages[0].quality.text_source == "pdf_text"
    assert hv(ex, "document_number") == "UK-2041"
    assert [(li.name.value, li.quantity.value, li.rate.value, li.amount.value) for li in ex.line_items] == [
        ("Boiler service", "1", "95.00", "95.00"), ("Radiator valve", "4", "12.50", "50.00"), ("Labour", "3", "45.00", "135.00")]
    assert ex.currency.code == "GBP"
    assert ex.validation.status == "pass"
    assert all(li.amount.ocr_confidence == 1.0 for li in ex.line_items)
    assert Decimal(ex.validation.recalculated_total) == Decimal("336.00")


# --- commercial invoice: "Total Account Charges" above "Amount Due" ------------------------------------
@pytest.fixture(scope="module")
def commercial(settings):
    return run(settings, "sample_commercial_invoice_usd.png")


def test_commercial_invoice_totals_use_arithmetic_not_label_strength(commercial):
    """'Total Account Charges $5000' is the line subtotal; the grand total is 'Amount Due $5400'."""
    ex = commercial.extraction
    assert [(li.name.value, li.quantity.value, li.amount.value) for li in ex.line_items] == [("Mahogany Table", "10", "5000.00")]
    assert ex.summary["subtotal"].value == "5000.00"
    assert [(t.rate.value, t.amount.value) for t in ex.taxes] == [("8", "400.00")]
    assert ex.summary["grand_total"].value == "5400.00"
    assert ex.summary["amount_paid"].value in (None, "")  # the $400 belongs to the tax row, not "Payment Transferrable to:"
    assert ex.validation.status == "pass"


def test_commercial_invoice_survives_a_soft_photo(settings, tmp_path):
    import cv2

    img = cv2.GaussianBlur(cv2.imread(str(FIXTURES / "sample_commercial_invoice_usd.png")), (0, 0), 2)
    p = tmp_path / "soft.jpg"
    cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    ex = Pipeline(settings, llm_mode="off").run([PageSource(1, "image", p)]).extraction
    assert ex.summary["grand_total"].value == "5400.00"
    assert ex.line_items[0].amount.value == "5000.00"
    assert ex.validation.status == "pass"
