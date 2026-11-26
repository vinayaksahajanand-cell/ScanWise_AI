"use client";

import { ArrowLeft, ArrowRight, Camera, Crop, Info, Loader2, Plus, RotateCw, ScanLine, Trash2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { CameraView, type CameraCapture } from "@/components/scanner/CameraView";
import { CornerEditor } from "@/components/scanner/CornerEditor";
import { type CapturedPage, detectOnImage, loadImage, newId } from "@/components/scanner/types";
import { PageContainer } from "@/components/shell/AppShell";
import { Badge, Button, Card, PageHeader, ProgressBar } from "@/components/ui/primitives";
import { uploadDocuments } from "@/lib/api";
import { FULL_QUAD } from "@/lib/docdetect";
import { rotateCanvas, warpQuad } from "@/lib/warp";

async function makePreview(p: CapturedPage): Promise<string | undefined> {
  if (!p.editable) return undefined;
  try {
    const img = await loadImage(p.url);
    return rotateCanvas(warpQuad(img, p.corners ?? FULL_QUAD, 720), p.rotation).toDataURL("image/jpeg", 0.82);
  } catch {
    return undefined;
  }
}

function ScanInner() {
  const router = useRouter();
  const search = useSearchParams();
  const forPricebook = search.get("for") === "pricebook";
  const [pages, setPages] = React.useState<CapturedPage[]>([]);
  const [view, setView] = React.useState<"camera" | "review">("camera");
  const [editing, setEditing] = React.useState<string | null>(null);
  const [uploading, setUploading] = React.useState(false);
  const [progress, setProgress] = React.useState(0);
  const pagesRef = React.useRef(pages);
  pagesRef.current = pages;

  React.useEffect(() => () => pagesRef.current.forEach((p) => URL.revokeObjectURL(p.url)), []);

  const refreshPreview = React.useCallback(async (page: CapturedPage) => {
    const preview = await makePreview(page);
    setPages((ps) => ps.map((p) => (p.id === page.id ? { ...p, preview } : p)));
  }, []);

  const onCapture = React.useCallback(
    (c: CameraCapture) => {
      const page: CapturedPage = {
        id: newId(),
        blob: c.blob,
        url: URL.createObjectURL(c.blob),
        name: `scan-page-${pagesRef.current.length + 1}.jpg`,
        width: c.width,
        height: c.height,
        corners: c.corners,
        detected: c.corners,
        rotation: 0,
        editable: true,
      };
      setPages((ps) => [...ps, page]);
      refreshPreview(page);
    },
    [refreshPreview],
  );

  const onNativeFiles = React.useCallback(
    async (files: FileList) => {
      for (const f of Array.from(files)) {
        const url = URL.createObjectURL(f);
        let editable = true;
        let corners = null;
        let w = 0;
        let h = 0;
        try {
          const img = await loadImage(url);
          w = img.naturalWidth;
          h = img.naturalHeight;
          corners = detectOnImage(img);
        } catch {
          editable = false;
        }
        const page: CapturedPage = { id: newId(), blob: f, url, name: f.name || `photo-${pagesRef.current.length + 1}.jpg`, width: w, height: h, corners, detected: corners, rotation: 0, editable };
        setPages((ps) => [...ps, page]);
        refreshPreview(page);
      }
      setView("review");
    },
    [refreshPreview],
  );

  const update = (id: string, patch: Partial<CapturedPage>) => {
    setPages((ps) => ps.map((p) => (p.id === id ? { ...p, ...patch } : p)));
    const page = pagesRef.current.find((p) => p.id === id);
    if (page) refreshPreview({ ...page, ...patch });
  };

  const remove = (id: string) => {
    setPages((ps) => {
      const p = ps.find((x) => x.id === id);
      if (p) URL.revokeObjectURL(p.url);
      return ps.filter((x) => x.id !== id);
    });
  };

  const move = (id: string, dir: -1 | 1) => {
    setPages((ps) => {
      const i = ps.findIndex((p) => p.id === id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= ps.length) return ps;
      const copy = [...ps];
      [copy[i], copy[j]] = [copy[j], copy[i]];
      return copy;
    });
  };

  const process = async () => {
    if (!pages.length) return;
    setUploading(true);
    setProgress(0);
    try {
      const res = await uploadDocuments(
        pages.map((p) => p.blob),
        {
          mode: "single",
          source: "camera",
          names: pages.map((p) => p.name),
          pagesMeta: pages.map((p) => ({ corners: p.corners ? p.corners.map((c) => [c.x, c.y]) : null, rotation: p.rotation, auto_crop: p.corners ? false : true })),
        },
        setProgress,
      );
      const doc = res.documents[0];
      toast.success(`Scanning ${pages.length} page${pages.length > 1 ? "s" : ""}…`);
      router.push(`/documents/${doc.id}${forPricebook ? "?pricebook=1" : ""}`);
    } catch (e) {
      toast.error((e as Error).message);
      setUploading(false);
    }
  };

  const editingPage = pages.find((p) => p.id === editing) ?? null;

  if (view === "camera" && !editingPage) {
    return (
      <CameraView
        pagesCount={pages.length}
        lastThumb={pages.at(-1)?.preview ?? pages.at(-1)?.url}
        onCapture={onCapture}
        onDone={() => setView("review")}
        onClose={() => (pages.length ? setView("review") : router.push("/"))}
        onNativeFiles={onNativeFiles}
      />
    );
  }

  return (
    <PageContainer>
      <PageHeader
        eyebrow={forPricebook ? "Price book import" : "Camera scan"}
        title={pages.length ? `Review ${pages.length} page${pages.length > 1 ? "s" : ""}` : "No pages yet"}
        description="Each page is perspective-corrected and enhanced on the server. Fine-tune crops for the best accuracy."
        actions={
          <>
            <Button onClick={() => setView("camera")}>
              <Plus className="size-4" /> Add page
            </Button>
            <Button variant="primary" onClick={process} disabled={!pages.length} loading={uploading}>
              <ScanLine className="size-4" /> Process document
            </Button>
          </>
        }
      />

      {pages.length ? (
        <>
          <Card className="mb-5 flex items-start gap-3 px-4 py-3 text-[13px] text-muted">
            <Info className="mt-0.5 size-4 shrink-0 text-accent" />
            <span>
              Pages are combined into <b className="text-fg-2">one document</b> in this order. Edges were detected automatically — tap <b className="text-fg-2">Crop</b> to adjust. Pages without a crop are auto-detected again on the server.
            </span>
          </Card>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {pages.map((p, i) => (
              <Card key={p.id} className="group overflow-hidden">
                <button type="button" onClick={() => p.editable && setEditing(p.id)} className="relative block aspect-[3/4] w-full bg-surface-3">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {p.preview || p.editable ? <img src={p.preview ?? p.url} alt={`Page ${i + 1}`} className="absolute inset-0 size-full object-contain p-2" /> : (
                    <div className="absolute inset-0 grid place-items-center text-[12px] text-muted">Preview not supported ({p.name.split(".").pop()?.toUpperCase()})</div>
                  )}
                  <Badge tone="accent" className="absolute left-2 top-2">
                    {i + 1}
                  </Badge>
                  {p.corners ? <Badge tone="ok" className="absolute right-2 top-2">Cropped</Badge> : null}
                </button>
                <div className="flex items-center justify-between gap-1 border-t border-border px-1.5 py-1.5">
                  <Button variant="ghost" size="icon-sm" onClick={() => setEditing(p.id)} disabled={!p.editable} aria-label="Adjust crop">
                    <Crop className="size-4" />
                  </Button>
                  <Button variant="ghost" size="icon-sm" onClick={() => update(p.id, { rotation: ((p.rotation + 90) % 360) as CapturedPage["rotation"] })} aria-label="Rotate">
                    <RotateCw className="size-4" />
                  </Button>
                  <Button variant="ghost" size="icon-sm" onClick={() => move(p.id, -1)} disabled={i === 0} aria-label="Move earlier">
                    <ArrowLeft className="size-4" />
                  </Button>
                  <Button variant="ghost" size="icon-sm" onClick={() => move(p.id, 1)} disabled={i === pages.length - 1} aria-label="Move later">
                    <ArrowRight className="size-4" />
                  </Button>
                  <Button variant="ghost" size="icon-sm" className="text-bad hover:bg-bad-soft hover:text-bad" onClick={() => remove(p.id)} aria-label="Delete page">
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </Card>
            ))}
            <button type="button" onClick={() => setView("camera")} className="grid aspect-[3/4] place-items-center rounded-2xl border-2 border-dashed border-border-strong text-muted transition hover:border-accent hover:text-accent">
              <div className="flex flex-col items-center gap-2 text-[13px] font-medium">
                <Camera className="size-6" /> Add another page
              </div>
            </button>
          </div>
        </>
      ) : (
        <Card className="grid place-items-center px-6 py-16 text-center">
          <Camera className="mb-3 size-8 text-faint" />
          <p className="text-[14px] text-muted">All pages were removed.</p>
          <Button className="mt-4" variant="primary" onClick={() => setView("camera")}>
            Open camera
          </Button>
        </Card>
      )}

      {editingPage ? (
        <CornerEditor
          page={editingPage}
          onCancel={() => setEditing(null)}
          onDelete={() => {
            remove(editingPage.id);
            setEditing(null);
          }}
          onSave={(corners, rotation) => {
            update(editingPage.id, { corners, rotation });
            setEditing(null);
          }}
        />
      ) : null}

      {uploading ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-overlay p-6 backdrop-blur-sm">
          <Card className="w-full max-w-sm p-6 text-center">
            <Loader2 className="mx-auto mb-3 size-7 animate-spin text-accent" />
            <div className="text-[15px] font-semibold">{progress < 1 ? "Uploading pages…" : "Starting extraction…"}</div>
            <ProgressBar value={progress} className="mt-4" />
          </Card>
        </div>
      ) : null}
    </PageContainer>
  );
}

export default function ScanPage() {
  return (
    <React.Suspense fallback={null}>
      <ScanInner />
    </React.Suspense>
  );
}
