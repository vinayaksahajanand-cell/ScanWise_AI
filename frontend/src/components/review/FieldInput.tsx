"use client";

import { BadgeCheck, CalendarDays, Calculator, CircleAlert, Eraser, LoaderCircle, MousePointerClick, TriangleAlert, Undo2, X } from "lucide-react";
import * as React from "react";

import { ConfidenceMeter, FieldStatusBadge, SourceBadges } from "@/components/status";
import { Button } from "@/components/ui/primitives";
import { isoToDmy, parseDmy } from "@/lib/dates";
import type { ExtractedField, FieldKind, FieldStatus } from "@/lib/types";
import { cn, formatMoney, formatNumber, unitShort } from "@/lib/utils";

const UNDERLINE: Record<FieldStatus, string> = {
  verified: "decoration-ok",
  high_confidence: "decoration-transparent",
  needs_review: "decoration-warn",
  unreadable: "decoration-violet",
  mismatch: "decoration-bad",
  missing: "decoration-transparent",
};

export function displayValue(field: ExtractedField | undefined, kind: FieldKind, currency?: string | null, withSymbol = false): string {
  if (!field || field.value === null || field.value === undefined) return "";
  const v = field.value;
  if (kind === "money") return formatMoney(v, currency, { symbol: withSymbol });
  if (kind === "number") return formatNumber(v);
  if (kind === "percent") return `${v}%`;
  if (kind === "date") return isoToDmy(v);
  if (kind === "unit") return unitShort(v, v) || v;
  return v;
}

/** Day/month/year text box (the value is stored as ISO). A calendar button opens the native picker. */
function DateInput({
  path,
  value,
  base,
  saving,
  onSave,
  onFocus,
}: {
  path: string;
  value: string;
  base: string;
  saving?: boolean;
  onSave: (path: string, value: string | null) => void;
  onFocus: (path: string) => void;
}) {
  const [text, setText] = React.useState(isoToDmy(value));
  const [bad, setBad] = React.useState(false);
  const picker = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    setText(isoToDmy(value));
    setBad(false);
  }, [value]);

  const commit = () => {
    const iso = parseDmy(text);
    if (iso === null) return setBad(true); // keep what was typed so it can be corrected
    setBad(false);
    if (iso !== value) onSave(path, iso || null);
    else setText(isoToDmy(value));
  };
  const openPicker = () => {
    const el = picker.current;
    if (!el) return;
    try {
      el.showPicker();
    } catch {
      el.click();
    }
  };

  return (
    <div className="relative">
      <input
        type="text"
        inputMode="decimal"
        autoComplete="off"
        placeholder="DD/MM/YYYY"
        value={text}
        aria-label={path}
        aria-invalid={bad || undefined}
        title={bad ? "Not a valid date — use DD/MM/YYYY" : undefined}
        onFocus={() => onFocus(path)}
        onChange={(e) => {
          const raw = e.target.value;
          // typing plain digits: add the slashes (01022023 -> 01/02/2023)
          const d = raw.replace(/\D/g, "");
          setText(/^\d+$/.test(raw) && d.length <= 8 ? [d.slice(0, 2), d.slice(2, 4), d.slice(4)].filter(Boolean).join("/") : raw);
          setBad(false);
        }}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLElement).blur();
          if (e.key === "Escape") {
            setText(isoToDmy(value));
            setBad(false);
          }
        }}
        className={cn(base, "tabular pr-8", bad && "border-bad bg-surface decoration-bad")}
      />
      <button type="button" tabIndex={-1} aria-label="Pick a date" onClick={openPicker} className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted hover:text-fg">
        <CalendarDays className="size-3.5" />
      </button>
      <input
        ref={picker}
        type="date"
        tabIndex={-1}
        aria-hidden
        value={/^\d{4}-\d{2}-\d{2}$/.test(value) ? value : ""}
        onChange={(e) => onSave(path, e.target.value || null)}
        className="pointer-events-none absolute right-0 top-0 h-px w-px opacity-0"
      />
      {saving ? <LoaderCircle className="absolute right-8 top-1/2 size-3.5 -translate-y-1/2 animate-spin text-muted" /> : null}
    </div>
  );
}

export function FieldInput({
  path,
  field,
  kind,
  currency,
  onSave,
  onFocus,
  saving,
  selected,
  className,
  align = "left",
  placeholder,
  withSymbol,
  options,
  multiline,
  listId,
}: {
  path: string;
  field: ExtractedField | undefined;
  kind?: FieldKind;
  currency?: string | null;
  onSave: (path: string, value: string | null) => void;
  onFocus: (path: string) => void;
  saving?: boolean;
  selected?: boolean;
  className?: string;
  align?: "left" | "right";
  placeholder?: string;
  withSymbol?: boolean;
  options?: { value: string; label: string }[];
  multiline?: boolean;
  listId?: string;
}) {
  const k = kind ?? field?.kind ?? "text";
  const value = field?.value ?? "";
  const [focused, setFocused] = React.useState(false);
  const [draft, setDraft] = React.useState(value);
  React.useEffect(() => {
    if (!focused) setDraft(field?.value ?? "");
  }, [field?.value, focused]);
  const status = field?.status ?? "missing";

  const commit = () => {
    setFocused(false);
    const next = draft.trim();
    if (next !== (field?.value ?? "")) onSave(path, next === "" ? null : next);
  };

  const base = cn(
    "w-full min-w-0 rounded-lg border border-transparent bg-transparent px-2 py-1 text-[13.5px] text-fg outline-none transition placeholder:text-faint",
    "hover:border-border hover:bg-surface focus:border-accent focus:bg-surface focus:ring-3 focus:ring-accent/15",
    "underline decoration-2 underline-offset-[5px]",
    UNDERLINE[status],
    selected && "border-accent/60 bg-surface",
    align === "right" && "text-right tabular",
    (k === "money" || k === "number" || k === "percent") && "tabular",
    className,
  );

  if (options) {
    return (
      <div className="relative">
        <select
          value={value}
          onFocus={() => onFocus(path)}
          onChange={(e) => onSave(path, e.target.value || null)}
          className={cn(base, "appearance-none pr-6 no-underline")}
        >
          {!options.some((o) => o.value === value) && value ? <option value={value}>{value}</option> : null}
          {!value ? <option value="">—</option> : null}
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {saving ? <LoaderCircle className="absolute right-1.5 top-1/2 size-3.5 -translate-y-1/2 animate-spin text-muted" /> : null}
      </div>
    );
  }

  if (k === "date") {
    return <DateInput path={path} value={value} base={base} saving={saving} onSave={onSave} onFocus={onFocus} />;
  }

  const shown = focused ? draft : displayValue(field, k, currency, withSymbol);
  const common = {
    value: shown,
    placeholder: placeholder ?? (field?.status === "unreadable" ? "unreadable" : "—"),
    onFocus: () => {
      setFocused(true);
      setDraft(field?.value ?? "");
      onFocus(path);
    },
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setDraft(e.target.value),
    onBlur: commit,
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !(multiline && e.shiftKey)) {
        e.preventDefault();
        (e.target as HTMLElement).blur();
      }
      if (e.key === "Escape") {
        setDraft(field?.value ?? "");
        setFocused(false);
        requestAnimationFrame(() => (e.target as HTMLElement).blur());
      }
    },
    "aria-label": path,
  };
  return (
    <div className="relative">
      {multiline ? (
        <textarea {...common} rows={2} className={cn(base, "resize-none leading-snug")} />
      ) : (
        <input {...common} list={listId} inputMode={k === "money" || k === "number" || k === "percent" ? "decimal" : undefined} className={base} />
      )}
      {saving ? <LoaderCircle className={cn("absolute top-1/2 size-3.5 -translate-y-1/2 animate-spin text-muted", align === "right" ? "left-1" : "right-1.5")} /> : null}
    </div>
  );
}

export function Inspector({
  label,
  path,
  field,
  currency,
  kind,
  onSave,
  onVerify,
  onPick,
  picking,
  onClose,
}: {
  label: string;
  path: string;
  field: ExtractedField;
  currency?: string | null;
  kind: FieldKind;
  onSave: (path: string, value: string | null) => void;
  onVerify: (path: string) => void;
  onPick: () => void;
  picking: boolean;
  onClose: () => void;
}) {
  const shown = displayValue(field, kind, currency, kind === "money") || "—";
  const original = field.extracted_value;
  return (
    <div className="animate-fade-in rounded-2xl border border-accent/30 bg-surface p-3.5 shadow-pop">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11.5px] font-semibold uppercase tracking-wider text-muted">{label}</div>
          <div className="mt-0.5 truncate text-[17px] font-semibold tracking-tight tabular">{shown}</div>
        </div>
        <div className="flex items-center gap-1.5">
          <FieldStatusBadge status={field.status} />
          <button type="button" onClick={onClose} className="grid size-7 place-items-center rounded-lg text-muted hover:bg-surface-3" aria-label="Close inspector">
            <X className="size-4" />
          </button>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <SourceBadges field={field} />
        <ConfidenceMeter value={field.ocr_confidence} />
        {field.page ? <span className="text-[11px] text-faint">Page {field.page}</span> : null}
      </div>
      <dl className="mt-2.5 space-y-1 text-[12.5px]">
        {field.raw && field.raw !== field.value ? (
          <div className="flex gap-2">
            <dt className="w-28 shrink-0 text-muted">Printed as</dt>
            <dd className="min-w-0 break-words font-medium text-fg-2">“{field.raw}”</dd>
          </div>
        ) : null}
        {field.ocr_text && field.ocr_text !== field.raw ? (
          <div className="flex gap-2">
            <dt className="w-28 shrink-0 text-muted">OCR text</dt>
            <dd className="min-w-0 break-words text-fg-2">“{field.ocr_text}”</dd>
          </div>
        ) : null}
        {field.corrected && original !== field.value ? (
          <div className="flex items-center gap-2">
            <dt className="w-28 shrink-0 text-muted">Machine read</dt>
            <dd className="flex min-w-0 items-center gap-2 text-fg-2">
              <span className="line-through decoration-faint">{(kind === "date" ? isoToDmy(original) : original) || "—"}</span>
              <button type="button" onClick={() => onSave(path, original)} className="inline-flex items-center gap-1 text-[12px] font-medium text-accent hover:underline">
                <Undo2 className="size-3" /> Restore
              </button>
            </dd>
          </div>
        ) : null}
      </dl>
      {field.issues.length && !field.verified_by_user ? (
        <ul className="mt-2.5 space-y-1">
          {Array.from(new Set(field.issues)).map((i) => (
            <li key={i} className="flex gap-1.5 text-[12.5px] text-warn">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
              <span>{i}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {field.validated === false ? (
        <div className="mt-2 flex gap-1.5 text-[12.5px] text-bad">
          <Calculator className="mt-0.5 size-3.5 shrink-0" /> Part of a calculation that does not reconcile — see Checks.
        </div>
      ) : null}
      {field.alternatives.length ? (
        <div className="mt-3 space-y-1.5">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">Other readings</div>
          {field.alternatives.map((a, i) => (
            <div key={i} className="flex items-center justify-between gap-2 rounded-xl bg-surface-2 px-2.5 py-1.5 text-[12.5px]">
              <div className="min-w-0">
                <span className="mr-1.5 rounded bg-surface-3 px-1.5 py-0.5 text-[10.5px] font-medium uppercase text-muted">{a.source.replace("_", " ")}</span>
                <span className="font-medium">{a.raw ?? (kind === "date" ? isoToDmy(a.value) : a.value)}</span>
                {a.note ? <span className="ml-1 text-faint">· {a.note}</span> : null}
              </div>
              {(a.value ?? a.raw) && (a.value ?? a.raw) !== field.value ? (
                <Button size="xs" variant="subtle" onClick={() => onSave(path, a.value ?? a.raw)}>
                  Use
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {field.status !== "verified" && field.value !== null ? (
          <Button size="sm" variant="primary" onClick={() => onVerify(path)}>
            <BadgeCheck className="size-3.5" /> Confirm value
          </Button>
        ) : null}
        <Button size="sm" variant={picking ? "subtle" : "secondary"} onClick={onPick}>
          <MousePointerClick className="size-3.5" /> {picking ? "Picking… (click a word)" : "Pick from document"}
        </Button>
        {field.value !== null ? (
          <Button size="sm" variant="ghost" onClick={() => onSave(path, null)}>
            <Eraser className="size-3.5" /> Clear
          </Button>
        ) : null}
      </div>
      {field.status === "unreadable" ? (
        <div className="mt-2 flex gap-1.5 text-[12px] text-violet">
          <CircleAlert className="mt-0.5 size-3.5" /> The value is on the page but could not be read reliably — type it or pick it from the document.
        </div>
      ) : null}
    </div>
  );
}
