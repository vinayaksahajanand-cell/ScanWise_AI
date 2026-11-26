"""Clients API (the New Client form) and the scan -> review/correct -> save flow."""

import pytest

from . import card_images as ci


def test_name_is_required(client):
    assert client.post("/api/clients", json={}).status_code == 422
    for blank in ("", "   ", "\n\t"):
        r = client.post("/api/clients", json={"name": blank})
        assert r.status_code == 422, blank
        assert "Name is required" in r.text


def test_create_with_only_a_name(client):
    r = client.post("/api/clients", json={"name": "  Jane   Roe "})
    assert r.status_code == 201
    c = r.json()
    assert c["name"] == "Jane Roe" and c["source"] == "manual"
    assert c["company"] is None and c["email"] is None
    assert client.get(f"/api/clients/{c['id']}").json() == c


def test_create_with_all_fields_and_search(client):
    body = {"name": "John Smith", "company": "ABC Technologies", "email": "john@abctech.com", "phone": "+1 555 123 4567",
            "job_title": "Sales Manager", "website": "www.abctech.com", "street": "123 Main Street", "city": "New York",
            "state": "NY", "zip_code": "10001", "country": "USA", "tax_id": "12-3456789", "source": "business_card"}
    c = client.post("/api/clients", json=body).json()
    for k, v in body.items():
        assert c[k] == v, k
    found = client.get("/api/clients", params={"q": "abctech"}).json()
    assert found["total"] >= 1 and any(i["id"] == c["id"] for i in found["items"])
    assert client.get("/api/clients", params={"q": "zzz-no-such-client"}).json() == {"total": 0, "items": []}


def test_empty_optional_fields_are_stored_as_null(client):
    c = client.post("/api/clients", json={"name": "A B", "company": "  ", "email": "", "tax_id": None}).json()
    assert c["company"] is None and c["email"] is None and c["tax_id"] is None


@pytest.mark.parametrize("field,value", [("email", "not-an-email"), ("email", "a@b"), ("phone", "12345"),
                                         ("phone", "call me maybe"), ("source", "carrier-pigeon"), ("company", "x" * 300)])
def test_invalid_values_are_rejected(client, field, value):
    r = client.post("/api/clients", json={"name": "Test", field: value})
    assert r.status_code == 422, (field, value, r.text)


def test_manual_correction_before_saving(client):
    """Scan -> the user fixes what the scan got wrong -> Add. The corrected values are what is stored."""
    scan = client.post("/api/business-cards/extract", files=[("files", ("c.png", ci.make_card(ci.FULL_FRONT), "image/png"))],
                       data={"llm_mode": "off"}).json()
    assert scan["name"] == "John Smith"
    total_before = client.get("/api/clients").json()["total"]  # scanning alone created no client
    corrected = {
        "name": "Johnathan Smith",  # user fixes the name
        "company": scan["company"], "email": scan["email"], "job_title": scan["job_title"],
        "phone": "+1 555 765 4321",  # and the phone
        "street": scan["address"]["street"], "city": scan["address"]["city"], "state": scan["address"]["state"],
        "zip_code": scan["address"]["zip_code"], "tax_id": "98-7654321",  # and fills in a field the card lacked
        "source": "business_card",
    }
    saved = client.post("/api/clients", json=corrected).json()
    assert saved["name"] == "Johnathan Smith" and saved["phone"] == "+1 555 765 4321" and saved["tax_id"] == "98-7654321"
    assert saved["email"] == scan["email"] and saved["source"] == "business_card"
    assert client.get("/api/clients").json()["total"] == total_before + 1


def test_add_requires_a_name_even_after_a_scan_without_one(client):
    scan = client.post("/api/business-cards/extract", files=[("files", ("c.png", ci.make_card([("hello@example.com", 60, 200, 70)]), "image/png"))],
                       data={"llm_mode": "off"}).json()
    assert scan["name"] is None and "name" in scan["missing_fields"]
    assert client.post("/api/clients", json={"name": scan["name"] or "", "email": scan["email"]}).status_code == 422
    assert client.post("/api/clients", json={"name": "Typed By User", "email": scan["email"]}).status_code == 201


def test_update_and_delete(client):
    c = client.post("/api/clients", json={"name": "Old Name", "email": "old@example.com"}).json()
    u = client.patch(f"/api/clients/{c['id']}", json={"name": "New Name", "city": " Pune "}).json()
    assert u["name"] == "New Name" and u["city"] == "Pune" and u["email"] == "old@example.com"
    assert client.patch(f"/api/clients/{c['id']}", json={"name": "  "}).status_code == 422
    assert client.patch(f"/api/clients/{c['id']}", json={"email": "bad"}).status_code == 422
    assert client.patch(f"/api/clients/{c['id']}", json={"email": None}).json()["email"] is None
    assert client.delete(f"/api/clients/{c['id']}").status_code == 204
    assert client.get(f"/api/clients/{c['id']}").status_code == 404
    assert client.delete(f"/api/clients/{c['id']}").status_code == 404
    assert client.patch("/api/clients/nope", json={"name": "x"}).status_code == 404
