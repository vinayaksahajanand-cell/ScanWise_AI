"use client";

import { BookOpen, Check, ChevronDown, FileJson, FileSpreadsheet, FileText, FileUp, Pencil, Plus, ScanLine, Search, Sheet as SheetIcon, Tag, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ItemEditor } from "@/components/pricebook/ItemEditor";
import { StagingDialog } from "@/components/pricebook/StagingDialog";
import { PageContainer } from "@/components/shell/AppShell";
import { ConfirmDialog, Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/overlays";
import { Button, Card, EmptyState, PageHeader, Segmented, Skeleton } from "@/components/ui/primitives";
import { Api } from "@/lib/api";
import type { PriceBookItem, StagedItem, UserSettings } from "@/lib/types";
import { cn, downloadUrl, formatMoney, unitShort } from "@/lib/utils";

export default function PriceBookPage() {
  return (
    <React.Suspense fallback={null}>
      <PriceBookInner />
    </React.Suspense>
  );
}

function PriceBookInner() {
  const search = useSearchParams();
  const deepLinked = React.useRef(false);
  const [q, setQ] = React.useState("");
  const [category, setCategory] = React.useState("All");
  const [items, setItems] = React.useState<PriceBookItem[] | null>(null);
  const [categories, setCategories] = React.useState<string[]>(["Services", "Materials"]);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [editor, setEditor] = React.useState<{ open: boolean; item: PriceBookItem | null }>({ open: false, item: null });
  const [staging, setStaging] = React.useState<(() => Promise<StagedItem[]>) | null>(null);
  const [confirmBulk, setConfirmBulk] = React.useState(false);
  const [settings, setSettings] = React.useState<UserSettings | null>(null);
  const importRef = React.useRef<HTMLInputElement>(null);

  const load = React.useCallback(() => {
    Api.priceBook(q || undefined, category)
      .then((r) => {
        setItems(r.items);
        setCategories(r.categories);
      })
      .catch((e: Error) => {
        toast.error(e.message);
        setItems([]);
      });
  }, [q, category]);

  React.useEffect(() => {
    const t = setTimeout(load, q ? 220 : 0);
    return () => clearTimeout(t);
  }, [load, q]);

  React.useEffect(() => {
    Api.settings().then(setSettings).catch(() => undefined);
  }, []);

  // deep links: /price-book?item=<id> opens the editor, ?new=1 opens a blank item
  React.useEffect(() => {
    if (deepLinked.current || !items) return;
    const id = search.get("item");
    if (id) {
      const it = items.find((x) => x.id === id);
      if (it) setEditor({ open: true, item: it });
    } else if (search.get("new") === "1") {
      setEditor({ open: true, item: null });
    }
    deepLinked.current = true;
  }, [items, search]);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const segs = ["All", "Services", "Materials", ...categories.filter((c) => c !== "Services" && c !== "Materials")].slice(0, 5);
  const commonCurrency = (() => {
    const counts: Record<string, number> = {};
    (items ?? []).forEach((i) => i.currency && (counts[i.currency] = (counts[i.currency] ?? 0) + 1));
    return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
  })();

  return (
    <PageContainer className="max-w-3xl">
      <PageHeader
        title="Price Book"
        description="Your services and materials with rates, units and tax — built from scanned bills and price lists."
        actions={
          <>
            <Menu>
              <MenuTrigger asChild>
                <Button>
                  More <ChevronDown className="size-3.5 text-muted" />
                </Button>
              </MenuTrigger>
              <MenuContent>
                <MenuLabel>Import</MenuLabel>
                <MenuItem icon={<FileUp />} onSelect={() => importRef.current?.click()}>
                  Bulk import CSV / Excel
                </MenuItem>
                <MenuLabel>Export</MenuLabel>
                <MenuItem icon={<FileSpreadsheet />} onSelect={() => downloadUrl(Api.priceBookExportUrl("xlsx", category))}>Excel (.xlsx)</MenuItem>
                <MenuItem icon={<SheetIcon />} onSelect={() => downloadUrl(Api.priceBookExportUrl("csv", category))}>CSV</MenuItem>
                <MenuItem icon={<FileJson />} onSelect={() => downloadUrl(Api.priceBookExportUrl("json", category))}>JSON</MenuItem>
                <MenuItem icon={<FileText />} onSelect={() => downloadUrl(Api.priceBookExportUrl("pdf", category))}>PDF</MenuItem>
              </MenuContent>
            </Menu>
            <Button variant="primary" onClick={() => setEditor({ open: true, item: null })}>
              <Plus className="size-4" /> Add item
            </Button>
          </>
        }
      />

      <div className="space-y-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-muted" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search items"
            className="h-12 w-full rounded-full border border-border bg-surface pl-11 pr-10 text-[16px] shadow-card outline-none transition placeholder:text-faint focus:border-accent focus:ring-3 focus:ring-accent/15"
          />
          {q ? (
            <button type="button" onClick={() => setQ("")} className="absolute right-3 top-1/2 grid size-6 -translate-y-1/2 place-items-center rounded-full bg-surface-3 text-muted" aria-label="Clear search">
              <X className="size-3.5" />
            </button>
          ) : null}
        </div>
        <Segmented value={category} onChange={(c) => { setCategory(c); setSelected(new Set()); }} options={segs.map((c) => ({ value: c, label: c }))} />

        <Link href="/scan?for=pricebook" className="flex items-center gap-3 rounded-2xl bg-surface px-4 py-4 text-[16px] font-medium text-accent shadow-card transition hover:bg-accent-soft">
          <ScanLine className="size-5" /> Scan Bill or Price List
        </Link>

        <Card className="overflow-hidden">
          {!items ? (
            <div className="space-y-3 p-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-12" />
              ))}
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              icon={<BookOpen className="size-5" />}
              title={q || category !== "All" ? "No matching items" : "Your price book is empty"}
              description={q || category !== "All" ? "Try a different search or category." : "Scan a bill or price list, import a CSV, or add items by hand."}
              action={
                !q && category === "All" ? (
                  <div className="flex gap-2">
                    <Link href="/scan?for=pricebook">
                      <Button variant="primary">
                        <ScanLine className="size-4" /> Scan price list
                      </Button>
                    </Link>
                    <Button onClick={() => setEditor({ open: true, item: null })}>
                      <Plus className="size-4" /> Add item
                    </Button>
                  </div>
                ) : null
              }
            />
          ) : (
            <ul>
              {items.map((it, i) => {
                const isSel = selected.has(it.id);
                const unit = it.unit === "custom" ? it.unit_label : unitShort(it.unit);
                return (
                  <li key={it.id} className={cn("group flex items-center gap-3 pl-4 transition-colors", isSel ? "bg-accent-soft/50" : "hover:bg-surface-2")}>
                    <button
                      type="button"
                      onClick={() => toggle(it.id)}
                      aria-label={isSel ? `Deselect ${it.name}` : `Select ${it.name}`}
                      className={cn("grid size-[26px] shrink-0 place-items-center rounded-full border-2 transition", isSel ? "border-accent bg-accent text-accent-fg" : "border-border-strong hover:border-accent")}
                    >
                      {isSel ? <Check className="size-3.5" strokeWidth={3} /> : null}
                    </button>
                    <button type="button" onClick={() => setEditor({ open: true, item: it })} className={cn("flex min-w-0 flex-1 items-center gap-3 py-3.5 pr-4 text-left", i < items.length - 1 && "border-b border-border")}>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[16.5px] font-medium tracking-[-0.01em]">{it.name}</div>
                        <div className="truncate text-[13.5px] text-muted">
                          {it.category}
                          {it.tax_mode === "rate" ? ` · ${it.tax_rate}% tax` : it.tax_mode === "not_taxable" ? " · not taxable" : ""}
                          {it.sku ? ` · ${it.sku}` : ""}
                        </div>
                      </div>
                      <div className="shrink-0 text-right text-[15.5px] tabular text-fg-2">
                        {it.rate ? formatMoney(it.rate, it.currency) : <span className="text-faint">no rate</span>}
                        {unit ? <span className="text-muted"> / {unit}</span> : null}
                      </div>
                      <Pencil className="size-[18px] shrink-0 text-accent opacity-80 transition group-hover:opacity-100" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
        {items && items.length ? <div className="px-1 text-[12.5px] text-muted">{items.length} item{items.length > 1 ? "s" : ""}</div> : null}
      </div>

      {selected.size ? (
        <div className="fixed inset-x-0 bottom-20 z-40 flex justify-center px-4 lg:bottom-6 lg:pl-[248px]">
          <div className="flex items-center gap-1.5 rounded-2xl border border-border bg-surface/95 p-1.5 pl-4 shadow-pop backdrop-blur animate-slide-up">
            <span className="mr-2 text-[13.5px] font-medium">{selected.size} selected</span>
            <Menu>
              <MenuTrigger asChild>
                <Button size="sm">
                  <Tag className="size-3.5" /> Category
                </Button>
              </MenuTrigger>
              <MenuContent align="center">
                {Array.from(new Set(["Services", "Materials", ...categories])).map((c) => (
                  <MenuItem
                    key={c}
                    onSelect={() =>
                      Api.bulkCategory([...selected], c)
                        .then((r) => {
                          toast.success(`${r.updated} item(s) moved to ${c}`);
                          setSelected(new Set());
                          load();
                        })
                        .catch((e: Error) => toast.error(e.message))
                    }
                  >
                    {c}
                  </MenuItem>
                ))}
              </MenuContent>
            </Menu>
            <Button size="sm" variant="ghost" className="text-bad hover:bg-bad-soft hover:text-bad" onClick={() => setConfirmBulk(true)}>
              <Trash2 className="size-3.5" /> Delete
            </Button>
            <Button size="icon-sm" variant="ghost" onClick={() => setSelected(new Set())} aria-label="Clear selection">
              <X className="size-4" />
            </Button>
          </div>
        </div>
      ) : null}

      <input
        ref={importRef}
        type="file"
        accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) setStaging(() => () => Api.importFile(f));
          e.target.value = "";
        }}
      />

      <ItemEditor
        open={editor.open}
        onOpenChange={(v) => setEditor((s) => ({ ...s, open: v }))}
        item={editor.item}
        taxPresets={settings?.tax_presets ?? ["10", "5"]}
        categories={Array.from(new Set([...(settings?.categories ?? []), ...categories]))}
        defaultCurrency={editor.item?.currency ?? settings?.default_currency_for_new_items ?? commonCurrency}
        onSaved={() => load()}
        onDeleted={(id) => setItems((cur) => cur?.filter((x) => x.id !== id) ?? null)}
      />
      {staging ? <StagingDialog open title="Import into price book" onOpenChange={(v) => !v && setStaging(null)} load={staging} onDone={load} /> : null}
      <ConfirmDialog
        open={confirmBulk}
        onOpenChange={setConfirmBulk}
        title={`Delete ${selected.size} item${selected.size > 1 ? "s" : ""}?`}
        description="Selected items and their price history will be removed."
        confirmLabel="Delete"
        onConfirm={() =>
          Api.bulkDelete([...selected])
            .then((r) => {
              toast.success(`${r.deleted} item(s) deleted`);
              setSelected(new Set());
              setConfirmBulk(false);
              load();
            })
            .catch((e: Error) => toast.error(e.message))
        }
      />
    </PageContainer>
  );
}
