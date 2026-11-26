"use client";

import {
  ArrowRight,
  BadgeCheck,
  BookOpen,
  Bot,
  Calculator,
  CircleX,
  FileCheck2,
  FileUp,
  Files,
  ListChecks,
  ScanLine,
  ScanText,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { DocumentRow } from "@/components/documents/DocumentRow";
import { PageContainer } from "@/components/shell/AppShell";
import { Badge, Button, Card, CardHeader, EmptyState, PageHeader, Skeleton } from "@/components/ui/primitives";
import { Api } from "@/lib/api";
import type { Dashboard, SystemInfo } from "@/lib/types";
import { cn, formatMoney } from "@/lib/utils";

function compact(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}K`;
  return n.toLocaleString();
}

function StatTile({ label, value, sub, icon: Icon, tone = "accent" }: { label: string; value: number; sub?: string; icon: typeof Files; tone?: "accent" | "ok" | "warn" | "bad" | "violet" }) {
  const tones = { accent: "bg-accent-soft text-accent", ok: "bg-ok-soft text-ok", warn: "bg-warn-soft text-warn", bad: "bg-bad-soft text-bad", violet: "bg-violet-soft text-violet" };
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-medium text-muted">{label}</span>
        <span className={cn("grid size-8 place-items-center rounded-xl", tones[tone])}>
          <Icon className="size-4" />
        </span>
      </div>
      <div className="mt-2 text-[28px] font-semibold tracking-tight text-fg tabular">{compact(value)}</div>
      {sub ? <div className="mt-0.5 truncate text-[12px] text-faint">{sub}</div> : null}
    </Card>
  );
}

const SEGMENTS = [
  { key: "approved", label: "Approved", cls: "bg-ok" },
  { key: "ready", label: "Ready to approve", cls: "bg-accent" },
  { key: "review", label: "Needs review", cls: "bg-warn" },
  { key: "progress", label: "In progress", cls: "bg-faint" },
  { key: "failed", label: "Failed", cls: "bg-bad" },
] as const;

function PipelineBar({ d }: { d: Dashboard }) {
  const values: Record<string, number> = {
    approved: d.approved,
    ready: Math.max(0, d.successful - d.approved),
    review: d.needs_review,
    progress: d.in_progress,
    failed: d.failed,
  };
  const total = Object.values(values).reduce((a, b) => a + b, 0) || 1;
  return (
    <div>
      <div className="flex h-2 w-full gap-[2px] overflow-hidden rounded-full" role="img" aria-label={SEGMENTS.map((s) => `${s.label}: ${values[s.key]}`).join(", ")}>
        {SEGMENTS.filter((s) => values[s.key] > 0).map((s) => (
          <div key={s.key} className={cn("h-full first:rounded-l-full last:rounded-r-full", s.cls)} style={{ width: `${(values[s.key] / total) * 100}%` }} title={`${s.label}: ${values[s.key]}`} />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
        {SEGMENTS.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5 text-[12.5px] text-muted">
            <span className={cn("size-2 rounded-full", s.cls)} />
            {s.label}
            <span className="tabular font-medium text-fg-2">{values[s.key]}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Welcome() {
  const steps = [
    { icon: ScanLine, title: "Capture", text: "Live edge detection, auto-capture when steady, perspective correction and multi-page scanning — or upload JPG, PNG, HEIC and PDF." },
    { icon: ScanText, title: "Extract with evidence", text: "PP-OCRv6 OCR + layout analysis (and a vision model if configured). Every value links to the exact words on the page." },
    { icon: Calculator, title: "Validate & review", text: "Totals, taxes and line math are recomputed with exact decimals. Anything uncertain is flagged — never guessed." },
  ];
  return (
    <Card className="overflow-hidden">
      <div className="relative px-6 py-10 sm:px-10">
        <div className="pointer-events-none absolute -right-20 -top-24 size-72 rounded-full bg-accent/10 blur-3xl" />
        <Badge tone="info" className="mb-4">
          <BadgeCheck className="size-3.5" /> Accuracy-first document reader
        </Badge>
        <h2 className="max-w-xl text-[28px] font-semibold leading-tight tracking-tight">Scan an invoice, bill or price list to get started</h2>
        <p className="mt-2 max-w-xl text-[14.5px] text-muted">
          ScanWise reads the document, explains where every number came from and checks the arithmetic before anything reaches your price book or exports.
        </p>
        <div className="mt-6 flex flex-wrap gap-2.5">
          <Link href="/scan">
            <Button variant="primary" size="lg">
              <ScanLine className="size-4.5" /> Scan with camera
            </Button>
          </Link>
          <Link href="/upload">
            <Button size="lg">
              <FileUp className="size-4.5" /> Upload files
            </Button>
          </Link>
        </div>
      </div>
      <div className="grid border-t border-border sm:grid-cols-3">
        {steps.map((s, i) => (
          <div key={s.title} className={cn("px-6 py-5", i > 0 && "border-t border-border sm:border-l sm:border-t-0")}>
            <div className="mb-2 flex items-center gap-2 text-[14px] font-semibold">
              <span className="grid size-7 place-items-center rounded-lg bg-accent-soft text-accent">
                <s.icon className="size-4" />
              </span>
              {s.title}
            </div>
            <p className="text-[13px] leading-relaxed text-muted">{s.text}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function DashboardPage() {
  const [d, setD] = React.useState<Dashboard | null>(null);
  const [sys, setSys] = React.useState<SystemInfo | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(() => {
    Api.dashboard()
      .then((x) => {
        setD(x);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  React.useEffect(() => {
    load();
    Api.system().then(setSys).catch(() => undefined);
  }, [load]);

  React.useEffect(() => {
    if (!d || d.in_progress === 0) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [d, load]);

  const greeting = (() => {
    const h = new Date().getHours();
    return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  })();

  return (
    <PageContainer>
      <PageHeader
        eyebrow={greeting}
        title="Dashboard"
        description="Everything ScanWise has read, and what still needs a human look."
        actions={
          <>
            <Link href="/upload">
              <Button>
                <FileUp className="size-4" /> Upload
              </Button>
            </Link>
            <Link href="/scan">
              <Button variant="primary">
                <ScanLine className="size-4" /> Scan document
              </Button>
            </Link>
          </>
        }
      />

      {error ? (
        <Card className="mb-6 border-bad/30 bg-bad-soft px-5 py-4 text-[14px] text-bad">
          <div className="flex items-center gap-2 font-medium">
            <TriangleAlert className="size-4" /> {error}
          </div>
          <p className="mt-1 text-[13px] opacity-80">Start the backend with: cd backend && python -m uvicorn app.main:app --port 8000</p>
        </Card>
      ) : null}

      {!d && !error ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[118px] rounded-2xl" />
          ))}
        </div>
      ) : null}

      {d && d.total_documents === 0 ? <Welcome /> : null}

      {d && d.total_documents > 0 ? (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <StatTile label="Documents processed" value={d.processed} sub={`${d.total_documents} total${d.in_progress ? ` · ${d.in_progress} in progress` : ""}`} icon={Files} />
            <StatTile label="Successfully extracted" value={d.successful} sub="Ready or approved" icon={FileCheck2} tone="ok" />
            <StatTile label="Needs review" value={d.needs_review} sub="Flagged fields to confirm" icon={TriangleAlert} tone="warn" />
            <StatTile label="Failed" value={d.failed} sub="Could not be read" icon={CircleX} tone="bad" />
            <StatTile label="Line items extracted" value={d.line_items} sub="Across all documents" icon={ListChecks} tone="violet" />
            <StatTile label="Price book items" value={d.price_book_items} sub="Services & materials" icon={BookOpen} />
          </div>

          <Card className="px-5 py-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-[14px] font-semibold">Document status</h3>
              {d.avg_processing_ms ? <span className="text-[12.5px] text-muted">Avg. processing {(d.avg_processing_ms / 1000).toFixed(1)} s / document</span> : null}
            </div>
            <PipelineBar d={d} />
          </Card>

          <div className="grid gap-6 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader
                title="Recent documents"
                action={
                  <Link href="/documents" className="flex items-center gap-1 text-[13px] font-medium text-accent hover:underline">
                    View all <ArrowRight className="size-3.5" />
                  </Link>
                }
              />
              <div className="divide-y divide-border border-t border-border">
                {d.recent.map((doc) => (
                  <DocumentRow key={doc.id} doc={doc} compact />
                ))}
              </div>
            </Card>

            <div className="space-y-6">
              <Card>
                <CardHeader title="Review queue" description={d.review_queue.length ? "Documents with fields to confirm" : undefined} />
                {d.review_queue.length === 0 ? (
                  <EmptyState className="py-8" icon={<BadgeCheck className="size-5" />} title="Nothing waiting" description="Flagged documents will appear here." />
                ) : (
                  <div className="divide-y divide-border border-t border-border">
                    {d.review_queue.map((doc) => (
                      <Link key={doc.id} href={`/documents/${doc.id}`} className="flex items-center justify-between gap-3 px-5 py-3 hover:bg-surface-2">
                        <div className="min-w-0">
                          <div className="truncate text-[13.5px] font-medium">{doc.vendor_name || doc.filename}</div>
                          <div className="text-[12px] text-muted">{doc.document_number ? `#${doc.document_number}` : doc.filename}</div>
                        </div>
                        <Badge tone="warn">{doc.review_count} to check</Badge>
                      </Link>
                    ))}
                  </div>
                )}
              </Card>

              {d.totals_by_currency.length ? (
                <Card>
                  <CardHeader title="Approved & ready totals" description="Per original currency — never converted" />
                  <div className="divide-y divide-border border-t border-border">
                    {d.totals_by_currency.map((t) => (
                      <div key={t.currency} className="flex items-center justify-between px-5 py-2.5 text-[13.5px]">
                        <span className="font-medium text-muted">{t.currency}</span>
                        <span className="tabular font-semibold">{formatMoney(t.total, t.currency)}</span>
                      </div>
                    ))}
                  </div>
                </Card>
              ) : null}

              <Card>
                <CardHeader title="Reading engines" />
                <div className="space-y-3 border-t border-border px-5 py-4 text-[13px]">
                  <div className="flex items-start gap-3">
                    <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">
                      <ScanText className="size-4" />
                    </span>
                    <div>
                      <div className="font-medium">OCR · PP-OCRv6 ({sys?.ocr.model_size ?? "small"})</div>
                      <div className="text-muted">Layout-aware extraction with multi-pass consensus</div>
                    </div>
                  </div>
                  <div className="flex items-start gap-3">
                    <span className={cn("grid size-8 shrink-0 place-items-center rounded-xl", sys?.llm.active ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn")}>
                      <Bot className="size-4" />
                    </span>
                    <div className="min-w-0">
                      <div className="truncate font-medium">Vision AI · {sys?.llm.active ?? "not configured"}</div>
                      <div className="text-muted">
                        {sys?.llm.active ? `Mode: ${sys.llm.mode}` : "Add an API key in backend/.env to cross-check every value with a vision model."}
                      </div>
                    </div>
                  </div>
                </div>
              </Card>
            </div>
          </div>
        </div>
      ) : null}
    </PageContainer>
  );
}
