"use client";

import { BadgeCheck, Bot, Calculator, CircleAlert, FileText, ScanText, ShieldCheck, TriangleAlert, User } from "lucide-react";

import type { DocStatus, ExtractedField, FieldStatus } from "@/lib/types";
import { cn, DOC_STATUS_META, FIELD_STATUS_META } from "@/lib/utils";

import { Badge } from "./ui/primitives";
import { Tip } from "./ui/overlays";

export function DocStatusBadge({ status, className }: { status: DocStatus; className?: string }) {
  const m = DOC_STATUS_META[status] ?? DOC_STATUS_META.queued;
  return (
    <Badge tone={m.tone} dot className={cn(status === "processing" && "[&>span:first-child]:animate-pulse", className)}>
      {m.label}
    </Badge>
  );
}

export function FieldStatusDot({ status, className }: { status: FieldStatus; className?: string }) {
  const m = FIELD_STATUS_META[status];
  return (
    <Tip content={m.label}>
      <span className={cn("inline-block size-2 shrink-0 rounded-full", m.dot, status === "missing" && "opacity-40", className)} />
    </Tip>
  );
}

export function FieldStatusBadge({ status }: { status: FieldStatus }) {
  const m = FIELD_STATUS_META[status];
  const Icon = status === "verified" ? BadgeCheck : status === "high_confidence" ? ShieldCheck : status === "mismatch" ? Calculator : status === "unreadable" ? CircleAlert : TriangleAlert;
  return (
    <Badge tone={m.tone} className="gap-1">
      <Icon className="size-3" />
      {m.label}
    </Badge>
  );
}

const SOURCE_META: Record<string, { label: string; icon: typeof Bot; tip: string }> = {
  layout: { label: "OCR", icon: ScanText, tip: "Read by OCR + layout analysis" },
  vision: { label: "Vision AI", icon: Bot, tip: "Read by the vision model" },
  pdf_text: { label: "PDF text", icon: FileText, tip: "Exact text from the PDF's text layer" },
  user: { label: "You", icon: User, tip: "Entered by a person" },
  classifier: { label: "Auto", icon: Bot, tip: "Classified automatically from the item text" },
  ocr: { label: "OCR", icon: ScanText, tip: "OCR reading" },
};

export function SourceBadges({ field, compact }: { field: ExtractedField; compact?: boolean }) {
  const sources = Array.from(new Set(field.sources.map((s) => (s.includes("+") ? s.split("+")[0] : s))));
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {sources.map((s) => {
        const m = SOURCE_META[s] ?? { label: s, icon: ScanText, tip: s };
        const Icon = m.icon;
        return (
          <Tip key={s} content={m.tip}>
            <span className="inline-flex items-center gap-1 rounded-md bg-surface-3 px-1.5 py-0.5 text-[10.5px] font-medium text-muted">
              <Icon className="size-3" />
              {!compact && m.label}
            </span>
          </Tip>
        );
      })}
      {field.validated ? (
        <Tip content="Confirmed by recalculating the document's arithmetic">
          <span className="inline-flex items-center gap-1 rounded-md bg-ok-soft px-1.5 py-0.5 text-[10.5px] font-medium text-ok">
            <Calculator className="size-3" />
            {!compact && "Math ✓"}
          </span>
        </Tip>
      ) : null}
    </span>
  );
}

export function ConfidenceMeter({ value }: { value: number | null }) {
  if (value === null || value === undefined) return null;
  const pct = Math.round(value * 100);
  const tone = value >= 0.9 ? "bg-ok" : value >= 0.75 ? "bg-warn" : "bg-bad";
  return (
    <Tip content={`OCR confidence ${pct}% — how sure the OCR engine is about these characters (not proof of correctness)`}>
      <span className="inline-flex items-center gap-1.5 text-[10.5px] text-muted tabular">
        <span className="relative h-1 w-8 overflow-hidden rounded-full bg-surface-3">
          <span className={cn("absolute inset-y-0 left-0 rounded-full", tone)} style={{ width: `${pct}%` }} />
        </span>
        {pct}%
      </span>
    </Tip>
  );
}
