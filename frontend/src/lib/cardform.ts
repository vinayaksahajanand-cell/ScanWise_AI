// Pure logic for the New Client form and the business-card scanner.
// No imports: this file must run under `node --test --experimental-strip-types`.

export type FieldKey =
  | "name"
  | "company"
  | "job_title"
  | "email"
  | "phone"
  | "website"
  | "street"
  | "city"
  | "state"
  | "zip_code"
  | "country"
  | "tax_id";

export const FIELD_KEYS: FieldKey[] = [
  "name",
  "company",
  "job_title",
  "email",
  "phone",
  "website",
  "street",
  "city",
  "state",
  "zip_code",
  "country",
  "tax_id",
];

export type ClientForm = Record<FieldKey, string>;
export type Flag = "uncertain" | "missing";
export type Flags = Partial<Record<FieldKey, Flag>>;
export type ClientSource = "manual" | "contacts" | "business_card";

/** Structural subset of the server's CardScanResult (declared inline: no imports allowed here). */
export interface ScanLike {
  name?: string | null;
  company?: string | null;
  email?: string | null;
  phone?: string | null;
  job_title?: string | null;
  website?: string | null;
  tax_id?: string | null;
  address?: {
    street?: string | null;
    city?: string | null;
    state?: string | null;
    zip_code?: string | null;
    country?: string | null;
  } | null;
  other_emails?: string[] | null;
  other_phones?: string[] | null;
  confidence?: Record<string, string> | null;
  missing_fields?: string[] | null;
  uncertain_fields?: string[] | null;
}

export interface Alternates {
  email: string[];
  phone: string[];
}

export function emptyForm(): ClientForm {
  return {
    name: "",
    company: "",
    job_title: "",
    email: "",
    phone: "",
    website: "",
    street: "",
    city: "",
    state: "",
    zip_code: "",
    country: "",
    tax_id: "",
  };
}

export function emptyAlternates(): Alternates {
  return { email: [], phone: [] };
}

const isKey = (k: string): k is FieldKey => (FIELD_KEYS as string[]).includes(k);
const clean = (v: string | null | undefined): string => (v ?? "").replace(/\s+/g, " ").trim();

/** Distinct, non-empty alternates that differ from the primary value (case-insensitive). */
function distinctOthers(primary: string, others: (string | null | undefined)[] | null | undefined): string[] {
  const seen = new Set<string>([primary.trim().toLowerCase()]);
  const out: string[] = [];
  for (const o of others ?? []) {
    const v = clean(o);
    const k = v.toLowerCase();
    if (!v || seen.has(k)) continue;
    seen.add(k);
    out.push(v);
  }
  return out;
}

export interface AppliedScan {
  form: ClientForm;
  flags: Flags;
  alternates: Alternates;
}

/** Map a scan result onto the form and work out which fields to highlight. */
export function applyScanResult(result: ScanLike): AppliedScan {
  const a = result.address ?? {};
  const form: ClientForm = {
    name: clean(result.name),
    company: clean(result.company),
    job_title: clean(result.job_title),
    email: clean(result.email),
    phone: clean(result.phone),
    website: clean(result.website),
    street: clean(a.street),
    city: clean(a.city),
    state: clean(a.state),
    zip_code: clean(a.zip_code),
    country: clean(a.country),
    tax_id: clean(result.tax_id),
  };
  const uncertain = new Set<string>(result.uncertain_fields ?? []);
  for (const [k, c] of Object.entries(result.confidence ?? {})) if (c === "medium" || c === "low") uncertain.add(k);
  const missing = new Set<string>(result.missing_fields ?? []);

  const flags: Flags = {};
  for (const k of FIELD_KEYS) {
    if (!form[k]) flags[k] = "missing"; // an empty value is missing whatever the server listed
    else if (uncertain.has(k)) flags[k] = "uncertain";
  }
  for (const k of missing) if (isKey(k) && !form[k]) flags[k] = "missing";

  return {
    form,
    flags,
    alternates: {
      email: distinctOthers(form.email, result.other_emails),
      phone: distinctOthers(form.phone, result.other_phones),
    },
  };
}

/**
 * Combine a scan with what is already typed: scanned values win, but a field the card did not
 * contain keeps whatever the user had entered (and is then not flagged as missing).
 */
export function mergeScan(base: ClientForm, applied: AppliedScan): AppliedScan {
  const form = { ...base };
  const flags: Flags = {};
  for (const k of FIELD_KEYS) {
    if (applied.form[k]) {
      form[k] = applied.form[k];
      if (applied.flags[k]) flags[k] = applied.flags[k];
    } else if (!base[k].trim()) {
      flags[k] = "missing";
    }
  }
  return {
    form,
    flags,
    alternates: {
      email: distinctOthers(form.email, applied.alternates.email),
      phone: distinctOthers(form.phone, applied.alternates.phone),
    },
  };
}

export function clearFlag(flags: Flags, key: FieldKey): Flags {
  if (!(key in flags)) return flags;
  const next = { ...flags };
  delete next[key];
  return next;
}

/** Put `pick` into the field and the previous value back into the alternates list. */
export function swapAlternate(current: string, alternates: string[], pick: string): { value: string; alternates: string[] } {
  const rest = alternates.filter((a) => a !== pick);
  const prev = current.trim();
  return { value: pick, alternates: prev ? [prev, ...rest] : rest };
}

export function formsEqual(a: ClientForm, b: ClientForm): boolean {
  return FIELD_KEYS.every((k) => a[k].trim() === b[k].trim());
}

export function canAdd(form: ClientForm): boolean {
  return form.name.trim().length > 0;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export function validateForm(form: ClientForm): Partial<Record<FieldKey, string>> {
  const errors: Partial<Record<FieldKey, string>> = {};
  if (!form.name.trim()) errors.name = "Name is required";
  const email = form.email.trim();
  if (email && !EMAIL_RE.test(email)) errors.email = "Enter a valid email address";
  const phone = form.phone.trim();
  if (phone && phone.replace(/\D/g, "").length < 7) errors.phone = "Phone number needs at least 7 digits";
  return errors;
}

/** Trim every value and turn blanks into null (the API stores null for "not provided"). */
export function toPayload(form: ClientForm, source?: ClientSource) {
  const v = (k: FieldKey) => form[k].trim() || null;
  const payload = {
    name: form.name.trim(),
    company: v("company"),
    email: v("email"),
    phone: v("phone"),
    job_title: v("job_title"),
    website: v("website"),
    street: v("street"),
    city: v("city"),
    state: v("state"),
    zip_code: v("zip_code"),
    country: v("country"),
    tax_id: v("tax_id"),
  };
  return source ? { ...payload, source } : payload;
}

export function formFromClient(c: Partial<Record<FieldKey, string | null>>): ClientForm {
  const f = emptyForm();
  for (const k of FIELD_KEYS) f[k] = (c[k] ?? "").toString();
  return f;
}

// ---------------------------------------------------------------------------
// Contacts import: vCard (.vcf) and the Contact Picker API
// ---------------------------------------------------------------------------

export interface ImportedContact {
  form: Partial<ClientForm>;
  otherEmails: string[];
  otherPhones: string[];
}

function unescapeVCard(v: string): string {
  return v.replace(/\\([nN,;\\])/g, (_, c: string) => (c === "n" || c === "N" ? "\n" : c));
}

/** Split on unescaped ';' (vCard structured values), keeping the escapes for later unescaping. */
function splitStructured(v: string): string[] {
  const parts: string[] = [];
  let cur = "";
  for (let i = 0; i < v.length; i++) {
    const ch = v[i];
    if (ch === "\\" && i + 1 < v.length) {
      cur += ch + v[i + 1];
      i++;
    } else if (ch === ";") {
      parts.push(cur);
      cur = "";
    } else cur += ch;
  }
  parts.push(cur);
  return parts;
}

export function countVCards(text: string): number {
  return (text.match(/^BEGIN:VCARD/gim) ?? []).length;
}

/** Parse the first card of a .vcf file (vCard 2.1 / 3.0 / 4.0). Quoted-printable is not decoded. */
export function parseVCard(text: string): ImportedContact {
  const unfolded = text.replace(/\r\n|\r/g, "\n").replace(/\n[ \t]/g, "");
  const lines = unfolded.split("\n");
  const emails: string[] = [];
  const phones: { value: string; pref: boolean }[] = [];
  const form: Partial<ClientForm> = {};
  let fn = "";
  let nameFromN = "";
  let inCard = false;
  let adrDone = false;

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    const upper = line.toUpperCase();
    if (upper === "BEGIN:VCARD") {
      if (inCard) break;
      inCard = true;
      continue;
    }
    if (upper === "END:VCARD") break;
    if (!inCard) continue;
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    const left = line.slice(0, colon);
    const value = line.slice(colon + 1);
    const segs = left.split(";");
    const prop = segs[0].replace(/^.*\./, "").toUpperCase(); // drop "item1." group prefix
    const params = segs.slice(1).join(";").toUpperCase();

    switch (prop) {
      case "FN":
        if (!fn) fn = clean(unescapeVCard(value));
        break;
      case "N": {
        const [family = "", given = "", additional = "", prefix = "", suffix = ""] = splitStructured(value).map((p) => clean(unescapeVCard(p)));
        void prefix;
        void suffix;
        nameFromN = [given, additional, family].filter(Boolean).join(" ");
        break;
      }
      case "ORG": {
        if (!form.company) form.company = clean(unescapeVCard(splitStructured(value)[0] ?? ""));
        break;
      }
      case "TITLE":
        if (!form.job_title) form.job_title = clean(unescapeVCard(value));
        break;
      case "EMAIL": {
        const v = clean(unescapeVCard(value)).replace(/^mailto:/i, "");
        if (v) emails.push(v);
        break;
      }
      case "TEL": {
        const v = clean(unescapeVCard(value)).replace(/^tel:/i, "");
        if (v) phones.push({ value: v, pref: /PREF/.test(params) });
        break;
      }
      case "URL":
        if (!form.website) form.website = clean(unescapeVCard(value));
        break;
      case "ADR": {
        if (adrDone) break;
        // pobox ; extended ; street ; locality ; region ; postal code ; country
        const [pobox = "", ext = "", street = "", city = "", region = "", zip = "", country = ""] = splitStructured(value).map((p) =>
          clean(unescapeVCard(p)),
        );
        void pobox;
        const st = [street, ext].filter(Boolean).join(", ");
        if (st || city || region || zip || country) {
          form.street = st;
          form.city = city;
          form.state = region;
          form.zip_code = zip;
          form.country = country;
          adrDone = true;
        }
        break;
      }
      default:
        break;
    }
  }

  const name = fn || nameFromN;
  if (name) form.name = name;
  // a TEL marked PREF becomes the primary number
  phones.sort((a, b) => Number(b.pref) - Number(a.pref));
  if (emails[0]) form.email = emails[0];
  if (phones[0]) form.phone = phones[0].value;
  for (const k of Object.keys(form) as FieldKey[]) if (!form[k]) delete form[k];

  return {
    form,
    otherEmails: distinctOthers(form.email ?? "", emails.slice(1)),
    otherPhones: distinctOthers(form.phone ?? "", phones.slice(1).map((p) => p.value)),
  };
}

/** One entry of `navigator.contacts.select()` (Contact Picker API). */
export interface PickerContact {
  name?: string[];
  email?: string[];
  tel?: string[];
  address?: {
    addressLine?: string[];
    city?: string;
    region?: string;
    postalCode?: string;
    country?: string;
  }[];
}

export function fromPickerContact(c: PickerContact): ImportedContact {
  const form: Partial<ClientForm> = {};
  const name = clean(c.name?.[0]);
  if (name) form.name = name;
  const emails = (c.email ?? []).map(clean).filter(Boolean);
  const phones = (c.tel ?? []).map(clean).filter(Boolean);
  if (emails[0]) form.email = emails[0];
  if (phones[0]) form.phone = phones[0];
  const a = c.address?.[0];
  if (a) {
    const street = clean((a.addressLine ?? []).join(", "));
    if (street) form.street = street;
    if (clean(a.city)) form.city = clean(a.city);
    if (clean(a.region)) form.state = clean(a.region);
    if (clean(a.postalCode)) form.zip_code = clean(a.postalCode);
    if (clean(a.country)) form.country = clean(a.country);
  }
  return { form, otherEmails: distinctOthers(form.email ?? "", emails.slice(1)), otherPhones: distinctOthers(form.phone ?? "", phones.slice(1)) };
}
