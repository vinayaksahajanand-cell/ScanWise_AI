// Dates are stored and sent as ISO (YYYY-MM-DD); people see and type DD/MM/YYYY.
// Deliberately locale-independent: toLocaleDateString() would print "Jan 1, 2023" or "01/02/2023"
// depending on the viewer's browser language, which is exactly the ambiguity this app must avoid.

const pad = (n: number) => String(n).padStart(2, "0");
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** "2023-02-01" or a full ISO timestamp -> "01/02/2023" (plus " 14:05" when withTime). */
export function formatDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return "—";
  const m = ISO_DATE.exec(iso);
  if (m && !withTime) return `${m[3]}/${m[2]}/${m[1]}`; // date-only: no Date object, so no timezone shift
  const d = new Date(m ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  const date = `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
  return withTime ? `${date} ${pad(d.getHours())}:${pad(d.getMinutes())}` : date;
}

/** For text boxes: "2023-02-01" -> "01/02/2023"; anything that isn't a plain ISO date is returned untouched. */
export function isoToDmy(iso: string | null | undefined): string {
  const m = ISO_DATE.exec(iso ?? "");
  return m ? `${m[3]}/${m[2]}/${m[1]}` : (iso ?? "");
}

/**
 * Typed day/month/year -> ISO. Accepts 1/2/2023, 01-02-2023, 01.02.2023, 1 2 23 and plain ISO.
 * Returns "" for empty input and null when it isn't a real calendar date (31/02/2023, 13/13/2023, ...).
 */
export function parseDmy(text: string): string | null {
  const s = text.trim();
  if (s === "") return "";
  let y: number, mo: number, d: number;
  const iso = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/.exec(s);
  const dmy = /^(\d{1,2})[\s./-]+(\d{1,2})[\s./-]+(\d{2}|\d{4})$/.exec(s);
  if (iso) [y, mo, d] = [+iso[1], +iso[2], +iso[3]];
  else if (dmy) [d, mo, y] = [+dmy[1], +dmy[2], dmy[3].length === 2 ? (+dmy[3] < 70 ? 2000 : 1900) + +dmy[3] : +dmy[3]];
  else return null;
  const t = new Date(Date.UTC(y, mo - 1, d));
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d) return null;
  return `${String(y).padStart(4, "0")}-${pad(mo)}-${pad(d)}`;
}
