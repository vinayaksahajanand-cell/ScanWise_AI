"use client";

import { Bot, Check, CircleCheck, CircleDashed, Crop, Globe, Info, LoaderCircle, Plus, ScanText, Tag, X } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { PageContainer } from "@/components/shell/AppShell";
import { Badge, Button, Card, CardHeader, Input, PageHeader, Segmented, Skeleton, Switch } from "@/components/ui/primitives";
import { Api } from "@/lib/api";
import type { SystemInfo, UserSettings } from "@/lib/types";
import { cn } from "@/lib/utils";

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  gemini: "Google Gemini",
  anthropic: "Anthropic Claude",
  nvidia: "NVIDIA NIM",
  compatible: "OpenAI-compatible endpoint",
};

const ENV_KEYS: Record<string, string> = {
  openai: "OPENAI_API_KEY (+ OPENAI_MODEL)",
  gemini: "GEMINI_API_KEY (+ GEMINI_MODEL)",
  anthropic: "ANTHROPIC_API_KEY (+ ANTHROPIC_MODEL)",
  nvidia: "NVIDIA_API_KEY (+ NVIDIA_MODEL)",
  compatible: "COMPATIBLE_BASE_URL + COMPATIBLE_MODEL",
};

function ChipList({ values, onChange, placeholder, suffix }: { values: string[]; onChange: (v: string[]) => void; placeholder: string; suffix?: string }) {
  const [draft, setDraft] = React.useState("");
  const add = () => {
    const v = draft.trim();
    if (!v || values.includes(v)) return;
    onChange([...values, v]);
    setDraft("");
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      {values.map((v) => (
        <span key={v} className="inline-flex h-8 items-center gap-1 rounded-full border border-border bg-surface-2 pl-3 pr-1 text-[13px] font-medium">
          {v}
          {suffix}
          <button type="button" onClick={() => onChange(values.filter((x) => x !== v))} className="grid size-6 place-items-center rounded-full text-muted hover:bg-surface-3 hover:text-fg" aria-label={`Remove ${v}`}>
            <X className="size-3.5" />
          </button>
        </span>
      ))}
      <div className="flex items-center gap-1">
        <Input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add())} placeholder={placeholder} className="h-8 w-36 rounded-full text-[13px]" />
        <Button size="icon-sm" variant="ghost" onClick={add} aria-label="Add">
          <Plus className="size-4" />
        </Button>
      </div>
    </div>
  );
}

function Row({ title, description, children }: { title: string; description?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3 border-t border-border px-5 py-4 first:border-t-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="max-w-md">
        <div className="text-[14px] font-medium">{title}</div>
        {description ? <div className="mt-0.5 text-[12.5px] text-muted">{description}</div> : null}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

export default function SettingsPage() {
  const [s, setS] = React.useState<UserSettings | null>(null);
  const [orig, setOrig] = React.useState<string>("");
  const [sys, setSys] = React.useState<SystemInfo | null>(null);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    Api.settings()
      .then((x) => {
        setS(x);
        setOrig(JSON.stringify(x));
      })
      .catch((e: Error) => toast.error(e.message));
    Api.system().then(setSys).catch(() => undefined);
  }, []);

  const dirty = s && JSON.stringify(s) !== orig;
  const set = <K extends keyof UserSettings>(k: K, v: UserSettings[K]) => setS((cur) => (cur ? { ...cur, [k]: v } : cur));

  const save = async () => {
    if (!s) return;
    setSaving(true);
    try {
      const saved = await Api.saveSettings(s);
      setS(saved);
      setOrig(JSON.stringify(saved));
      toast.success("Settings saved");
      Api.system().then(setSys).catch(() => undefined);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <PageContainer className="max-w-4xl">
      <PageHeader
        title="Settings"
        description="How documents are read, validated and organised. API keys live only in backend/.env and are never shown here."
        actions={
          <Button variant="primary" onClick={save} disabled={!dirty} loading={saving}>
            <Check className="size-4" /> Save changes
          </Button>
        }
      />
      {!s ? (
        <div className="space-y-4">
          <Skeleton className="h-48 rounded-2xl" />
          <Skeleton className="h-40 rounded-2xl" />
        </div>
      ) : (
        <div className="space-y-6">
          <Card>
            <CardHeader icon={<Bot className="size-4.5" />} title="Vision AI" description="A multimodal model reads the page image and cross-checks every OCR value. Its readings are never trusted blindly — each value must agree with OCR evidence and the arithmetic." />
            <div className="border-t border-border">
              <Row title="When to use the vision model" description={s.llm_mode === "always" ? "Every document is read by OCR and the vision model, then cross-validated (most accurate)." : s.llm_mode === "fallback" ? "Only when the OCR + layout result is weak (missing items, totals that don't reconcile, low confidence)." : "OCR + layout analysis only. No data leaves this server."}>
                <Segmented
                  stretch={false}
                  value={s.llm_mode}
                  onChange={(v) => set("llm_mode", v)}
                  options={[
                    { value: "always", label: "Always" },
                    { value: "fallback", label: "When uncertain" },
                    { value: "off", label: "Off" },
                  ]}
                />
              </Row>
              <div className="border-t border-border px-5 py-4">
                <div className="mb-2.5 text-[13px] font-medium">Providers</div>
                {sys ? (
                  <div className="grid gap-2 sm:grid-cols-2">
                    {sys.llm.providers.map((p) => (
                      <div key={p.name} className={cn("flex items-start gap-2.5 rounded-xl border px-3 py-2.5", sys.llm.active?.startsWith(p.name + "/") ? "border-accent/40 bg-accent-soft/50" : "border-border")}>
                        {p.configured ? <CircleCheck className="mt-0.5 size-4 text-ok" /> : <CircleDashed className="mt-0.5 size-4 text-faint" />}
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 text-[13px] font-medium">
                            {PROVIDER_LABELS[p.name] ?? p.name}
                            {sys.llm.active?.startsWith(p.name + "/") ? <Badge tone="info">Active</Badge> : null}
                          </div>
                          <div className="truncate text-[12px] text-muted">{p.configured ? p.model : `Set ${ENV_KEYS[p.name]}`}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <Skeleton className="h-20" />
                )}
                {!sys?.llm.active ? (
                  <p className="mt-3 flex gap-1.5 text-[12.5px] text-muted">
                    <Info className="mt-0.5 size-3.5 shrink-0" /> No provider configured — documents are read with OCR + layout analysis only. Add a key to <code className="rounded bg-surface-3 px-1">backend/.env</code> and restart the backend.
                  </p>
                ) : null}
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader icon={<ScanText className="size-4.5" />} title="OCR engine" description="Text detection and recognition with bounding boxes and per-word confidence." />
            <div className="space-y-2 border-t border-border px-5 py-4">
              {sys?.ocr.engines.map((e) => (
                <div key={e.name} className="flex items-center justify-between gap-3 text-[13px]">
                  <span className="flex items-center gap-2">
                    {e.available ? <CircleCheck className="size-4 text-ok" /> : <CircleDashed className="size-4 text-faint" />}
                    <span className="font-medium">{e.name}</span>
                    <span className="text-muted">{e.description}</span>
                  </span>
                  {sys.ocr.engine === e.name ? <Badge tone="info">In use · {sys.ocr.model_size}</Badge> : null}
                </div>
              ))}
              {sys ? (
                <p className="pt-1 text-[12.5px] text-muted">
                  Numbers below {Math.round(sys.review_thresholds.numeric * 100)}% OCR confidence and text below {Math.round(sys.review_thresholds.text * 100)}% are flagged for review unless the arithmetic confirms them. Change engines and thresholds in backend/.env.
                </p>
              ) : null}
            </div>
          </Card>

          <Card>
            <CardHeader icon={<Globe className="size-4.5" />} title="Regional" />
            <div className="border-t border-border">
              <Row title="Date order" description="How a printed date like 03/04/2026 is read. Day/month (DD/MM/YYYY) is the default; dates are always shown as DD/MM/YYYY. Auto infers it from the document's currency and flags anything ambiguous.">
                <Segmented
                  stretch={false}
                  value={s.date_order}
                  onChange={(v) => set("date_order", v)}
                  options={[
                    { value: "DMY", label: "DD/MM/YYYY" },
                    { value: "MDY", label: "MM/DD/YYYY" },
                    { value: "auto", label: "Auto" },
                    { value: "YMD", label: "YYYY-MM-DD" },
                  ]}
                />
              </Row>
              <Row title="Default currency for new price-book items" description="Only pre-selects the currency when you add items by hand. Extracted amounts always keep the document's own currency — nothing is ever converted.">
                <select
                  value={s.default_currency_for_new_items ?? ""}
                  onChange={(e) => set("default_currency_for_new_items", e.target.value || null)}
                  className="h-9 rounded-xl border border-border bg-surface px-3 text-[13.5px]"
                >
                  <option value="">None</option>
                  {["USD", "INR", "EUR", "GBP", "AED", "CAD", "AUD", "JPY", "SGD", "NZD", "CHF", "ZAR"].map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </Row>
            </div>
          </Card>

          <Card>
            <CardHeader icon={<Crop className="size-4.5" />} title="Capture" />
            <div className="border-t border-border">
              <Row title="Auto-crop uploaded photos" description="Detect the paper edges in uploaded photos and correct perspective. Only applied when the document is clearly separated from its background — screenshots and scans are never cropped.">
                <Switch checked={s.auto_crop} onCheckedChange={(v) => set("auto_crop", v)} />
              </Row>
            </div>
          </Card>

          <Card>
            <CardHeader icon={<Tag className="size-4.5" />} title="Price book" description="Options offered in the item editor." />
            <div className="border-t border-border">
              <Row title="Tax presets" description="Quick-pick rates shown next to Default and Not taxable.">
                <ChipList values={s.tax_presets} onChange={(v) => set("tax_presets", v)} placeholder="e.g. 18" suffix="%" />
              </Row>
              <Row title="Categories" description="Services and Materials are built in. Add your own (e.g. Equipment, Fees).">
                <ChipList values={s.categories} onChange={(v) => set("categories", v)} placeholder="New category" />
              </Row>
              <Row title="Custom units" description="Extra billing units for your trade (e.g. panel, point, run).">
                <ChipList values={s.custom_units} onChange={(v) => set("custom_units", v)} placeholder="New unit" />
              </Row>
            </div>
          </Card>

          {sys ? (
            <Card className="px-5 py-4 text-[12.5px] text-muted">
              ScanWise AI v{sys.version} · Database: {sys.database} · Upload limit {sys.limits.max_upload_mb} MB · {sys.limits.max_pages_per_document} pages per document · {sys.limits.worker_concurrency} parallel worker(s)
            </Card>
          ) : null}
        </div>
      )}
      {dirty ? (
        <div className="fixed inset-x-0 bottom-20 z-40 flex justify-center px-4 lg:bottom-6 lg:pl-[248px]">
          <div className="flex items-center gap-3 rounded-2xl border border-border bg-surface/95 p-1.5 pl-4 shadow-pop backdrop-blur animate-slide-up">
            <span className="text-[13.5px] font-medium">Unsaved changes</span>
            <Button size="sm" variant="ghost" onClick={() => setS(JSON.parse(orig))}>
              Discard
            </Button>
            <Button size="sm" variant="primary" onClick={save} loading={saving}>
              {saving ? <LoaderCircle className="size-3.5 animate-spin" /> : <Check className="size-3.5" />} Save
            </Button>
          </div>
        </div>
      ) : null}
    </PageContainer>
  );
}
