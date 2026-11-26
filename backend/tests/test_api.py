"""REST API: upload -> process -> review -> approve -> export, price book, settings."""

import io
import json

from .conftest import FIXTURES, wait_for


def upload(client, name, **form):
    with open(FIXTURES / name, "rb") as fh:
        r = client.post("/api/documents", files=[("files", (name, fh, "image/png"))], data=form)
    assert r.status_code == 201, r.text
    return r.json()["documents"][0]["id"]


def test_health_and_system(client):
    assert client.get("/api/health").json()["status"] == "ok"
    s = client.get("/api/system").json()
    assert s["ocr"]["engine"] == "rapidocr"
    assert all("key" not in json.dumps(p).lower() for p in s["llm"]["providers"])  # never leaks secrets


def test_rejects_bad_uploads(client):
    r = client.post("/api/documents", files=[("files", ("evil.exe", io.BytesIO(b"MZ\x90\x00" * 100), "application/octet-stream"))])
    assert r.status_code == 422 and "unsupported file type" in r.text
    r = client.post("/api/documents", files=[("files", ("empty.png", io.BytesIO(b""), "image/png"))])
    assert r.status_code == 422


def test_full_review_flow(client):
    doc_id = upload(client, "sample_invoice_inr.png")
    assert wait_for(client, doc_id)["status"] in ("review", "ready")
    d = client.get(f"/api/documents/{doc_id}").json()
    ex = d["extraction"]
    assert d["document_number"] == "INV-00001" and d["currency"] == "INR" and d["grand_total"] == "6726.00"
    assert ex["review"]["total_fields"] > 30

    tok = client.get(f"/api/documents/{doc_id}/pages/1/tokens").json()
    assert len(tok["tokens"]) > 40 and all(0 <= t["bbox"][0] <= 1 for t in tok["tokens"])
    img = client.get(d["pages"][0]["image_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    # invalid correction is rejected, valid one is applied + audited + revalidated
    assert client.patch(f"/api/documents/{doc_id}/extraction", json={"updates": [{"path": "summary.grand_total", "value": "abc"}]}).status_code == 422
    item_id = ex["line_items"][1]["id"]
    r = client.patch(f"/api/documents/{doc_id}/extraction", json={"updates": [{"path": f"line_items.{item_id}.rate", "value": "650"}]}).json()
    li = r["extraction"]["line_items"][1]
    assert li["rate"]["value"] == "650.00" and li["rate"]["extracted_value"] == "600.00" and li["rate"]["status"] == "verified"
    assert any(c["status"] == "fail" for c in r["extraction"]["validation"]["checks"])  # 2 x 650 != 1200
    r = client.patch(f"/api/documents/{doc_id}/extraction", json={"updates": [{"path": f"line_items.{item_id}.rate", "value": "600.00"}]}).json()
    assert not any(c["status"] == "fail" for c in r["extraction"]["validation"]["checks"])

    # add + delete a row
    added = client.post(f"/api/documents/{doc_id}/line-items", json={"after_id": item_id}).json()
    assert len(added["extraction"]["line_items"]) == 3
    gone = client.delete(f"/api/documents/{doc_id}/line-items/{added['new_id']}").json()
    assert len(gone["extraction"]["line_items"]) == 2

    # approval: blocked while issues remain, allowed when acknowledged
    blocked = client.post(f"/api/documents/{doc_id}/approve", json={"acknowledge": False})
    if blocked.status_code == 409:
        assert blocked.json()["detail"]["reasons"]
        ok = client.post(f"/api/documents/{doc_id}/approve", json={"acknowledge": True})
    else:
        ok = blocked
    assert ok.status_code == 200 and ok.json()["document"]["status"] == "approved"

    for fmt, ctype in (("json", "application/json"), ("csv", "text/csv"), ("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
        r = client.get(f"/api/documents/{doc_id}/export", params={"format": fmt})
        assert r.status_code == 200 and ctype in r.headers["content-type"] and len(r.content) > 300
    csv_text = client.get(f"/api/documents/{doc_id}/export", params={"format": "csv"}).content.decode("utf-8-sig")
    assert "Wiring and rewiring" in csv_text and "4500.00" in csv_text

    actions = [a["action"] for a in client.get(f"/api/documents/{doc_id}/audit").json()]
    assert {"created", "processed", "field_updated", "line_added", "line_deleted", "approved"} <= set(actions)


def test_price_book_flow(client):
    doc_id = upload(client, "sample_price_list_usd.png")
    wait_for(client, doc_id)
    staged = client.post("/api/pricebook/stage", json={"document_id": doc_id}).json()
    assert len(staged) == 11
    res = client.post("/api/pricebook/commit", json={"decisions": [{"data": s["data"], "action": "create", "target_id": None} for s in staged]}).json()
    assert res["created"] == 11

    items = client.get("/api/pricebook").json()["items"]
    ac = next(i for i in items if i["name"] == "AC inspection")
    assert ac["rate"] == "40.00" and ac["unit"] == "visit" and ac["category"] == "Services" and ac["currency"] == "USD"
    assert [i["name"] for i in client.get("/api/pricebook", params={"category": "Materials"}).json()["items"]] == ["Replacement filter"]
    assert len(client.get("/api/pricebook", params={"q": "labor"}).json()["items"]) == 2

    # duplicate detection: suggested, never auto-merged
    dups = client.post("/api/pricebook/check-duplicates", json={"name": "A/C Inspections", "unit": "visit"}).json()
    assert dups and dups[0]["name"] == "AC inspection"
    restaged = client.post("/api/pricebook/stage", json={"document_id": doc_id}).json()
    assert {s["suggested_action"] for s in restaged} == {"skip"}

    # rate change keeps history
    upd = client.patch(f"/api/pricebook/{ac['id']}", json={"name": "AC inspection", "rate": "45", "currency": "USD", "unit": "visit",
                                                          "category": "Services", "tax_mode": "rate", "tax_rate": "10"}).json()
    assert upd["rate"] == "45.00" and [h["rate"] for h in upd["history"]][:2] == ["45.00", "40.00"]

    # bulk import with duplicate detection
    csv_data = "Item,Rate,Unit,Category,Tax\nDuct cleaning,175,job,Services,10%\nCopper pipe,3.20,ft,Materials,\n"
    imp = client.post("/api/pricebook/import", files=[("file", ("items.csv", io.BytesIO(csv_data.encode()), "text/csv"))]).json()
    by_name = {s["data"]["name"]: s for s in imp}
    assert by_name["Duct cleaning"]["suggested_action"] == "update" and by_name["Copper pipe"]["suggested_action"] == "create"

    for fmt in ("csv", "xlsx", "json", "pdf"):
        assert client.get("/api/pricebook/export", params={"format": fmt}).status_code == 200


def test_settings_dashboard_and_line_items(client):
    s = client.get("/api/settings").json()
    s["llm_mode"] = "fallback"
    s["tax_presets"] = ["18", "12", "5"]
    assert client.put("/api/settings", json=s).json()["tax_presets"] == ["18", "12", "5"]
    assert client.put("/api/settings", json={**s, "llm_mode": "sometimes"}).status_code == 422
    d = client.get("/api/dashboard").json()
    assert d["total_documents"] >= 1 and d["line_items"] >= 2
    rows = client.get("/api/line-items", params={"category": "Materials"}).json()["items"]
    assert all(r["category"] == "Materials" for r in rows)
    assert client.get("/api/line-items/export", params={"format": "xlsx"}).status_code == 200
