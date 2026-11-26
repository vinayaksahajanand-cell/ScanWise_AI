import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import { formatDate } from "./dates";
import type { DocStatus, FieldStatus } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const SYMBOLS: Record<string, string> = {
  USD: "$", CAD: "CA$", AUD: "A$", SGD: "S$", NZD: "NZ$", HKD: "HK$", MXN: "MX$", INR: "₹", EUR: "€", GBP: "£", JPY: "¥",
  CNY: "¥", KRW: "₩", AED: "AED ", CHF: "CHF ", SAR: "SAR ", ZAR: "R", BRL: "R$", PHP: "₱", THB: "฿", TRY: "₺", RUB: "₽",
  VND: "₫", NGN: "₦", ILS: "₪", PKR: "Rs ", LKR: "Rs ", NPR: "Rs ", MYR: "RM ", IDR: "Rp ",
};
const ZERO_DEC = new Set(["JPY", "KRW", "VND", "CLP", "ISK"]);
const THREE_DEC = new Set(["BHD", "KWD", "OMR", "JOD", "TND"]);

export function currencySymbol(code: string | null | undefined): string {
  if (!code) return "";
  return SYMBOLS[code] ?? `${code} `;
}

export function minorUnits(code: string | null | undefined) {
  if (!code) return 2;
  if (ZERO_DEC.has(code)) return 0;
  if (THREE_DEC.has(code)) return 3;
  return 2;
}

/** Group digits without converting through floats (keeps exact decimals). */
export function groupDigits(value: string, indian = false): string {
  const neg = value.startsWith("-");
  const v = neg ? value.slice(1) : value;
  const [int, frac] = v.split(".");
  let grouped: string;
  if (indian && int.length > 3) {
    const last3 = int.slice(-3);
    const rest = int.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
    grouped = `${rest},${last3}`;
  } else {
    grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  return `${neg ? "−" : ""}${grouped}${frac !== undefined ? `.${frac}` : ""}`;
}

export function padDecimals(value: string, places: number): string {
  if (!/^-?\d+(\.\d+)?$/.test(value)) return value;
  const [i, f = ""] = value.split(".");
  if (places === 0) return f && /[1-9]/.test(f) ? value : i;
  return `${i}.${(f + "0".repeat(places)).slice(0, Math.max(places, f.length))}`;
}

export function formatMoney(value: string | null | undefined, currency?: string | null, opts: { symbol?: boolean } = {}): string {
  if (value === null || value === undefined || value === "") return "—";
  const v = padDecimals(String(value), minorUnits(currency));
  const grouped = groupDigits(v, currency === "INR");
  const sym = opts.symbol === false ? "" : currencySymbol(currency);
  if (grouped.startsWith("−")) return `−${sym}${grouped.slice(1)}`;
  return `${sym}${grouped}`;
}

export function formatNumber(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return groupDigits(String(value));
}

export { formatDate }; // DD/MM/YYYY, independent of the browser's language (see ./dates)

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)} d ago`;
  return formatDate(iso);
}

export function titleCase(s: string | null | undefined): string {
  if (!s) return "";
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const FIELD_STATUS_META: Record<FieldStatus, { label: string; tone: "ok" | "info" | "warn" | "bad" | "violet" | "muted"; dot: string }> = {
  verified: { label: "Verified", tone: "ok", dot: "bg-ok" },
  high_confidence: { label: "High confidence", tone: "info", dot: "bg-accent" },
  needs_review: { label: "Needs review", tone: "warn", dot: "bg-warn" },
  unreadable: { label: "Unreadable", tone: "violet", dot: "bg-violet" },
  mismatch: { label: "Calculation mismatch", tone: "bad", dot: "bg-bad" },
  missing: { label: "Not on document", tone: "muted", dot: "bg-faint" },
};

export const DOC_STATUS_META: Record<DocStatus, { label: string; tone: "ok" | "info" | "warn" | "bad" | "violet" | "muted" }> = {
  queued: { label: "Queued", tone: "muted" },
  processing: { label: "Processing", tone: "info" },
  review: { label: "Needs review", tone: "warn" },
  ready: { label: "Ready to approve", tone: "info" },
  approved: { label: "Approved", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
};

export const UNIT_SHORT: Record<string, string> = {
  hour: "h", visit: "visit", job: "job", unit: "unit", each: "ea", ft: "ft", lb: "lb", kg: "kg", g: "g", t: "t", m: "m",
  cm: "cm", mm: "mm", km: "km", mi: "mi", in: "in", yd: "yd", sq_ft: "sq ft", sq_m: "sq m", sq_yd: "sq yd", cu_m: "m³",
  cu_ft: "cu ft", l: "L", ml: "mL", gal: "gal", box: "box", pack: "pack", set: "set", pair: "pr", roll: "roll", bag: "bag",
  sheet: "sheet", bottle: "btl", can: "can", carton: "ctn", dozen: "doz", lot: "lot", minute: "min", day: "day", week: "wk",
  month: "mo", year: "yr", license: "license",
};

export function unitShort(code: string | null | undefined, label?: string | null): string {
  if (!code) return label || "";
  if (code === "custom") return label || "";
  return UNIT_SHORT[code] ?? code;
}

export function downloadUrl(href: string) {
  const a = document.createElement("a");
  a.href = href;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
