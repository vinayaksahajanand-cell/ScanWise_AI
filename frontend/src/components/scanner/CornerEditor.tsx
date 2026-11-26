"use client";

import { Check, Crop, Eye, Maximize, RotateCcw, RotateCw, Trash2, Wand2, X } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/primitives";
import { FULL_QUAD, type Pt, type Quad } from "@/lib/docdetect";
import { cn } from "@/lib/utils";
import { rotateCanvas, warpQuad } from "@/lib/warp";

import { type CapturedPage, detectOnImage, loadImage } from "./types";

type Rect = { x: number; y: number; w: number; h: number };

function fitRect(cw: number, ch: number, iw: number, ih: number, pad = 28): Rect {
  const s = Math.min((cw - pad * 2) / iw, (ch - pad * 2) / ih);
  const w = iw * s;
  const h = ih * s;
  return { x: (cw - w) / 2, y: (ch - h) / 2, w, h };
}

export function CornerEditor({
  page,
  onSave,
  onCancel,
  onDelete,
}: {
  page: CapturedPage;
  onSave: (corners: Quad | null, rotation: CapturedPage["rotation"]) => void;
  onCancel: () => void;
  onDelete?: () => void;
}) {
  const box = React.useRef<HTMLDivElement>(null);
  const [img, setImg] = React.useState<HTMLImageElement | null>(null);
  const [size, setSize] = React.useState({ w: 0, h: 0 });
  const [quad, setQuad] = React.useState<Quad>(page.corners ?? page.detected ?? FULL_QUAD);
  const [rotation, setRotation] = React.useState(page.rotation);
  const [drag, setDrag] = React.useState<{ kind: "corner" | "edge"; index: number; pointer: Pt } | null>(null);
  const [preview, setPreview] = React.useState(false);
  const [previewUrl, setPreviewUrl] = React.useState<string | null>(null);

  React.useEffect(() => {
    loadImage(page.url).then(setImg).catch(() => setImg(null));
  }, [page.url]);

  React.useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  React.useEffect(() => {
    if (!preview || !img) return;
    const t = setTimeout(() => {
      const c = rotateCanvas(warpQuad(img, quad, 1100), rotation);
      setPreviewUrl(c.toDataURL("image/jpeg", 0.85));
    }, 30);
    return () => clearTimeout(t);
  }, [preview, img, quad, rotation]);

  const rect = img && size.w ? fitRect(size.w, size.h, img.naturalWidth, img.naturalHeight) : null;
  const toScreen = (p: Pt) => ({ x: rect!.x + p.x * rect!.w, y: rect!.y + p.y * rect!.h });
  const toNorm = (x: number, y: number): Pt => ({
    x: Math.min(1, Math.max(0, (x - rect!.x) / rect!.w)),
    y: Math.min(1, Math.max(0, (y - rect!.y) / rect!.h)),
  });

  const onPointerDown = (kind: "corner" | "edge", index: number) => (e: React.PointerEvent) => {
    e.preventDefault();
    (e.target as Element).setPointerCapture(e.pointerId);
    const b = box.current!.getBoundingClientRect();
    setDrag({ kind, index, pointer: { x: e.clientX - b.left, y: e.clientY - b.top } });
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag || !rect) return;
    const b = box.current!.getBoundingClientRect();
    const px = e.clientX - b.left;
    const py = e.clientY - b.top;
    const np = toNorm(px, py);
    setQuad((q) => {
      const next = q.map((p) => ({ ...p })) as Quad;
      if (drag.kind === "corner") {
        next[drag.index] = np;
      } else {
        const prev = toNorm(drag.pointer.x, drag.pointer.y);
        const dx = np.x - prev.x;
        const dy = np.y - prev.y;
        for (const i of [drag.index, (drag.index + 1) % 4]) {
          next[i] = { x: Math.min(1, Math.max(0, q[i].x + dx)), y: Math.min(1, Math.max(0, q[i].y + dy)) };
        }
      }
      return next;
    });
    setDrag({ ...drag, pointer: { x: px, y: py } });
  };
  const onPointerUp = () => setDrag(null);

  const autoDetect = () => {
    if (!img) return;
    const q = detectOnImage(img);
    if (q) setQuad(q);
  };

  const isFull = quad.every((p, i) => Math.abs(p.x - FULL_QUAD[i].x) < 0.002 && Math.abs(p.y - FULL_QUAD[i].y) < 0.002);
  const pts = rect ? quad.map(toScreen) : [];
  const loupe = drag && rect && img ? drag.pointer : null;
  const zoom = 2.6;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[#07090d] text-white">
      <div className="flex items-center justify-between gap-2 px-3 py-3 sm:px-5">
        <Button variant="ghost" className="text-white hover:bg-white/10 hover:text-white" onClick={onCancel}>
          <X className="size-4" /> Cancel
        </Button>
        <div className="text-center">
          <div className="text-[15px] font-semibold">Adjust page</div>
          <div className="text-[12px] text-white/55">Drag the corners to the paper edges</div>
        </div>
        <Button variant="primary" onClick={() => onSave(isFull ? null : quad, rotation)}>
          <Check className="size-4" /> Done
        </Button>
      </div>

      <div
        ref={box}
        className="relative min-h-0 flex-1 touch-none select-none"
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {preview && previewUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={previewUrl} alt="Perspective-corrected preview" className="absolute inset-0 m-auto max-h-[calc(100%-40px)] max-w-[calc(100%-40px)] rounded-md object-contain shadow-2xl" />
        ) : rect && img ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={page.url} alt="Captured page" className="absolute" style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }} draggable={false} />
            <svg className="absolute inset-0 size-full">
              <path
                fillRule="evenodd"
                fill="rgb(0 0 0 / 0.55)"
                d={`M${rect.x},${rect.y}h${rect.w}v${rect.h}h${-rect.w}z M${pts.map((p) => `${p.x},${p.y}`).join(" L")} z`}
              />
              <polygon points={pts.map((p) => `${p.x},${p.y}`).join(" ")} fill="rgb(79 139 255 / 0.10)" stroke="#4f8bff" strokeWidth={2.5} strokeLinejoin="round" />
              {pts.map((p, i) => {
                const n = pts[(i + 1) % 4];
                const mx = (p.x + n.x) / 2;
                const my = (p.y + n.y) / 2;
                return (
                  <g key={`e${i}`} onPointerDown={onPointerDown("edge", i)} className="cursor-move">
                    <circle cx={mx} cy={my} r={22} fill="transparent" />
                    <rect x={mx - 11} y={my - 4} width={22} height={8} rx={4} fill="#fff" stroke="#4f8bff" strokeWidth={2} transform={`rotate(${(Math.atan2(n.y - p.y, n.x - p.x) * 180) / Math.PI} ${mx} ${my})`} />
                  </g>
                );
              })}
              {pts.map((p, i) => (
                <g key={`c${i}`} onPointerDown={onPointerDown("corner", i)} className="cursor-grab active:cursor-grabbing">
                  <circle cx={p.x} cy={p.y} r={24} fill="transparent" />
                  <circle cx={p.x} cy={p.y} r={drag?.kind === "corner" && drag.index === i ? 13 : 11} fill="rgb(79 139 255 / 0.25)" stroke="#fff" strokeWidth={2.5} />
                </g>
              ))}
            </svg>
            {loupe ? (
              <div
                className="pointer-events-none absolute size-32 overflow-hidden rounded-full border-[3px] border-white shadow-2xl"
                style={{
                  left: Math.max(8, Math.min(size.w - 136, loupe.x - 150)),
                  top: Math.max(8, loupe.y - 150),
                  backgroundImage: `url(${page.url})`,
                  backgroundRepeat: "no-repeat",
                  backgroundSize: `${rect.w * zoom}px ${rect.h * zoom}px`,
                  backgroundPosition: `${-(loupe.x - rect.x) * zoom + 64}px ${-(loupe.y - rect.y) * zoom + 64}px`,
                }}
              >
                <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-[#4f8bff]" />
                <div className="absolute left-0 top-1/2 h-px w-full -translate-y-1/2 bg-[#4f8bff]" />
              </div>
            ) : null}
          </>
        ) : (
          <div className="absolute inset-0 grid place-items-center text-white/60">Loading image…</div>
        )}
      </div>

      <div className="safe-bottom flex flex-wrap items-center justify-center gap-1.5 px-3 py-3 sm:gap-2">
        {[
          { icon: Wand2, label: "Auto-detect", onClick: autoDetect, active: false },
          { icon: Maximize, label: "Full page", onClick: () => setQuad(FULL_QUAD), active: isFull },
          { icon: RotateCcw, label: "Left", onClick: () => setRotation(((rotation + 270) % 360) as CapturedPage["rotation"]), active: false },
          { icon: RotateCw, label: "Right", onClick: () => setRotation(((rotation + 90) % 360) as CapturedPage["rotation"]), active: false },
          { icon: preview ? Crop : Eye, label: preview ? "Edit crop" : "Preview", onClick: () => setPreview((v) => !v), active: preview },
        ].map((b) => (
          <button
            key={b.label}
            type="button"
            onClick={b.onClick}
            className={cn("flex min-w-[64px] flex-col items-center gap-1 rounded-xl px-3 py-2 text-[11.5px] font-medium transition", b.active ? "bg-white/15 text-white" : "text-white/75 hover:bg-white/10 hover:text-white")}
          >
            <b.icon className="size-5" />
            {b.label}
          </button>
        ))}
        {onDelete ? (
          <button type="button" onClick={onDelete} className="flex min-w-[64px] flex-col items-center gap-1 rounded-xl px-3 py-2 text-[11.5px] font-medium text-[#ff8a80] hover:bg-white/10">
            <Trash2 className="size-5" /> Delete
          </button>
        ) : null}
      </div>
      {rotation ? <div className="pointer-events-none absolute left-1/2 top-16 -translate-x-1/2 rounded-full bg-white/15 px-3 py-1 text-[12px]">Rotated {rotation}°</div> : null}
    </div>
  );
}
