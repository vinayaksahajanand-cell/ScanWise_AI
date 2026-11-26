"use client";

import { ArrowRight, BookOpen, CircleAlert, Copy, LoaderCircle } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Sheet } from "@/components/ui/overlays";
import { Badge, Button, Input, Segmented } from "@/components/ui/primitives";
import { Api } from "@/lib/api";
import type { StagedItem } from "@/lib/types";
import { cn, currencySymbol, formatMoney, unitShort } from "@/lib/utils";

type Decision = { action: "create" | "update" | "skip"; target: string | null };

/**
 * Review extracted/imported rows before they reach the price book.
 * Duplicates are suggested, never merged automatically; changed rates show old → new.
 */
export function StagingDialog({
  open,
  onOpenChange,
  load,
  title = "Add to price book",
  onDone,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  load: () => Promise<StagedItem[]>;
  title?: string;
  onDone?: () => void;
}) {
  const [items, setItems] = React.useState<StagedItem[] | null>(null);
  const [decisions, setDecisions] = React.useState<Record<string, Decision>>({});
  const [error, setError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    setItems(null);
    setError(null);
    load()
      .then((st) => {
        setItems(st);
        setDecisions(Object.fromEntries(st.map((s) => [s.key, { action: s.suggested_action, target: s.suggested_target ?? s.matches[0]?.item_id ?? null }])));
      })
      .catch((e: Error) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const patch = (key: string, data: Partial<StagedItem["data"]>) =>
    setItems((cur) => cur?.map((s) => (s.key === key ? { ...s, data: { ...s.data, ...data } } : s)) ?? null);

  const counts = Object.values(decisions).reduce((a, d) => ({ ...a, [d.action]: (a[d.action] ?? 0) + 1 }), {} as Record<string, number>);

  const commit = async () => {
    if (!items) return;
    setSaving(true);
    try {
      const res = await Api.commit(items.map((s) => ({ data: s.data, action: decisions[s.key].action, target_id: decisions[s.key].action === "update" ? decisions[s.key].target : null })));
      toast.success(`Price book updated`, { description: `${res.created} added · ${res.updated} updated · ${res.skipped} skipped` });
      onOpenChange(false);
      onDone?.();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description="Check names, rates and units. Possible duplicates are matched by name, SKU and unit — you decide what happens."
      size="lg"
      footer={
        <>
          <span className="mr-auto text-[12.5px] text-muted">
            {counts.create ?? 0} new · {counts.update ?? 0} update · {counts.skip ?? 0} skip
          </span>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={commit} loading={saving} disabled={!items?.length}>
            <BookOpen className="size-4" /> Save to price book
          </Button>
        </>
      }
    >
      {error ? (
        <div className="m-5 flex items-center gap-2 rounded-xl bg-bad-soft px-4 py-3 text-[13px] text-bad">
          <CircleAlert className="size-4" /> {error}
        </div>
      ) : !items ? (
        <div className="grid place-items-center py-16">
          <LoaderCircle className="size-6 animate-spin text-muted" />
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {items.map((s) => {
            const d = decisions[s.key];
            const target = s.matches.find((m) => m.item_id === d.target) ?? s.matches[0];
            return (
              <li key={s.key} className={cn("px-5 py-4 transition-opacity", d.action === "skip" && "opacity-55")}>
                <div className="grid gap-2 sm:grid-cols-[1fr_130px_96px_150px]">
                  <Input value={s.data.name} onChange={(e) => patch(s.key, { name: e.target.value })} aria-label="Item name" className="font-medium" />
                  <div className="relative">
                    <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[13px] text-muted">{currencySymbol(s.data.currency).trim() || "¤"}</span>
                    <Input value={s.data.rate ?? ""} onChange={(e) => patch(s.key, { rate: e.target.value || null })} placeholder="Rate" className="pl-8 text-right tabular" aria-label="Rate" inputMode="decimal" />
                  </div>
                  <Input value={s.data.unit === "custom" ? s.data.unit_label ?? "" : s.data.unit ?? ""} onChange={(e) => patch(s.key, { unit: e.target.value || null, unit_label: null })} placeholder="Unit" list="sw-units" aria-label="Unit" />
                  <Segmented
                    size="sm"
                    value={s.data.category === "Services" || s.data.category === "Materials" ? s.data.category : "Other"}
                    onChange={(v) => patch(s.key, { category: v === "Other" ? (s.data.category === "Services" || s.data.category === "Materials" ? "Other" : s.data.category) : v })}
                    options={[
                      { value: "Services", label: "Services" },
                      { value: "Materials", label: "Materials" },
                      { value: "Other", label: "Other" },
                    ]}
                  />
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-muted">
                  {s.data.tax_mode === "rate" ? <Badge tone="muted">Tax {s.data.tax_rate}%</Badge> : s.data.tax_mode === "not_taxable" ? <Badge tone="muted">Not taxable</Badge> : <Badge tone="muted">Default tax</Badge>}
                  {s.data.currency ? <Badge tone="muted">{s.data.currency}</Badge> : <Badge tone="warn">No currency</Badge>}
                  {s.warnings.map((w) => (
                    <span key={w} className="flex items-center gap-1 text-warn">
                      <CircleAlert className="size-3.5" /> {w}
                    </span>
                  ))}
                </div>
                {s.matches.length ? (
                  <div className="mt-3 rounded-xl border border-border bg-surface-2 p-3">
                    <div className="mb-2 flex items-center gap-1.5 text-[12.5px] font-medium text-fg-2">
                      <Copy className="size-3.5 text-warn" /> Possible duplicate{s.matches.length > 1 ? "s" : ""} in your price book
                    </div>
                    <div className="space-y-1.5">
                      {s.matches.map((m) => (
                        <label key={m.item_id} className="flex cursor-pointer items-center gap-2 text-[12.5px]">
                          <input type="radio" name={`t-${s.key}`} checked={d.target === m.item_id} onChange={() => setDecisions((x) => ({ ...x, [s.key]: { ...x[s.key], target: m.item_id } }))} className="accent-[var(--accent)]" />
                          <span className="font-medium">{m.name}</span>
                          <span className="tabular text-muted">
                            {m.rate ? formatMoney(m.rate, m.currency) : "no rate"}
                            {m.unit ? ` / ${unitShort(m.unit)}` : ""}
                          </span>
                          <Badge tone="muted">{m.reason}</Badge>
                        </label>
                      ))}
                    </div>
                    {target && target.rate && s.data.rate && Number(target.rate) !== Number(s.data.rate) ? (
                      <div className="mt-2 flex items-center gap-2 text-[12.5px]">
                        <span className="text-muted">Rate change:</span>
                        <span className="tabular line-through decoration-faint">{formatMoney(target.rate, target.currency)}</span>
                        <ArrowRight className="size-3.5 text-muted" />
                        <span className="font-semibold tabular text-fg">{formatMoney(s.data.rate, s.data.currency)}</span>
                      </div>
                    ) : null}
                  </div>
                ) : null}
                <div className="mt-3">
                  <Segmented
                    size="sm"
                    stretch={false}
                    value={d.action}
                    onChange={(a) => setDecisions((x) => ({ ...x, [s.key]: { ...x[s.key], action: a } }))}
                    options={[
                      { value: "create", label: "Add as new item" },
                      ...(s.matches.length ? [{ value: "update" as const, label: "Update existing" }] : []),
                      { value: "skip", label: "Skip" },
                    ]}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Sheet>
  );
}
