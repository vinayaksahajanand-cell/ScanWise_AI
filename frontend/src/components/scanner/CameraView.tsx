"use client";

import { Camera, ChevronRight, Flashlight, FlashlightOff, ImagePlus, SwitchCamera, TriangleAlert, Upload, X } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Button } from "@/components/ui/primitives";
import { detectDocument, lerpQuad, type Quad, quadArea, quadDistance } from "@/lib/docdetect";
import { cn } from "@/lib/utils";

type Caps = { torch?: boolean; zoom?: { min: number; max: number; step: number } };

const DETECT_INTERVAL_MS = 120;
const STABLE_MS = 900;
/** Below this many effective pixels on the page's long side, small print becomes unreliable. */
const MIN_PAGE_PX = 1000;

type StillCapture = { takePhoto: (s?: object) => Promise<Blob>; getPhotoCapabilities?: () => Promise<{ imageWidth?: { max?: number } }> };
type StillCaptureCtor = new (t: MediaStreamTrack) => StillCapture;
const stillCtor = (): StillCaptureCtor | null => (typeof window !== "undefined" ? ((window as unknown as { ImageCapture?: StillCaptureCtor }).ImageCapture ?? null) : null);

/** Long side of the page in captured pixels for a normalised quad in a w×h frame. */
function pagePixels(q: Quad, w: number, h: number) {
  let m = 0;
  for (let i = 0; i < 4; i++) m = Math.max(m, Math.hypot((q[(i + 1) % 4].x - q[i].x) * w, (q[(i + 1) % 4].y - q[i].y) * h));
  return m;
}

/** Map video-normalised corners onto a still with a different aspect ratio (same optical centre). */
function mapToStill(q: Quad, vw: number, vh: number, pw: number, ph: number): Quad {
  const va = vw / vh;
  const pa = pw / ph;
  if (Math.abs(va - pa) < 0.01) return q;
  return q.map((p) =>
    va > pa ? { x: p.x, y: 0.5 + (p.y - 0.5) * (pa / va) } : { x: 0.5 + (p.x - 0.5) * (va / pa), y: p.y },
  ) as Quad;
}

async function detectOnBlob(blob: Blob): Promise<{ quad: Quad | null; w: number; h: number }> {
  const bmp = await createImageBitmap(blob);
  const s = 360 / Math.max(bmp.width, bmp.height);
  const w = Math.max(16, Math.round(bmp.width * s));
  const h = Math.max(16, Math.round(bmp.height * s));
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(bmp, 0, 0, w, h);
  const out = { quad: detectDocument(ctx.getImageData(0, 0, w, h).data, w, h).quad, w: bmp.width, h: bmp.height };
  bmp.close();
  return out;
}

export interface CameraCapture {
  blob: Blob;
  width: number;
  height: number;
  corners: Quad | null;
}

function cameraError(e: unknown): string {
  const name = (e as { name?: string })?.name;
  if (name === "NotAllowedError" || name === "SecurityError") return "Camera permission was denied. Allow camera access in your browser settings and try again.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No camera was found on this device.";
  if (name === "NotReadableError") return "The camera is being used by another app.";
  return "The camera could not be started.";
}

export function CameraView({
  pagesCount,
  lastThumb,
  onCapture,
  onDone,
  onClose,
  onNativeFiles,
}: {
  pagesCount: number;
  lastThumb?: string | null;
  onCapture: (c: CameraCapture) => void;
  onDone: () => void;
  onClose: () => void;
  onNativeFiles: (files: FileList) => void;
}) {
  const videoRef = React.useRef<HTMLVideoElement>(null);
  const wrapRef = React.useRef<HTMLDivElement>(null);
  const streamRef = React.useRef<MediaStream | null>(null);
  const trackRef = React.useRef<MediaStreamTrack | null>(null);
  const nativeInput = React.useRef<HTMLInputElement>(null);

  const [state, setState] = React.useState<"starting" | "live" | "error">("starting");
  const [error, setError] = React.useState<string | null>(null);
  const [facing, setFacing] = React.useState<"environment" | "user">("environment");
  const [caps, setCaps] = React.useState<Caps>({});
  const [torch, setTorch] = React.useState(false);
  const [zoom, setZoom] = React.useState(1);
  const [auto, setAuto] = React.useState(true);
  const [quad, setQuad] = React.useState<Quad | null>(null);
  const [progress, setProgress] = React.useState(0);
  const [hint, setHint] = React.useState("Starting camera…");
  const [ready, setReady] = React.useState(false);
  const [flash, setFlash] = React.useState(false);
  const [box, setBox] = React.useState({ w: 0, h: 0 });
  const [vdim, setVdim] = React.useState({ w: 0, h: 0 });

  // detector state kept in refs (no re-render per frame)
  const det = React.useRef({
    smooth: null as Quad | null,
    history: [] as Quad[],
    misses: 0,
    stableSince: 0,
    sharpMax: 0,
    armed: true,
    lastCaptured: null as Quad | null,
    lastCaptureAt: 0,
    capturing: false,
    lastRun: 0,
  });
  const autoRef = React.useRef(auto);
  autoRef.current = auto;
  const stillRef = React.useRef<{ capture: StillCapture; scale: number } | null>(null);

  React.useEffect(() => {
    try {
      const v = localStorage.getItem("sw-autocapture");
      if (v !== null) setAuto(v === "1");
    } catch {
      /* ignore */
    }
  }, []);

  const stop = React.useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    trackRef.current = null;
  }, []);

  const start = React.useCallback(
    async (mode: "environment" | "user") => {
      stop();
      setState("starting");
      setError(null);
      setHint("Starting camera…");
      if (!navigator.mediaDevices?.getUserMedia) {
        setState("error");
        setError(window.isSecureContext ? "This browser does not support camera access." : "Live camera needs a secure (HTTPS) connection. Open the app via https:// or localhost — or use your phone's camera app below.");
        return;
      }
      let stream: MediaStream | null = null;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: { ideal: mode }, width: { ideal: 3840 }, height: { ideal: 2160 } },
        });
      } catch (e) {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: mode } });
        } catch {
          setState("error");
          setError(cameraError(e));
          return;
        }
      }
      streamRef.current = stream;
      const track = stream.getVideoTracks()[0];
      trackRef.current = track;
      const v = videoRef.current;
      if (v) {
        v.srcObject = stream;
        await v.play().catch(() => undefined);
      }
      const c = (track.getCapabilities?.() ?? {}) as MediaTrackCapabilities & { torch?: boolean; zoom?: { min: number; max: number; step: number }; focusMode?: string[] };
      setCaps({ torch: !!c.torch, zoom: c.zoom && c.zoom.max > c.zoom.min ? c.zoom : undefined });
      setTorch(false);
      setZoom(c.zoom?.min ?? 1);
      if (c.focusMode?.includes("continuous")) {
        track.applyConstraints({ advanced: [{ focusMode: "continuous" } as MediaTrackConstraintSet] }).catch(() => undefined);
      }
      // Full-resolution stills (often 12 MP vs a 1080p stream) when the platform supports it
      stillRef.current = null;
      const Ctor = stillCtor();
      if (Ctor) {
        try {
          const ic = new Ctor(track);
          const pc = await ic.getPhotoCapabilities?.();
          const vw = track.getSettings().width ?? v?.videoWidth ?? 0;
          const maxW = pc?.imageWidth?.max ?? 0;
          if (maxW && vw && maxW > vw * 1.3) stillRef.current = { capture: ic, scale: maxW / vw };
        } catch {
          stillRef.current = null;
        }
      }
      setState("live");
    },
    [stop],
  );

  React.useEffect(() => {
    start(facing);
    return stop;
  }, [facing, start, stop]);

  React.useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const capture = React.useCallback(async () => {
    const v = videoRef.current;
    const d = det.current;
    if (!v || !v.videoWidth || d.capturing) return;
    d.capturing = true;
    setFlash(true);
    setTimeout(() => setFlash(false), 180);
    navigator.vibrate?.(25);
    let result: CameraCapture | null = null;
    const still = stillRef.current;
    if (still) {
      try {
        const photo = await Promise.race([still.capture.takePhoto(), new Promise<never>((_, rej) => setTimeout(() => rej(new Error("timeout")), 3000))]);
        const det2 = await detectOnBlob(photo);
        const corners = det2.quad ?? (d.smooth ? mapToStill(d.smooth, v.videoWidth, v.videoHeight, det2.w, det2.h) : null);
        result = { blob: photo, width: det2.w, height: det2.h, corners };
      } catch {
        result = null; // fall back to the video frame
      }
    }
    if (!result) {
      const c = document.createElement("canvas");
      c.width = v.videoWidth;
      c.height = v.videoHeight;
      c.getContext("2d")!.drawImage(v, 0, 0);
      const blob = await new Promise<Blob | null>((r) => c.toBlob(r, "image/jpeg", 0.93));
      if (blob) result = { blob, width: c.width, height: c.height, corners: d.smooth };
    }
    if (result) onCapture(result);
    d.lastCaptured = d.smooth;
    d.lastCaptureAt = performance.now();
    d.armed = false;
    d.stableSince = 0;
    d.capturing = false;
    setProgress(0);
  }, [onCapture]);

  // detection loop
  React.useEffect(() => {
    if (state !== "live") return;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
    let raf = 0;
    const tick = (t: number) => {
      raf = requestAnimationFrame(tick);
      const d = det.current;
      if (t - d.lastRun < DETECT_INTERVAL_MS) return;
      d.lastRun = t;
      const v = videoRef.current;
      if (!v || v.readyState < 2 || !v.videoWidth) return;
      if (vdim.w !== v.videoWidth || vdim.h !== v.videoHeight) setVdim({ w: v.videoWidth, h: v.videoHeight });
      const s = 320 / Math.max(v.videoWidth, v.videoHeight);
      const w = Math.round(v.videoWidth * s);
      const h = Math.round(v.videoHeight * s);
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
      ctx.drawImage(v, 0, 0, w, h);
      const r = detectDocument(ctx.getImageData(0, 0, w, h).data, w, h);
      d.sharpMax = Math.max(r.sharpness, d.sharpMax * 0.97);
      const sharpEnough = r.sharpness > 12 && r.sharpness > d.sharpMax * 0.55;
      if (r.quad) {
        d.misses = 0;
        d.smooth = d.smooth && quadDistance(d.smooth, r.quad) < 0.12 ? lerpQuad(d.smooth, r.quad, 0.5) : r.quad;
        d.history.push(r.quad);
        if (d.history.length > 6) d.history.shift();
        if (!d.armed && (!d.lastCaptured || quadDistance(d.lastCaptured, r.quad) > 0.08) && t - d.lastCaptureAt > 1200) d.armed = true;
      } else {
        d.misses++;
        if (d.misses > 3) {
          d.smooth = null;
          d.history = [];
          if (t - d.lastCaptureAt > 800) d.armed = true;
        }
      }
      const q = d.smooth;
      setQuad(q);
      const area = q ? quadArea(q) : 0;
      const steady = !!r.quad && d.history.length >= 5 && d.history.every((h2) => quadDistance(h2, r.quad!) < 0.02);
      const pagePx = q ? pagePixels(q, v.videoWidth, v.videoHeight) * (stillRef.current?.scale ?? 1) : 0;
      const legible = pagePx >= MIN_PAGE_PX;
      const isReady = !!q && steady && sharpEnough && area > 0.18 && legible;
      setReady(isReady);
      let msg: string;
      if (!d.armed && autoRef.current) msg = "Captured — place the next page";
      else if (!q) msg = "Looking for a document…";
      else if (area < 0.18) msg = "Move closer";
      else if (!legible) msg = "Move closer — text is too small to read reliably";
      else if (r.brightness < 55) msg = caps.torch ? "Too dark — turn on the flash" : "Too dark — add more light";
      else if (!sharpEnough) msg = "Hold steady";
      else if (!steady) msg = "Hold still";
      else msg = autoRef.current ? "Capturing…" : "Ready — tap the shutter";
      setHint(msg);
      if (autoRef.current && isReady && d.armed && !d.capturing) {
        if (!d.stableSince) d.stableSince = t;
        const p = Math.min(1, (t - d.stableSince) / STABLE_MS);
        setProgress(p);
        if (p >= 1) capture();
      } else {
        d.stableSince = 0;
        setProgress(0);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state, capture, caps.torch, vdim.w, vdim.h]);

  const toggleTorch = async () => {
    const t = trackRef.current;
    if (!t) return;
    try {
      await t.applyConstraints({ advanced: [{ torch: !torch } as MediaTrackConstraintSet] });
      setTorch(!torch);
    } catch {
      /* unsupported */
    }
  };

  const applyZoom = async (z: number) => {
    setZoom(z);
    try {
      await trackRef.current?.applyConstraints({ advanced: [{ zoom: z } as MediaTrackConstraintSet] });
    } catch {
      /* unsupported */
    }
  };

  const toggleAuto = () => {
    setAuto((a) => {
      try {
        localStorage.setItem("sw-autocapture", a ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !a;
    });
  };

  // map normalised video coords -> screen (object-contain)
  let pts: { x: number; y: number }[] = [];
  if (quad && vdim.w && box.w) {
    const s = Math.min(box.w / vdim.w, box.h / vdim.h);
    const dw = vdim.w * s;
    const dh = vdim.h * s;
    const ox = (box.w - dw) / 2;
    const oy = (box.h - dh) / 2;
    pts = quad.map((p) => ({ x: ox + p.x * dw, y: oy + p.y * dh }));
  }
  const stroke = ready ? "#34c38f" : "#4f8bff";

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-black text-white lg:left-[248px]">
      {/* top bar */}
      <div className="flex items-center justify-between gap-2 px-3 pb-2 pt-[max(12px,env(safe-area-inset-top))]">
        <button type="button" onClick={onClose} className="grid size-10 place-items-center rounded-full bg-white/10 backdrop-blur hover:bg-white/20" aria-label="Close scanner">
          <X className="size-5" />
        </button>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggleAuto}
            className={cn("h-9 rounded-full px-3.5 text-[12.5px] font-semibold tracking-wide backdrop-blur transition", auto ? "bg-[#4f8bff] text-white" : "bg-white/10 text-white/80")}
          >
            {auto ? "AUTO" : "MANUAL"}
          </button>
          {caps.torch ? (
            <button type="button" onClick={toggleTorch} className={cn("grid size-10 place-items-center rounded-full backdrop-blur", torch ? "bg-[#f5a524] text-black" : "bg-white/10")} aria-label="Toggle flash">
              {torch ? <Flashlight className="size-5" /> : <FlashlightOff className="size-5" />}
            </button>
          ) : null}
          <button type="button" onClick={() => setFacing((f) => (f === "environment" ? "user" : "environment"))} className="grid size-10 place-items-center rounded-full bg-white/10 backdrop-blur hover:bg-white/20" aria-label="Switch camera">
            <SwitchCamera className="size-5" />
          </button>
        </div>
      </div>

      {/* viewfinder */}
      <div ref={wrapRef} className="relative min-h-0 flex-1 overflow-hidden">
        <video ref={videoRef} playsInline muted className="absolute inset-0 size-full object-contain" />
        {state === "live" ? (
          <svg className="pointer-events-none absolute inset-0 size-full">
            {pts.length === 4 ? (
              <>
                <polygon points={pts.map((p) => `${p.x},${p.y}`).join(" ")} fill={ready ? "rgb(52 195 143 / 0.16)" : "rgb(79 139 255 / 0.14)"} stroke={stroke} strokeWidth={3} strokeLinejoin="round" style={{ transition: "all 120ms linear" }} />
                {pts.map((p, i) => (
                  <circle key={i} cx={p.x} cy={p.y} r={7} fill="#fff" stroke={stroke} strokeWidth={3} />
                ))}
              </>
            ) : (
              <g opacity={0.55}>
                {/* framing guide */}
                {(() => {
                  const gw = Math.min(box.w * 0.78, box.h * 0.62);
                  const gh = gw * 1.38;
                  const x = (box.w - gw) / 2;
                  const y = (box.h - gh) / 2;
                  const l = 28;
                  const d = `M${x},${y + l}V${y}H${x + l} M${x + gw - l},${y}H${x + gw}V${y + l} M${x + gw},${y + gh - l}V${y + gh}H${x + gw - l} M${x + l},${y + gh}H${x}V${y + gh - l}`;
                  return <path d={d} stroke="#fff" strokeWidth={3} fill="none" strokeLinecap="round" />;
                })()}
              </g>
            )}
          </svg>
        ) : null}
        {state !== "error" ? (
          <div className="pointer-events-none absolute inset-x-0 top-3 flex justify-center">
            <div className={cn("rounded-full px-4 py-1.5 text-[13px] font-medium backdrop-blur-md transition", ready ? "bg-[#34c38f]/90 text-black" : "bg-black/55 text-white")}>{hint}</div>
          </div>
        ) : null}
        <div className={cn("pointer-events-none absolute inset-0 bg-white transition-opacity duration-150", flash ? "opacity-80" : "opacity-0")} />

        {state === "error" ? (
          <div className="absolute inset-0 grid place-items-center p-6">
            <div className="max-w-sm text-center">
              <div className="mx-auto mb-4 grid size-14 place-items-center rounded-2xl bg-white/10">
                <TriangleAlert className="size-7 text-[#f5a524]" />
              </div>
              <h2 className="text-[17px] font-semibold">Camera unavailable</h2>
              <p className="mt-2 text-[14px] text-white/70">{error}</p>
              <div className="mt-6 flex flex-col gap-2.5">
                <Button variant="primary" size="lg" onClick={() => nativeInput.current?.click()}>
                  <Camera className="size-4.5" /> Use the phone&apos;s camera app
                </Button>
                <Button size="lg" className="border-white/15 bg-white/10 text-white hover:bg-white/15" onClick={() => start(facing)}>
                  Try again
                </Button>
                <Link href="/upload" className="mt-1 text-[13.5px] font-medium text-[#8fb4ff] hover:underline">
                  Upload files instead
                </Link>
              </div>
            </div>
          </div>
        ) : null}
      </div>

      {/* zoom */}
      {caps.zoom && state === "live" ? (
        <div className="flex items-center justify-center gap-3 px-8 pt-2">
          <span className="text-[11px] text-white/60 tabular">{zoom.toFixed(1)}×</span>
          <input
            type="range"
            min={caps.zoom.min}
            max={Math.min(caps.zoom.max, caps.zoom.min * 6)}
            step={caps.zoom.step || 0.1}
            value={zoom}
            onChange={(e) => applyZoom(parseFloat(e.target.value))}
            className="h-1 w-48 accent-white"
            aria-label="Zoom"
          />
        </div>
      ) : null}

      {/* controls */}
      <div className="grid grid-cols-3 items-center px-6 pb-[max(18px,env(safe-area-inset-bottom))] pt-4">
        <div className="flex items-center">
          {pagesCount > 0 ? (
            <button type="button" onClick={onDone} className="relative" aria-label="Review captured pages">
              <div className="size-14 overflow-hidden rounded-xl border-2 border-white/80 bg-white/10 shadow-lg">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                {lastThumb ? <img src={lastThumb} alt="" className="size-full object-cover" /> : null}
              </div>
              <span className="absolute -right-2 -top-2 grid size-6 place-items-center rounded-full bg-[#4f8bff] text-[12px] font-bold">{pagesCount}</span>
            </button>
          ) : (
            <button type="button" onClick={() => nativeInput.current?.click()} className="flex flex-col items-center gap-1 text-[11px] text-white/70 hover:text-white">
              <span className="grid size-11 place-items-center rounded-full bg-white/10">
                <ImagePlus className="size-5" />
              </span>
              Photo
            </button>
          )}
        </div>
        <div className="flex justify-center">
          <button
            type="button"
            onClick={capture}
            disabled={state !== "live"}
            aria-label="Capture page"
            className="relative grid size-[76px] place-items-center rounded-full disabled:opacity-40"
          >
            <svg className="absolute inset-0 size-full -rotate-90" viewBox="0 0 76 76">
              <circle cx="38" cy="38" r="35" stroke="rgb(255 255 255 / 0.9)" strokeWidth="4" fill="none" />
              <circle cx="38" cy="38" r="35" stroke="#34c38f" strokeWidth="4.5" fill="none" strokeDasharray={2 * Math.PI * 35} strokeDashoffset={2 * Math.PI * 35 * (1 - progress)} strokeLinecap="round" />
            </svg>
            <span className="size-[60px] rounded-full bg-white transition active:scale-90" />
          </button>
        </div>
        <div className="flex justify-end">
          {pagesCount > 0 ? (
            <button type="button" onClick={onDone} className="flex h-11 items-center gap-1 rounded-full bg-white px-4 text-[14px] font-semibold text-black shadow-lg active:scale-95">
              Done <ChevronRight className="size-4" />
            </button>
          ) : (
            <Link href="/upload" className="flex flex-col items-center gap-1 text-[11px] text-white/70 hover:text-white">
              <span className="grid size-11 place-items-center rounded-full bg-white/10">
                <Upload className="size-5" />
              </span>
              Upload
            </Link>
          )}
        </div>
      </div>
      <input
        ref={nativeInput}
        type="file"
        accept="image/*"
        capture="environment"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) onNativeFiles(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );
}
