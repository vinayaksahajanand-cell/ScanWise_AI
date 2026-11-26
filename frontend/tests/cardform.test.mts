// Run: npm test   (Node's built-in test runner with TypeScript type stripping)
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyScanResult,
  canAdd,
  clearFlag,
  countVCards,
  emptyForm,
  formsEqual,
  fromPickerContact,
  mergeScan,
  parseVCard,
  swapAlternate,
  toPayload,
  validateForm,
} from "../src/lib/cardform.ts";

const FULL = {
  name: "John Smith",
  company: "ABC Technologies",
  email: "john@example.com",
  phone: "+1 555 123 4567",
  job_title: "Sales Manager",
  website: "https://example.com",
  tax_id: "GSTIN 27AAPFU0939F1ZV",
  address: { street: "123 Main Street", city: "New York", state: "NY", zip_code: "10001", country: "USA" },
  other_emails: [],
  other_phones: [],
  confidence: {
    name: "high", company: "high", email: "high", phone: "high", job_title: "high", website: "high", tax_id: "high",
    street: "high", city: "high", state: "high", zip_code: "high", country: "high",
  },
  missing_fields: [],
  uncertain_fields: [],
};

test("a card with every field maps onto the form with nothing flagged", () => {
  const { form, flags, alternates } = applyScanResult(FULL);
  assert.equal(form.name, "John Smith");
  assert.equal(form.company, "ABC Technologies");
  assert.equal(form.street, "123 Main Street");
  assert.equal(form.zip_code, "10001");
  assert.equal(form.country, "USA");
  assert.equal(form.tax_id, "GSTIN 27AAPFU0939F1ZV");
  assert.deepEqual(flags, {});
  assert.deepEqual(alternates, { email: [], phone: [] });
});

test("a partial card flags the empty fields as missing and never invents values", () => {
  const { form, flags } = applyScanResult({
    name: "Priya Shah", company: null, email: "priya@shah.in", phone: null, job_title: null, website: null, tax_id: null,
    address: { street: null, city: "Pune", state: null, zip_code: null, country: null },
    confidence: { name: "high", email: "high", city: "high" },
    missing_fields: ["company", "phone"],
  });
  assert.equal(form.company, "");
  assert.equal(form.phone, "");
  assert.equal(form.city, "Pune");
  assert.equal(flags.company, "missing");
  assert.equal(flags.phone, "missing");
  assert.equal(flags.tax_id, "missing"); // empty even though the server did not list it
  assert.equal(flags.name, undefined);
  assert.equal(flags.email, undefined);
});

test("uncertain fields (listed, or medium/low confidence) get the amber flag", () => {
  const { flags } = applyScanResult({
    ...FULL,
    phone: "+1 555 123 456?",
    uncertain_fields: ["phone"],
    confidence: { ...FULL.confidence, email: "low", street: "medium", name: "high" },
  });
  assert.equal(flags.phone, "uncertain");
  assert.equal(flags.email, "uncertain");
  assert.equal(flags.street, "uncertain");
  assert.equal(flags.name, undefined);
});

test("an uncertain flag never attaches to an empty field (that one is 'missing')", () => {
  const { flags } = applyScanResult({ ...FULL, website: null, uncertain_fields: ["website"], confidence: { website: "low" } });
  assert.equal(flags.website, "missing");
});

test("multiple emails and phones become de-duplicated alternates", () => {
  const { form, alternates } = applyScanResult({
    ...FULL,
    other_emails: ["sales@abc.com", "JOHN@example.com", " sales@abc.com ", ""],
    other_phones: ["+1 555 987 6543", "+1 555 123 4567"],
  });
  assert.equal(form.email, "john@example.com");
  assert.deepEqual(alternates.email, ["sales@abc.com"]);
  assert.deepEqual(alternates.phone, ["+1 555 987 6543"]);
});

test("swapping an alternate keeps the old primary available", () => {
  const r = swapAlternate("john@example.com", ["sales@abc.com", "info@abc.com"], "info@abc.com");
  assert.equal(r.value, "info@abc.com");
  assert.deepEqual(r.alternates, ["john@example.com", "sales@abc.com"]);
  const empty = swapAlternate("", ["a@b.co"], "a@b.co");
  assert.deepEqual(empty.alternates, []);
});

test("front + back: values from the back fill what the front lacked (merge keeps typed values)", () => {
  const base = { ...emptyForm(), company: "Typed Co", tax_id: "TAX-1" };
  const applied = applyScanResult({ ...FULL, company: null, tax_id: null, missing_fields: ["company", "tax_id"] });
  const merged = mergeScan(base, applied);
  assert.equal(merged.form.name, "John Smith"); // scanned wins
  assert.equal(merged.form.company, "Typed Co"); // card had none: typed value stays
  assert.equal(merged.form.tax_id, "TAX-1");
  assert.equal(merged.flags.company, undefined);
  assert.equal(merged.flags.tax_id, undefined);
});

test("merge flags only fields that are empty after combining", () => {
  const merged = mergeScan(emptyForm(), applyScanResult({ ...FULL, job_title: null }));
  assert.equal(merged.flags.job_title, "missing");
  assert.equal(merged.flags.name, undefined);
});

test("editing a flagged field clears only that flag", () => {
  const flags = { phone: "uncertain", company: "missing" } as const;
  const next = clearFlag({ ...flags }, "phone");
  assert.deepEqual(next, { company: "missing" });
  assert.equal(clearFlag(next, "city"), next); // unchanged object when nothing to clear
});

test("Name gates the Add action (whitespace does not count)", () => {
  const f = emptyForm();
  assert.equal(canAdd(f), false);
  f.name = "   ";
  assert.equal(canAdd(f), false);
  f.name = " Ana ";
  assert.equal(canAdd(f), true);
  assert.equal(validateForm({ ...emptyForm() }).name, "Name is required");
  assert.equal(validateForm(f).name, undefined);
});

test("validation: email format and phone digit count are only checked when filled", () => {
  const base = { ...emptyForm(), name: "A" };
  assert.deepEqual(validateForm(base), {});
  assert.ok(validateForm({ ...base, email: "not-an-email" }).email);
  assert.ok(validateForm({ ...base, email: "a@b" }).email);
  assert.equal(validateForm({ ...base, email: "a@b.co" }).email, undefined);
  assert.ok(validateForm({ ...base, phone: "12345" }).phone);
  assert.equal(validateForm({ ...base, phone: "(555) 123-4567" }).phone, undefined);
  assert.equal(validateForm({ ...base, phone: "+91 98765 43210" }).phone, undefined);
});

test("payload trims everything and turns blanks into null; source is optional", () => {
  const p = toPayload({ ...emptyForm(), name: "  Ana Ruiz ", company: " ACME ", email: "  ", city: "\tMadrid " }, "business_card");
  assert.equal(p.name, "Ana Ruiz");
  assert.equal(p.company, "ACME");
  assert.equal(p.email, null);
  assert.equal(p.city, "Madrid");
  assert.equal(p.tax_id, null);
  assert.equal((p as { source?: string }).source, "business_card");
  assert.equal("source" in toPayload({ ...emptyForm(), name: "X" }), false);
});

test("formsEqual ignores surrounding whitespace only", () => {
  const a = { ...emptyForm(), name: "A" };
  assert.equal(formsEqual(a, { ...a, name: " A " }), true);
  assert.equal(formsEqual(a, { ...a, city: "X" }), false);
});

const VCF = [
  "BEGIN:VCARD",
  "VERSION:3.0",
  "N:Smith;John;Q.;Dr.;",
  "FN:John Q. Smith",
  "ORG:ABC Technologies;Sales",
  "TITLE:Sales Manager",
  "TEL;TYPE=CELL:+1 555 123 4567",
  "TEL;TYPE=WORK,PREF:+1 555 000 1111",
  "TEL;TYPE=HOME:+1 555 222 3333",
  "EMAIL;TYPE=INTERNET:john@example.com",
  "item1.EMAIL:sales@abc.com",
  "URL:https://abc.example",
  "ADR;TYPE=WORK:;Suite 4;123 Main Street;New York;NY;10001;USA",
  "NOTE:This is a long note that is folded over",
  "  two lines",
  "END:VCARD",
  "BEGIN:VCARD",
  "FN:Second Person",
  "END:VCARD",
].join("\r\n");

test("vCard: name, org, title, address, url and the first card only", () => {
  assert.equal(countVCards(VCF), 2);
  const { form } = parseVCard(VCF);
  assert.equal(form.name, "John Q. Smith");
  assert.equal(form.company, "ABC Technologies");
  assert.equal(form.job_title, "Sales Manager");
  assert.equal(form.website, "https://abc.example");
  assert.equal(form.street, "123 Main Street, Suite 4");
  assert.equal(form.city, "New York");
  assert.equal(form.state, "NY");
  assert.equal(form.zip_code, "10001");
  assert.equal(form.country, "USA");
});

test("vCard: multiple TEL/EMAIL — PREF phone is primary, the rest are alternates", () => {
  const r = parseVCard(VCF);
  assert.equal(r.form.phone, "+1 555 000 1111");
  assert.deepEqual(r.otherPhones, ["+1 555 123 4567", "+1 555 222 3333"]);
  assert.equal(r.form.email, "john@example.com");
  assert.deepEqual(r.otherEmails, ["sales@abc.com"]);
});

test("vCard: folded lines are joined, escapes undone, N used when FN is absent", () => {
  const folded = "BEGIN:VCARD\nVERSION:3.0\nN:de la Cruz;Maria;;;\nORG:Foo\\, Bar\\; Inc\nADR:;;1 Long\n  Street;Town;;;\nEND:VCARD";
  const r = parseVCard(folded);
  assert.equal(r.form.name, "Maria de la Cruz");
  assert.equal(r.form.company, "Foo, Bar; Inc");
  assert.equal(r.form.street, "1 Long Street");
  assert.equal(r.form.city, "Town");
});

test("vCard: text without a card yields an empty result", () => {
  assert.deepEqual(parseVCard("hello world"), { form: {}, otherEmails: [], otherPhones: [] });
});

test("Contact Picker entries map the same way", () => {
  const r = fromPickerContact({
    name: ["Ana Ruiz"],
    email: ["ana@x.es", "ruiz@y.es"],
    tel: ["+34 600 111 222"],
    address: [{ addressLine: ["Calle Mayor 1", "2B"], city: "Madrid", region: "MD", postalCode: "28013", country: "ES" }],
  });
  assert.equal(r.form.name, "Ana Ruiz");
  assert.equal(r.form.street, "Calle Mayor 1, 2B");
  assert.equal(r.form.zip_code, "28013");
  assert.deepEqual(r.otherEmails, ["ruiz@y.es"]);
  assert.deepEqual(fromPickerContact({}).form, {});
});
