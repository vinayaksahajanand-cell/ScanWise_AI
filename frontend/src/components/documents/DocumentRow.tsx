"use client";

import { Camera, ChevronRight, FileText, ImageIcon } from "lucide-react";
import Link from "next/link";

import type { DocumentSummary } from "@/lib/types";
import { cn, formatDate, formatMoney, timeAgo, titleCase } from "@/lib/utils";

import { DocStatusBadge } from "../status";
import { ProgressBar } from "../ui/primitives";

export function DocThumb({ doc, className }: { doc: DocumentSummary; className?: string }) {
  return (
    <div className={cn("relative grid size-12 shrink-0 place-items-center overflow-hidden rounded-xl border border-border bg-surface-2", className)}>
      {doc.thumb_url && doc.status !== "queued" ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={doc.thumb_url} alt="" className="size-full object-cover object-top" loading="lazy" />
      ) : doc.source === "camera" ? (
        <Camera className="size-5 text-faint" />
      ) : doc.filename.toLowerCase().endsWith(".pdf") ? (
        <FileText className="size-5 text-faint" />
      ) : (
        <ImageIcon className="size-5 text-faint" />
      )}
      {doc.page_count > 1 ? (
        <span className="absolute bottom-0.5 right-0.5 rounded-md bg-fg/80 px-1 text-[9.5px] font-semibold text-bg">{doc.page_count}p</span>
      ) : null}
    </div>
  );
}

export function DocumentRow({ doc, compact }: { doc: DocumentSummary; compact?: boolean }) {
  const busy = doc.status === "queued" || doc.status === "processing";
  const title = doc.vendor_name || doc.filename;
  return (
    <Link
      href={`/documents/${doc.id}`}
      className="group flex items-center gap-3.5 px-4 py-3 transition-colors hover:bg-surface-2 sm:px-5"
    >
      <DocThumb doc={doc} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[14px] font-medium text-fg">{title}</span>
          {doc.document_number ? <span className="hidden truncate text-[12.5px] text-muted sm:inline">#{doc.document_number}</span> : null}
        </div>
        {busy ? (
          <div className="mt-1.5 flex items-center gap-2.5">
            <ProgressBar value={doc.progress || 0.04} className="max-w-48" />
            <span className="truncate text-[12px] text-muted">{doc.progress_message || "Queued"}</span>
          </div>
        ) : (
          <div className="mt-0.5 flex items-center gap-1.5 truncate text-[12.5px] text-muted">
            <span>{titleCase(doc.document_type) || "Document"}</span>
            {doc.document_date ? <span>· {formatDate(doc.document_date)}</span> : null}
            {!compact && doc.line_item_count ? <span className="hidden sm:inline">· {doc.line_item_count} items</span> : null}
            {doc.status === "failed" && doc.error ? <span className="truncate text-bad">· {doc.error}</span> : null}
          </div>
        )}
      </div>
      <div className="hidden flex-col items-end gap-1 sm:flex">
        <span className="tabular text-[14px] font-semibold text-fg">{doc.grand_total ? formatMoney(doc.grand_total, doc.currency) : ""}</span>
        <span className="text-[11.5px] text-faint">{timeAgo(doc.created_at)}</span>
      </div>
      <div className="flex w-[124px] shrink-0 justify-end">
        <DocStatusBadge status={doc.status} />
      </div>
      <ChevronRight className="hidden size-4 text-faint transition group-hover:translate-x-0.5 group-hover:text-muted md:block" />
    </Link>
  );
}
