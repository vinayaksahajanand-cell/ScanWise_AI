"""Business-card text rules: pure functions, no OCR, no network."""

import pytest

from app.cards.rules import Line, parse_card, parse_locality


def card(*rows, side=0):
    """rows: (text, glyph height, y) -> OCR-like lines."""
    return [Line(t, 0.97, h, y, 10, side) for t, h, y in rows]


def test_all_fields_us_card():
    p = parse_card(card(
        ("ABC Technologies Inc.", 44, 40), ("John Smith", 60, 120), ("Sales Manager", 30, 170),
        ("T: +1 (555) 123-4567", 24, 230), ("john.smith@abctech.com", 24, 262), ("www.abctech.com", 24, 294),
        ("123 Main Street, Suite 400", 24, 330), ("New York, NY 10001", 24, 362),
    ))
    assert p.values == {
        "name": "John Smith", "company": "ABC Technologies Inc.", "job_title": "Sales Manager",
        "phone": "+1 (555) 123-4567", "email": "john.smith@abctech.com", "website": "www.abctech.com",
        "street": "123 Main Street, Suite 400", "city": "New York", "state": "NY", "zip_code": "10001",
    }
    assert p.conf["email"] == "high" and p.conf["zip_code"] == "high"


def test_partial_card_returns_only_what_is_printed():
    p = parse_card(card(("Maria Garcia", 60, 100), ("maria@example.org", 24, 160)))
    assert p.values == {"name": "Maria Garcia", "email": "maria@example.org"}
    for absent in ("phone", "company", "street", "city", "zip_code", "tax_id", "website"):
        assert absent not in p.values


def test_multiple_phones_and_emails_pick_mobile_and_ignore_fax():
    p = parse_card(card(
        ("Rakesh Patel", 60, 100), ("O: 022 2345 6789", 24, 160), ("M: +91 98200 12345", 24, 190),
        ("Fax: 022 2345 0000", 24, 220), ("sales@patelelectric.in", 24, 250), ("rakesh@patelelectric.in", 24, 280),
    ))
    assert p.values["phone"] == "+91 98200 12345"  # mobile outranks the office line
    assert p.other_phones == ["022 2345 6789"]  # fax is never offered
    assert p.values["email"] == "sales@patelelectric.in" and p.other_emails == ["rakesh@patelelectric.in"]


def test_email_matching_the_website_domain_is_preferred():
    p = parse_card(card(("john@gmail.com", 24, 100), ("john@abctech.com", 24, 130), ("www.abctech.com", 24, 160)))
    assert p.values["email"] == "john@abctech.com" and p.other_emails == ["john@gmail.com"]


def test_website_is_not_derived_from_an_email_domain():
    p = parse_card(card(("Jane Roe", 60, 50), ("jane@startup.io", 24, 120)))
    assert "website" not in p.values


def test_email_with_spaces_around_at_sign():
    p = parse_card(card(("Jane Roe", 60, 50), ("jane . roe @ startup.io".replace(" . ", "."), 24, 120)))
    assert p.values["email"] == "jane.roe@startup.io"


@pytest.mark.parametrize("line,expected", [
    ("GSTIN: 27AAPFU0939F1ZV", "27AAPFU0939F1ZV"),
    ("27AAPFU0939F1ZV", "27AAPFU0939F1ZV"),  # bare GSTIN, recognised by its structure
    ("EIN: 12-3456789", "12-3456789"),
    ("VAT No. GB 123 4567 89", "GB 123 4567 89"),
    ("ABN 12 345 678 901", "12 345 678 901"),
    ("Tax ID: 98-7654321", "98-7654321"),
])
def test_tax_id_formats(line, expected):
    p = parse_card(card(("Some Person", 60, 50), (line, 22, 300)))
    assert p.values["tax_id"] == expected
    assert "phone" not in p.values  # its digits must not be mistaken for a phone number


def test_labels_inside_words_are_not_tax_ids():
    p = parse_card(card(("Panther Systems Pvt Ltd", 40, 40), ("Tina Brown", 60, 100)))
    assert "tax_id" not in p.values


@pytest.mark.parametrize("text,expect", [
    ("New York, NY 10001", dict(city="New York", state="NY", zip_code="10001")),
    ("123 Main St, Austin, TX 78701", dict(city="Austin", state="TX", zip_code="78701", street="123 Main St")),
    ("Toronto, ON M5V 2T6", dict(city="Toronto", state="ON", zip_code="M5V 2T6")),
    ("Mumbai - 400069, Maharashtra, India", dict(city="Mumbai", state="Maharashtra", zip_code="400069", country="India")),
    ("Plot 5, MIDC, Andheri East, Mumbai 400069", dict(city="Mumbai", zip_code="400069", street="Plot 5, MIDC, Andheri East")),
    ("14 Baker Street, London NW1 6XE", dict(city="London", zip_code="NW1 6XE", street="14 Baker Street")),
    ("Hauptstrasse 5, 10115 Berlin", dict(city="Berlin", zip_code="10115", street="Hauptstrasse 5")),
])
def test_address_layouts(text, expect):
    loc = parse_locality(text)
    assert loc is not None
    for k, v in expect.items():
        assert getattr(loc, k) == v, (k, loc)


def test_street_is_collected_from_the_lines_above_the_locality_line():
    p = parse_card(card(("Tower B, 5th Floor", 24, 300), ("22 Park Avenue", 24, 330), ("Boston, MA 02108", 24, 360)))
    assert p.values["street"] == "Tower B, 5th Floor, 22 Park Avenue"
    assert (p.values["city"], p.values["state"], p.values["zip_code"]) == ("Boston", "MA", "02108")


def test_phone_numbers_are_not_read_as_postal_codes_or_vice_versa():
    p = parse_card(card(("Tel: +91 98200 123456", 24, 100), ("Pune 411001", 24, 130)))
    assert p.values["phone"] == "+91 98200 123456"
    assert p.values["zip_code"] == "411001" and p.values["city"] == "Pune"


def test_title_and_company_are_not_mistaken_for_the_name():
    p = parse_card(card(("Rao Consulting Pvt. Ltd.", 44, 30), ("Chief Executive Officer", 30, 130), ("Anita Rao", 60, 90)))
    assert p.values["name"] == "Anita Rao"
    assert p.values["job_title"] == "Chief Executive Officer"
    assert p.values["company"] == "Rao Consulting Pvt. Ltd."


def test_a_title_containing_a_company_word_is_still_a_title():
    p = parse_card(card(("Emma Wilson", 60, 60), ("Studio Lead", 28, 110)))
    assert p.values["job_title"] == "Studio Lead" and p.values["name"] == "Emma Wilson"


def test_name_confirmed_by_email_is_high_confidence():
    p = parse_card(card(("John Smith", 60, 100), ("Sales Manager", 28, 150), ("john.smith@abctech.com", 24, 200)))
    assert p.conf["name"] == "high"


def test_low_ocr_confidence_lowers_the_reported_confidence():
    lines = [Line("jane@startup.io", 0.62, 24, 100, 10)]
    assert parse_card(lines).conf["email"] == "low"
    lines = [Line("jane@startup.io", 0.85, 24, 100, 10)]
    assert parse_card(lines).conf["email"] == "medium"


def test_text_without_contact_details_gives_nothing():
    assert parse_card(card(("Welcome", 30, 10), ("est. 1998", 20, 50))).values == {}
    assert parse_card([]).values == {}


def test_dates_and_years_are_not_phone_numbers():
    p = parse_card(card(("Jane Roe", 60, 50), ("Since 2015-04-12", 24, 120), ("Reg. 12/05/2024", 24, 150)))
    assert "phone" not in p.values
