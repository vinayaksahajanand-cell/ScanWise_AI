"""Deterministic business-card parser over OCR lines.

Used (a) alone when no vision model is available and (b) as the evidence the vision result is
cross-checked against. It only returns values that are present in the text; it never invents.
Everything is best effort: layouts, scripts and typography vary without limit, so each value
carries a confidence and anything doubtful is reported as such.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CONF_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Line:
    text: str
    conf: float = 1.0  # OCR confidence 0..1
    h: float = 0.0  # glyph height in pixels (relative size is what matters)
    y: float = 0.0
    x: float = 0.0
    side: int = 0  # 0 = front, 1 = back


@dataclass
class Parsed:
    values: dict[str, str] = field(default_factory=dict)
    conf: dict[str, str] = field(default_factory=dict)
    other_emails: list[str] = field(default_factory=list)
    other_phones: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- patterns
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
TLDS = "com|net|org|io|co|in|uk|us|ai|dev|biz|info|edu|gov|app|tech|online|store|shop|xyz|me|ca|au|de|fr|nl|sg|ae|eu|ch|se|no|dk|it|es|jp|cn|br|mx|za|nz|ie"
URL_RE = re.compile(
    rf"(?i)(?<![A-Za-z0-9@.\-])((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.(?:{TLDS})(?:\.[a-z]{{2}})?(?:/[^\s,;|]*)?)(?![A-Za-z0-9@\-])"
)
PHONE_RE = re.compile(r"(?<![\w@.])(\+?\(?\d[\d\s().\-]{5,}\d)(?![\w@])")
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
TAX_LABEL_RE = re.compile(
    r"(?i)\b(GSTIN|GST\s*(?:No\.?|Number|IN)?|VAT\s*(?:No\.?|ID|Number|Reg\.?\s*No\.?)?|EIN|TIN|Tax\s*(?:ID|No\.?|Number)|ABN|ACN|PAN|TRN|NIF|SIRET|SIREN|USt-?IdNr\.?)"
    r"(?![A-Za-z])\s*[:#.\-]*\s*([A-Za-z0-9][A-Za-z0-9 .\-/]{3,28})"
)

MOBILE_LABEL = re.compile(r"(?i)\b(m|mob|mobile|cell|c|whatsapp|wa)\b[\s:.\-]*$")
DIRECT_LABEL = re.compile(r"(?i)\b(d|direct|dl|dir)\b[\s:.\-]*$")
FAX_LABEL = re.compile(r"(?i)\b(fax|f)\b[\s:.\-]*$")
PLAIN_LABEL = re.compile(r"(?i)\b(t|tel|telephone|ph|phone|o|office|w|work|p)\b[\s:.\-]*$")

US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
CA_PROVINCES = set("AB BC MB NB NL NS NT NU ON PE QC SK YT".split())
INDIA_STATES = {s.lower() for s in (
    "Andhra Pradesh|Arunachal Pradesh|Assam|Bihar|Chhattisgarh|Goa|Gujarat|Haryana|Himachal Pradesh|Jharkhand|Karnataka|Kerala|"
    "Madhya Pradesh|Maharashtra|Manipur|Meghalaya|Mizoram|Nagaland|Odisha|Orissa|Punjab|Rajasthan|Sikkim|Tamil Nadu|Telangana|Tripura|"
    "Uttar Pradesh|Uttarakhand|West Bengal|Delhi|New Delhi|Chandigarh|Puducherry|Jammu and Kashmir|Ladakh|Andaman and Nicobar Islands|"
    "Dadra and Nagar Haveli|Daman and Diu|Lakshadweep"
).split("|")}

COUNTRY_RE = re.compile(
    r"(?i)(?:^|[,\s])(USA|U\.S\.A\.?|United States(?: of America)?|India|United Kingdom|U\.K\.|UK|England|Canada|Australia|Germany|France|"
    r"Singapore|UAE|United Arab Emirates|Netherlands|Italy|Spain|Ireland|New Zealand|South Africa|Japan|China|Brazil|Mexico|Switzerland|"
    r"Sweden|Norway|Denmark|Malaysia|Indonesia|Philippines|Pakistan|Bangladesh|Sri Lanka|Nepal|Saudi Arabia|Qatar|Kuwait|Oman|Bahrain)\.?\s*$"
)

STREET_WORDS = re.compile(
    r"(?i)\b(street|st\.?|road|rd\.?|avenue|ave\.?|boulevard|blvd\.?|lane|ln\.?|drive|dr\.?|way|court|ct\.?|place|pl\.?|highway|hwy\.?|"
    r"parkway|pkwy\.?|suite|ste\.?|floor|fl\.?|unit|plot|sector|nagar|marg|building|bldg\.?|tower|block|opp\.?|near|p\.?\s?o\.?\s*box|"
    r"industrial|estate|park|plaza|complex|colony|society|chowk|road no)\b"
)
STREET_SPLIT = re.compile(
    r"(?i)^(.*?\b(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|court|ct|place|pl|parkway|pkwy|highway|hwy)\b\.?"
    r"(?:,?\s*(?:#|suite|ste|unit|apt|floor|fl)\.?\s*[\w\-]+)?)[,\s]+(\D.*)$"
)
ADDR_LABEL = re.compile(r"(?i)^\s*(?:address|addr|add|office|regd\.?\s*office|head\s*office|a)\s*[:.\-]\s*")

TITLE_RE = re.compile(
    r"(?i)\b(ceo|cto|cfo|coo|cmo|founder|co-?founder|owner|proprietor|partner|president|vice[\s-]*president|vp|director|manager|engineer|"
    r"developer|architect|consultant|executive|officer|head|lead|supervisor|specialist|analyst|designer|advisor|adviser|accountant|"
    r"attorney|lawyer|associate|representative|coordinator|administrator|technician|surgeon|doctor|professor|principal|chairman|"
    r"chairperson|md|managing|sales|marketing)\b"
)
COMPANY_RE = re.compile(
    r"(?i)(?:\b(inc|incorporated|llc|ltd|limited|pvt|private|corp|corporation|co|company|gmbh|ag|plc|llp|lp|sa|srl|bv|pty|group|technologies|"
    r"technology|solutions|systems|enterprises|industries|associates|studio|studios|labs|works|services|consulting|holdings|traders|"
    r"trading|electric|electricals|motors|software|networks|ventures|partners|agency|bank|clinic|hospital|university|institute|"
    r"foundation|logistics|construction|builders|developers|infra|pharma|foods|designs|interiors|solutions)\b|&\s*sons)"
)
LEGAL_FORM_RE = re.compile(r"(?i)(inc|incorporated|llc|ltd|limited|pvt|private|corp|corporation|co|company|gmbh|ag|plc|llp|lp|sa|srl|bv|pty)")
LABEL_WORDS = re.compile(r"(?i)\b(e-?mail|email|phone|mobile|mob|tel|fax|website|web|address|www|gstin|vat|pan|cin|ph|cell)\b")
HONORIFIC = re.compile(r"(?i)^(dr|mr|mrs|ms|miss|prof|er|ca|adv|sri|shri|smt)\.?$")
NAME_WORD = re.compile(r"^[^\W\d_](?:[^\W\d_]|['.\-])*$", re.UNICODE)


# --------------------------------------------------------------------------- helpers
def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _degrade(level: str, ocr_conf: float) -> str:
    """Lower a confidence level when the OCR itself was unsure about the characters."""
    n = CONF_ORDER[level]
    if ocr_conf < 0.80:
        n -= 2
    elif ocr_conf < 0.90:
        n -= 1
    n = max(0, n)
    return ("low", "medium", "high")[n]


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _norm_email_text(text: str) -> str:
    return re.sub(r"\s*@\s*", "@", text)


def _is_phoneish_line(text: str) -> bool:
    return len(digits(text)) >= 10 and len(re.sub(r"[\d\s+().\-:]", "", re.sub(r"(?i)\b(tel|ph|phone|mob|mobile|cell|fax|m|t|o|d)\b", "", text))) <= 4


def _tidy_tax(value: str) -> str | None:
    tokens = value.strip(" .-:/").split()
    if not tokens:
        return None
    out = [tokens[0]]
    for t in tokens[1:]:
        if sum(c.isdigit() for c in t) >= max(1, len(t) // 2):
            out.append(t)
        else:
            break
    s = " ".join(out).strip(" .-/")
    return s if sum(c.isdigit() for c in s) >= 4 else None


def _split_parts(pre: str) -> list[str]:
    return [p.strip(" ,;-–") for p in re.split(r"[,;]", pre) if p.strip(" ,;-–")]


# --------------------------------------------------------------------------- locality (city/state/zip)
@dataclass
class Locality:
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country: str | None = None
    street: str | None = None  # street text found on the same line


def _street_city_from_pre(pre: str) -> tuple[str | None, str | None]:
    """'123 Main St, New York' -> ('123 Main St', 'New York'); 'New York' -> (None, 'New York')."""
    parts = _split_parts(pre)
    if len(parts) >= 2:
        return ", ".join(parts[:-1]), parts[-1]
    if len(parts) == 1:
        m = STREET_SPLIT.match(parts[0])
        if m:
            return m.group(1).strip(" ,"), m.group(2).strip(" ,")
        if re.match(r"^\d", parts[0]) and STREET_WORDS.search(parts[0]):
            return parts[0], None  # only a street, no city
        return None, parts[0]
    return None, None


def parse_locality(text: str) -> Locality | None:
    t = ADDR_LABEL.sub("", _clean(text)).strip(" ,")
    if not t or "@" in t:
        return None
    ctry = None
    m = COUNTRY_RE.search(t)
    if m:
        ctry = m.group(1)
        t = t[: m.start()].strip(" ,")

    # United States / Canada:  ... , NY 10001   |   ... , ON M5V 2T6
    m = re.search(r"^(?P<pre>.*?)[,\s]+(?P<state>[A-Z]{2})\.?[,\s]+(?P<zip>\d{5}(?:-\d{4})?)\s*$", t)
    if m and m.group("state") in US_STATES:
        street, city = _street_city_from_pre(m.group("pre"))
        return Locality(city, m.group("state"), m.group("zip"), ctry or None, street)
    m = re.search(r"^(?P<pre>.*?)[,\s]+(?P<state>[A-Z]{2})[,\s]+(?P<zip>[A-Z]\d[A-Z]\s?\d[A-Z]\d)\s*$", t)
    if m and m.group("state") in CA_PROVINCES:
        street, city = _street_city_from_pre(m.group("pre"))
        return Locality(city, m.group("state"), m.group("zip"), ctry or None, street)
    m = re.match(r"^(?P<state>[A-Z]{2})[,\s]+(?P<zip>\d{5}(?:-\d{4})?)$", t)  # "NY 10001" (city is on the line above)
    if m and m.group("state") in US_STATES:
        return Locality(None, m.group("state"), m.group("zip"), ctry or None)

    if _is_phoneish_line(t):
        return None

    # United Kingdom
    m = re.search(r"^(?P<pre>.*?)[,\s]*\b(?P<zip>[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b\s*$", t)
    if m and (m.group("pre") or "").strip():
        street, city = _street_city_from_pre(m.group("pre"))
        return Locality(city, None, m.group("zip"), ctry or None, street)

    # India: 6-digit PIN, optionally after "-" and followed by the state/country
    m = re.search(r"^(?P<pre>.*?)[,\s\-–]*(?<![\d+])\b(?P<zip>[1-9]\d{2}\s?\d{3})\b\s*[,\-–]?\s*(?P<post>.*)$", t)
    if m and re.search(r"[A-Za-z]{3}", m.group("pre") or ""):
        parts = _split_parts(m.group("pre"))
        post = m.group("post").strip(" ,.-")
        state = None
        if post and post.lower() in INDIA_STATES:
            state = post
        if parts and parts[-1].lower() in INDIA_STATES:
            state = parts.pop()
        if not parts:
            return Locality(None, state, re.sub(r"\s+", "", m.group("zip")), ctry or ("India" if state else None))
        city = parts.pop()
        street = ", ".join(parts) or None
        return Locality(city, state, re.sub(r"\s+", "", m.group("zip")), ctry or ("India" if state else None), street)

    # Netherlands  1012 AB Amsterdam   |   Germany/France/...  10115 Berlin
    m = re.search(r"^(?P<pre>.*?)[,\s]*(?P<zip>\b\d{4}\s?[A-Z]{2}\b)\s+(?P<city>[^\d,]+)$", t)
    if m:
        street = ", ".join(_split_parts(m.group("pre"))) or None
        return Locality(m.group("city").strip(), None, m.group("zip"), ctry or None, street)
    m = re.search(r"^(?P<pre>.*?)[,\s]*\b(?P<zip>\d{5})\s+(?P<city>[^\W\d_][^\d,]*)$", t, re.UNICODE)
    if m:
        street = ", ".join(_split_parts(m.group("pre"))) or None
        return Locality(m.group("city").strip(), None, m.group("zip"), ctry or None, street)
    return None


# --------------------------------------------------------------------------- main parser
def parse_card(lines: list[Line]) -> Parsed:
    out = Parsed()
    lines = [Line(_clean(ln.text), ln.conf, ln.h, ln.y, ln.x, ln.side) for ln in lines if _clean(ln.text)]
    lines.sort(key=lambda ln: (ln.side, ln.y, ln.x))
    used: set[int] = set()

    def conf_for(level: str, idx: int) -> str:
        return _degrade(level, lines[idx].conf)

    # ---- tax id (first, so its digits are never mistaken for a phone number)
    masked = [ln.text for ln in lines]
    for i, ln in enumerate(lines):
        found: tuple[str, str] | None = None
        g = GSTIN_RE.search(ln.text)
        if g:
            found = (g.group(0), "high")
        else:
            m = TAX_LABEL_RE.search(ln.text)
            if m:
                v = _tidy_tax(m.group(2))
                if v:
                    found = (v, "high" if len(v.replace(" ", "")) >= 8 else "medium")
                    masked[i] = ln.text[: m.start()] + " " + ln.text[m.end():]
        if found and "tax_id" not in out.values:
            out.values["tax_id"], out.conf["tax_id"] = found[0], _degrade(found[1], ln.conf)
            if g:
                masked[i] = GSTIN_RE.sub(" ", ln.text)
            used.add(i)

    # ---- e-mail
    emails: list[tuple[str, int]] = []
    for i, ln in enumerate(lines):
        for m in EMAIL_RE.finditer(_norm_email_text(masked[i])):
            e = m.group(0).strip(".")
            if e.lower() not in {x[0].lower() for x in emails}:
                emails.append((e, i))
        used.update({i} if EMAIL_RE.search(_norm_email_text(masked[i])) else set())

    # ---- website (after removing e-mail addresses so their domain isn't read as a site)
    sites: list[tuple[str, int]] = []
    for i, ln in enumerate(lines):
        no_mail = EMAIL_RE.sub(" ", _norm_email_text(masked[i]))
        for m in URL_RE.finditer(no_mail):
            u = m.group(1).rstrip(".,;")
            if u.lower() not in {x[0].lower() for x in sites}:
                sites.append((u, i))
            used.add(i)

    # ---- phones
    phones: list[tuple[int, int, str, int]] = []  # (rank, order, text, line)
    seen_digits: set[str] = set()
    order = 0
    for i, ln in enumerate(lines):
        text = EMAIL_RE.sub(" ", _norm_email_text(masked[i]))
        text = URL_RE.sub(" ", text)
        for m in PHONE_RE.finditer(text):
            raw = m.group(1)
            d = digits(raw)
            if not 7 <= len(d) <= 15 or d in seen_digits:
                continue
            before = text[max(0, m.start() - 14): m.start()]
            if FAX_LABEL.search(before):
                continue
            if re.fullmatch(r"(19|20)\d{2}[\s.\-/]\d{1,2}[\s.\-/]\d{1,2}", raw.strip()):  # a date
                continue
            rank = 0 if MOBILE_LABEL.search(before) else 1 if DIRECT_LABEL.search(before) else 2 if PLAIN_LABEL.search(before) else 3
            if rank == 3 and raw.strip().startswith("+"):
                rank = 2
            seen_digits.add(d)
            phones.append((rank, order, _clean(raw), i))
            order += 1
            used.add(i)
    phones.sort()
    if phones:
        out.values["phone"] = phones[0][2]
        d = digits(phones[0][2])
        good_shape = 10 <= len(d) <= 13 or phones[0][2].startswith("+")
        out.conf["phone"] = _degrade("high" if good_shape else "medium", lines[phones[0][3]].conf)
        out.other_phones = [p[2] for p in phones[1:]]

    # ---- primary e-mail / website
    if emails:
        # prefer the address whose domain matches the website, then the first one
        pick = emails[0]
        if sites:
            host = re.sub(r"(?i)^https?://|^www\.", "", sites[0][0]).split("/")[0].lower()
            for e in emails:
                if e[0].lower().split("@")[-1] == host:
                    pick = e
                    break
        out.values["email"] = pick[0]
        out.conf["email"] = conf_for("high", pick[1])
        out.other_emails = [e[0] for e in emails if e is not pick]
    if sites:
        out.values["website"] = sites[0][0]
        out.conf["website"] = conf_for("high", sites[0][1])

    # ---- address
    loc_idx = None
    loc: Locality | None = None
    for i, ln in enumerate(lines):
        if i in used and (EMAIL_RE.search(ln.text) or _is_phoneish_line(ln.text)):
            continue
        cand = parse_locality(ln.text)
        if cand and (cand.zip_code or cand.state):
            loc, loc_idx = cand, i
            break
    if loc is not None and loc_idx is not None:
        used.add(loc_idx)
        city = loc.city
        street_parts: list[str] = []
        if loc.street:
            street_parts.append(loc.street)
        j = loc_idx - 1
        if not city and j >= 0 and not (lines[j].side != lines[loc_idx].side) and j not in used:
            prev = ADDR_LABEL.sub("", lines[j].text).strip(" ,")
            if prev and not re.search(r"\d", prev):
                city = prev
                used.add(j)
                j -= 1
        if not loc.street:
            for _ in range(2):
                if j < 0 or lines[j].side != lines[loc_idx].side or j in used:
                    break
                prev = ADDR_LABEL.sub("", lines[j].text).strip(" ,")
                near = abs(lines[loc_idx].y - lines[j].y) <= max(3.5 * max(lines[loc_idx].h, lines[j].h), 60)
                if near and prev and (re.search(r"\d", prev) or STREET_WORDS.search(prev)) and not TITLE_RE.search(prev):
                    street_parts.insert(0, prev)
                    used.add(j)
                    j -= 1
                else:
                    break
        lc = conf_for("high" if loc.zip_code else "medium", loc_idx)
        if city:
            out.values["city"], out.conf["city"] = city, lc if loc.state or loc.street else _degrade("medium", lines[loc_idx].conf)
        if loc.state:
            out.values["state"], out.conf["state"] = loc.state, lc
        if loc.zip_code:
            out.values["zip_code"], out.conf["zip_code"] = loc.zip_code, lc
        if street_parts:
            out.values["street"] = ", ".join(street_parts)
            out.conf["street"] = _degrade("medium", lines[loc_idx].conf)
        if loc.country:
            out.values["country"], out.conf["country"] = loc.country, lc
    if "country" not in out.values:
        for i, ln in enumerate(lines):
            m = COUNTRY_RE.search(ln.text)
            if m and len(ln.text) <= len(m.group(1)) + 24 and not EMAIL_RE.search(ln.text):
                out.values["country"], out.conf["country"] = m.group(1), conf_for("medium", i)
                used.add(i)
                break

    # ---- job title
    title_idx = None
    for i, ln in enumerate(lines):
        if i in used or re.search(r"\d", ln.text) or len(ln.text.split()) > 7:
            continue
        if TITLE_RE.search(ln.text) and not LEGAL_FORM_RE.search(ln.text):
            out.values["job_title"], out.conf["job_title"] = ln.text, conf_for("medium", i)
            title_idx = i
            used.add(i)
            break

    # ---- company
    comp_idx = None
    best = None
    for i, ln in enumerate(lines):
        if i in used or "@" in ln.text or not COMPANY_RE.search(ln.text) or len(ln.text.split()) > 9:
            continue
        key = (ln.h, -ln.y if ln.side == 0 else -1e9)
        if best is None or key > best[0]:
            best, comp_idx = (key, i), i
    if comp_idx is not None:
        out.values["company"], out.conf["company"] = lines[comp_idx].text, conf_for("medium", comp_idx)
        used.add(comp_idx)

    # ---- name
    hmax = max((ln.h for ln in lines), default=0) or 1.0
    ymax = max((ln.y for ln in lines if ln.side == 0), default=0) or 1.0
    email_blob = (out.values.get("email", "") + " " + out.values.get("website", "")).lower()
    scored: list[tuple[float, int, bool]] = []
    for i, ln in enumerate(lines):
        if i in used or re.search(r"\d|@", ln.text) or LABEL_WORDS.search(ln.text):
            continue
        words = [w for w in ln.text.replace(",", " ").split() if not HONORIFIC.match(w)]
        if not 2 <= len(words) <= 4 or not all(NAME_WORD.match(w) for w in words) or all(len(w.strip(".")) < 2 for w in words):
            continue
        if COMPANY_RE.search(ln.text) or TITLE_RE.search(ln.text) or COUNTRY_RE.search(ln.text):
            continue
        if all(w.islower() for w in words):
            continue
        score = 2.0 * (ln.h / hmax) + 0.5 * (1 - ln.y / ymax if ln.side == 0 else 0)
        if title_idx is not None and lines[title_idx].side == ln.side and abs(lines[title_idx].y - ln.y) <= max(3.0 * max(ln.h, 1), 90):
            score += 1.5
        in_email = any(len(w) >= 3 and w.lower().strip(".") in email_blob for w in words)
        if in_email:
            score += 2.0
        if 2 <= len(words) <= 3:
            score += 0.3
        if ln.side == 1:
            score *= 0.5
        scored.append((score, i, in_email))
    if scored:
        scored.sort(reverse=True)
        top = scored[0]
        level = "high" if top[2] and (title_idx is not None or top[0] > 3) else "medium"
        if len(scored) > 1 and top[0] - scored[1][0] < 0.3 and not top[2]:
            level = "low"
        out.values["name"], out.conf["name"] = lines[top[1]].text, _degrade(level, lines[top[1]].conf)
    return out
