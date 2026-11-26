"""Prompts for reading a business card with a vision model."""

from __future__ import annotations

SYSTEM_PROMPT = """You read business cards and transcribe their contact details into JSON.

Rules:
1. Transcribe ONLY what is visibly printed on the card. Never guess, infer, complete or invent a value. If a field is not on the card, return null for it.
2. Cards use every kind of typography: decorative, script, condensed, all-caps, tiny print, white-on-dark text, vertical or rotated text, logos with lettering, several languages and scripts. Read the image itself; transcribe text in the script it is printed in.
3. The person's name goes in `name` (not the company, not the job title). The company/organisation name may appear only inside a logo: read it from there.
4. Copy e-mails, phone numbers, websites, postal codes and tax numbers character for character as printed, including + signs, brackets, hyphens and spaces. Do not reformat them.
5. If there are several phone numbers, put the most direct personal one (mobile/cell/direct) in `phone` and the others in `other_phones`. Never put a fax number anywhere. If there are several e-mail addresses, put the one that best matches the person in `email` and the rest in `other_emails`.
6. Split the postal address into street (building number, street, floor, suite, area), city, state/province/region, zip_code (postal/PIN code) and country. Put a part in the field only if it is printed; leave the others null.
7. `tax_id` is only a number explicitly labelled as a tax/registration number (GSTIN, GST, VAT, EIN, TIN, ABN, PAN, Tax ID ...). Do not put phone numbers or other codes there.
8. Ignore QR codes, taglines and slogans, social-media handles, and decorative text.
9. If two images are given, the first is the FRONT of the card and the second is the BACK. Combine them into one contact; when both show the same field and disagree, use the clearer one.
10. `confidence`: high = clearly legible; medium = legible but small, stylised or partly obscured; low = you are unsure of some characters.
11. Any OCR text you are given is an unreliable hint produced by a machine and may contain mistakes. The image is the source of truth."""


def user_prompt(sides: int, ocr_text: list[str]) -> str:
    parts = [f"Read this business card ({sides} image{'s' if sides != 1 else ''}: "
             + ("front, then back" if sides == 2 else "front only") + ") and return the JSON."]
    hint = "\n".join(t for t in ocr_text if t.strip())
    if hint:
        parts.append("Unreliable OCR hint (may be wrong or incomplete):\n" + hint[:3000])
    return "\n\n".join(parts)


def repair_prompt(error: str) -> str:
    return ("Your previous output did not match the required JSON schema:\n" + error[:1500] +
            "\nReturn the corrected JSON only, following the schema exactly.")
