"use client";

import { Copy, History, LoaderCircle } from "lucide-react";
import { Dialog as RDialog } from "radix-ui";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ui/overlays";
import { Chip, Input, SectionLabel, Segmented, Textarea } from "@/components/ui/primitives";
import { Api } from "@/lib/api";
import type { PriceBookItem } from "@/lib/types";
import { cn, currencySymbol, formatDate, formatMoney, unitShort } from "@/lib/utils";

const QUICK_UNITS: { code: string; label: string }[] = [
  { code: "hour", label: "Hour" },
  { code: "visit", label: "Visit" },
  { code: "job", label: "Job" },
  { code: "unit", label: "Unit" },
  { code: "each", label: "Each (ea)" },
  { code: "ft", label: "Foot (ft)" },
  { code: "lb", label: "Pound (lb)" },
];
const MORE_UNITS: { code: string; label: string }[] = [
  { code: "kg", label: "Kilogram (kg)" },
  { code: "m", label: "Meter (m)" },
  { code: "sq_ft", label: "Sq ft" },
  { code: "sq_m", label: "Sq m" },
  { code: "l", label: "Liter (L)" },
  { code: "gal", label: "Gallon" },
  { code: "box", label: "Box" },
  { code: "pack", label: "Pack" },
  { code: "set", label: "Set" },
  { code: "day", label: "Day" },
  { code: "month", label: "Month" },
];
const CURRENCIES = ["USD", "INR", "EUR", "GBP", "AED", "CAD", "AUD", "JPY", "SGD", "NZD", "CHF", "ZAR", "SAR", "MXN", "BRL"];

type Dup = { item_id: string; name: string; rate: string | null; currency: string | null; unit: string | null; reason: string };

export function ItemEditor({
  open,
  onOpenChange,
  item,
  taxPresets,
  categories,
  defaultCurrency,
  onSaved,
  onDeleted,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  item: PriceBookItem | null;
  taxPresets: string[];
  categories: string[];
  defaultCurrency: string | null;
  onSaved: (i: PriceBookItem) => void;
  onDeleted: (id: string) => void;
}) {
  const [name, setName] = React.useState("");
  const [rate, setRate] = React.useState("");
  const [currency, setCurrency] = React.useState<string>("USD");
  const [unit, setUnit] = React.useState<string | null>(null);
  const [customUnit, setCustomUnit] = React.useState("");
  const [category, setCategory] = React.useState("Services");
  const [customCat, setCustomCat] = React.useState("");
  const [tax, setTax] = React.useState<string>("default"); // default | not_taxable | <rate>
  const [customTax, setCustomTax] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [sku, setSku] = React.useState("");
  const [moreUnits, setMoreUnits] = React.useState(false);
  const [dups, setDups] = React.useState<Dup[]>([]);
  const [saving, setSaving] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [history, setHistory] = React.useState<PriceBookItem["history"]>([]);

  React.useEffect(() => {
    if (!open) return;
    setName(item?.name ?? "");
    setRate(item?.rate ?? "");
    setCurrency(item?.currency ?? defaultCurrency ?? "USD");
    const u = item?.unit ?? null;
    setUnit(u === "custom" ? "custom" : u);
    setCustomUnit(u === "custom" ? item?.unit_label ?? "" : "");
    setMoreUnits(!!u && !QUICK_UNITS.some((q) => q.code === u));
    const cats = ["Services", "Materials", ...categories];
    setCategory(item?.category ?? "Services");
    setCustomCat(item && !cats.includes(item.category) ? item.category : "");
    if (item?.tax_mode === "rate" && item.tax_rate) {
      setTax(taxPresets.includes(item.tax_rate) ? item.tax_rate : "custom");
      setCustomTax(taxPresets.includes(item.tax_rate) ? "" : item.tax_rate);
    } else setTax(item?.tax_mode === "not_taxable" ? "not_taxable" : "default");
    setDescription(item?.description ?? "");
    setSku(item?.sku ?? "");
    setHistory([]);
    if (item) Api.priceBookItem(item.id).then((x) => setHistory(x.history ?? [])).catch(() => undefined);
  }, [open, item, defaultCurrency, categories, taxPresets]);

  React.useEffect(() => {
    if (!open || name.trim().length < 3) {
      setDups([]);
      return;
    }
    const t = setTimeout(() => {
      Api.checkDuplicates(name, unit === "custom" ? customUnit : unit, item?.id).then(setDups).catch(() => setDups([]));
    }, 350);
    return () => clearTimeout(t);
  }, [open, name, unit, customUnit, item?.id]);

  const save = async () => {
    if (!name.trim()) {
      toast.error("Enter an item name");
      return;
    }
    setSaving(true);
    const body = {
      name: name.trim(),
      rate: rate.trim() || null,
      currency,
      unit: unit === "custom" ? customUnit.trim() || null : unit,
      category: category === "__custom" ? customCat.trim() || "Other" : category,
      tax_mode: tax === "default" ? "default" : tax === "not_taxable" ? "not_taxable" : "rate",
      tax_rate: tax === "custom" ? customTax.trim() || null : tax === "default" || tax === "not_taxable" ? null : tax,
      description: description.trim() || null,
      sku: sku.trim() || null,
    };
    try {
      const saved = item ? await Api.updateItem(item.id, body) : await Api.createItem(body);
      toast.success(item ? "Item updated" : "Item added to price book");
      onSaved(saved);
      onOpenChange(false);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const catOptions = Array.from(new Set(["Services", "Materials", ...categories.filter((c) => c !== "Services" && c !== "Materials")]));

  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-50 bg-overlay backdrop-blur-[2px] data-[state=open]:animate-fade-in" />
        <RDialog.Content className="fixed inset-x-0 bottom-0 z-50 flex max-h-[94dvh] flex-col overflow-hidden rounded-t-[24px] bg-bg shadow-pop outline-none data-[state=open]:animate-slide-up sm:inset-auto sm:left-1/2 sm:top-1/2 sm:w-full sm:max-w-[560px] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-[24px]">
          <div className="mx-auto mt-2 h-1.5 w-10 rounded-full bg-border-strong sm:hidden" />
          <div className="flex items-center justify-between px-4 py-3">
            <RDialog.Close asChild>
              <button type="button" className="h-10 rounded-full border border-border bg-surface px-4 text-[15px] font-medium text-accent shadow-card">
                Cancel
              </button>
            </RDialog.Close>
            <RDialog.Title className="text-[17px] font-semibold">{item ? "Edit Item" : "New Item"}</RDialog.Title>
            <RDialog.Description className="sr-only">Edit a price book item</RDialog.Description>
            <button type="button" onClick={save} disabled={saving} className="flex h-10 items-center gap-1.5 rounded-full border border-border bg-surface px-4 text-[15px] font-semibold text-accent shadow-card disabled:opacity-50">
              {saving ? <LoaderCircle className="size-4 animate-spin" /> : null}
              Done
            </button>
          </div>

          <div className="scrollbar-thin flex-1 space-y-5 overflow-y-auto px-4 pb-6">
            <div className="overflow-hidden rounded-2xl bg-surface shadow-card">
              <label className="flex items-center gap-4 px-4 py-3">
                <span className="w-16 shrink-0 text-[16px]">Item</span>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. AC inspection" className="min-w-0 flex-1 bg-transparent text-[16px] outline-none placeholder:text-faint" autoFocus={!item} />
              </label>
              <div className="ml-4 h-px bg-border" />
              <div className="flex items-center gap-4 px-4 py-3">
                <span className="w-16 shrink-0 text-[16px]">Rate</span>
                <span className="text-[16px] text-muted">{currencySymbol(currency).trim()}</span>
                <input value={rate} onChange={(e) => setRate(e.target.value)} placeholder="0.00" inputMode="decimal" className="min-w-0 flex-1 bg-transparent text-[16px] tabular outline-none placeholder:text-faint" aria-label="Rate" />
                <select value={currency} onChange={(e) => setCurrency(e.target.value)} className="rounded-lg bg-surface-3 px-2 py-1 text-[13px] font-medium" aria-label="Currency">
                  {Array.from(new Set([currency, ...CURRENCIES])).map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {dups.length ? (
              <div className="rounded-2xl border border-warn/30 bg-warn-soft px-4 py-3 text-[13px]">
                <div className="mb-1 flex items-center gap-1.5 font-medium text-warn">
                  <Copy className="size-3.5" /> Similar item already in your price book
                </div>
                {dups.map((d) => (
                  <div key={d.item_id} className="text-fg-2">
                    {d.name} · <span className="tabular">{d.rate ? formatMoney(d.rate, d.currency) : "no rate"}</span>
                    {d.unit ? ` / ${unitShort(d.unit)}` : ""} <span className="text-muted">({d.reason})</span>
                  </div>
                ))}
              </div>
            ) : null}

            <div className="space-y-2.5">
              <SectionLabel>Unit</SectionLabel>
              <div className="flex flex-wrap gap-2">
                {[...QUICK_UNITS, ...(moreUnits ? MORE_UNITS : [])].map((u) => (
                  <Chip key={u.code} active={unit === u.code} onClick={() => setUnit(unit === u.code ? null : u.code)}>
                    {u.label}
                  </Chip>
                ))}
                <Chip active={unit === "custom"} onClick={() => setUnit("custom")}>
                  Custom…
                </Chip>
                {!moreUnits ? (
                  <button type="button" onClick={() => setMoreUnits(true)} className="h-9 px-2 text-[14px] font-medium text-accent">
                    More units
                  </button>
                ) : null}
              </div>
              {unit === "custom" ? <Input value={customUnit} onChange={(e) => setCustomUnit(e.target.value)} placeholder="e.g. per tonne, per panel" className="max-w-xs" autoFocus /> : null}
            </div>

            <div className="space-y-2.5">
              <SectionLabel>Category</SectionLabel>
              <Segmented
                value={catOptions.includes(category) ? category : "__custom"}
                onChange={(v) => setCategory(v)}
                options={[...catOptions.slice(0, 3).map((c) => ({ value: c, label: c })), { value: "__custom", label: "Other…" }]}
              />
              {category === "__custom" || !catOptions.includes(category) ? (
                <Input value={customCat || (category !== "__custom" ? category : "")} onChange={(e) => { setCustomCat(e.target.value); setCategory("__custom"); }} placeholder="Category name, e.g. Equipment" className="max-w-xs" />
              ) : null}
            </div>

            <div className="space-y-2.5">
              <SectionLabel>Tax</SectionLabel>
              <Segmented
                value={tax}
                onChange={setTax}
                options={[
                  { value: "default", label: "Default" },
                  ...taxPresets.map((p) => ({ value: p, label: `${p}%` })),
                  { value: "not_taxable", label: "Not taxable" },
                  { value: "custom", label: "Custom" },
                ]}
              />
              {tax === "custom" ? (
                <div className="relative max-w-[160px]">
                  <Input value={customTax} onChange={(e) => setCustomTax(e.target.value)} placeholder="e.g. 18" inputMode="decimal" className="pr-8" autoFocus />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-muted">%</span>
                </div>
              ) : null}
            </div>

            <div className="space-y-2.5">
              <SectionLabel>Details</SectionLabel>
              <div className="space-y-2 rounded-2xl bg-surface p-3 shadow-card">
                <Textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Description (optional)" rows={2} />
                <Input value={sku} onChange={(e) => setSku(e.target.value)} placeholder="SKU / item code (optional)" />
              </div>
            </div>

            {history && history.length ? (
              <div className="space-y-2.5">
                <SectionLabel className="flex items-center gap-1.5">
                  <History className="size-3.5" /> Price history
                </SectionLabel>
                <div className="divide-y divide-border overflow-hidden rounded-2xl bg-surface shadow-card">
                  {history.map((h, i) => (
                    <div key={i} className="flex items-center justify-between px-4 py-2.5 text-[13.5px]">
                      <div>
                        <div className="font-medium tabular">
                          {h.rate ? formatMoney(h.rate, h.currency) : "—"}
                          {h.unit ? <span className="text-muted"> / {unitShort(h.unit)}</span> : null}
                        </div>
                        <div className="text-[12px] text-muted">{h.note}</div>
                      </div>
                      <span className="text-[12px] text-faint">{formatDate(h.recorded_at, true)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {item ? (
              <button type="button" onClick={() => setConfirmDelete(true)} className={cn("w-full rounded-2xl bg-surface py-3.5 text-[16px] font-medium text-bad shadow-card transition hover:bg-bad-soft")}>
                Delete Item
              </button>
            ) : null}
          </div>
          <ConfirmDialog
            open={confirmDelete}
            onOpenChange={setConfirmDelete}
            title={`Delete “${item?.name}”?`}
            description="The item and its price history will be removed from your price book."
            confirmLabel="Delete"
            onConfirm={async () => {
              if (!item) return;
              try {
                await Api.deleteItem(item.id);
                toast.success("Item deleted");
                setConfirmDelete(false);
                onDeleted(item.id);
                onOpenChange(false);
              } catch (e) {
                toast.error((e as Error).message);
              }
            }}
          />
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}
