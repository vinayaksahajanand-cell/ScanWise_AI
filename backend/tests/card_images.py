"""Synthetic business-card images for tests (no binary fixtures; every layout is drawn here)."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# (text, font size, y, x)  -- a typical front side
FULL_FRONT = [
    ("ABC Technologies Inc.", 54, 50, 70),
    ("John Smith", 78, 150, 70),
    ("Sales Manager", 40, 250, 70),
    ("T: +1 (555) 123-4567", 38, 360, 70),
    ("john.smith@abctech.com", 38, 420, 70),
    ("www.abctech.com", 38, 480, 70),
    ("123 Main Street, Suite 400", 38, 560, 70),
    ("New York, NY 10001", 38, 620, 70),
]

PARTIAL = [("Maria Garcia", 78, 200, 70), ("maria.garcia@example.org", 40, 330, 70)]

MULTI = [
    ("Patel Electric Works", 54, 50, 70),
    ("Rakesh Patel", 78, 150, 70),
    ("Managing Director", 40, 250, 70),
    ("M: +91 98200 12345", 38, 340, 70),
    ("O: 022 2345 6789", 38, 395, 70),
    ("Fax: 022 2345 0000", 38, 450, 70),
    ("rakesh@patelelectric.in", 38, 515, 70),
    ("sales@patelelectric.in", 38, 570, 70),
]

BACK = [
    ("ABC Technologies Inc.", 54, 60, 70),
    ("Visit us: www.abctech.com", 40, 180, 70),
    ("EIN: 12-3456789", 40, 260, 70),
    ("Fax: +1 555 000 1111", 40, 340, 70),
]


def make_card(lines, size=(1400, 800), bg=(255, 255, 255), fg=(20, 20, 20), rotate=0, blur=0.0, fmt="PNG") -> bytes:
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    for text, px, y, x in lines:
        d.text((x, y), text, font=ImageFont.load_default(size=px), fill=fg)
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor=bg)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    img.save(buf, format=fmt, **({"quality": 92} if fmt == "JPEG" else {}))
    return buf.getvalue()


def make_pdf(*pages: list, size=(1400, 800)) -> bytes:
    """An image-only PDF (like a scanned card): one page per list of text lines."""
    imgs = []
    for lines in pages:
        img = Image.new("RGB", size, (255, 255, 255))
        d = ImageDraw.Draw(img)
        for text, px, y, x in lines:
            d.text((x, y), text, font=ImageFont.load_default(size=px), fill=(20, 20, 20))
        imgs.append(img)
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", resolution=200, save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def make_digital_pdf(*pages: list, encrypt: str | None = None) -> bytes:
    """A vector/text PDF with a real text layer (a card exported from a design tool).
    Lines are (text, font size in pt, y in pt) on a 3.5 x 2 inch page."""
    import pymupdf

    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=252, height=144)
        for text, size, y in lines:
            page.insert_text((14, y), text, fontsize=size)
    kw = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": encrypt, "owner_pw": encrypt + "-owner"} if encrypt else {}
    data = doc.tobytes(**kw)
    doc.close()
    return data


DIGITAL_FRONT = [("ABC Technologies Inc.", 11, 22), ("John Smith", 16, 48), ("Sales Manager", 9, 62),
                 ("john.smith@abctech.com", 9, 90), ("+1 (555) 123-4567", 9, 104), ("New York, NY 10001", 9, 124)]
DIGITAL_BACK = [("Visit www.abctech.com", 11, 40), ("EIN: 12-3456789", 11, 70)]
