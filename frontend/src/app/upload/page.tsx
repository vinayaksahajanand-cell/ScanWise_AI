"use client";

import { Crop, FileText, FileUp, Files, ImageIcon, RotateCw, ScanLine, Trash2, UploadCloud, X } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { DocumentRow } from "@/components/documents/DocumentRow";
import { CornerEditor } from "@/components/scanner/CornerEditor";
import { type CapturedPage, loadImage, newId } from "@/components/scanner/types";
import { PageContainer } from "@/components/shell/AppShell";
import { Badge, Button, Card, PageHeader, ProgressBar, Segmented } from "@/components/ui/primitives";
import { Api, uploadDocuments } from "@/lib/api";
import type { DocStatus, DocumentSummary, SystemInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif,image/tiff,application/pdf,.heic,.heif,.pdf,.jpg,.jpeg,.png,.webp,.tif,.tiff";

interface UploadItem extends CapturedPage {
  file: File;
  kind: "image" | "pdf";
}

function prettySize(n: number) {
  if (n > 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(n / 1024))} KB`;
}

export default function UploadPage() {
  const [items, setItems] = React.useState<UploadItem[]>([]);
  const [mode, setMode] = React.useState<"batch" | "single">("batch");
  const [dragging, setDragging] = React.useState(false);
  const [editing, setEditing] = React.useState<string | null>(null);
  const [uploading, setUploading] = React.useState(false);
  const [progress, setProgress] = React.useState(0);
  const [created, setCreated] = React.useState<DocumentSummary[]>([]);
  const [sys, setSys] = React.useState<SystemInfo | null>(null);
  const input = React.useRef<HTMLInputElement>(null);
  const itemsRef = React.useRef(items);
  itemsRef.current = items;

  React.useEffect(() => {
    Api.system().then(setSys).catch(() => undefined);
    return () => itemsRef.current.forEach((i) => URL.revokeObjectURL(i.url));
  }, []);

  const addFiles = React.useCallback(async (files: File[]) => {
    const maxMb = sys?.limits.max_upload_mb ?? 40;
    const next: UploadItem[] = [];
    for (const f of files) {
      const isPdf = f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
      if (f.size > maxMb * 1024 * 1024) {
        toast.error(`${f.name} is larger than ${maxMb} MB`);
        continue;
      }
      const url = URL.createObjectURL(f);
      let editable = false;
      let w = 0;
      let h = 0;
      if (!isPdf) {
        try {
          const img = await loadImage(url);
          editable = true;
          w = img.naturalWidth;
          h = img.naturalHeight;
        } catch {
          editable = false; // HEIC/TIFF in most browsers — the server decodes these
        }
      }
      next.push({ id: newId(), file: f, blob: f, url, name: f.name, width: w, height: h, corners: null, detected: null, rotation: 0, editable, kind: isPdf ? "pdf" : "image" });
    }
    setItems((cur) => [...cur, ...next]);
  }, [sys]);

  React.useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const files = Array.from(e.clipboardData?.files ?? []);
      if (files.length) addFiles(files);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [addFiles]);

  // live status of created documents
  React.useEffect(() => {
    if (!created.length || created.every((d) => !["queued", "processing"].includes(d.status))) return;
    const t = setInterval(async () => {
      const fresh = await Promise.all(
        created.map(async (d): Promise<DocumentSummary> => {
          if (!["queued", "processing"].includes(d.status)) return d;
          const s = await Api.status(d.id).catch(() => null);
          if (!s) return d;
          if (["queued", "processing"].includes(s.status)) {
            return { ...d, status: s.status as DocStatus, progress: s.progress, progress_message: s.progress_message };
          }
          return Api.document(d.id).catch(() => d);
        }),
      );
      setCreated(fresh);
    }, 1500);
    return () => clearInterval(t);
  }, [created]);

  const remove = (id: string) =>
    setItems((cur) => {
      const it = cur.find((x) => x.id === id);
      if (it) URL.revokeObjectURL(it.url);
      return cur.filter((x) => x.id !== id);
    });

  const start = async () => {
    if (!items.length) return;
    setUploading(true);
    setProgress(0);
    try {
      const res = await uploadDocuments(
        items.map((i) => i.file),
        {
          mode: items.length === 1 ? "single" : mode,
          source: "upload",
          pagesMeta: items.map((i) => ({ corners: i.corners ? i.corners.map((c) => [c.x, c.y]) : null, rotation: i.rotation })),
        },
        setProgress,
      );
      setCreated(res.documents);
      const dups = Object.keys(res.duplicates).length;
      toast.success(`${res.documents.length} document${res.documents.length > 1 ? "s" : ""} queued for extraction`, {
        description: dups ? `${dups} file(s) were uploaded before — processing again anyway.` : undefined,
      });
      items.forEach((i) => URL.revokeObjectURL(i.url));
      setItems([]);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const editingItem = items.find((i) => i.id === editing) ?? null;

  return (
    <PageContainer>
      <PageHeader
        title="Upload documents"
        description="Invoices, bills, receipts, quotations, purchase orders and price lists — as photos, scans or PDFs."
        actions={
          <Link href="/scan">
            <Button>
              <ScanLine className="size-4" /> Use camera instead
            </Button>
          </Link>
        }
      />

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          addFiles(Array.from(e.dataTransfer.files));
        }}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        className={cn(
          "group relative cursor-pointer overflow-hidden rounded-3xl border-2 border-dashed px-6 py-12 text-center transition-all",
          dragging ? "scale-[1.01] border-accent bg-accent-soft" : "border-border-strong bg-surface hover:border-accent/60 hover:bg-surface-2",
        )}
      >
        <div className={cn("mx-auto mb-4 grid size-16 place-items-center rounded-2xl transition", dragging ? "bg-accent text-accent-fg" : "bg-accent-soft text-accent group-hover:scale-105")}>
          <UploadCloud className="size-8" />
        </div>
        <div className="text-[17px] font-semibold">{dragging ? "Drop to add files" : "Drag & drop files here"}</div>
        <div className="mt-1 text-[14px] text-muted">
          or <span className="font-medium text-accent">browse your computer</span> · you can also paste an image
        </div>
        <div className="mt-5 flex flex-wrap justify-center gap-1.5">
          {["PDF", "JPG", "PNG", "WEBP", "HEIC", "TIFF"].map((f) => (
            <Badge key={f} tone="muted">
              {f}
            </Badge>
          ))}
        </div>
        <div className="mt-3 text-[12px] text-faint">
          Up to {sys?.limits.max_upload_mb ?? 40} MB per file · {sys?.limits.max_files_per_request ?? 30} files · {sys?.limits.max_pages_per_document ?? 40} pages per document
        </div>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => {
            addFiles(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
      </div>

      {items.length ? (
        <div className="mt-6 space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-[14px] font-semibold">
              {items.length} file{items.length > 1 ? "s" : ""} selected
            </div>
            {items.length > 1 ? (
              <Segmented
                stretch={false}
                value={mode}
                onChange={setMode}
                options={[
                  { value: "batch", label: (<span className="flex items-center gap-1.5"><Files className="size-3.5" /> Separate documents</span>) },
                  { value: "single", label: (<span className="flex items-center gap-1.5"><FileText className="size-3.5" /> One multi-page document</span>) },
                ]}
              />
            ) : null}
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {items.map((it, i) => (
              <Card key={it.id} className="overflow-hidden">
                <div className="relative aspect-[4/5] bg-surface-3">
                  {it.kind === "image" && it.editable ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={it.url} alt={it.name} className="absolute inset-0 size-full object-contain p-2 transition-transform" style={{ transform: `rotate(${it.rotation}deg)` }} />
                  ) : (
                    <div className="absolute inset-0 grid place-items-center">
                      <div className="flex flex-col items-center gap-2 text-muted">
                        {it.kind === "pdf" ? <FileText className="size-10" /> : <ImageIcon className="size-10" />}
                        <span className="text-[11px] font-semibold uppercase">{it.kind === "pdf" ? "PDF" : it.name.split(".").pop()}</span>
                      </div>
                    </div>
                  )}
                  {mode === "single" && items.length > 1 ? <Badge tone="accent" className="absolute left-2 top-2">{i + 1}</Badge> : null}
                  {it.corners ? <Badge tone="ok" className="absolute right-2 top-2">Cropped</Badge> : null}
                  <button type="button" onClick={() => remove(it.id)} className="absolute right-2 bottom-2 grid size-7 place-items-center rounded-full bg-fg/70 text-bg opacity-80 hover:opacity-100" aria-label="Remove">
                    <X className="size-3.5" />
                  </button>
                </div>
                <div className="border-t border-border px-3 py-2">
                  <div className="truncate text-[12.5px] font-medium" title={it.name}>
                    {it.name}
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[11.5px] text-muted">{prettySize(it.file.size)}</span>
                    {it.kind === "image" && it.editable ? (
                      <div className="flex">
                        <Button variant="ghost" size="icon-sm" onClick={() => setEditing(it.id)} aria-label="Crop">
                          <Crop className="size-3.5" />
                        </Button>
                        <Button variant="ghost" size="icon-sm" onClick={() => setItems((c) => c.map((x) => (x.id === it.id ? { ...x, rotation: ((x.rotation + 90) % 360) as UploadItem["rotation"] } : x)))} aria-label="Rotate">
                          <RotateCw className="size-3.5" />
                        </Button>
                      </div>
                    ) : (
                      <span className="text-[11px] text-faint">{it.kind === "pdf" ? "auto text/OCR" : "auto-processed"}</span>
                    )}
                  </div>
                </div>
              </Card>
            ))}
          </div>
          <Card className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-[13px] text-muted">
              {uploading ? (
                <div className="w-64">
                  <div className="mb-1.5 text-fg-2">Uploading… {Math.round(progress * 100)}%</div>
                  <ProgressBar value={progress} />
                </div>
              ) : items.length > 1 && mode === "single" ? (
                "All files will be merged into one document, in the order shown."
              ) : (
                "Each file becomes its own document and is processed in the background."
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setItems([])} disabled={uploading}>
                <Trash2 className="size-4" /> Clear
              </Button>
              <Button variant="primary" onClick={start} loading={uploading}>
                <FileUp className="size-4" /> Extract {items.length > 1 && mode === "batch" ? `${items.length} documents` : "document"}
              </Button>
            </div>
          </Card>
        </div>
      ) : null}

      {created.length ? (
        <Card className="mt-6">
          <div className="flex items-center justify-between px-5 py-3.5">
            <h3 className="text-[14px] font-semibold">Processing</h3>
            <Link href="/documents" className="text-[13px] font-medium text-accent hover:underline">
              All documents
            </Link>
          </div>
          <div className="divide-y divide-border border-t border-border">
            {created.map((d) => (
              <DocumentRow key={d.id} doc={d} />
            ))}
          </div>
        </Card>
      ) : null}

      {editingItem ? (
        <CornerEditor
          page={editingItem}
          onCancel={() => setEditing(null)}
          onSave={(corners, rotation) => {
            setItems((c) => c.map((x) => (x.id === editingItem.id ? { ...x, corners, rotation } : x)));
            setEditing(null);
          }}
        />
      ) : null}
    </PageContainer>
  );
}
