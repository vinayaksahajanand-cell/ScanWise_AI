"use client";

import { ChevronDown, Download, FileJson, FileSpreadsheet, Search, Sheet as SheetIcon, Table2 } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { PageContainer } from "@/components/shell/AppShell";
import { FieldStatusDot } from "@/components/status";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/overlays";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Segmented, Skeleton } from "@/components/ui/primitives";
import { Api } from "@/lib/api";
import type { ExtractedRow, FieldStatus } from "@/lib/types";
import { downloadUrl, formatDate, formatMoney, formatNumber, unitShort } from "@/lib/utils";

export default function ExtractedPage() {
  const [rows, setRows] = React.useState<ExtractedRow[] | null>(null);
  const [q, setQ] = React.useState("");
  const [category, setCategory] = React.useState("All");
  const [currency, setCurrency] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [all, setAll] = React.useState<ExtractedRow[]>([]);

  const params = React.useMemo(
    () => ({ q: q || undefined, category: category === "All" ? undefined : category, currency: currency || undefined, status: status || undefined }),
    [q, category, currency, status],
  );

  React.useEffect(() => {
    Api.lineItems({}).then((r) => setAll(r.items)).catch(() => undefined);
  }, []);

  React.useEffect(() => {
    const t = setTimeout(() => {
      Api.lineItems(params)
        .then((r) => setRows(r.items))
        .catch(() => setRows([]));
    }, q ? 250 : 0);
    return () => clearTimeout(t);
  }, [params, q]);

  const currencies = Array.from(new Set(all.map((r) => r.currency).filter(Boolean))) as string[];
  const categories = Array.from(new Set(all.map((r) => r.category).filter(Boolean))) as string[];
  const catOptions = ["All", ...(["Services", "Materials"].filter((c) => categories.includes(c) || true)), ...categories.filter((c) => !["Services", "Materials"].includes(c))];

  return (
    <PageContainer wide>
      <PageHeader
        title="Extracted data"
        description="Every line item read from your documents, with its review status. Values are shown in each document's original currency."
        actions={
          <Menu>
            <MenuTrigger asChild>
              <Button>
                <Download className="size-4" /> Export <ChevronDown className="size-3.5 text-muted" />
              </Button>
            </MenuTrigger>
            <MenuContent>
              <MenuItem icon={<FileSpreadsheet />} onSelect={() => downloadUrl(Api.lineItemsExportUrl("xlsx", params))}>Excel (.xlsx)</MenuItem>
              <MenuItem icon={<SheetIcon />} onSelect={() => downloadUrl(Api.lineItemsExportUrl("csv", params))}>CSV</MenuItem>
              <MenuItem icon={<FileJson />} onSelect={() => downloadUrl(Api.lineItemsExportUrl("json", params))}>JSON</MenuItem>
            </MenuContent>
          </Menu>
        }
      />
      <div className="mb-4 flex flex-col gap-3 xl:flex-row xl:items-center">
        <div className="relative xl:w-80">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search items or vendors" className="pl-9" />
        </div>
        <div className="no-scrollbar overflow-x-auto">
          <Segmented stretch={false} value={category} onChange={setCategory} options={Array.from(new Set(catOptions)).map((c) => ({ value: c, label: c }))} />
        </div>
        <div className="flex gap-2">
          <select value={currency} onChange={(e) => setCurrency(e.target.value)} className="h-9 rounded-xl border border-border bg-surface px-3 text-[13.5px]" aria-label="Currency filter">
            <option value="">All currencies</option>
            {currencies.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-9 rounded-xl border border-border bg-surface px-3 text-[13.5px]" aria-label="Status filter">
            <option value="">Any status</option>
            <option value="needs_review">Needs review</option>
            <option value="mismatch">Calculation mismatch</option>
            <option value="high_confidence">High confidence</option>
            <option value="verified">Verified</option>
          </select>
        </div>
      </div>
      <Card className="overflow-hidden">
        {!rows ? (
          <div className="space-y-3 p-5">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-9" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState icon={<Table2 className="size-5" />} title="No line items" description="Items appear here once documents have been processed." />
        ) : (
          <div className="scrollbar-thin overflow-x-auto">
            <table className="w-full min-w-[980px] text-[13px]">
              <thead>
                <tr className="border-b border-border bg-surface-2 text-left text-[11px] font-semibold uppercase tracking-wider text-muted">
                  <th className="px-4 py-2.5">Item</th>
                  <th className="px-3 py-2.5">Document</th>
                  <th className="px-3 py-2.5 text-right">Qty</th>
                  <th className="px-3 py-2.5">Unit</th>
                  <th className="px-3 py-2.5 text-right">Rate</th>
                  <th className="px-3 py-2.5 text-right">Tax</th>
                  <th className="px-3 py-2.5 text-right">Amount</th>
                  <th className="px-3 py-2.5">Category</th>
                  <th className="px-4 py-2.5">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((r) => (
                  <tr key={`${r.document_id}-${r.item_id}`} className="hover:bg-surface-2">
                    <td className="max-w-[320px] px-4 py-2.5">
                      <div className="truncate font-medium">{r.name || "—"}</div>
                      {r.description ? <div className="truncate text-[12px] text-muted">{r.description}</div> : null}
                    </td>
                    <td className="px-3 py-2.5">
                      <Link href={`/documents/${r.document_id}`} className="block max-w-[220px] truncate text-accent hover:underline">
                        {r.vendor || "Document"} {r.document_number ? `#${r.document_number}` : ""}
                      </Link>
                      <div className="text-[11.5px] text-faint">{formatDate(r.document_date)}</div>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular">{formatNumber(r.quantity)}</td>
                    <td className="px-3 py-2.5 text-muted">{unitShort(r.unit, r.unit) || "—"}</td>
                    <td className="px-3 py-2.5 text-right tabular">{formatMoney(r.rate, r.currency)}</td>
                    <td className="px-3 py-2.5 text-right tabular text-muted">{r.tax_rate ? `${r.tax_rate}%` : "—"}</td>
                    <td className="px-3 py-2.5 text-right font-medium tabular">{formatMoney(r.amount, r.currency)}</td>
                    <td className="px-3 py-2.5">{r.category ? <Badge tone={r.category === "Services" ? "info" : r.category === "Materials" ? "violet" : "muted"}>{r.category}</Badge> : "—"}</td>
                    <td className="px-4 py-2.5">
                      <span className="flex items-center gap-1.5 text-[12px] text-muted">
                        <FieldStatusDot status={r.status as FieldStatus} />
                        {r.status.replace("_", " ")}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {rows && rows.length ? <div className="mt-3 text-right text-[12.5px] text-muted">{rows.length} line items</div> : null}
    </PageContainer>
  );
}
