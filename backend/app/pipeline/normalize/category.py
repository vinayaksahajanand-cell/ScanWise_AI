"""Line-item category classification (Services / Materials / other).

Deterministic keyword scoring. It never forces a category: weak evidence
returns "Other" with low confidence so the item is reviewed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SERVICE_TERMS = (
    "labor", "labour", "install", "installation", "installing", "inspection", "inspect", "repair", "repairs",
    "service", "servicing", "maintenance", "maintain", "cleaning", "clean", "consultation", "consulting", "consult",
    "survey", "visit", "call-out", "callout", "call out", "emergency", "diagnostic", "diagnosis", "testing", "test",
    "commissioning", "setup", "set up", "fitting", "wiring", "rewiring", "plumbing", "painting", "design",
    "support", "training", "removal", "disposal", "replacement labor", "charge", "fee", "hours", "work", "tune-up",
    "tune up", "check", "checkup", "check-up", "audit", "assessment", "overhaul", "relocation", "uninstall", "dismantling",
)
MATERIAL_TERMS = (
    "filter", "pipe", "piping", "cable", "wire", "thermostat", "fastener", "fasteners", "screw", "screws", "bolt",
    "bolts", "nut", "nuts", "washer", "bracket", "kit", "part", "parts", "accessory", "accessories", "refrigerant",
    "gas", "copper", "pvc", "valve", "fitting kit", "tape", "insulation", "duct", "coil", "compressor", "capacitor",
    "motor", "fan", "switch", "breaker", "socket", "outlet", "panel", "conduit", "glue", "sealant", "paint", "cement",
    "sand", "brick", "tile", "board", "sheet", "rod", "battery", "bulb", "led", "lamp", "fixture", "hose", "clamp",
    "connector", "adapter", "material", "materials", "supplies", "unit cost", "spare", "spares", "box", "roll",
)
FEE_TERMS = ("travel", "mileage", "freight", "shipping", "delivery", "permit", "disposal fee", "surcharge", "convenience fee")
EQUIPMENT_TERMS = ("rental", "rent", "hire", "equipment", "machine hire", "scaffold")
SUBSCRIPTION_TERMS = ("subscription", "license", "licence", "plan", "membership", "annual", "monthly")

SERVICE_UNITS = {"hour", "visit", "job", "day", "week", "month", "minute"}
MATERIAL_UNITS = {"each", "ft", "lb", "kg", "g", "t", "m", "cm", "mm", "in", "yd", "sq_ft", "sq_m", "cu_m", "cu_ft", "l", "ml",
                  "gal", "box", "pack", "set", "pair", "roll", "bag", "sheet", "bottle", "can", "carton", "dozen"}

DEFAULT_CATEGORIES = ("Services", "Materials")


@dataclass
class CategoryGuess:
    category: str
    confidence: str  # high | medium | low
    reason: str


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    found = []
    for t in terms:
        if re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", text):
            found.append(t)
    return found


def classify_item(name: str | None, description: str | None = None, unit_code: str | None = None) -> CategoryGuess:
    text = f"{name or ''} {description or ''}".lower()
    name_l = (name or "").lower()
    s_hits = _hits(name_l, SERVICE_TERMS)
    m_hits = _hits(name_l, MATERIAL_TERMS)
    score_s = len(s_hits) * 2.0 + len(_hits(text, SERVICE_TERMS)) * 0.5
    score_m = len(m_hits) * 2.0 + len(_hits(text, MATERIAL_TERMS)) * 0.5
    # An activity word ("install", "repair") turns a material noun into a service: "Thermostat install".
    activity = _hits(name_l, ("install", "installation", "repair", "inspection", "cleaning", "maintenance", "labor",
                              "labour", "service", "servicing", "replacement labor", "fitting", "wiring", "rewiring"))
    if activity:
        score_s += 2.0
    if unit_code in SERVICE_UNITS:
        score_s += 1.0
    elif unit_code in MATERIAL_UNITS:
        score_m += 1.0
    fee = _hits(name_l, FEE_TERMS)
    equipment = _hits(name_l, EQUIPMENT_TERMS)
    if fee and score_s < 3 and score_m < 3:
        return CategoryGuess("Fees", "medium", f"matched fee term '{fee[0]}'")
    if equipment and score_m < 3:
        return CategoryGuess("Equipment", "medium", f"matched equipment term '{equipment[0]}'")
    if score_s == 0 and score_m == 0:
        return CategoryGuess("Other", "low", "no service or material keywords")
    if score_s > score_m:
        conf = "high" if score_s - score_m >= 2 else "medium"
        return CategoryGuess("Services", conf, "service keywords: " + ", ".join((s_hits or activity or ["unit"])[:3]))
    if score_m > score_s:
        conf = "high" if score_m - score_s >= 2 else "medium"
        return CategoryGuess("Materials", conf, "material keywords: " + ", ".join((m_hits or ["unit"])[:3]))
    return CategoryGuess("Other", "low", "service and material evidence are tied")


def canonical_category(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    t = text.strip().lower()
    if t in {"service", "services", "labor", "labour", "svc", "work"}:
        return "Services"
    if t in {"material", "materials", "parts", "part", "goods", "supplies", "products", "product", "items"}:
        return "Materials"
    return text.strip()[:40].title() if text.strip().islower() else text.strip()[:40]
