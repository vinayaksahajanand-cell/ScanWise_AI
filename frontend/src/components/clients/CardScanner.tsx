"use client";

import { Camera, ImagePlus, RotateCcw, ScanLine, TriangleAlert, X } from "lucide-react";
import * as React from "react";

import { Button, Spinner } from "@/components/ui/primitives";
import { Api, ApiError } from "@/lib/api";
import { CARD_ACCEPT, CardImageError, isPreviewable, prepareCardImage } from "@/lib/cardimage";
import type { CardScanResult } from "@/lib/types";
import { cn } from "@/lib/utils";

type Side = "front" | "back";
type Slot = { file: File; preview: string } | null;

function hintFor(status: number): string | null {
  if (status === 415) return "Use a JPG, PNG, WEBP, HEIC, TIFF, BMP, GIF or PDF file.";
  if (status === 413) return "The photo is too large — try a smaller one.";
  if (status === 422) return "Make sure the whole card is in the frame, in focus and evenly lit, then try again.";
  if (status === 0) return "Check your connection and that the backend is running.";
  return null;
}

/** Capture or upload the front (required) and back (optional) of a business card, then run the scan. */
export function CardScanner({ onResult, onCancel }: { onResult: (r: CardScanResult) => void; onCancel: () => void }) {
  const [slots, setSlots] = React.useState<Record<Side, Slot>>({ front: null, back: null });
  const [slotError, setSlotError] = React.useState<Record<Side, string | null>>({ front: null, back: null });
  const [preparing, setPreparing] = React.useState<Record<Side, boolean>>({ front: false, back: false });
  const [scanning, setScanning] = React.useState(false);
  const [elapsed, setElapsed] = React.useState(0);
  const [error, setError] = React.useState<{ message: string; hint: string | null } | null>(null);
  const abortRef = React.useRef<AbortController | null>(null);
  const slotsRef = React.useRef(slots);
  slotsRef.current = slots;

  // leaving the step (Cancel / Back / dialog closed) aborts an in-flight scan and frees previews
  React.useEffect(
    () => () => {
      abortRef.current?.abort();
      Object.values(slotsRef.current).forEach((s) => s && URL.revokeObjectURL(s.preview));
    },
    [],
  );

  React.useEffect(() => {
    if (!scanning) return;
    setElapsed(0);
    const t0 = Date.now();
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 500);
    return () => clearInterval(t);
  }, [scanning]);

  const setSlot = (side: Side, next: Slot) =>
    setSlots((cur) => {
      if (cur[side]) URL.revokeObjectURL(cur[side]!.preview);
      return { ...cur, [side]: next };
    });

  const pick = async (side: Side, file: File | undefined) => {
    if (!file) return;
    setError(null);
    setSlotError((e) => ({ ...e, [side]: null }));
    setPreparing((p) => ({ ...p, [side]: true }));
    try {
      const prepared = await prepareCardImage(file);
      setSlot(side, { file: prepared, preview: isPreviewable(prepared) ? URL.createObjectURL(prepared) : "" });
    } catch (e) {
      setSlotError((s) => ({ ...s, [side]: e instanceof CardImageError ? e.message : "That image could not be used." }));
    } finally {
      setPreparing((p) => ({ ...p, [side]: false }));
    }
  };

  const scan = async () => {
    if (!slots.front || scanning) return;
    const files = [slots.front.file, ...(slots.back ? [slots.back.file] : [])];
    const ctl = new AbortController();
    abortRef.current = ctl;
    setError(null);
    setScanning(true);
    try {
      const result = await Api.extractCard(files, undefined, ctl.signal);
      if (!ctl.signal.aborted) onResult(result);
    } catch (e) {
      if (ctl.signal.aborted) return;
      const status = e instanceof ApiError ? e.status : 0;
      setError({ message: e instanceof Error ? e.message : "The card could not be read.", hint: hintFor(status) });
    } finally {
      if (abortRef.current === ctl) setScanning(false);
    }
  };

  const retake = () => {
    setSlot("front", null);
    setSlot("back", null);
    setError(null);
  };

  if (scanning) {
    return (
      <div className="flex flex-col items-center gap-4 px-6 py-14 text-center" role="status" aria-live="polite">
        <div className="relative grid size-16 place-items-center rounded-2xl bg-accent-soft text-accent">
          <ScanLine className="size-7" />
          <span className="absolute -right-1 -top-1 rounded-full bg-surface p-1 shadow-card">
            <Spinner className="size-4 text-accent" />
          </span>
        </div>
        <div>
          <div className="text-[16px] font-semibold text-fg">Reading card…</div>
          <p className="mt-1 text-[13px] text-muted">
            {elapsed < 8
              ? "Finding the name, company, phone, email and address."
              : "Still working — the vision model can take a few extra seconds."}
          </p>
          <p className="mt-2 text-[12px] tabular text-faint">{elapsed}s</p>
        </div>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4 px-4 py-4">
      <p className="px-1 text-[13px] leading-relaxed text-muted">
        Take a clear photo of the card, filling the frame in good light. Add the back too if it has more details. Nothing is saved until you review the result and press Add.
      </p>

      <SideSlot label="Front" required slot={slots.front} busy={preparing.front} error={slotError.front} onPick={(f) => pick("front", f)} onRemove={() => setSlot("front", null)} />
      <SideSlot label="Back" slot={slots.back} busy={preparing.back} error={slotError.back} onPick={(f) => pick("back", f)} onRemove={() => setSlot("back", null)} />

      {error ? (
        <div className="flex gap-3 rounded-2xl border border-bad/30 bg-bad-soft px-4 py-3 text-[13.5px] text-bad" role="alert">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <div className="min-w-0">
            <div className="font-medium">{error.message}</div>
            {error.hint ? <div className="mt-0.5 text-[13px] opacity-90">{error.hint}</div> : null}
            <div className="mt-2 flex gap-2">
              <Button size="xs" variant="secondary" onClick={scan}>
                <RotateCcw className="size-3.5" /> Try again
              </Button>
              <Button size="xs" variant="ghost" className="text-bad hover:bg-bad/10 hover:text-bad" onClick={retake}>
                Retake photos
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="flex gap-2 pt-1">
        <Button variant="ghost" className="flex-1" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" size="lg" className="flex-[2]" disabled={!slots.front || preparing.front || preparing.back} onClick={scan}>
          <ScanLine className="size-4" /> Scan card{slots.front && slots.back ? " (2 sides)" : ""}
        </Button>
      </div>
    </div>
  );
}

function SideSlot({
  label,
  required,
  slot,
  busy,
  error,
  onPick,
  onRemove,
}: {
  label: string;
  required?: boolean;
  slot: Slot;
  busy: boolean;
  error: string | null;
  onPick: (f: File | undefined) => void;
  onRemove: () => void;
}) {
  const camera = React.useRef<HTMLInputElement>(null);
  const upload = React.useRef<HTMLInputElement>(null);
  const take = (e: React.ChangeEvent<HTMLInputElement>) => {
    onPick(e.target.files?.[0]);
    e.target.value = ""; // allow picking the same file again
  };
  return (
    <div className="rounded-2xl bg-surface p-3 shadow-card">
      <div className="mb-2 flex items-center justify-between px-1">
        <div className="text-[14px] font-medium text-fg">
          {label} {required ? <span className="text-[12px] font-normal text-muted">· required</span> : <span className="text-[12px] font-normal text-muted">· optional</span>}
        </div>
        {slot ? (
          <button type="button" onClick={onRemove} className="grid size-6 place-items-center rounded-full bg-surface-3 text-muted hover:text-fg" aria-label={`Remove ${label.toLowerCase()} photo`}>
            <X className="size-3.5" />
          </button>
        ) : null}
      </div>

      {slot ? (
        <div className="flex items-center gap-3">
          {slot.preview ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={slot.preview} alt={`${label} of the business card`} className="h-24 w-40 shrink-0 rounded-xl border border-border bg-surface-3 object-cover" />
          ) : (
            <div className="grid h-24 w-40 shrink-0 place-items-center rounded-xl border border-border bg-surface-3 px-2 text-center">
              <span className="text-[13px] font-semibold uppercase tracking-wide text-accent">{(slot.file.name.split(".").pop() || "file").slice(0, 5)}</span>
              <span className="line-clamp-2 break-all text-[11.5px] text-muted">{slot.file.name}</span>
            </div>
          )}
          <div className="flex min-w-0 flex-col items-start gap-1.5">
            <div className="truncate text-[12.5px] text-muted">{Math.max(1, Math.round(slot.file.size / 1024))} KB · ready</div>
            <Button size="xs" variant="secondary" onClick={() => camera.current?.click()}>
              <Camera className="size-3.5" /> Retake
            </Button>
            <Button size="xs" variant="ghost" onClick={() => upload.current?.click()}>
              <ImagePlus className="size-3.5" /> Replace
            </Button>
          </div>
        </div>
      ) : (
        <div className={cn("grid gap-2 sm:grid-cols-2", busy && "opacity-60")}>
          <Button variant="subtle" size="lg" disabled={busy} onClick={() => camera.current?.click()}>
            {busy ? <Spinner className="text-accent" /> : <Camera className="size-4" />} Take photo
          </Button>
          <Button variant="secondary" size="lg" disabled={busy} onClick={() => upload.current?.click()}>
            <ImagePlus className="size-4" /> Upload image or PDF
          </Button>
        </div>
      )}

      {error ? (
        <p className="mt-2 px-1 text-[12.5px] text-bad" role="alert">
          {error}
        </p>
      ) : null}

      {/* capture="environment" opens the rear camera on phones; on desktop it is just a file picker */}
      <input ref={camera} type="file" accept="image/*" capture="environment" className="hidden" onChange={take} />
      <input ref={upload} type="file" accept={CARD_ACCEPT} className="hidden" onChange={take} />
    </div>
  );
}
