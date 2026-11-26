"""Business-card scanner: upload validation, OCR path, vision path (scripted provider, no network),
failure handling, and isolation from the invoice pipeline."""

import pytest

from app.cards import service as card_service
from app.cards.schema import CARD_DSL, FIELD_KEYS
from app.pipeline.llm import providers
from app.pipeline.llm.providers import OpenAICompatibleProvider, ProviderError, VisionProvider

from . import card_images as ci

URL = "/api/business-cards/extract"


def post(client, *images, mode="off", names=None):
    files = [("files", ((names or [f"card{i}.png" for i in range(len(images))])[i], data, "image/png")) for i, data in enumerate(images)]
    return client.post(URL, files=files, data={"llm_mode": mode} if mode else {})


class ScriptedCardProvider(VisionProvider):
    name = "scripted"

    def __init__(self, *responses):
        super().__init__("card-model", 5, 0, 4)
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, messages, images, schema=None, schema_name="document_extraction"):
        self.calls.append({"images": len(images), "schema": schema, "schema_name": schema_name, "messages": list(messages)})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def vision(**fields):
    """A complete, schema-shaped vision answer. fields: key=(text, confidence) or key='text'."""
    out = {k: None for k in FIELD_KEYS}
    for k, v in fields.items():
        if k in ("other_emails", "other_phones", "observations", "language"):
            out[k] = v
        else:
            text, conf = (v, "high") if isinstance(v, str) else v
            out[k] = {"text": text, "confidence": conf}
    out.setdefault("other_emails", [])
    out.setdefault("other_phones", [])
    out.setdefault("observations", [])
    out.setdefault("language", "en")
    return out


@pytest.fixture(autouse=True)
def _fresh_cache():
    card_service.clear_cache()
    yield
    card_service.clear_cache()


@pytest.fixture
def use_provider(monkeypatch):
    def _use(provider):
        monkeypatch.setattr(card_service, "build_provider", lambda s: provider)
        return provider

    return _use


@pytest.fixture(scope="module")
def full_png():
    return ci.make_card(ci.FULL_FRONT)


# ----------------------------------------------------------------------------- OCR path
def test_all_fields_present(client, full_png):
    r = post(client, full_png)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "John Smith"
    assert d["company"] == "ABC Technologies Inc."
    assert d["job_title"] == "Sales Manager"
    assert d["email"] == "john.smith@abctech.com"
    assert "".join(c for c in d["phone"] if c.isdigit()) == "15551234567"
    assert d["website"] == "www.abctech.com"
    assert d["address"] == {"street": "123 Main Street, Suite 400", "city": "New York", "state": "NY", "zip_code": "10001", "country": None}
    assert d["extractors"] == ["ocr"] and d["sides_read"] == 1
    assert set(d["missing_fields"]) == {"country", "tax_id"}
    assert d["confidence"]["email"] == "high"


def test_response_has_the_documented_shape(client, full_png):
    d = post(client, full_png).json()
    assert set(d) >= {"name", "company", "email", "phone", "address", "website", "job_title", "tax_id", "confidence",
                      "missing_fields", "uncertain_fields", "warnings", "other_emails", "other_phones", "extractors",
                      "sides_read", "processing_ms"}
    assert set(d["address"]) == {"street", "city", "state", "zip_code", "country"}
    assert d["uncertain_fields"] == [k for k in FIELD_KEYS if k in d["confidence"] and d["confidence"][k] != "high"]


def test_partial_card(client):
    d = post(client, ci.make_card(ci.PARTIAL)).json()
    assert d["name"] == "Maria Garcia" and d["email"] == "maria.garcia@example.org"
    assert d["phone"] is None and d["company"] is None and d["address"]["street"] is None
    assert {"phone", "company", "street", "city", "state", "zip_code", "tax_id"} <= set(d["missing_fields"])


def test_multiple_phones_and_emails(client):
    d = post(client, ci.make_card(ci.MULTI)).json()
    assert "".join(c for c in d["phone"] if c.isdigit()) == "919820012345"  # the mobile one
    assert [("".join(c for c in p if c.isdigit())) for p in d["other_phones"]] == ["02223456789"]  # fax excluded
    assert d["email"] == "rakesh@patelelectric.in" and d["other_emails"] == ["sales@patelelectric.in"]


def test_dark_jpeg_card_and_rotated_card(client):
    dark = ci.make_card(ci.FULL_FRONT, bg=(18, 32, 70), fg=(240, 240, 240), fmt="JPEG")
    rotated = ci.make_card(ci.FULL_FRONT, rotate=90)
    for data, name, kind in ((dark, "dark.jpg", "image/jpeg"), (rotated, "rot.png", "image/png")):
        r = client.post(URL, files=[("files", (name, data, kind))], data={"llm_mode": "off"})
        assert r.status_code == 200, (name, r.text)
        d = r.json()
        assert d["email"] == "john.smith@abctech.com" and d["address"]["zip_code"] == "10001", name


def test_front_and_back_are_merged(client, full_png):
    d = post(client, full_png, ci.make_card(ci.BACK)).json()
    assert d["sides_read"] == 2
    assert d["name"] == "John Smith" and d["tax_id"] == "12-3456789"  # tax id is only on the back
    assert d["other_phones"] == []  # the fax on the back is ignored


# ----------------------------------------------------------------------------- every supported file type
def _read_ok(client, data, name, **kw):
    r = client.post(URL, files=[("files", (name, data, "application/octet-stream"))], data={"llm_mode": "off"})
    assert r.status_code == 200, (name, r.text)
    d = r.json()
    assert d["name"] == "John Smith" and d["email"] == "john.smith@abctech.com" and d["address"]["zip_code"] == "10001", name
    return d


@pytest.mark.parametrize("fmt,ext", [("JPEG", "jpg"), ("JPEG", "jpeg"), ("PNG", "png"), ("WEBP", "webp"), ("BMP", "bmp"), ("TIFF", "tif"), ("GIF", "gif")])
def test_image_formats(client, fmt, ext):
    _read_ok(client, ci.make_card(ci.FULL_FRONT, fmt=fmt), f"card.{ext}")


def test_heic(client):
    pytest.importorskip("pillow_heif")
    try:
        data = ci.make_card(ci.FULL_FRONT, fmt="HEIF")
    except Exception:  # noqa: BLE001
        pytest.skip("this Pillow/pillow-heif build cannot encode HEIC")
    _read_ok(client, data, "IMG_0001.HEIC")


def test_the_file_name_does_not_decide_the_type(client):
    _read_ok(client, ci.make_card(ci.FULL_FRONT, fmt="PNG"), "photo.pdf")  # a PNG called .pdf
    _read_ok(client, ci.make_card(ci.FULL_FRONT, fmt="JPEG"), "no-extension")


def test_scanned_pdf(client):
    d = _read_ok(client, ci.make_pdf(ci.FULL_FRONT), "card.pdf")
    assert d["sides_read"] == 1


def test_digital_pdf_with_a_text_layer(client):
    d = _read_ok(client, ci.make_digital_pdf(ci.DIGITAL_FRONT), "card.pdf")
    assert d["warnings"] == []  # a small page is not mistaken for a low-resolution photo


def test_two_page_pdf_is_front_and_back(client):
    d = client.post(URL, files=[("files", ("both.pdf", ci.make_pdf(ci.FULL_FRONT, ci.BACK), "application/pdf"))], data={"llm_mode": "off"}).json()
    assert d["sides_read"] == 2 and d["name"] == "John Smith" and d["tax_id"] == "12-3456789"
    d = client.post(URL, files=[("files", ("both.pdf", ci.make_digital_pdf(ci.DIGITAL_FRONT, ci.DIGITAL_BACK), "application/pdf"))], data={"llm_mode": "off"}).json()
    assert d["sides_read"] == 2 and d["tax_id"] == "12-3456789"


def test_pdf_with_more_than_two_pages_reads_two_and_says_so(client):
    d = client.post(URL, files=[("files", ("many.pdf", ci.make_pdf(ci.FULL_FRONT, ci.BACK, ci.PARTIAL), "application/pdf"))], data={"llm_mode": "off"}).json()
    assert d["sides_read"] == 2 and any("first 2 pages" in w for w in d["warnings"])


def test_front_image_plus_back_pdf(client, full_png):
    r = client.post(URL, files=[("files", ("f.png", full_png, "image/png")), ("files", ("b.pdf", ci.make_pdf(ci.BACK), "application/pdf"))], data={"llm_mode": "off"})
    d = r.json()
    assert r.status_code == 200 and d["sides_read"] == 2 and d["tax_id"] == "12-3456789"


def test_two_files_use_only_the_first_page_of_each(client):
    two = ci.make_pdf(ci.FULL_FRONT, ci.BACK)
    r = client.post(URL, files=[("files", ("a.pdf", two, "application/pdf")), ("files", ("b.pdf", two, "application/pdf"))], data={"llm_mode": "off"})
    d = r.json()
    assert r.status_code == 200 and d["sides_read"] == 2 and d["tax_id"] is None  # page 2 (the tax id) was not read
    assert any("first page" in w for w in d["warnings"])


def test_password_protected_pdf_is_refused_clearly(client):
    r = post(client, ci.make_digital_pdf(ci.DIGITAL_FRONT, encrypt="secret"), names=["locked.pdf"])
    assert r.status_code == 422 and "password" in r.json()["detail"]


def test_pdf_without_any_text_is_a_clean_error(client):
    r = post(client, ci.make_pdf([]), names=["blank.pdf"])
    assert r.status_code == 422 and "No contact details" in r.json()["detail"]


# ----------------------------------------------------------------------------- vision path
def test_front_and_back_use_one_vision_call(client, full_png, use_provider):
    p = use_provider(ScriptedCardProvider(vision(
        name="John Smith", company="ABC Technologies Inc.", email="john.smith@abctech.com", tax_id="12-3456789")))
    d = post(client, full_png, ci.make_card(ci.BACK), mode="always").json()
    assert len(p.calls) == 1, "front and back must share a single vision request"
    assert p.calls[0]["images"] == 2
    assert p.calls[0]["schema_name"] == "business_card" and p.calls[0]["schema"] is CARD_DSL
    assert d["tax_id"] == "12-3456789" and d["extractors"][0] == "ocr" and d["extractors"][1].startswith("vision:scripted/card-model")


def test_vision_values_found_on_the_card_are_high_confidence(client, full_png, use_provider):
    use_provider(ScriptedCardProvider(vision(name="John Smith", email="john.smith@abctech.com", company="ABC Technologies Inc.")))
    d = post(client, full_png, mode="always").json()
    assert d["confidence"]["name"] == "high" and d["confidence"]["email"] == "high" and d["confidence"]["company"] == "high"


def test_vision_value_not_on_the_card_is_flagged_not_trusted(client, use_provider):
    use_provider(ScriptedCardProvider(vision(name="Maria Garcia", phone="+1 212 555 0100")))  # the phone is not printed
    d = post(client, ci.make_card(ci.PARTIAL), mode="always").json()
    assert d["confidence"]["name"] == "high"
    assert d["confidence"]["phone"] == "low" and "phone" in d["uncertain_fields"]


def test_vision_and_ocr_disagreement_is_reported(client, full_png, use_provider):
    use_provider(ScriptedCardProvider(vision(email="john.smyth@abctech.com")))  # a misreading
    d = post(client, full_png, mode="always").json()
    assert d["email"] == "john.smith@abctech.com"  # the value actually on the card (OCR) wins
    assert d["confidence"]["email"] == "medium" and any("email" in w for w in d["warnings"])


def test_malformed_vision_output_gets_one_repair_retry(client, full_png, use_provider):
    p = use_provider(ScriptedCardProvider({"name": "not-an-object"}, vision(name="John Smith")))
    d = post(client, full_png, mode="always").json()
    assert len(p.calls) == 2 and d["name"] == "John Smith"
    assert any(role == "assistant" for role, _ in p.calls[1]["messages"])  # the repair prompt was sent


def test_identical_rescan_does_not_call_the_vision_model_again(client, full_png, use_provider):
    p = use_provider(ScriptedCardProvider(vision(name="John Smith")))
    post(client, full_png, mode="always")
    second = post(client, full_png, mode="always").json()
    assert len(p.calls) == 1
    assert "(cached)" in second["extractors"][-1]


def test_llm_mode_off_never_calls_the_model(client, full_png, use_provider):
    p = use_provider(ScriptedCardProvider(vision(name="X")))
    post(client, full_png, mode="off")
    assert p.calls == []


def test_fallback_mode_calls_vision_only_for_weak_ocr(client, full_png, use_provider):
    p = use_provider(ScriptedCardProvider(vision(name="Hello Person", email="hello@example.com")))
    post(client, full_png, mode="fallback")  # OCR got everything: no call
    assert p.calls == []
    post(client, ci.make_card([("hello@example.com", 60, 200, 70)]), mode="fallback")  # no name, one line: weak
    assert len(p.calls) == 1


def test_default_mode_comes_from_the_user_setting(client, full_png, use_provider):
    p = use_provider(ScriptedCardProvider(vision(name="John Smith")))
    client.put("/api/settings", json={"llm_mode": "off"})
    try:
        post(client, full_png, mode=None)
        assert p.calls == []
    finally:
        client.put("/api/settings", json={"llm_mode": "always"})


# ----------------------------------------------------------------------------- failures
def test_ai_failure_falls_back_to_ocr_with_a_clear_warning(client, full_png, use_provider):
    use_provider(ScriptedCardProvider(ProviderError('openai HTTP 429: {"error": {"code": "insufficient_quota"}}')))
    r = post(client, full_png, mode="always")
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "John Smith" and d["extractors"] == ["ocr"]
    assert any("no credit" in w for w in d["warnings"])
    assert not any("insufficient_quota" in w or "openai" in w for w in d["warnings"]), "raw provider errors must not reach the user"


def test_no_provider_configured_is_explained(client, full_png):
    d = post(client, full_png, mode="always").json()  # conftest configures no provider
    assert any("No AI vision provider" in w for w in d["warnings"])


def test_ai_failure_on_an_unreadable_card_is_a_clean_error(client, use_provider):
    use_provider(ScriptedCardProvider(ProviderError("openai HTTP 500")))
    r = post(client, ci.make_card([]), mode="always")
    assert r.status_code == 422 and "No contact details" in r.json()["detail"]


def test_blurry_photo_gives_a_helpful_error(client):
    r = post(client, ci.make_card(ci.FULL_FRONT, blur=30))
    assert r.status_code == 422
    assert "blurry" in r.json()["detail"]


def test_tiny_image_is_rejected(client):
    r = post(client, ci.make_card([("Hi", 20, 10, 10)], size=(250, 150)))
    assert r.status_code == 422 and "too small" in r.json()["detail"]


# ----------------------------------------------------------------------------- upload validation
@pytest.mark.parametrize("data,status", [
    (b"just some text", 415),
    (b"PK\x03\x04" + b"\x00" * 64, 415),  # a zip archive
    (b"MZ" + b"\x90\x00" * 40, 415),  # an executable
    (b"\x00\x01\x02\x03" * 32, 415),
    # supported formats whose content is damaged: a clear "could not be opened", not a crash
    (b"\x89PNG\r\n\x1a\n" + b"garbage" * 20, 422),
    (b"GIF89a" + b"\x00" * 64, 422),
    (b"%PDF-1.4\n" + b"0" * 64, 422),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64, 422),
    (b"", 400),
])
def test_invalid_uploads(client, data, status):
    r = post(client, data)
    assert r.status_code == status, r.text
    assert r.json()["detail"]


def test_unsupported_type_message_lists_the_supported_formats(client):
    detail = post(client, b"just some text").json()["detail"]
    for fmt in ("JPG", "PNG", "WEBP", "HEIC", "TIFF", "BMP", "GIF", "PDF"):
        assert fmt in detail


def test_too_many_images_and_no_images(client, full_png):
    assert post(client, full_png, full_png, full_png).status_code == 400
    assert client.post(URL, data={"llm_mode": "off"}).status_code == 422


def test_oversized_upload(client, full_png, settings, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    r = post(client, full_png)
    assert r.status_code == 413 and "larger than" in r.json()["detail"]


def test_extraction_saves_nothing(client, full_png):
    before = client.get("/api/clients").json()["total"]
    post(client, full_png)
    assert client.get("/api/clients").json()["total"] == before


# ----------------------------------------------------------------------------- isolation from invoices
class _Resp:
    def __init__(self, body):
        self.status_code, self.text, self.headers, self._b = 200, "{}", {}, body

    def json(self):
        return self._b


def _payloads(monkeypatch, **kwargs):
    seen = []

    class C:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None, json=None):
            seen.append(json)
            return _Resp({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(providers.httpx, "Client", C)
    OpenAICompatibleProvider("openai", "http://x/v1", "k", "gpt-4o", 5, 0, 4).complete("s", [("user", "u")], [], **kwargs)
    return seen[0]["response_format"]["json_schema"]


def test_invoice_extraction_still_uses_the_invoice_schema(monkeypatch):
    js = _payloads(monkeypatch)  # no schema argument: exactly what the invoice pipeline does
    assert js["name"] == "document_extraction"
    assert {"line_items", "taxes", "summary", "header"} <= set(js["schema"]["properties"])


def test_card_extraction_uses_its_own_schema(monkeypatch):
    js = _payloads(monkeypatch, schema=CARD_DSL, schema_name="business_card")
    assert js["name"] == "business_card"
    assert "line_items" not in js["schema"]["properties"] and "email" in js["schema"]["properties"]


def test_invoice_endpoints_are_unaffected(client):
    assert client.get("/api/documents").status_code == 200
    assert client.get("/api/dashboard").status_code == 200
    # a card upload never creates a document
    before = client.get("/api/documents").json()["total"]
    post(client, ci.make_card(ci.PARTIAL))
    assert client.get("/api/documents").json()["total"] == before
