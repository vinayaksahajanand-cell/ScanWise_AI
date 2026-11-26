"use client";

import {
  ArrowLeft,
  BadgeCheck,
  BookOpen,
  Bot,
  ChevronDown,
  CircleAlert,
  Download,
  Ellipsis,
  FileJson,
  FileSpreadsheet,
  FileText,
  ListChecks,
  LoaderCircle,
  RefreshCw,
  ScanText,
  Sheet as SheetIcon,
  SkipForward,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { StagingDialog } from "@/components/pricebook/StagingDialog";
import { PipelinePanel, ValidationPanel } from "@/components/review/Checks";
import { DocumentViewer } from "@/components/review/DocumentViewer";
import { FieldInput, Inspector } from "@/components/review/FieldInput";
import { LineItemsTable, TotalsPanel } from "@/components/review/ItemsAndTotals";
import { DocStatusBadge, FieldStatusDot } from "@/components/status";
import { ConfirmDialog, Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, Tip } from "@/components/ui/overlays";
import { Badge, Button, Card, ProgressBar, Segmented } from "@/components/ui/primitives";
import { Api, ApiError } from "@/lib/api";
import { allFields, attentionFields, DOC_TYPE_LABELS, getField, HEADER_GROUPS, HEADER_LABELS } from "@/lib/fields";
import { type BBox, type DocumentDetail, type Extraction, type FieldKind, LINE_KEYS, type LineItem, type OcrToken, type UserSettings } from "@/lib/types";
import { cn, downloadUrl, formatMoney, titleCase } from "@/lib/utils";

const CURRENCIES = ["USD", "INR", "EUR", "GBP", "AED", "CAD", "AUD", "JPY", "SGD", "NZD", "HKD", "CHF", "CNY", "SAR", "QAR", "ZAR", "MXN", "BRL", "SEK", "NOK", "DKK", "PLN", "TRY", "KRW", "THB", "MYR", "IDR", "PHP", "VND", "PKR", "LKR", "BDT", "NPR", "EGP", "NGN", "KES", "ILS"];

const STEPS = [
  { at: 0, label: "Reading pages (OCR / PDF text)" },
  { at: 0.62, label: "Analysing layout & tables" },
  { at: 0.7, label: "Cross-checking with vision AI" },
  { at: 0.88, label: "Validating totals with exact math" },
];

function Section({ id, title, icon, action, children, className }: { id: string; title: string; icon?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section id={id} className={cn("scroll-mt-24", className)}>
      <div className="mb-2.5 flex items-center justify-between gap-2 px-1">
        <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.06em] text-muted">
          {icon}
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function DocumentInner() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const search = useSearchParams();
  const [doc, setDoc] = React.useState<DocumentDetail | null>(null);
  const [ex, setEx] = React.useState<Extraction | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [page, setPage] = React.useState(1);
  const [selected, setSelected] = React.useState<{ path: string; bbox: BBox | null; nonce: number } | null>(null);
  const [tokens, setTokens] = React.useState<Record<number, OcrToken[]>>({});
  const [saving, setSaving] = React.useState<Set<string>>(new Set());
  const [pick, setPick] = React.useState<string | null>(null);
  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(new Set());
  const [staging, setStaging] = React.useState<{ ids?: string[] } | null>(null);
  const [confirm, setConfirm] = React.useState<null | "delete" | "reprocess" | "approve">(null);
  const [reprocessMode, setReprocessMode] = React.useState<string | undefined>(undefined);
  const [busy, setBusy] = React.useState(false);
  const [recalcing, setRecalcing] = React.useState(false);
  const [mobileTab, setMobileTab] = React.useState<"document" | "data">("data");
  const [showLog, setShowLog] = React.useState(false);
  const [settings, setSettings] = React.useState<UserSettings | null>(null);
  const autoStaged = React.useRef(false);

  const load = React.useCallback(async () => {
    try {
      const d = await Api.document(id);
      setDoc(d);
      setEx(d.extraction);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  React.useEffect(() => {
    load();
    Api.settings().then(setSettings).catch(() => undefined);
  }, [load]);

  const processing = doc?.status === "queued" || doc?.status === "processing";
  React.useEffect(() => {
    if (!processing) return;
    const t = setInterval(async () => {
      try {
        const s = await Api.status(id);
        if (s.status === "queued" || s.status === "processing") {
          setDoc((d) => (d ? { ...d, status: s.status as DocumentDetail["status"], progress: s.progress, progress_message: s.progress_message } : d));
        } else {
          await load();
        }
      } catch {
        /* keep polling */
      }
    }, 900);
    return () => clearInterval(t);
  }, [processing, id, load]);

  React.useEffect(() => {
    if (!doc || processing || !doc.pages.find((p) => p.page_no === page)?.image_url || tokens[page]) return;
    Api.tokens(id, page)
      .then((t) => setTokens((cur) => ({ ...cur, [page]: t.tokens })))
      .catch(() => undefined);
  }, [doc, processing, page, id, tokens]);

  React.useEffect(() => {
    if (search.get("pricebook") === "1" && ex && !processing && !autoStaged.current && ex.line_items.length) {
      autoStaged.current = true;
      setStaging({});
    }
  }, [search, ex, processing]);

  const fields = React.useMemo(() => (ex ? allFields(ex) : []), [ex]);
  const attention = React.useMemo(() => (ex ? attentionFields(ex) : []), [ex]);

  const select = React.useCallback(
    (path: string, scroll = false) => {
      if (!ex) return;
      const f = getField(ex, path);
      setSelected({ path, bbox: f?.bbox ?? null, nonce: Date.now() });
      if (f?.bbox && f.bbox.page !== page) setPage(f.bbox.page);
      if (scroll) {
        requestAnimationFrame(() => document.querySelector(`[aria-label="${path}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }));
      }
    },
    [ex, page],
  );

  const applyResponse = (r: { document: DocumentDetail | object; extraction: Extraction }) => {
    setEx(r.extraction);
    setDoc((d) => (d ? { ...d, ...(r.document as object), extraction: r.extraction } : d));
  };

  const save = React.useCallback(
    async (path: string, value: string | null) => {
      setSaving((s) => new Set(s).add(path));
      try {
        const r = await Api.patch(id, [{ path, value }]);
        applyResponse(r);
      } catch (e) {
        toast.error((e as Error).message);
      } finally {
        setSaving((s) => {
          const n = new Set(s);
          n.delete(path);
          return n;
        });
      }
    },
    [id],
  );

  const verify = React.useCallback(
    async (paths: string[]) => {
      try {
        const r = await Api.patch(id, [], paths);
        applyResponse(r);
      } catch (e) {
        toast.error((e as Error).message);
      }
    },
    [id],
  );

  const nextIssue = () => {
    if (!attention.length) return;
    const idx = attention.findIndex((f) => f.path === selected?.path);
    const next = attention[(idx + 1) % attention.length];
    select(next.path, true);
    setMobileTab("data");
  };

  const onPickToken = (t: OcrToken) => {
    if (!pick) return;
    save(pick, t.text);
    setPick(null);
  };

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    try {
      await fn();
      if (ok) toast.success(ok);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && (e.details as { reasons?: string[] })?.reasons) {
        setConfirm("approve");
      } else toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const approve = (acknowledge: boolean) =>
    run(async () => {
      const r = await Api.approve(id, acknowledge);
      applyResponse(r);
      setConfirm(null);
      toast.success("Extraction approved", { description: "All values are now marked as verified." });
    });

  if (error && !doc) {
    return (
      <div className="grid min-h-[60dvh] place-items-center p-6">
        <Card className="max-w-md p-6 text-center">
          <CircleAlert className="mx-auto mb-3 size-7 text-bad" />
          <div className="font-semibold">Could not load this document</div>
          <p className="mt-1 text-[13.5px] text-muted">{error}</p>
          <Link href="/documents">
            <Button className="mt-4">Back to documents</Button>
          </Link>
        </Card>
      </div>
    );
  }
  if (!doc) {
    return (
      <div className="grid min-h-[60dvh] place-items-center">
        <LoaderCircle className="size-6 animate-spin text-muted" />
      </div>
    );
  }

  const cur = ex?.currency.code ?? null;
  const edit = { currency: cur, selectedPath: selected?.path ?? null, saving, onSave: save, onFocus: (p: string) => select(p) };
  const selField = ex && selected ? getField(ex, selected.path) : null;
  const selRef = fields.find((f) => f.path === selected?.path);
  const rowBox = ex && selected?.path.startsWith("line_items.") ? ex.line_items.find((l) => l.id === selected.path.split(".")[1])?.bbox ?? null : null;
  const r = ex?.review;
  const doneFields = r ? r.high_confidence + r.verified : 0;
  const totalFields = r?.total_fields ?? 0;
  const categories = settings?.categories ?? ["Services", "Materials"];
  const visionUsed = ex?.extractors.find((e) => e.startsWith("vision:"));

  const header = (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border bg-surface/90 px-3 py-2.5 backdrop-blur sm:px-4">
      <Link href="/documents" className="grid size-9 place-items-center rounded-xl text-fg-2 hover:bg-surface-3" aria-label="Back to documents">
        <ArrowLeft className="size-4.5" />
      </Link>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-[15.5px] font-semibold tracking-tight">{doc.vendor_name || doc.filename}</h1>
          <DocStatusBadge status={doc.status} />
        </div>
        <div className="truncate text-[12px] text-muted">
          {titleCase(doc.document_type) || "Document"}
          {doc.document_number ? ` · #${doc.document_number}` : ""}
          {doc.grand_total ? ` · ${formatMoney(doc.grand_total, doc.currency)}` : ""} · {doc.page_count} page{doc.page_count > 1 ? "s" : ""}
        </div>
      </div>
      <div className="flex items-center gap-1.5">
        {ex && ex.line_items.length ? (
          <Button size="sm" onClick={() => setStaging(selectedIds.size ? { ids: [...selectedIds] } : {})} className="hidden sm:inline-flex">
            <BookOpen className="size-3.5" /> {selectedIds.size ? `Add ${selectedIds.size} to price book` : "Add to price book"}
          </Button>
        ) : null}
        {ex ? (
          <Menu>
            <MenuTrigger asChild>
              <Button size="sm">
                <Download className="size-3.5" /> Export <ChevronDown className="size-3 text-muted" />
              </Button>
            </MenuTrigger>
            <MenuContent>
              <MenuItem icon={<FileSpreadsheet />} onSelect={() => downloadUrl(Api.exportUrl(id, "xlsx"))}>Excel (.xlsx)</MenuItem>
              <MenuItem icon={<SheetIcon />} onSelect={() => downloadUrl(Api.exportUrl(id, "csv"))}>CSV — line items</MenuItem>
              <MenuItem icon={<FileJson />} onSelect={() => downloadUrl(Api.exportUrl(id, "json"))}>JSON — with evidence & audit</MenuItem>
              <MenuItem icon={<FileText />} onSelect={() => downloadUrl(Api.exportUrl(id, "pdf"))}>PDF report</MenuItem>
            </MenuContent>
          </Menu>
        ) : null}
        <Menu>
          <MenuTrigger asChild>
            <Button size="icon-sm" aria-label="More actions">
              <Ellipsis className="size-4" />
            </Button>
          </MenuTrigger>
          <MenuContent>
            <MenuLabel>Process again</MenuLabel>
            <MenuItem icon={<RefreshCw />} disabled={processing} onSelect={() => { setReprocessMode(undefined); setConfirm("reprocess"); }}>With current settings</MenuItem>
            <MenuItem icon={<Bot />} disabled={processing} onSelect={() => { setReprocessMode("always"); setConfirm("reprocess"); }}>With vision AI (if configured)</MenuItem>
            <MenuItem icon={<ScanText />} disabled={processing} onSelect={() => { setReprocessMode("off"); setConfirm("reprocess"); }}>OCR + layout only</MenuItem>
            <MenuSeparator />
            {ex && ex.line_items.length ? <MenuItem icon={<BookOpen />} onSelect={() => setStaging({})}>Add all items to price book</MenuItem> : null}
            <MenuItem icon={<Download />} onSelect={() => downloadUrl(`/api/documents/${id}/source`)}>Download original file</MenuItem>
            <MenuSeparator />
            <MenuItem icon={<Trash2 />} tone="danger" disabled={doc.status === "processing"} onSelect={() => setConfirm("delete")}>Delete document</MenuItem>
          </MenuContent>
        </Menu>
        {ex && doc.status !== "approved" ? (
          <Button size="sm" variant="primary" loading={busy && confirm === null} onClick={() => approve(false)}>
            <BadgeCheck className="size-3.5" /> Approve
          </Button>
        ) : null}
      </div>
      <div className="w-full md:hidden">
        <Segmented size="sm" value={mobileTab} onChange={setMobileTab} options={[{ value: "data", label: "Extracted data" }, { value: "document", label: "Document" }]} />
      </div>
    </div>
  );

  return (
    <div className="flex h-[calc(100dvh-120px)] flex-col lg:h-dvh">
      {header}
      <div className="flex min-h-0 flex-1">
        <div className={cn("min-w-0 border-r border-border md:block md:w-[44%] xl:w-[48%]", mobileTab === "document" ? "block w-full" : "hidden")}>
          <DocumentViewer
            doc={doc}
            page={page}
            onPage={setPage}
            fields={fields}
            selected={selected}
            rowBox={rowBox}
            onSelectPath={(p) => select(p, true)}
            tokens={tokens[page] ?? null}
            pickMode={pick}
            onPickToken={onPickToken}
            processing={processing}
          />
        </div>

        <div className={cn("scrollbar-thin min-w-0 flex-1 overflow-y-auto bg-bg pb-24 md:block lg:pb-8", mobileTab === "data" ? "block" : "hidden")}>
          <div className="mx-auto max-w-[880px] space-y-6 p-3 sm:p-5">
            {processing ? (
              <Card className="p-6">
                <div className="flex items-center gap-3">
                  <LoaderCircle className="size-5 animate-spin text-accent" />
                  <div className="text-[15px] font-semibold">{doc.progress_message || "Waiting in queue…"}</div>
                </div>
                <ProgressBar value={Math.max(0.04, doc.progress)} className="mt-4" />
                <ol className="mt-5 space-y-2.5">
                  {STEPS.map((s, i) => {
                    const next = STEPS[i + 1]?.at ?? 1;
                    const state = doc.progress >= next ? "done" : doc.progress >= s.at && doc.status === "processing" ? "active" : "todo";
                    return (
                      <li key={s.label} className="flex items-center gap-2.5 text-[13.5px]">
                        <span className={cn("grid size-5 place-items-center rounded-full", state === "done" ? "bg-ok text-white" : state === "active" ? "bg-accent-soft text-accent" : "bg-surface-3 text-faint")}>
                          {state === "done" ? <BadgeCheck className="size-3.5" /> : state === "active" ? <LoaderCircle className="size-3.5 animate-spin" /> : <span className="size-1.5 rounded-full bg-current" />}
                        </span>
                        <span className={state === "todo" ? "text-muted" : "text-fg"}>{s.label}</span>
                      </li>
                    );
                  })}
                </ol>
                <p className="mt-5 text-[12.5px] text-muted">Every value will be linked to its position on the page and every total re-computed. Nothing is guessed — uncertain values are flagged for you.</p>
              </Card>
            ) : null}

            {doc.status === "failed" ? (
              <Card className="border-bad/30 p-5">
                <div className="flex items-center gap-2 font-semibold text-bad">
                  <TriangleAlert className="size-4.5" /> Processing failed
                </div>
                <p className="mt-1.5 text-[13.5px] text-fg-2">{doc.error}</p>
                <Button className="mt-4" variant="primary" onClick={() => setConfirm("reprocess")}>
                  <RefreshCw className="size-4" /> Try again
                </Button>
                {doc.pipeline_log.length ? (
                  <div className="mt-5">
                    <PipelinePanel log={doc.pipeline_log} />
                  </div>
                ) : null}
              </Card>
            ) : null}

            {ex && !processing ? (
              <>
                {/* review summary */}
                <Card className="p-4">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-[14px] font-semibold">
                        {attention.length ? (
                          <>
                            <TriangleAlert className="size-4 text-warn" /> {attention.length} value{attention.length > 1 ? "s" : ""} to check
                          </>
                        ) : (
                          <>
                            <BadgeCheck className="size-4 text-ok" /> {doc.status === "approved" ? "Approved" : "Everything checks out"}
                          </>
                        )}
                      </div>
                      <div className="mt-2 flex items-center gap-3">
                        <ProgressBar value={totalFields ? doneFields / totalFields : 0} tone="ok" className="max-w-56" />
                        <span className="text-[12px] tabular text-muted">
                          {doneFields}/{totalFields} confident or verified
                        </span>
                      </div>
                    </div>
                    {attention.length ? (
                      <Button variant="primary" size="sm" onClick={nextIssue}>
                        <SkipForward className="size-3.5" /> {selected && attention.some((a) => a.path === selected.path) ? "Next issue" : "Review issues"}
                      </Button>
                    ) : null}
                  </div>
                  <div className="mt-4 grid gap-3 border-t border-border pt-3.5 sm:grid-cols-3">
                    <div>
                      <div className="mb-1 text-[11.5px] font-medium text-muted">Document type</div>
                      <FieldInput
                        path="document_type"
                        field={ex.document_type}
                        kind="enum"
                        options={Object.entries(DOC_TYPE_LABELS).map(([value, label]) => ({ value, label }))}
                        onSave={save}
                        onFocus={() => undefined}
                        saving={saving.has("document_type")}
                      />
                    </div>
                    <div>
                      <div className="mb-1 flex items-center gap-1.5 text-[11.5px] font-medium text-muted">
                        Currency <FieldStatusDot status={ex.currency.status} />
                      </div>
                      <div className="flex items-center gap-1.5">
                        <select
                          value={ex.currency.code ?? ""}
                          onChange={(e) => save("currency", e.target.value)}
                          className="h-8 min-w-0 flex-1 rounded-lg border border-border bg-surface px-2 text-[13.5px] font-medium"
                          aria-label="Currency"
                        >
                          {!ex.currency.code ? <option value="">Unknown</option> : null}
                          {Array.from(new Set([...(ex.currency.code ? [ex.currency.code] : []), ...CURRENCIES])).map((c) => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                        </select>
                        {ex.currency.status === "needs_review" ? (
                          <Tip content="Confirm this currency">
                            <Button size="icon-sm" variant="subtle" onClick={() => verify(["currency"])} aria-label="Confirm currency">
                              <BadgeCheck className="size-4" />
                            </Button>
                          </Tip>
                        ) : null}
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 text-[11.5px] font-medium text-muted">Read by</div>
                      <div className="flex flex-wrap gap-1">
                        <Badge tone="muted">
                          <ScanText className="size-3" /> OCR + layout
                        </Badge>
                        {visionUsed ? (
                          <Badge tone="info">
                            <Bot className="size-3" /> {visionUsed.replace("vision:", "").split("/")[0]}
                          </Badge>
                        ) : null}
                      </div>
                    </div>
                  </div>
                  {ex.currency.status === "needs_review" || ex.currency.evidence.length ? (
                    <p className="mt-2.5 text-[12px] text-muted">
                      {ex.currency.issues[0] ? <span className="text-warn">{ex.currency.issues[0]}. </span> : null}
                      {ex.currency.evidence.length ? `Evidence: ${ex.currency.evidence.slice(0, 4).join(" · ")}` : null}
                    </p>
                  ) : null}
                  {ex.warnings.filter((w) => !ex.currency.issues.includes(w)).length ? (
                    <ul className="mt-3 space-y-1 rounded-xl bg-warn-soft px-3 py-2">
                      {Array.from(new Set(ex.warnings.filter((w) => !ex.currency.issues.includes(w)))).map((w) => (
                        <li key={w} className="flex gap-1.5 text-[12.5px] text-warn">
                          <CircleAlert className="mt-0.5 size-3.5 shrink-0" /> {w}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </Card>

                {/* inspector */}
                {selected && selField && selRef !== undefined ? (
                  <div className="sticky top-2 z-10">
                    <Inspector
                      label={selRef?.label ?? selected.path}
                      path={selected.path}
                      field={selField}
                      currency={cur}
                      kind={selField.kind as FieldKind}
                      onSave={save}
                      onVerify={(p) => verify([p])}
                      onPick={() => {
                        setPick((p) => (p ? null : selected.path));
                        setMobileTab("document");
                      }}
                      picking={pick === selected.path}
                      onClose={() => {
                        setSelected(null);
                        setPick(null);
                      }}
                    />
                  </div>
                ) : null}

                {/* header fields */}
                {ex.document_type.value !== "price_list" || fields.some((f) => f.group === "header") ? (
                  <Section id="details" title="Document details" icon={<FileText className="size-3.5" />}>
                    <Card className="divide-y divide-border">
                      {HEADER_GROUPS.map((g) => {
                        const keys = g.keys.filter((k) => ex.header[k]?.value !== null && ex.header[k]?.value !== undefined || ex.header[k]?.status === "unreadable" || ["document_number", "document_date", "vendor_name", "customer_name"].includes(k));
                        return (
                          <div key={g.title} className="px-3 py-3">
                            <div className="mb-1.5 px-2 text-[12px] font-semibold text-fg-2">{g.title}</div>
                            <div className="grid gap-x-3 gap-y-1.5 sm:grid-cols-2">
                              {keys.map((k) => (
                                <div key={k} className={cn("min-w-0", k.endsWith("address") && "sm:col-span-2")}>
                                  <div className="flex items-center gap-1.5 px-2 text-[11.5px] text-muted">
                                    {HEADER_LABELS[k]}
                                    {ex.header[k]?.value ? <FieldStatusDot status={ex.header[k].status} className="size-1.5" /> : null}
                                  </div>
                                  <FieldInput path={`header.${k}`} field={ex.header[k]} onSave={save} onFocus={(p) => select(p)} saving={saving.has(`header.${k}`)} selected={selected?.path === `header.${k}`} multiline={k.endsWith("address")} className="bg-surface-2/70" />
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                      })}
                    </Card>
                  </Section>
                ) : null}

                <Section
                  id="items"
                  title={`${ex.document_type.value === "price_list" ? "Price list items" : "Line items"} (${ex.line_items.length})`}
                  icon={<ListChecks className="size-3.5" />}
                  action={
                    ex.line_items.length ? (
                      <Button size="xs" variant="subtle" onClick={() => setStaging(selectedIds.size ? { ids: [...selectedIds] } : {})}>
                        <BookOpen className="size-3.5" /> {selectedIds.size ? `Add ${selectedIds.size} to price book` : "Add to price book"}
                      </Button>
                    ) : null
                  }
                >
                  <Card className="overflow-hidden">
                    {ex.line_items.length ? (
                      <LineItemsTable
                        ex={ex}
                        edit={edit}
                        categories={categories}
                        selectedIds={selectedIds}
                        onToggleId={(lid) => setSelectedIds((s) => { const n = new Set(s); if (n.has(lid)) n.delete(lid); else n.add(lid); return n; })}
                        onToggleAll={() => setSelectedIds((s) => (s.size === ex.line_items.length ? new Set() : new Set(ex.line_items.map((l) => l.id))))}
                        onAdd={(after) => Api.addLine(id, after).then((r) => { applyResponse(r); if (r.new_id) setTimeout(() => select(`line_items.${r.new_id}.name`, true), 50); }).catch((e: Error) => toast.error(e.message))}
                        onDelete={(lid) => Api.deleteLine(id, lid).then(applyResponse).then(() => toast.success("Row deleted (recorded in the audit log)")).catch((e: Error) => toast.error(e.message))}
                        onVerifyRow={(li: LineItem) => verify(LINE_KEYS.filter((k) => li[k].value !== null).map((k) => `line_items.${li.id}.${k}`))}
                      />
                    ) : (
                      <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
                        <p className="text-[13.5px] text-muted">No line items were found on this document.</p>
                        <Button size="sm" onClick={() => Api.addLine(id).then(applyResponse)}>Add a line item</Button>
                      </div>
                    )}
                  </Card>
                </Section>

                {ex.document_type.value !== "price_list" || ex.taxes.length || ex.summary.grand_total?.value ? (
                  <Section id="totals" title="Totals & taxes" icon={<FileSpreadsheet className="size-3.5" />}>
                    <Card className="py-3">
                      <TotalsPanel
                        ex={ex}
                        edit={edit}
                        onAddTax={() => Api.addTax(id).then(applyResponse).catch((e: Error) => toast.error(e.message))}
                        onDeleteTax={(tid) => Api.deleteTax(id, tid).then(applyResponse).catch((e: Error) => toast.error(e.message))}
                      />
                    </Card>
                  </Section>
                ) : null}

                <Section id="checks" title="Validation" icon={<BadgeCheck className="size-3.5" />}>
                  <ValidationPanel
                    ex={ex}
                    onApply={(p, v) => save(p, v)}
                    onShow={(p) => select(p, true)}
                    recalcing={recalcing}
                    onRecalc={() => {
                      setRecalcing(true);
                      Api.recalculate(id).then(applyResponse).catch((e: Error) => toast.error(e.message)).finally(() => setRecalcing(false));
                    }}
                  />
                </Section>

                <Section
                  id="log"
                  title="Processing log"
                  icon={<ScanText className="size-3.5" />}
                  action={
                    <Button size="xs" variant="ghost" onClick={() => setShowLog((v) => !v)}>
                      {showLog ? "Hide" : "Show"}
                    </Button>
                  }
                >
                  {showLog ? (
                    <Card className="space-y-4 p-4">
                      <PipelinePanel log={doc.pipeline_log} />
                      {ex.notes.length ? (
                        <div className="border-t border-border pt-3">
                          <div className="mb-1 text-[12px] font-semibold text-muted">Notes</div>
                          <ul className="list-disc space-y-0.5 pl-5 text-[12.5px] text-muted">
                            {ex.notes.map((n) => (
                              <li key={n}>{n}</li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                    </Card>
                  ) : null}
                </Section>
              </>
            ) : null}
          </div>
        </div>
      </div>

      {staging ? (
        <StagingDialog open onOpenChange={(v) => !v && setStaging(null)} load={() => Api.stage(id, staging.ids)} onDone={() => setSelectedIds(new Set())} />
      ) : null}

      <ConfirmDialog
        open={confirm === "delete"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Delete this document?"
        description="The original file, extracted data and page images will be removed. Price-book items created from it are kept."
        confirmLabel="Delete"
        loading={busy}
        onConfirm={() => run(async () => { await Api.deleteDocument(id); router.push("/documents"); }, "Document deleted")}
      />
      <ConfirmDialog
        open={confirm === "reprocess"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Process this document again?"
        tone="primary"
        description={`The current extraction${doc.status === "approved" ? " (including your approval)" : ""} and any corrections will be replaced by a fresh reading${reprocessMode === "always" ? " using the vision model" : reprocessMode === "off" ? " using OCR + layout only" : ""}. Corrections stay in the audit log.`}
        confirmLabel="Re-process"
        loading={busy}
        onConfirm={() =>
          run(async () => {
            await Api.reprocess(id, reprocessMode);
            setConfirm(null);
            setSelected(null);
            setTokens({});
            await load();
          })
        }
      />
      <ConfirmDialog
        open={confirm === "approve"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Approve with open issues?"
        tone="primary"
        description={
          <div className="space-y-2">
            <p>Some values have not been confirmed:</p>
            <ul className="list-disc pl-5 text-[13px] text-muted">
              {ex?.validation?.status === "fail" ? <li>Totals do not reconcile</li> : null}
              {attention.length ? <li>{attention.length} field(s) flagged for review</li> : null}
              {ex?.currency.status === "needs_review" ? <li>Currency {ex.currency.code} is unconfirmed</li> : null}
            </ul>
            <p>Approving marks every value as verified by you. This is recorded in the audit log.</p>
          </div>
        }
        confirmLabel="Approve anyway"
        loading={busy}
        onConfirm={() => approve(true)}
      />
    </div>
  );
}

export default function DocumentPage() {
  return (
    <React.Suspense fallback={null}>
      <DocumentInner />
    </React.Suspense>
  );
}
