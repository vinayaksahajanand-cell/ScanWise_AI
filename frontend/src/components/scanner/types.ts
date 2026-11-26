import { detectDocument, type Quad } from "@/lib/docdetect";

export interface CapturedPage {
  id: string;
  blob: Blob;
  url: string; // object URL of the full-resolution capture
  name: string;
  width: number;
  height: number;
  corners: Quad | null; // normalised crop quad (null = whole image)
  detected: Quad | null; // what auto-detection proposed
  rotation: 0 | 90 | 180 | 270;
  preview?: string; // data URL of the perspective-corrected preview
  editable: boolean; // false for formats the browser cannot decode (HEIC)
}

export const newId = () => Math.random().toString(36).slice(2, 10);

export function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Image could not be decoded"));
    img.src = url;
  });
}

/** Run the live detector on a still image (used for uploads / native-camera photos). */
export function detectOnImage(img: HTMLImageElement): Quad | null {
  const maxSide = 360;
  const s = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
  const w = Math.max(16, Math.round(img.naturalWidth * s));
  const h = Math.max(16, Math.round(img.naturalHeight * s));
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(img, 0, 0, w, h);
  const d = detectDocument(ctx.getImageData(0, 0, w, h).data, w, h);
  return d.quad;
}
