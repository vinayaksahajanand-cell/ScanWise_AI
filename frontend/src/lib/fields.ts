import type { BBox, ExtractedField, Extraction, FieldStatus, LineKey } from "./types";

export const HEADER_LABELS: Record<string, string> = {
  document_number: "Invoice / document no.",
  po_number: "PO number",
  reference_number: "Reference",
  document_date: "Document date",
  due_date: "Due date",
  payment_terms: "Payment terms",
  vendor_name: "Vendor",
  vendor_address: "Vendor address",
  vendor_phone: "Vendor phone",
  vendor_email: "Vendor email",
  vendor_tax_id: "Vendor tax ID",
  customer_name: "Customer",
  customer_contact: "Customer contact",
  customer_address: "Customer address",
  customer_phone: "Customer phone",
  customer_email: "Customer email",
  customer_tax_id: "Customer tax ID",
  place_of_supply: "Place of supply",
};

export const HEADER_GROUPS: { title: string; keys: string[] }[] = [
  { title: "Document", keys: ["document_number", "document_date", "due_date", "payment_terms", "po_number", "reference_number", "place_of_supply"] },
  { title: "Vendor", keys: ["vendor_name", "vendor_address", "vendor_phone", "vendor_email", "vendor_tax_id"] },
  { title: "Customer", keys: ["customer_name", "customer_contact", "customer_address", "customer_phone", "customer_email", "customer_tax_id"] },
];

export const SUMMARY_LABELS: Record<string, string> = {
  subtotal: "Subtotal",
  discount_total: "Discount",
  taxable_amount: "Taxable amount",
  tax_total: "Total tax",
  shipping: "Shipping",
  other_charges: "Other charges",
  rounding: "Rounding",
  grand_total: "Grand total",
  amount_paid: "Amount paid",
  balance_due: "Balance due",
};

export const LINE_LABELS: Record<LineKey, string> = {
  name: "Item",
  description: "Description",
  sku: "SKU",
  quantity: "Qty",
  unit: "Unit",
  rate: "Rate",
  discount: "Discount",
  tax_rate: "Tax %",
  tax_amount: "Tax amount",
  amount: "Amount",
  line_total: "Line total",
  category: "Category",
};

export const DOC_TYPE_LABELS: Record<string, string> = {
  invoice: "Invoice",
  tax_invoice: "Tax invoice",
  proforma_invoice: "Proforma invoice",
  receipt: "Receipt",
  bill: "Bill",
  quotation: "Quotation",
  estimate: "Estimate",
  purchase_order: "Purchase order",
  credit_note: "Credit note",
  price_list: "Price list",
  other: "Other",
};

export interface FieldRef {
  path: string;
  label: string;
  group: "header" | "line" | "tax" | "summary" | "document";
  field: ExtractedField;
  bbox: BBox | null;
  status: FieldStatus;
}

export function getField(ex: Extraction, path: string): ExtractedField | null {
  const parts = path.split(".");
  if (path === "document_type") return ex.document_type;
  if (parts[0] === "header") return ex.header[parts[1]] ?? null;
  if (parts[0] === "summary") return ex.summary[parts[1]] ?? null;
  if (parts[0] === "line_items") {
    const li = ex.line_items.find((l) => l.id === parts[1]);
    return li ? (li[parts[2] as LineKey] ?? null) : null;
  }
  if (parts[0] === "taxes") {
    const t = ex.taxes.find((x) => x.id === parts[1]);
    return t ? ((t as unknown as Record<string, ExtractedField>)[parts[2]] ?? null) : null;
  }
  return null;
}

/** All present (or unreadable) fields in visual review order. */
export function allFields(ex: Extraction): FieldRef[] {
  const out: FieldRef[] = [];
  const push = (path: string, label: string, group: FieldRef["group"], f: ExtractedField | undefined) => {
    if (!f) return;
    if (f.value === null && f.status !== "unreadable" && !f.raw) return;
    out.push({ path, label, group, field: f, bbox: f.bbox, status: f.status });
  };
  for (const g of HEADER_GROUPS) for (const k of g.keys) push(`header.${k}`, HEADER_LABELS[k] ?? k, "header", ex.header[k]);
  ex.line_items.forEach((li, i) => {
    const name = li.name.value || `Item ${i + 1}`;
    (Object.keys(LINE_LABELS) as LineKey[]).forEach((k) => push(`line_items.${li.id}.${k}`, `${name} · ${LINE_LABELS[k]}`, "line", li[k]));
  });
  ex.taxes.forEach((t) => {
    const name = t.label.value || t.tax_type || "Tax";
    push(`taxes.${t.id}.label`, `${name} · label`, "tax", t.label);
    push(`taxes.${t.id}.rate`, `${name} · rate`, "tax", t.rate);
    push(`taxes.${t.id}.amount`, `${name} · amount`, "tax", t.amount);
  });
  for (const k of Object.keys(SUMMARY_LABELS)) push(`summary.${k}`, SUMMARY_LABELS[k], "summary", ex.summary[k]);
  return out;
}

export const ATTENTION: FieldStatus[] = ["mismatch", "unreadable", "needs_review"];

export function attentionFields(ex: Extraction): FieldRef[] {
  return allFields(ex).filter((f) => ATTENTION.includes(f.status) && f.field.kind !== "category");
}

export function fieldLabel(ex: Extraction, path: string): string {
  if (path === "currency") return "Currency";
  const f = allFields(ex).find((x) => x.path === path);
  return f?.label ?? path;
}
