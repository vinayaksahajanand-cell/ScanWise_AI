"""Deterministic Decimal validation engine."""

from app.pipeline import finance
from app.schemas.extraction import CurrencyInfo, ExtractedField, ExtractionResult, LineItem, TaxLine


def money(v):
    return ExtractedField(kind="money", value=v, raw=v, extracted_value=v)


def item(i, name, qty, rate, amount, tax_rate=None):
    li = LineItem(id=f"li{i}", index=i)
    li.name = ExtractedField(kind="text", value=name)
    li.quantity = ExtractedField(kind="number", value=qty)
    li.rate = money(rate)
    li.amount = money(amount)
    if tax_rate:
        li.tax_rate = ExtractedField(kind="percent", value=tax_rate)
    return li


def tax(i, label, ttype, rate, amount):
    t = TaxLine(id=f"tx{i}", tax_type=ttype)
    t.label = ExtractedField(kind="text", value=label)
    t.rate = ExtractedField(kind="percent", value=rate)
    t.amount = money(amount)
    return t


def doc(items, taxes=(), currency="USD", **summary):
    ex = ExtractionResult(currency=CurrencyInfo(code=currency, minor_units=0 if currency == "JPY" else 2))
    ex.line_items = list(items)
    ex.taxes = list(taxes)
    ex.ensure_keys()
    for k, v in summary.items():
        ex.summary[k] = money(v)
    return ex


def statuses(report):
    return {c.label: c.status for c in report.checks}


def test_simple_invoice_reconciles():
    ex = doc([item(0, "AC inspection", "1", "40.00", "40.00"), item(1, "Repair labor", "2", "80.00", "160.00")],
             [tax(0, "Tax 10%", "TAX", "10", "20.00")], subtotal="200.00", grand_total="220.00")
    r = finance.validate(ex)
    assert r.status == "pass"
    assert r.recalculated_total == "220.00" and r.difference == "0.00"
    assert all(f.validated for f in (ex.summary["grand_total"], ex.line_items[1].amount))


def test_tax_applying_to_a_subset_of_items_is_explained():
    # The user's sample: CGST/SGST 9% only on the first item, IGST 18% only on labour.
    ex = doc([item(0, "Wiring and rewiring", "1", "4500.00", "4500.00"), item(1, "Labour charge", "2", "600.00", "1200.00")],
             [tax(0, "CGST 9%", "CGST", "9", "405.00"), tax(1, "SGST 9%", "SGST", "9", "405.00"), tax(2, "IGST 18%", "IGST", "18", "216.00")],
             currency="INR", subtotal="5700.00", grand_total="6726.00", amount_paid="-6726.00", balance_due="0.00")
    r = finance.validate(ex)
    st = statuses(r)
    assert st["CGST 9%"] == st["SGST 9%"] == st["IGST 18%"] == "explained"
    assert ex.taxes[0].applies_to == ["li0"] and ex.taxes[2].applies_to == ["li1"]
    assert st["Grand total"] == "pass" and st["Total − paid = balance"] == "pass"
    assert r.status == "warning"  # mixed CGST/SGST + IGST is flagged as unusual
    assert not any(c.status == "fail" for c in r.checks)


def test_tax_inclusive_prices_are_detected():
    ex = doc([item(0, "Widget", "1", "120.00", "120.00")], [tax(0, "VAT 20%", "VAT", "20", "20.00")],
             currency="GBP", subtotal="120.00", grand_total="120.00")
    r = finance.validate(ex)
    assert r.prices_include_tax is True
    assert r.status in ("pass", "warning")


def test_line_misread_digit_gets_a_suggestion_but_is_not_changed():
    ex = doc([item(0, "Labour", "2", "600.00", "1700.00")], subtotal="1200.00", grand_total="1200.00")
    r = finance.validate(ex)
    line = next(c for c in r.checks if c.category == "line")
    assert line.status == "fail"
    assert any(s.path == "line_items.li0.amount" and s.suggested == "1200.00" for s in line.suggestions)
    assert ex.line_items[0].amount.value == "1700.00"  # never silently corrected


def test_currency_symbol_read_as_seven_is_explained():
    ex = doc([item(0, "Wiring", "1", "6726.00", "6726.00")], currency="INR", subtotal="6726.00",
             grand_total="6726.00", amount_paid="-76726.00", balance_due="0.00")
    r = finance.validate(ex)
    bal = next(c for c in r.checks if c.category == "balance")
    assert bal.status == "fail"
    s = bal.suggestions[0]
    assert s.path == "summary.amount_paid" and s.suggested == "-6726.00" and "currency symbol" in s.reason
    # only the suspicious field is blamed; independently confirmed totals stay valid
    assert ex.summary["amount_paid"].validated is False
    assert ex.summary["grand_total"].validated is True


def test_rounding_difference_is_a_warning():
    ex = doc([item(0, "A", "1", "100.00", "100.00")], subtotal="100.00", grand_total="100.40")
    r = finance.validate(ex)
    assert statuses(r)["Grand total"] == "warning"


def test_unexplained_total_mismatch_fails_without_fake_suggestion():
    ex = doc([item(0, "A", "1", "100.00", "100.00")], subtotal="100.00", grand_total="153.20")
    r = finance.validate(ex)
    total = next(c for c in r.checks if c.category == "total")
    assert total.status == "fail" and total.difference == "53.20"
    assert r.status == "fail"


def test_zero_decimal_currency():
    ex = doc([item(0, "Ramen", "2", "800", "1600")], [tax(0, "Tax 10%", "TAX", "10", "160")],
             currency="JPY", subtotal="1600", grand_total="1760")
    assert finance.validate(ex).status == "pass"


def test_due_date_before_issue_date_warns():
    ex = doc([item(0, "A", "1", "10.00", "10.00")], subtotal="10.00", grand_total="10.00")
    ex.header["document_date"] = ExtractedField(kind="date", value="2026-09-24")
    ex.header["due_date"] = ExtractedField(kind="date", value="2026-09-01")
    r = finance.validate(ex)
    assert statuses(r).get("Due date") == "warning"
