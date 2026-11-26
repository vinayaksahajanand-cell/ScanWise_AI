"""Generate a synthetic invoice benchmark with exact ground truth.

Each document is produced in three variants:
  doc.pdf    digital PDF with a text layer
  scan.png   rasterised page (flat scan, 200 dpi)
  photo.jpg  phone-style photo: perspective, desk background, uneven light, blur, noise

Usage:  python -m benchmark.generate --count 24 --out benchmark/data
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.exports import _font

LOCALES = {
    "USD": {"sym": "$", "fmt": "us", "date": "{m:02d}/{d:02d}/{y}", "tax": ("SALES_TAX", "Sales Tax", ["8.25", "7", "6.5"]),
            "city": ["Austin, TX 78701", "Denver, CO 80202", "Boston, MA 02110"], "phone": "+1 512 555 01{n:02d}"},
    "INR": {"sym": "₹", "fmt": "in", "date": "{d} {mon} {y}", "tax": ("GST", "GST", ["18", "12"]),
            "city": ["Surat, Gujarat 395002", "Pune, Maharashtra 411001", "Jaipur, Rajasthan 302001"], "phone": "+91 98250 4{n:04d}"},
    "EUR": {"sym": "€", "fmt": "eu", "date": "{d:02d}.{m:02d}.{y}", "tax": ("VAT", "MwSt", ["19", "7"]),
            "city": ["10115 Berlin, Germany", "80331 München, Germany"], "phone": "+49 30 5550{n:03d}"},
    "GBP": {"sym": "£", "fmt": "us", "date": "{d} {mon} {y}", "tax": ("VAT", "VAT", ["20"]),
            "city": ["London W1U 3BW", "Manchester M1 1AE"], "phone": "+44 20 7946 0{n:03d}"},
    "AED": {"sym": "AED ", "fmt": "us", "date": "{d:02d}/{m:02d}/{y}", "tax": ("VAT", "VAT", ["5"]),
            "city": ["Dubai, UAE", "Abu Dhabi, UAE"], "phone": "+971 4 555 0{n:03d}"},
    "JPY": {"sym": "¥", "fmt": "jp", "date": "{y}-{m:02d}-{d:02d}", "tax": ("TAX", "Consumption Tax", ["10"]),
            "city": ["Tokyo, Japan 100-0001"], "phone": "+81 3 5555 0{n:03d}"},
}
VENDORS = ["Patel Electric Works", "Northwind HVAC Services", "Bright Spark Electrical", "CoolAir Mechanical", "Riverside Plumbing Co",
           "Summit Facility Care", "Orion Climate Systems", "Blue Line Maintenance"]
CUSTOMERS = ["Shree Krupa Developers", "Acme Office Parks", "Greenfield Developers", "Harbor View Hotel", "Lakeside Apartments",
             "Metro Clinic Group", "Sunrise Retail Ltd"]
ITEMS = [
    ("AC inspection", "visit", "Services", (35, 90)), ("Repair labor", "h", "Services", (45, 120)), ("Installation labor", "h", "Services", (50, 130)),
    ("Duct cleaning", "job", "Services", (120, 260)), ("Thermostat install", "job", "Services", (80, 160)), ("Emergency call-out", "visit", "Services", (60, 150)),
    ("Site survey", "job", "Services", (150, 400)), ("Consultation", "h", "Services", (90, 180)), ("Replacement filter", "ea", "Materials", (15, 60)),
    ("Copper pipe", "ft", "Materials", (2, 9)), ("Electrical cable", "m", "Materials", (1, 6)), ("Thermostat", "ea", "Materials", (40, 140)),
    ("Refrigerant R32", "kg", "Materials", (18, 45)), ("Mounting bracket", "set", "Materials", (12, 40)),
]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def dstr(v: Decimal) -> str:
    """Plain decimal text without exponent ('10', '2.5', '9') — Decimal.normalize() would give '1E+1'."""
    s = format(v, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def q2(v: Decimal, places: int) -> Decimal:
    return v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def fmt(v: Decimal, style: str, places: int) -> str:
    s = f"{abs(q2(v, places)):,.{places}f}"
    if style == "eu":
        s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    elif style == "in":
        whole, _, frac = s.replace(",", "").partition(".")
        if len(whole) > 3:
            head, tail = whole[:-3], whole[-3:]
            groups = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            whole = ",".join(groups + [tail])
        s = whole + (f".{frac}" if frac else "")
    return ("-" if v < 0 else "") + s


def money(v: Decimal, cur: str) -> str:
    L = LOCALES[cur]
    places = 0 if cur == "JPY" else 2
    s = fmt(v, L["fmt"], places)
    return f"{s} €" if cur == "EUR" else f"{L['sym']}{s}"


def make_doc(i: int, rng: random.Random) -> dict:
    cur = rng.choice(list(LOCALES))
    L = LOCALES[cur]
    places = 0 if cur == "JPY" else 2
    y, m, d = 2026, rng.randint(1, 10), rng.randint(1, 28)
    date_raw = L["date"].format(y=y, m=m, d=d, mon=MONTHS[m - 1])
    due_m = m + 1
    due_raw = L["date"].format(y=y, m=due_m, d=d, mon=MONTHS[due_m - 1])
    n_items = rng.randint(2, 7)
    picks = rng.sample(ITEMS, n_items)
    rows = []
    for name, unit, cat, (lo, hi) in picks:
        qty = Decimal(rng.choice(["1", "2", "3", "4", "1.5", "2.5", "10", "25"])) if unit in ("h", "ft", "m", "kg") else Decimal(rng.randint(1, 6))
        rate = Decimal(rng.randint(lo * 100, hi * 100)) / 100
        if cur == "JPY":
            rate = Decimal(int(rate * 100))
        elif cur == "INR":
            rate = Decimal(int(rate * 80))
        amount = q2(qty * rate, places)
        rows.append({"name": name, "unit": unit, "category": cat, "quantity": dstr(qty),
                     "rate": str(q2(rate, places)), "amount": str(amount)})
    subtotal = sum(Decimal(r["amount"]) for r in rows)
    ttype, tlabel, trates = L["tax"]
    rate = Decimal(rng.choice(trates))
    taxes = []
    if cur == "INR":
        half = rate / 2  # e.g. 18 -> 9
        a = q2(subtotal * half / 100, places)
        taxes = [{"label": f"CGST {dstr(half)}%", "rate": dstr(half), "amount": str(a)},
                 {"label": f"SGST {dstr(half)}%", "rate": dstr(half), "amount": str(a)}]
    else:
        a = q2(subtotal * rate / 100, places)
        taxes = [{"label": f"{tlabel} {dstr(rate)}%", "rate": dstr(rate), "amount": str(a)}]
    total = subtotal + sum(Decimal(t["amount"]) for t in taxes)
    vendor = rng.choice(VENDORS)
    customer = rng.choice(CUSTOMERS)
    return {
        "id": f"bm{i:03d}", "currency": cur, "document_number": f"INV-{2026}{i:03d}", "document_date_raw": date_raw,
        "document_date": f"{y}-{m:02d}-{d:02d}", "due_date": f"{y}-{due_m:02d}-{d:02d}", "due_date_raw": due_raw,
        "vendor_name": vendor, "vendor_address": f"{rng.randint(2, 99)} Market Road, {rng.choice(L['city'])}",
        "vendor_phone": L["phone"].format(n=i), "customer_name": customer, "items": rows, "taxes": taxes,
        "subtotal": str(q2(subtotal, places)), "grand_total": str(q2(total, places)), "template": rng.choice(["classic", "boxed"]),
    }


def render_pdf(doc: dict, path: Path) -> list[str]:
    """Draw the invoice; returns every string drawn (for CER/WER)."""
    reg, bold = _font()
    cur = doc["currency"]
    c = canvas.Canvas(str(path), pagesize=A4)
    W, H = A4
    drawn: list[str] = []

    def t(x, y, s, size=10, font=reg, right=False):
        c.setFont(font, size)
        (c.drawRightString if right else c.drawString)(x, y, s)
        drawn.append(s)

    t(50, H - 70, "INVOICE", 22, bold)
    t(360, H - 60, "Invoice #")
    t(545, H - 60, doc["document_number"], right=True)
    t(360, H - 75, "Date")
    t(545, H - 75, doc["document_date_raw"], right=True)
    t(360, H - 90, "Due date")
    t(545, H - 90, doc["due_date_raw"], right=True)
    t(50, H - 125, "From", 9, bold)
    t(50, H - 140, doc["vendor_name"], 11, bold)
    t(50, H - 154, doc["vendor_address"], 9)
    t(50, H - 167, doc["vendor_phone"], 9)
    t(320, H - 125, "Bill To", 9, bold)
    t(320, H - 140, doc["customer_name"], 11, bold)
    y = H - 215
    boxed = doc["template"] == "boxed"
    if boxed:
        c.setFillColorRGB(0.92, 0.94, 0.98)
        c.rect(45, y - 6, 505, 20, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
    for x, h, right in ((52, "Description", False), (330, "Qty", True), (360, "Unit", False), (455, "Rate", True), (545, "Amount", True)):
        t(x, y, h, 9.5, bold, right)
    c.line(45, y - 8, 550, y - 8)
    y -= 26
    for r in doc["items"]:
        t(52, y, r["name"], 10)
        t(330, y, r["quantity"], 10, right=True)
        t(360, y, r["unit"], 10)
        t(455, y, money(Decimal(r["rate"]), cur), 10, right=True)
        t(545, y, money(Decimal(r["amount"]), cur), 10, right=True)
        y -= 22
    c.line(45, y + 8, 550, y + 8)
    y -= 16
    t(380, y, "Subtotal")
    t(545, y, money(Decimal(doc["subtotal"]), cur), right=True)
    for tx in doc["taxes"]:
        y -= 16
        t(380, y, tx["label"])
        t(545, y, money(Decimal(tx["amount"]), cur), right=True)
    y -= 20
    t(380, y, "Total", 11, bold)
    t(545, y, money(Decimal(doc["grand_total"]), cur), 11, bold, right=True)
    t(50, 80, "Thank you for your business.", 9)
    c.save()
    return drawn


def photo_from(scan: np.ndarray, rng: random.Random) -> np.ndarray:
    """Phone-style capture: page on a desk, perspective, light falloff, blur and sensor noise."""
    h0, w0 = scan.shape[:2]
    W, H = 2400, 3200
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    tone = rng.choice([(60, 75, 95), (40, 40, 45), (95, 110, 120), (120, 90, 70)])
    grain = 14 * np.sin(xx / rng.uniform(6, 14) + 2 * np.sin(yy / 50.0))
    desk = np.stack([tone[0] + grain * 0.6, tone[1] + grain * 0.8, tone[2] + grain], -1)
    m = rng.uniform(0.06, 0.12)
    j = lambda: rng.uniform(-0.035, 0.035)  # noqa: E731
    dst = np.float32([[W * (m + j()), H * (m + j())], [W * (1 - m + j()), H * (m + j())],
                      [W * (1 - m + j()), H * (1 - m + j())], [W * (m + j()), H * (1 - m + j())]])
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [w0, 0], [w0, h0], [0, h0]]), dst)
    page = cv2.warpPerspective(scan, M, (W, H), flags=cv2.INTER_AREA).astype(np.float32)
    mask = cv2.warpPerspective(np.full((h0, w0), 255, np.uint8), M, (W, H)).astype(np.float32)[..., None] / 255
    img = desk * (1 - mask) + page * mask
    light = 0.8 + 0.25 * (1 - (xx / W * rng.uniform(0.2, 0.8) + yy / H * rng.uniform(0.2, 0.8)))
    img = np.clip(img * light[..., None], 0, 255).astype(np.uint8)
    img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.6, 1.3))
    noise = np.random.default_rng(rng.randint(0, 10**6)).normal(0, rng.uniform(2, 5), img.shape)
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--out", default="benchmark/data")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rng = random.Random(a.seed)
    for i in range(a.count):
        doc = make_doc(i, rng)
        d = out / doc["id"]
        d.mkdir()
        doc["text_strings"] = render_pdf(doc, d / "doc.pdf")
        with pymupdf.open(d / "doc.pdf") as pdf:
            pix = pdf[0].get_pixmap(dpi=200, alpha=False)
            scan = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, ::-1].copy()
        cv2.imwrite(str(d / "scan.png"), scan)
        cv2.imwrite(str(d / "photo.jpg"), photo_from(scan, rng), [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        (d / "truth.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"generated {a.count} documents in {out}")


if __name__ == "__main__":
    main()
