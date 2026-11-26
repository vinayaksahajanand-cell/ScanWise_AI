// Browser-side preparation of business-card photos before upload.
// Mirrors the server's checks (type, size) so users get instant, friendly errors, fixes EXIF rotation
// and shrinks very large camera photos to keep the upload fast. Formats a browser cannot decode or
// draw reliably (PDF, TIFF, HEIC on most browsers) are sent untouched: the server reads them.

export const CARD_MAX_BYTES = 15 * 1024 * 1024;
export const CARD_MAX_SIDE = 2400;
export const CARD_MIN_SIDE = 200;

export class CardImageError extends Error {}

/** Everything the server accepts for a business card. */
export const CARD_ACCEPT = "image/*,application/pdf,.pdf,.jpg,.jpeg,.png,.webp,.gif,.bmp,.heic,.heif,.tif,.tiff";
export const CARD_FORMATS_TEXT = "JPG, PNG, WEBP, HEIC, TIFF, BMP, GIF or PDF";

const EXT_RE = /\.(jpe?g|png|webp|gif|bmp|heic|heif|tiff?|pdf)$/i;
const MIME_OK = /^(image\/(jpe?g|png|webp|gif|bmp|heic|heif|tiff?|x-ms-bmp)|application\/pdf)$/i;
/** Sent as-is (no canvas round-trip), and shown with a file tile instead of a thumbnail. */
const RAW_EXT_RE = /\.(pdf|tiff?)$/i;
const RAW_MIME_RE = /^(application\/pdf|image\/tiff?)$/i;

function looksLikeAllowedType(file: File): boolean {
  const t = (file.type || "").toLowerCase();
  if (t && MIME_OK.test(t)) return true;
  // some cameras/browsers leave the type empty or generic: fall back to the extension
  return (!t || t === "application/octet-stream") && EXT_RE.test(file.name);
}

/** True when the browser can draw this file in an <img> (so a thumbnail can be shown). */
export function isPreviewable(file: File): boolean {
  const t = (file.type || "").toLowerCase();
  return /^image\/(jpeg|png|webp|gif|bmp)$/.test(t);
}

async function decode(file: File): Promise<{ source: CanvasImageSource; width: number; height: number; close: () => void }> {
  if (typeof createImageBitmap === "function") {
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
      return { source: bmp, width: bmp.width, height: bmp.height, close: () => bmp.close() };
    } catch {
      /* fall through to <img> */
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.decoding = "async";
    img.src = url;
    await img.decode(); // <img> applies EXIF orientation by default in current browsers
    return { source: img, width: img.naturalWidth, height: img.naturalHeight, close: () => undefined };
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Validate and normalise one card photo. Always returns an upright JPEG no larger than
 * CARD_MAX_SIDE on its long edge, so the type sent to the server is always honest.
 */
export async function prepareCardImage(file: File): Promise<File> {
  if (!looksLikeAllowedType(file)) {
    throw new CardImageError(`That file type is not supported. Use ${CARD_FORMATS_TEXT}.`);
  }
  if (file.size === 0) throw new CardImageError("That file is empty.");
  if (file.size > CARD_MAX_BYTES) {
    throw new CardImageError(`That file is ${(file.size / 1048576).toFixed(1)} MB. The limit is ${CARD_MAX_BYTES / 1048576} MB.`);
  }
  if (RAW_MIME_RE.test(file.type || "") || RAW_EXT_RE.test(file.name)) return file; // PDF / TIFF: the server renders and reads it

  let decoded;
  try {
    decoded = await decode(file);
  } catch {
    // e.g. HEIC in a browser without a HEIC decoder: the server can read it, so send it untouched
    if (/\.(heic|heif)$/i.test(file.name) || /^image\/hei[cf]$/i.test(file.type || "")) return file;
    throw new CardImageError("That image could not be opened. Try taking the photo again.");
  }
  try {
    const { width, height } = decoded;
    if (Math.min(width, height) < CARD_MIN_SIDE) {
      throw new CardImageError(`That photo is too small (${width}×${height}). Move closer so the card fills the frame.`);
    }
    const scale = Math.min(1, CARD_MAX_SIDE / Math.max(width, height));
    const w = Math.round(width * scale);
    const h = Math.round(height * scale);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return file; // no canvas: send the original, the server validates it again
    ctx.fillStyle = "#ffffff"; // PNG transparency would otherwise turn black in a JPEG
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(decoded.source, 0, 0, w, h);
    const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, "image/jpeg", 0.9));
    if (!blob) return file;
    const base = (file.name || "card").replace(/\.[^.]+$/, "") || "card";
    return new File([blob], `${base}.jpg`, { type: "image/jpeg", lastModified: Date.now() });
  } finally {
    decoded.close();
  }
}
