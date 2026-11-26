"use client";

import { ContactRound, Mail, MapPin, Pencil, Phone, RotateCw, ScanLine, Search, Trash2, UserRoundPlus, Users, X } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { NewClientDialog } from "@/components/clients/NewClientDialog";
import { PageContainer } from "@/components/shell/AppShell";
import { ConfirmDialog } from "@/components/ui/overlays";
import { Badge, Button, Card, EmptyState, PageHeader, Skeleton } from "@/components/ui/primitives";
import { Api } from "@/lib/api";
import type { Client } from "@/lib/types";
import { cn } from "@/lib/utils";

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("") || "?";

function ActionCard({ icon, title, description, onClick, primary }: { icon: React.ReactNode; title: string; description: string; onClick: () => void; primary?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group flex items-start gap-4 rounded-3xl border p-5 text-left shadow-card transition focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-accent/25",
        primary ? "border-accent/40 bg-accent-soft hover:border-accent" : "border-border bg-surface hover:border-accent/50 hover:bg-surface-2",
      )}
    >
      <span className={cn("grid size-12 shrink-0 place-items-center rounded-2xl", primary ? "bg-accent text-white" : "bg-surface-3 text-accent")}>{icon}</span>
      <span className="min-w-0">
        <span className="block text-[17px] font-semibold tracking-tight">{title}</span>
        <span className="mt-0.5 block text-[14px] leading-snug text-muted">{description}</span>
      </span>
    </button>
  );
}

export default function ClientsPage() {
  const [q, setQ] = React.useState("");
  const [items, setItems] = React.useState<Client[] | null>(null);
  const [total, setTotal] = React.useState(0);
  const [error, setError] = React.useState<string | null>(null);
  const [editor, setEditor] = React.useState<{ open: boolean; client: Client | null; start: "form" | "scan" }>({ open: false, client: null, start: "form" });
  const [toDelete, setToDelete] = React.useState<Client | null>(null);
  const [deleting, setDeleting] = React.useState(false);
  const seq = React.useRef(0);

  const load = React.useCallback(() => {
    const mine = ++seq.current;
    setError(null);
    Api.listClients(q.trim() || undefined)
      .then((r) => {
        if (mine !== seq.current) return; // a newer search is already in flight
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e: Error) => {
        if (mine !== seq.current) return;
        setError(e.message);
        setItems([]);
      });
  }, [q]);

  React.useEffect(() => {
    const t = setTimeout(load, q ? 220 : 0);
    return () => clearTimeout(t);
  }, [load, q]);

  const confirmDelete = async () => {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await Api.deleteClient(toDelete.id);
      toast.success(`${toDelete.name} deleted`);
      setItems((cur) => cur?.filter((c) => c.id !== toDelete.id) ?? null);
      setTotal((t) => Math.max(0, t - 1));
      setToDelete(null);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  const filtering = q.trim().length > 0;

  return (
    <PageContainer className="max-w-3xl">
      <PageHeader
        title="Scan Business Card"
        description="Scan a business card to add a client, or enter one by hand. Everything is saved to your client list below."
      />

      <div className="mb-8 grid gap-3 sm:grid-cols-2">
        <ActionCard
          primary
          icon={<ContactRound className="size-6" />}
          title="Scan Business Card"
          description="Take a photo or upload an image. We read it, you review and edit it, then add the client."
          onClick={() => setEditor({ open: true, client: null, start: "scan" })}
        />
        <ActionCard
          icon={<UserRoundPlus className="size-6" />}
          title="New Client"
          description="No business card? Enter the client's details yourself."
          onClick={() => setEditor({ open: true, client: null, start: "form" })}
        />
      </div>

      <div className="mb-3 flex items-baseline justify-between px-1">
        <h2 className="text-[17px] font-semibold tracking-tight">Saved clients</h2>
        {items ? <span className="text-[13px] text-muted">{filtering ? `${total} found` : `${total} total`}</span> : null}
      </div>

      <div className="space-y-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-muted" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search clients"
            aria-label="Search clients"
            className="h-12 w-full rounded-full border border-border bg-surface pl-11 pr-10 text-[16px] shadow-card outline-none transition placeholder:text-faint focus:border-accent focus:ring-3 focus:ring-accent/15"
          />
          {q ? (
            <button type="button" onClick={() => setQ("")} className="absolute right-3 top-1/2 grid size-6 -translate-y-1/2 place-items-center rounded-full bg-surface-3 text-muted" aria-label="Clear search">
              <X className="size-3.5" />
            </button>
          ) : null}
        </div>

        <Card className="overflow-hidden">
          {!items ? (
            <div className="space-y-3 p-4">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-14" />
              ))}
            </div>
          ) : error ? (
            <EmptyState
              icon={<Users className="size-5" />}
              title="Could not load clients"
              description={error}
              action={
                <Button onClick={load}>
                  <RotateCw className="size-4" /> Try again
                </Button>
              }
            />
          ) : items.length === 0 ? (
            <EmptyState
              icon={<Users className="size-5" />}
              title={filtering ? "No matching clients" : "No clients yet"}
              description={filtering ? "Try a different name, company, email or phone." : "Use Scan Business Card or New Client above to add your first client."}
            />
          ) : (
            <ul>
              {items.map((c, i) => (
                <li key={c.id} className="group flex items-center gap-1 pl-4 transition-colors hover:bg-surface-2">
                  <button
                    type="button"
                    onClick={() => setEditor({ open: true, client: c, start: "form" })}
                    className={cn("flex min-w-0 flex-1 items-center gap-3 py-3.5 pr-2 text-left", i < items.length - 1 && "border-b border-border")}
                  >
                    <div className="grid size-10 shrink-0 place-items-center rounded-full bg-accent-soft text-[14px] font-semibold text-accent">{initials(c.name)}</div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-[16px] font-medium tracking-[-0.01em]">{c.name}</span>
                        {c.source === "business_card" ? (
                          <Badge tone="info" className="shrink-0">
                            <ScanLine className="size-3" /> Card
                          </Badge>
                        ) : null}
                      </div>
                      {c.company || c.job_title ? <div className="truncate text-[13.5px] text-muted">{[c.job_title, c.company].filter(Boolean).join(" · ")}</div> : null}
                      <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[12.5px] text-muted">
                        {c.email ? (
                          <span className="inline-flex min-w-0 items-center gap-1">
                            <Mail className="size-3 shrink-0" />
                            <span className="truncate">{c.email}</span>
                          </span>
                        ) : null}
                        {c.phone ? (
                          <span className="inline-flex items-center gap-1">
                            <Phone className="size-3 shrink-0" />
                            {c.phone}
                          </span>
                        ) : null}
                        {c.city ? (
                          <span className="inline-flex items-center gap-1">
                            <MapPin className="size-3 shrink-0" />
                            {[c.city, c.state].filter(Boolean).join(", ")}
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <Pencil className="size-[18px] shrink-0 text-accent opacity-80 transition group-hover:opacity-100" />
                  </button>
                  <Button size="icon-sm" variant="ghost" className="mr-2 text-muted hover:bg-bad-soft hover:text-bad" aria-label={`Delete ${c.name}`} onClick={() => setToDelete(c)}>
                    <Trash2 className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {items && items.length > 0 && !error ? (
          <div className="px-1 text-[12.5px] text-muted">
            {total} client{total === 1 ? "" : "s"}
            {filtering ? " found" : ""}
          </div>
        ) : null}
      </div>

      <NewClientDialog open={editor.open} onOpenChange={(v) => setEditor((s) => ({ ...s, open: v }))} client={editor.client} startWith={editor.start} onSaved={() => load()} />

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => !v && setToDelete(null)}
        title={`Delete ${toDelete?.name ?? "client"}?`}
        description="This removes the client record. Documents you have already scanned are not affected."
        confirmLabel="Delete"
        loading={deleting}
        onConfirm={confirmDelete}
      />
    </PageContainer>
  );
}
