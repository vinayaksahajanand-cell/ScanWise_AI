from decimal import Decimal

from app.pipeline.normalize.category import classify_item
from app.pipeline.normalize.currency import detect_currency
from app.pipeline.normalize.dates import parse_date
from app.pipeline.normalize.tax import parse_tax_label, tax_mode_from_rate
from app.pipeline.normalize.units import normalize_unit
from app.pipeline.rules import canon_words


def test_currency_from_indian_context_without_symbol():
    texts = ["GSTIN 24AAAFP4821C1Z5", "CGST 9%", "SGST 9%", "Surat, Gujarat 395002, +91 98250 41872", "UPI: shop@sbi", "4,500.00"]
    c = detect_currency(texts)
    assert c.code == "INR" and not c.ambiguous


def test_dollar_alone_is_ambiguous():
    c = detect_currency(["$40.00 / visit", "$80.00/h", "$150.00 /job"])
    assert c.code == "USD"
    assert c.ambiguous and c.confidence == "medium"


def test_dollar_with_canadian_context():
    c = detect_currency(["$120.00", "$45.00", "Toronto, ON M5V 2T6", "GST/HST No 123456789"])
    assert c.code == "CAD"


def test_explicit_euro():
    c = detect_currency(["€ 1.200,00", "€ 300,00", "MwSt 19%"])
    assert c.code == "EUR" and c.confidence == "high"


def test_symbol_contradicted_by_context_is_flagged():
    # PP-OCR frequently reads ₹ as €. Indian GST context must turn this into a question.
    texts = ["€4,500.00", "€405.00", "€405.00", "CGST 9%", "SGST 9%", "IGST 18%", "GSTIN 24AAAFP4821C1Z5", "+91 98250 41872"]
    c = detect_currency(texts)
    assert c.ambiguous and c.confidence == "low"
    assert any("INR" in i and "EUR" in i for i in c.issues)


def test_units():
    assert normalize_unit("h").code == "hour"
    assert normalize_unit("Hrs").code == "hour"
    assert normalize_unit("ea").code == "each"
    assert normalize_unit("sq. ft").code == "sq_ft"
    assert normalize_unit("m²").code == "sq_m"
    assert normalize_unit("visit").code == "visit"
    u = normalize_unit("panel")
    assert u.code == "custom" and u.original == "panel"


def test_tax_labels():
    t = parse_tax_label("CGST 9%")
    assert t.tax_type == "CGST" and t.rate == Decimal("9")
    assert parse_tax_label("IGST @ 18%").tax_type == "IGST"
    assert parse_tax_label("VAT (20%) incl.").inclusive is True
    assert parse_tax_label("Exempt").exempt
    assert tax_mode_from_rate("Not taxable") == ("not_taxable", None)
    assert tax_mode_from_rate("10%") == ("rate", Decimal("10"))


def test_dates():
    assert parse_date("24 Sep 2026").iso == "2026-09-24"
    assert parse_date("Sep 24, 2026").iso == "2026-09-24"
    assert parse_date("2026-09-24").iso == "2026-09-24"
    assert parse_date("24/09/2026").iso == "2026-09-24"  # 24 > 12: unambiguous
    amb = parse_date("03/04/2026")
    assert amb.ambiguous and amb.iso == "2026-04-03"  # assumed DMY, flagged
    us = parse_date("03/04/2026", "MDY")
    assert us.iso == "2026-03-04" and us.ambiguous
    assert parse_date("31/02/2026").iso is None


def test_categories():
    assert classify_item("AC inspection").category == "Services"
    assert classify_item("Thermostat install").category == "Services"
    assert classify_item("Replacement filter").category == "Materials"
    assert classify_item("Copper pipe", unit_code="ft").category == "Materials"
    assert classify_item("Wiring and rewiring", unit_code="job").category == "Services"
    g = classify_item("Miscellaneous")
    assert g.category == "Other" and g.confidence == "low"


def test_label_canonicalisation_survives_ocr_noise():
    assert canon_words("Descrigtion").lower() == "description"
    assert canon_words("Subtota1").lower() == "subtotal"
    assert canon_words("lnvoice #").startswith("invoice")
    assert canon_words("Arnount").lower() == "amount"
    assert canon_words("Patel Electric Works") == "Patel Electric Works"  # names untouched
