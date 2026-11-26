"use client";

import { FileText, FileUp, ScanLine, Search } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { DocumentRow } from "@/components/documents/DocumentRow";
import { PageContainer } from "@/components/shell/AppShell";
import { Button, Card, EmptyState, Input, PageHeader, Segmented, Skeleton } from "@/components/ui/primitives";
import { Api } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";

const FILTERS = [
  { value: "all", label: "All" },
  { value: "review", label: "Needs review" },
  { value: "ready", label: "Ready" },
  { value: "approved", label: "Approved" },
  { value: "queued,processing", label: "Processing" },
  { value: "failed", label: "Failed" },
] as const;
type Filter = (typeof FILTERS)[number]["value"];

export default function DocumentsPage() {
  const [filter, setFilter] = React.useState<Filter>("all");
  const [q, setQ] = React.useState("");
  const [items, setItems] = React.useState<DocumentSummary[] | null>(null);
  const [total, setTotal] = React.useState(0);
  const [limit, setLimit] = React.useState(40);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(() => {
    Api.documents({ status: filter === "all" ? undefined : filter, q: q || undefined, limit })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [filter, q, limit]);

  React.useEffect(() => {
    const t = setTimeout(load, q ? 250 : 0);
    return () => clearTimeout(t);
  }, [load, q]);

  React.useEffect(() => {
    if (!items?.some((d) => d.status === "queued" || d.status === "processing")) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [items, load]);

  return (
    <PageContainer>
      <PageHeader
        title="Documents"
        description="Every scanned and uploaded document with its extraction status."
        actions={
          <>
            <Link href="/upload">
              <Button>
                <FileUp className="size-4" /> Upload
              </Button>
            </Link>
            <Link href="/scan">
              <Button variant="primary">
                <ScanLine className="size-4" /> Scan
              </Button>
            </Link>
          </>
        }
      />
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative lg:w-80">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search vendor, number or file" className="pl-9" />
        </div>
        <div className="no-scrollbar overflow-x-auto">
          <Segmented stretch={false} value={filter} onChange={setFilter} options={FILTERS.map((f) => ({ value: f.value, label: f.label }))} />
        </div>
      </div>
      <Card className="overflow-hidden">
        {error ? <div className="px-5 py-4 text-[13.5px] text-bad">{error}</div> : null}
        {!items && !error ? (
          <div className="space-y-3 p-5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : null}
        {items && items.length === 0 ? (
          <EmptyState
            icon={<FileText className="size-5" />}
            title={q || filter !== "all" ? "No matching documents" : "No documents yet"}
            description={q || filter !== "all" ? "Try another search or filter." : "Scan or upload an invoice, bill or price list to get started."}
            action={
              !q && filter === "all" ? (
                <Link href="/scan">
                  <Button variant="primary">
                    <ScanLine className="size-4" /> Scan a document
                  </Button>
                </Link>
              ) : null
            }
          />
        ) : null}
        {items && items.length ? (
          <div className="divide-y divide-border">
            {items.map((d) => (
              <DocumentRow key={d.id} doc={d} />
            ))}
          </div>
        ) : null}
        {items && items.length < total ? (
          <div className="border-t border-border p-3 text-center">
            <Button variant="ghost" size="sm" onClick={() => setLimit((l) => l + 40)}>
              Load more ({total - items.length} remaining)
            </Button>
          </div>
        ) : null}
      </Card>
    </PageContainer>
  );
}
