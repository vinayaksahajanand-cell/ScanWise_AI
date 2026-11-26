"""CSV / Excel / JSON / PDF exports.

Money columns are exported as exact decimals (strings in CSV/JSON, numeric cells
in Excel). Currency is always explicit — values are never converted.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.schemas.extraction import HEADER_KEYS, SUMMARY_KEYS, ExtractionResult

HEADER_LABELS = {
    "document_number": "Document / invoice no.", "po_number": "PO number", "reference_number": "Reference",
    "document_date": "Document date", "due_date": "Due date", "payment_terms": "Payment terms",
    "vendor_name": "Vendor", "vendor_address": "Vendor address", "vendor_phone": "Vendor phone",
    "vendor_email": "Vendor email", "vendor_tax_id": "Vendor tax ID", "customer_name": "Customer",
    "customer_contact": "Customer contact", "customer_address": "Customer address", "customer_phone": "Customer phone",
    "customer_email": "Customer email", "customer_tax_id": "Customer tax ID", "place_of_supply": "Place of supply",
}
SUMMARY_LABELS = {
    "subtotal": "Subtotal", "discount_total": "Discount", "taxable_amount": "Taxable amount", "tax_total": "Total tax",
    "shipping": "Shipping", "other_charges": "Other charges", "rounding": "Rounding", "grand_total": "Grand total",
    "amount_paid": "Amount paid", "balance_due": "Balance due",
}
ITEM_COLUMNS = [
    ("#", None), ("Item", "name"), ("Description", "description"), ("SKU", "sku"), ("Qty", "quantity"),
    ("Unit", "unit"), ("Rate", "rate"), ("Discount", "discount"), ("Tax %", "tax_rate"), ("Tax amount", "tax_amount"),
    ("Amount", "amount"), ("Line total", "line_total"), ("Category", "category"),
]
MONEY_KEYS = {"rate", "discount", "tax_amount", "amount", "line_total"}
DATE_KEYS = {"document_date", "due_date"}


def dmy(v):
    """'2023-02-01' -> '01/02/2023' for human-facing exports (CSV/Excel/PDF). JSON keeps ISO for machines."""
    return f"{v[8:10]}/{v[5:7]}/{v[:4]}" if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else v


def header_text(key: str, value):
    return dmy(value) if key in DATE_KEYS else value


def _d(v: str | None) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _item_rows(ex: ExtractionResult) -> list[list]:
    rows = []
    for li in ex.line_items:
        row: list = [li.index + 1]
        for _, key in ITEM_COLUMNS[1:]:
            f = getattr(li, key)
            row.append(f.value)
        row += [ex.currency.code, li.status, li.page, li.original_text]
        rows.append(row)
    return rows


ITEM_HEADERS = [c for c, _ in ITEM_COLUMNS] + ["Currency", "Status", "Page", "Original text"]


def document_json(meta: dict, ex: ExtractionResult, original: dict | None, audit: list[dict]) -> bytes:
    payload = {"document": meta, "extraction": json.loads(ex.model_dump_json()), "original_extraction": original, "audit": audit,
               "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def document_csv(ex: ExtractionResult) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ITEM_HEADERS)
    for row in _item_rows(ex):
        w.writerow(["" if c is None else c for c in row])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def rows_csv(headers: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if c is None else c for c in r])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


# ---------------------------------------------------------------- Excel
def _xlsx_sheet(ws, headers: list[str], rows: list[list], money_cols: set[int] | None = None) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A68")
        c.alignment = Alignment(vertical="center")
    for r in rows:
        out = []
        for i, v in enumerate(r):
            if money_cols and i in money_cols and isinstance(v, str):
                d = _d(v)
                out.append(d if d is not None else v)
            else:
                out.append(v)
        ws.append(out)
    if money_cols:
        for row in ws.iter_rows(min_row=2):
            for i in money_cols:
                if i < len(row) and isinstance(row[i].value, Decimal):
                    row[i].number_format = "#,##0.00##"
    for i, h in enumerate(headers, start=1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if i - 1 < len(r) and r[i - 1] is not None] + [8])
        ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 60)
    ws.freeze_panes = "A2"


def document_xlsx(meta: dict, ex: ExtractionResult) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    rows: list[list] = [
        ["File", meta.get("filename")], ["Status", meta.get("status")],
        ["Document type", ex.document_type.value], ["Currency", ex.currency.code],
        ["Currency confidence", f"{ex.currency.confidence or '-'}{' (ambiguous)' if ex.currency.ambiguous else ''}"],
    ]
    for k in HEADER_KEYS:
        f = ex.header.get(k)
        if f is not None and f.value:
            rows.append([HEADER_LABELS.get(k, k), header_text(k, f.value)])
    for k in SUMMARY_KEYS:
        f = ex.summary.get(k)
        if f is not None and f.value is not None:
            rows.append([SUMMARY_LABELS.get(k, k), _d(f.value)])
    if ex.validation:
        rows += [["Validation", ex.validation.status], ["Recalculated total", _d(ex.validation.recalculated_total)],
                 ["Difference", _d(ex.validation.difference)]]
    _xlsx_sheet(ws, ["Field", "Value"], rows)
    money_idx = {i for i, (_, k) in enumerate(ITEM_COLUMNS) if k in MONEY_KEYS}
    _xlsx_sheet(wb.create_sheet("Line items"), ITEM_HEADERS, _item_rows(ex), money_idx | {4, 8})
    tax_rows = [[t.label.value, t.tax_type, t.rate.value, _d(t.base.value), _d(t.amount.value),
                 ", ".join(t.applies_to or []) or "document", t.status] for t in ex.taxes]
    _xlsx_sheet(wb.create_sheet("Taxes"), ["Label", "Type", "Rate %", "Base", "Amount", "Applies to", "Status"], tax_rows, {3, 4})
    checks = [[c.category, c.label, c.status, _d(c.extracted), _d(c.expected), _d(c.difference), c.message]
              for c in (ex.validation.checks if ex.validation else [])]
    _xlsx_sheet(wb.create_sheet("Validation"), ["Type", "Check", "Status", "Extracted", "Recalculated", "Difference", "Message"], checks, {3, 4, 5})
    from app.pipeline.assess import iter_fields

    review = [[p, f.value, f.raw, f.status, ", ".join(f.sources), f.ocr_confidence, "; ".join(f.issues)]
              for p, f in iter_fields(ex) if f.present or f.status == "unreadable"]
    _xlsx_sheet(wb.create_sheet("Field review"), ["Field", "Value", "Printed text", "Status", "Sources", "OCR confidence", "Issues"], review)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def rows_xlsx(sheet: str, headers: list[str], rows: list[list], money_cols: set[int] | None = None) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet[:31]
    _xlsx_sheet(ws, headers, rows, money_cols)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---------------------------------------------------------------- PDF
_FONT: tuple[str, str] | None = None


def _font() -> tuple[str, str]:
    """Register a Unicode TTF if available (₹, €, accents); fall back to Helvetica."""
    global _FONT
    if _FONT:
        return _FONT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
    ]
    for reg, bold in candidates:
        if Path(reg).exists() and Path(bold).exists():
            try:
                pdfmetrics.registerFont(TTFont("SW", reg))
                pdfmetrics.registerFont(TTFont("SW-Bold", bold))
                _FONT = ("SW", "SW-Bold")
                return _FONT
            except Exception:  # noqa: BLE001
                continue
    _FONT = ("Helvetica", "Helvetica-Bold")
    return _FONT


def _money(v: str | None, cur: str | None) -> str:
    d = _d(v)
    if d is None:
        return "—"
    return f"{cur + ' ' if cur else ''}{d:,.2f}" if d.as_tuple().exponent >= -2 else f"{cur + ' ' if cur else ''}{d:,}"


def document_pdf(meta: dict, ex: ExtractionResult) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    reg, bold = _font()
    base = ParagraphStyle("b", fontName=reg, fontSize=8.5, leading=11)
    small = ParagraphStyle("s", parent=base, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#555555"))
    h1 = ParagraphStyle("h1", parent=base, fontName=bold, fontSize=16, leading=20, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base, fontName=bold, fontSize=10.5, leading=14, spaceBefore=10, spaceAfter=4,
                        textColor=colors.HexColor("#1F3A68"))
    cur = ex.currency.code
    status_color = {"pass": "#1A7F4B", "explained": "#1A7F4B", "warning": "#B7791F", "fail": "#C53030", "skipped": "#718096"}

    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"ScanWise extraction — {meta.get('filename')}")
    story: list = []
    dt = (ex.document_type.value or "document").replace("_", " ").title()
    num = ex.header.get("document_number")
    story.append(Paragraph(f"{dt} {num.value if num and num.value else ''}".strip(), h1))
    story.append(Paragraph(
        f"Source: {meta.get('filename')} · Status: {meta.get('status')} · Currency: {cur or 'unknown'}"
        f"{' (ambiguous)' if ex.currency.ambiguous else ''} · Extracted by: {', '.join(ex.extractors)}", small))

    hdr = [[Paragraph(HEADER_LABELS.get(k, k), small), Paragraph(str(header_text(k, ex.header[k].value)), base)]
           for k in HEADER_KEYS if ex.header.get(k) is not None and ex.header[k].value]
    if hdr:
        story.append(Paragraph("Document details", h2))
        t = Table(hdr, colWidths=[42 * mm, 140 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 2)]))
        story.append(t)

    if ex.line_items:
        story.append(Paragraph("Line items", h2))
        data = [[Paragraph(f"<b>{h}</b>", small) for h in ("#", "Item", "Qty", "Unit", "Rate", "Tax", "Amount", "Category", "Status")]]
        for li in ex.line_items:
            name = li.name.value or "—"
            if li.description.value:
                name += f"<br/><font size=7 color='#666666'>{li.description.value}</font>"
            data.append([
                str(li.index + 1), Paragraph(name, base), li.quantity.value or "", li.unit.value or "",
                _money(li.rate.value, None), f"{li.tax_rate.value}%" if li.tax_rate.value else "", _money(li.amount.value, None),
                li.category.value or "", Paragraph(li.status.replace("_", " "), small),
            ])
        t = Table(data, colWidths=[7 * mm, 58 * mm, 12 * mm, 13 * mm, 22 * mm, 12 * mm, 24 * mm, 18 * mm, 16 * mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), reg), ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2F8")), ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#1F3A68")),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#DDDDDD")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (2, 1), (2, -1), "RIGHT"), ("ALIGN", (4, 1), (6, -1), "RIGHT"),
        ]))
        story.append(t)

    tot: list[list[str]] = []
    for k in SUMMARY_KEYS:
        if k == "tax_total":  # individual tax lines sit just before the total-tax / grand-total rows
            tot.extend([tx.label.value or tx.tax_type or "Tax", _money(tx.amount.value, cur)] for tx in ex.taxes)
        f = ex.summary.get(k)
        if f is not None and f.value is not None:
            tot.append([SUMMARY_LABELS[k], _money(f.value, cur)])
    if tot:
        story.append(Paragraph("Totals", h2))
        t = Table(tot, colWidths=[50 * mm, 40 * mm], hAlign="RIGHT")
        t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), reg), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                               ("LINEABOVE", (0, -1), (-1, -1), 0.4, colors.grey)]))
        story.append(t)

    if ex.validation and ex.validation.checks:
        v = ex.validation
        story.append(Paragraph(f"Validation — {v.status.upper()} (extracted {_money(v.extracted_total, cur)}, "
                               f"recalculated {_money(v.recalculated_total, cur)})", h2))
        data = [[Paragraph(f"<b>{h}</b>", small) for h in ("Check", "Status", "Detail")]]
        for c in v.checks:
            data.append([Paragraph(c.label, base), Paragraph(f"<font color='{status_color.get(c.status, '#333333')}'><b>{c.status}</b></font>", base),
                         Paragraph(c.message, small)])
        t = Table(data, colWidths=[50 * mm, 18 * mm, 114 * mm], repeatRows=1)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD"))]))
        story.append(t)
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Generated by ScanWise AI. Values are transcribed from the source document; totals are recomputed with "
                           "exact decimal arithmetic. Items marked 'needs review' were not confirmed by a person.", small))
    doc.build(story)
    return out.getvalue()


def pricebook_pdf(rows: list[list]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    reg, bold = _font()
    base = ParagraphStyle("b", fontName=reg, fontSize=8.5, leading=11)
    h1 = ParagraphStyle("h1", parent=base, fontName=bold, fontSize=16, leading=20, spaceAfter=8)
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title="Price Book")
    data = [["Item", "Category", "Rate", "Unit", "Tax", "SKU"]] + [[Paragraph(str(r[0]), base)] + [("" if c is None else str(c)) for c in r[1:]] for r in rows]
    t = Table(data, colWidths=[70 * mm, 24 * mm, 30 * mm, 18 * mm, 18 * mm, 22 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), reg), ("FONTNAME", (0, 0), (-1, 0), bold), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2F8")), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    doc.build([Paragraph("Price Book", h1), t])
    return out.getvalue()
