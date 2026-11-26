"use client";

import { BadgeCheck, Calculator, ChevronDown, CircleCheck, CircleDashed, CircleX, Info, RefreshCw, TriangleAlert, Wand2 } from "lucide-react";
import * as React from "react";

import { Badge, Button } from "@/components/ui/primitives";
import { fieldLabel } from "@/lib/fields";
import type { Extraction, StageLog, ValidationCheck } from "@/lib/types";
import { cn, formatMoney } from "@/lib/utils";

const CHECK_META: Record<ValidationCheck["status"], { icon: typeof CircleCheck; cls: string; label: string }> = {
  fail: { icon: CircleX, cls: "text-bad", label: "Mismatch" },
  warning: { icon: TriangleAlert, cls: "text-warn", label: "Warning" },
  explained: { icon: Info, cls: "text-accent", label: "Explained" },
  pass: { icon: CircleCheck, cls: "text-ok", label: "Pass" },
  skipped: { icon: CircleDashed, cls: "text-faint", label: "Skipped" },
};

const REPORT_META = {
  pass: { tone: "ok" as const, label: "All totals reconcile", icon: BadgeCheck },
  warning: { tone: "warn" as const, label: "Reconciles with warnings", icon: TriangleAlert },
  fail: { tone: "bad" as const, label: "Totals do not reconcile", icon: CircleX },
  incomplete: { tone: "muted" as const, label: "Not enough values to verify", icon: CircleDashed },
  not_applicable: { tone: "info" as const, label: "Price list — no totals to verify", icon: Info },
};

export function ValidationPanel({
  ex,
  onApply,
  onShow,
  onRecalc,
  recalcing,
}: {
  ex: Extraction;
  onApply: (path: string, value: string) => void;
  onShow: (path: string) => void;
  onRecalc: () => void;
  recalcing: boolean;
}) {
  const [showPassed, setShowPassed] = React.useState(false);
  const v = ex.validation;
  const cur = ex.currency.code;
  if (!v) return null;
  const meta = REPORT_META[v.status];
  const order = { fail: 0, warning: 1, explained: 2, pass: 3, skipped: 4 } as const;
  const checks = [...v.checks].sort((a, b) => order[a.status] - order[b.status]);
  const visible = checks.filter((c) => showPassed || c.status === "fail" || c.status === "warning" || c.status === "explained");
  const hidden = checks.length - visible.length;

  return (
    <div className="space-y-3">
      <div className={cn("flex flex-col gap-3 rounded-2xl px-4 py-3 sm:flex-row sm:items-center sm:justify-between", { ok: "bg-ok-soft", warn: "bg-warn-soft", bad: "bg-bad-soft", muted: "bg-surface-3", info: "bg-accent-soft" }[meta.tone])}>
        <div className="flex items-center gap-2.5">
          <meta.icon className={cn("size-5", meta.tone === "ok" ? "text-ok" : meta.tone === "bad" ? "text-bad" : meta.tone === "warn" ? "text-warn" : meta.tone === "info" ? "text-accent" : "text-muted")} />
          <div>
            <div className="text-[14px] font-semibold">{meta.label}</div>
            {v.extracted_total || v.recalculated_total ? (
              <div className="text-[12.5px] text-muted tabular">
                Document {formatMoney(v.extracted_total, cur)} · Recalculated {formatMoney(v.recalculated_total, cur)}
                {v.difference && Number(v.difference) !== 0 ? <span className="font-medium text-bad"> · Δ {formatMoney(v.difference, cur)}</span> : null}
              </div>
            ) : null}
          </div>
        </div>
        <Button size="sm" onClick={onRecalc} loading={recalcing}>
          <RefreshCw className="size-3.5" /> Recalculate
        </Button>
      </div>
      {v.prices_include_tax !== null ? (
        <div className="flex items-center gap-2 px-1 text-[12.5px] text-muted">
          <Calculator className="size-3.5" /> Prices are treated as tax-{v.prices_include_tax ? "inclusive" : "exclusive"}.
        </div>
      ) : null}
      <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border">
        {visible.map((c) => {
          const m = CHECK_META[c.status];
          return (
            <li key={c.id} className="bg-surface px-3.5 py-2.5">
              <div className="flex items-start gap-2.5">
                <m.icon className={cn("mt-0.5 size-4 shrink-0", m.cls)} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span className="text-[13px] font-medium">{c.label}</span>
                    {c.status !== "pass" ? <span className={cn("text-[11px] font-semibold uppercase tracking-wide", m.cls)}>{m.label}</span> : null}
                  </div>
                  <p className="mt-0.5 text-[12.5px] text-muted">{c.message}</p>
                  {c.status === "fail" && (c.expected || c.extracted) ? (
                    <div className="mt-1.5 flex flex-wrap gap-1.5 text-[11.5px] tabular">
                      <Badge tone="muted">Document {formatMoney(c.extracted, cur)}</Badge>
                      <Badge tone="info">Expected {formatMoney(c.expected, cur)}</Badge>
                      {c.difference ? <Badge tone="bad">Δ {formatMoney(c.difference, cur)}</Badge> : null}
                    </div>
                  ) : null}
                  {c.suggestions.map((s) => (
                    <div key={s.path + s.suggested} className="mt-2 flex flex-wrap items-center gap-2 rounded-xl bg-accent-soft px-2.5 py-1.5 text-[12.5px]">
                      <Wand2 className="size-3.5 text-accent" />
                      <span className="min-w-0 flex-1">
                        <b>{fieldLabel(ex, s.path)}</b>: {s.reason} → <span className="font-semibold tabular">{s.suggested}</span>
                      </span>
                      <Button size="xs" variant="ghost" onClick={() => onShow(s.path)}>
                        Show
                      </Button>
                      <Button size="xs" variant="primary" onClick={() => onApply(s.path, s.suggested)}>
                        Apply
                      </Button>
                    </div>
                  ))}
                  {c.fields.length && c.status !== "pass" ? (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {c.fields.slice(0, 6).map((p) => (
                        <button key={p} type="button" onClick={() => onShow(p)} className="rounded-md bg-surface-3 px-1.5 py-0.5 text-[11px] text-muted hover:bg-accent-soft hover:text-accent">
                          {fieldLabel(ex, p)}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
        {visible.length === 0 ? <li className="bg-surface px-4 py-3 text-[13px] text-muted">No issues found.</li> : null}
      </ul>
      {hidden > 0 || showPassed ? (
        <button type="button" onClick={() => setShowPassed((x) => !x)} className="flex items-center gap-1 px-1 text-[12.5px] font-medium text-accent hover:underline">
          <ChevronDown className={cn("size-3.5 transition", showPassed && "rotate-180")} />
          {showPassed ? "Hide passed checks" : `Show ${hidden} passed check${hidden > 1 ? "s" : ""}`}
        </button>
      ) : null}
    </div>
  );
}

const STAGE_TONE: Record<StageLog["status"], string> = {
  ok: "bg-ok",
  retry: "bg-accent",
  warning: "bg-warn",
  skipped: "bg-faint",
  failed: "bg-bad",
};

export function PipelinePanel({ log }: { log: StageLog[] }) {
  if (!log.length) return null;
  const total = log.reduce((a, s) => a + s.duration_ms, 0);
  return (
    <div>
      <ol className="relative space-y-3 pl-5">
        <span className="absolute bottom-2 left-[5px] top-2 w-px bg-border" />
        {log.map((s, i) => (
          <li key={i} className="relative">
            <span className={cn("absolute -left-5 top-1 size-[11px] rounded-full ring-4 ring-surface", STAGE_TONE[s.status])} />
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[13px] font-medium capitalize">{s.stage}</span>
              <span className="shrink-0 text-[11.5px] tabular text-faint">{s.status === "skipped" ? "skipped" : `${s.duration_ms.toLocaleString()} ms`}</span>
            </div>
            {s.detail ? <p className="text-[12px] leading-relaxed text-muted">{s.detail}</p> : null}
          </li>
        ))}
      </ol>
      <div className="mt-3 text-right text-[12px] text-faint tabular">Total {(total / 1000).toFixed(2)} s</div>
    </div>
  );
}
