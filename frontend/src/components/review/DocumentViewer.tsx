"use client";

import { ChevronLeft, ChevronRight, ImageIcon, Layers, Maximize, MousePointerClick, RotateCw, ScanText, StretchHorizontal, ZoomIn, ZoomOut } from "lucide-react";
import * as React from "react";

import { Tip } from "@/components/ui/overlays";
import type { FieldRef } from "@/lib/fields";
import type { BBox, DocumentDetail, FieldStatus, OcrToken } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_COLOR: Record<FieldStatus, string> = {
  high_confidence: "var(--accent)",
  verified: "var(--ok)",
  needs_review: "var(--warn)",
  unreadable: "var(--violet)",
  mismatch: "var(--bad)",
  missing: "var(--faint)",
};

type View = { s: number; tx: number; ty: number };

export function DocumentViewer({
  doc,
  page,
  onPage,
  fields,
  selected,
  rowBox,
  onSelectPath,
  tokens,
  pickMode,
  onPickToken,
  processing,
}: {
  doc: DocumentDetail;
  page: number;
  onPage: (p: number) => void;
  fields: FieldRef[];
  selected: { path: string; bbox: BBox | null; nonce: number } | null;
  rowBox?: BBox | null;
  onSelectPath: (path: string) => void;
  tokens: OcrToken[] | null;
  pickMode: string | null;
  onPickToken: (t: OcrToken) => void;
  processing?: boolean;
}) {
  const wrap = React.useRef<HTMLDivElement>(null);
  const [box, setBox] = React.useState({ w: 0, h: 0 });
  const [nat, setNat] = React.useState({ w: 0, h: 0 });
  const [rot, setRot] = React.useState(0);
  const [view, setView] = React.useState<View>({ s: 1, tx: 0, ty: 0 });
  const [animate, setAnimate] = React.useState(false);
  const [variant, setVariant] = React.useState<"processed" | "original">("processed");
  const [showFields, setShowFields] = React.useState(true);
  const [showTokens, setShowTokens] = React.useState(false);
  const [hover, setHover] = React.useState<{ t: OcrToken; x: number; y: number } | null>(null);
  const viewRef = React.useRef(view);
  viewRef.current = view;

  const info = doc.pages.find((p) => p.page_no === page) ?? doc.pages[0];
  const src = processing || variant === "original" || !info?.image_url ? info?.original_url : info.image_url;
  const RW = rot % 180 ? nat.h : nat.w;
  const RH = rot % 180 ? nat.w : nat.h;
  const showOverlay = !processing && variant === "processed" && !!info?.image_url;

  React.useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fitScale = React.useCallback(
    (mode: "page" | "width") => {
      const pad = 20;
      if (!RW || !box.w) return 1;
      return mode === "page" ? Math.min((box.w - pad * 2) / RW, (box.h - pad * 2) / RH) : (box.w - pad * 2) / RW;
    },
    [RW, RH, box.w, box.h],
  );

  const fit = React.useCallback(
    (mode: "page" | "width", anim = true) => {
      if (!RW || !box.w) return;
      const s = fitScale(mode);
      setAnimate(anim);
      setView({ s, tx: (box.w - RW * s) / 2, ty: mode === "page" ? (box.h - RH * s) / 2 : 20 });
    },
    [RW, RH, box.w, box.h, fitScale],
  );

  React.useEffect(() => {
    if (!RW || !box.w) return;
    const sw = fitScale("width");
    // tall pages read best at fit-width unless that is enormous
    fit(RH * sw > box.h * 2.2 ? "page" : "width", false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nat.w, nat.h, rot, page, box.w > 0]);

  const zoomAt = React.useCallback(
    (factor: number, cx: number, cy: number) => {
      const v = viewRef.current;
      const min = fitScale("page") * 0.5;
      const s2 = Math.min(10, Math.max(min, v.s * factor));
      setAnimate(false);
      setView({ s: s2, tx: cx - ((cx - v.tx) * s2) / v.s, ty: cy - ((cy - v.ty) * s2) / v.s });
    },
    [fitScale],
  );

  React.useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) {
        zoomAt(Math.exp(-e.deltaY * 0.0025), e.clientX - r.left, e.clientY - r.top);
      } else {
        setAnimate(false);
        setView((v) => ({ ...v, tx: v.tx - e.deltaX, ty: v.ty - e.deltaY }));
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  // drag to pan + pinch to zoom
  const pointers = React.useRef(new Map<number, { x: number; y: number }>());
  const lastPinch = React.useRef<number | null>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    if ((e.target as Element).closest("[data-hit]")) return;
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const p = pointers.current.get(e.pointerId);
    if (!p) return;
    const pts = [...pointers.current.values()];
    if (pts.length === 2) {
      const other = pts.find((q) => q !== p)!;
      const d = Math.hypot(e.clientX - other.x, e.clientY - other.y);
      if (lastPinch.current) {
        const r = wrap.current!.getBoundingClientRect();
        zoomAt(d / lastPinch.current, (e.clientX + other.x) / 2 - r.left, (e.clientY + other.y) / 2 - r.top);
      }
      lastPinch.current = d;
    } else {
      setAnimate(false);
      setView((v) => ({ ...v, tx: v.tx + (e.clientX - p.x), ty: v.ty + (e.clientY - p.y) }));
    }
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
  };
  const onPointerUp = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) lastPinch.current = null;
  };

  // bbox (normalised, unrotated) -> rotated content coordinates
  const toContent = React.useCallback(
    (b: BBox) => {
      const W = nat.w, H = nat.h;
      const corners = [
        [b.x0 * W, b.y0 * H],
        [b.x1 * W, b.y0 * H],
        [b.x1 * W, b.y1 * H],
        [b.x0 * W, b.y1 * H],
      ].map(([x, y]) => (rot === 90 ? [H - y, x] : rot === 180 ? [W - x, H - y] : rot === 270 ? [y, W - x] : [x, y]));
      const xs = corners.map((c) => c[0]);
      const ys = corners.map((c) => c[1]);
      return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
    },
    [nat.w, nat.h, rot],
  );

  // focus the selected field
  React.useEffect(() => {
    if (!selected?.bbox || !nat.w || !box.w) return;
    if (selected.bbox.page !== page) {
      onPage(selected.bbox.page);
      return;
    }
    const r = toContent(selected.bbox);
    const v = viewRef.current;
    const minS = fitScale("width");
    let s = Math.max(v.s, minS);
    const onScreenW = r.w * s;
    if (onScreenW < 110) s = Math.min(6, 110 / Math.max(r.w, 1));
    if (r.w * s > box.w * 0.85) s = (box.w * 0.85) / r.w;
    s = Math.max(s, fitScale("page"));
    setAnimate(true);
    setView({ s, tx: box.w / 2 - (r.x + r.w / 2) * s, ty: box.h * 0.42 - (r.y + r.h / 2) * s });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.path, selected?.nonce, page, nat.w, box.w]);

  const pageFields = showOverlay && showFields ? fields.filter((f) => f.bbox && f.bbox.page === page) : [];
  const W = nat.w, H = nat.h;
  const sel = selected?.bbox && selected.bbox.page === page ? selected.bbox : null;

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-surface-3/60 dark:bg-black/30">
      <div
        ref={wrap}
        className={cn("relative min-h-0 flex-1 touch-none overflow-hidden", pickMode ? "cursor-crosshair" : "cursor-grab active:cursor-grabbing")}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={(e) => {
          const r = wrap.current!.getBoundingClientRect();
          zoomAt(1.8, e.clientX - r.left, e.clientY - r.top);
        }}
      >
        {src ? (
          <div
            className="absolute left-0 top-0"
            style={{
              width: RW || 1,
              height: RH || 1,
              transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.s})`,
              transformOrigin: "0 0",
              transition: animate ? "transform 320ms cubic-bezier(0.2, 0.8, 0.2, 1)" : "none",
            }}
          >
            <div className="absolute shadow-[0_2px_24px_rgb(0_0_0/0.18)]" style={{ width: W || 1, height: H || 1, left: (RW - W) / 2, top: (RH - H) / 2, transform: `rotate(${rot}deg)` }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                key={src}
                src={src}
                alt={`Page ${page}`}
                draggable={false}
                className="block size-full select-none bg-white"
                onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
              />
              {processing ? (
                <div className="pointer-events-none absolute inset-0 overflow-hidden">
                  <div className="absolute inset-x-0 top-0 h-[10%] animate-scanline bg-gradient-to-b from-transparent via-accent/35 to-accent/0" />
                </div>
              ) : null}
              {W && showOverlay ? (
                <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 size-full overflow-visible">
                  {rowBox && rowBox.page === page ? (
                    <rect x={rowBox.x0 * W - 4} y={rowBox.y0 * H - 3} width={(rowBox.x1 - rowBox.x0) * W + 8} height={(rowBox.y1 - rowBox.y0) * H + 6} rx={4} fill="var(--accent)" fillOpacity={0.06} stroke="var(--accent)" strokeOpacity={0.35} strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
                  ) : null}
                  {showTokens && tokens
                    ? tokens.map((t) => (
                        <rect
                          key={t.id}
                          data-hit
                          x={t.bbox[0] * W}
                          y={t.bbox[1] * H}
                          width={(t.bbox[2] - t.bbox[0]) * W}
                          height={(t.bbox[3] - t.bbox[1]) * H}
                          rx={2}
                          fill={pickMode ? "var(--accent)" : t.conf < 0.85 ? "var(--warn)" : "var(--fg)"}
                          fillOpacity={pickMode ? 0.06 : 0.035}
                          stroke={t.noise ? "var(--faint)" : t.conf < 0.85 ? "var(--warn)" : "var(--muted)"}
                          strokeOpacity={0.6}
                          strokeDasharray={t.noise ? "2 2" : undefined}
                          vectorEffect="non-scaling-stroke"
                          className={cn("cursor-pointer transition-[fill-opacity] hover:fill-opacity-20", pickMode && "hover:[fill-opacity:0.25]")}
                          onMouseMove={(e) => {
                            const r = wrap.current!.getBoundingClientRect();
                            setHover({ t, x: e.clientX - r.left, y: e.clientY - r.top });
                          }}
                          onMouseLeave={() => setHover(null)}
                          onClick={() => pickMode && onPickToken(t)}
                        />
                      ))
                    : null}
                  {pageFields.map((f) => {
                    const b = f.bbox!;
                    const c = STATUS_COLOR[f.status];
                    const isSel = selected?.path === f.path;
                    return (
                      <rect
                        key={f.path}
                        data-hit
                        x={b.x0 * W - 2}
                        y={b.y0 * H - 2}
                        width={(b.x1 - b.x0) * W + 4}
                        height={(b.y1 - b.y0) * H + 4}
                        rx={3}
                        fill={c}
                        fillOpacity={isSel ? 0.16 : f.status === "high_confidence" ? 0.05 : 0.12}
                        stroke={c}
                        strokeOpacity={isSel ? 1 : f.status === "high_confidence" ? 0.45 : 0.85}
                        strokeWidth={isSel ? 2.5 : 1.25}
                        vectorEffect="non-scaling-stroke"
                        className={cn("cursor-pointer transition-all", pickMode && "pointer-events-none")}
                        onClick={() => onSelectPath(f.path)}
                      >
                        <title>{`${f.label}: ${f.field.value ?? f.field.raw ?? "—"}`}</title>
                      </rect>
                    );
                  })}
                  {sel ? (
                    <rect
                      x={sel.x0 * W - 5}
                      y={sel.y0 * H - 5}
                      width={(sel.x1 - sel.x0) * W + 10}
                      height={(sel.y1 - sel.y0) * H + 10}
                      rx={5}
                      fill="none"
                      stroke="var(--accent)"
                      strokeWidth={3}
                      vectorEffect="non-scaling-stroke"
                      className="pointer-events-none animate-pulse"
                    />
                  ) : null}
                </svg>
              ) : null}
            </div>
          </div>
        ) : null}

        {hover ? (
          <div className="pointer-events-none absolute z-10 max-w-xs rounded-lg bg-fg px-2.5 py-1.5 text-[12px] text-bg shadow-pop" style={{ left: Math.min(hover.x + 14, box.w - 220), top: hover.y + 16 }}>
            <div className="font-medium">{hover.t.text}</div>
            <div className="opacity-70">
              {hover.t.source === "pdf_text" ? "PDF text layer" : `OCR confidence ${Math.round(hover.t.conf * 100)}%`}
              {hover.t.noise ? " · ignored (noise)" : ""}
            </div>
            {hover.t.alt_text ? <div className="mt-0.5 text-warn">2nd pass read: “{hover.t.alt_text}”</div> : null}
            {pickMode ? <div className="mt-0.5 text-accent">Click to use this text</div> : null}
          </div>
        ) : null}

        {pickMode ? (
          <div className="pointer-events-none absolute inset-x-0 top-3 flex justify-center">
            <div className="flex items-center gap-2 rounded-full bg-accent px-3.5 py-1.5 text-[12.5px] font-medium text-accent-fg shadow-pop">
              <MousePointerClick className="size-4" /> Click any word on the document to fill the field
            </div>
          </div>
        ) : null}
      </div>

      {/* toolbar */}
      <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-3">
        <div className="pointer-events-auto flex items-center gap-0.5 rounded-2xl border border-border bg-surface/95 p-1 shadow-pop backdrop-blur">
          {doc.pages.length > 1 ? (
            <>
              <ToolBtn tip="Previous page" onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1}>
                <ChevronLeft className="size-4" />
              </ToolBtn>
              <span className="px-1.5 text-[12px] font-medium tabular text-fg-2">
                {page} / {doc.pages.length}
              </span>
              <ToolBtn tip="Next page" onClick={() => onPage(Math.min(doc.pages.length, page + 1))} disabled={page >= doc.pages.length}>
                <ChevronRight className="size-4" />
              </ToolBtn>
              <div className="mx-1 h-5 w-px bg-border" />
            </>
          ) : null}
          <ToolBtn tip="Zoom out" onClick={() => zoomAt(1 / 1.25, box.w / 2, box.h / 2)}>
            <ZoomOut className="size-4" />
          </ToolBtn>
          <span className="w-11 text-center text-[11.5px] font-medium tabular text-muted">{Math.round((view.s / (fitScale("width") || 1)) * 100)}%</span>
          <ToolBtn tip="Zoom in" onClick={() => zoomAt(1.25, box.w / 2, box.h / 2)}>
            <ZoomIn className="size-4" />
          </ToolBtn>
          <ToolBtn tip="Fit width" onClick={() => fit("width")}>
            <StretchHorizontal className="size-4" />
          </ToolBtn>
          <ToolBtn tip="Fit page" onClick={() => fit("page")}>
            <Maximize className="size-4" />
          </ToolBtn>
          <ToolBtn tip="Rotate view" onClick={() => setRot((r) => (r + 90) % 360)}>
            <RotateCw className="size-4" />
          </ToolBtn>
          <div className="mx-1 h-5 w-px bg-border" />
          <ToolBtn tip={showFields ? "Hide field boxes" : "Show field boxes"} active={showFields} onClick={() => setShowFields((v) => !v)} disabled={!showOverlay}>
            <Layers className="size-4" />
          </ToolBtn>
          <ToolBtn tip={showTokens ? "Hide OCR text layer" : "Show OCR text layer"} active={showTokens} onClick={() => setShowTokens((v) => !v)} disabled={!showOverlay}>
            <ScanText className="size-4" />
          </ToolBtn>
          {info?.image_url ? (
            <ToolBtn tip={variant === "processed" ? "Show original capture" : "Show processed page"} active={variant === "original"} onClick={() => setVariant((v) => (v === "processed" ? "original" : "processed"))}>
              <ImageIcon className="size-4" />
            </ToolBtn>
          ) : null}
        </div>
      </div>
      {variant === "original" && !processing ? (
        <div className="pointer-events-none absolute left-3 top-3 rounded-full bg-fg/80 px-3 py-1 text-[11.5px] font-medium text-bg">Original capture — evidence boxes hidden</div>
      ) : null}
    </div>
  );
}

function ToolBtn({ children, tip, onClick, active, disabled }: { children: React.ReactNode; tip: string; onClick: () => void; active?: boolean; disabled?: boolean }) {
  return (
    <Tip content={tip}>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label={tip}
        className={cn("grid size-8 place-items-center rounded-xl text-fg-2 transition hover:bg-surface-3 disabled:opacity-35", active && "bg-accent-soft text-accent")}
      >
        {children}
      </button>
    </Tip>
  );
}
