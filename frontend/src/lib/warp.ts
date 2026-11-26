/** Perspective warp for client-side previews (the server re-does it at full resolution). */

import type { Quad } from "./docdetect";

type Mat = number[];

/** Solve for the homography H mapping src[i] -> dst[i] (4 point pairs). */
export function homography(src: { x: number; y: number }[], dst: { x: number; y: number }[]): Mat {
  const A: number[][] = [];
  for (let i = 0; i < 4; i++) {
    const { x, y } = src[i];
    const { x: u, y: v } = dst[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y, u]);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y, v]);
  }
  // Gaussian elimination with partial pivoting on the 8x9 augmented matrix
  for (let c = 0; c < 8; c++) {
    let piv = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[piv][c])) piv = r;
    [A[c], A[piv]] = [A[piv], A[c]];
    const d = A[c][c] || 1e-12;
    for (let k = c; k < 9; k++) A[c][k] /= d;
    for (let r = 0; r < 8; r++) {
      if (r === c) continue;
      const f = A[r][c];
      if (!f) continue;
      for (let k = c; k < 9; k++) A[r][k] -= f * A[c][k];
    }
  }
  return [A[0][8], A[1][8], A[2][8], A[3][8], A[4][8], A[5][8], A[6][8], A[7][8], 1];
}

function dist(a: { x: number; y: number }, b: { x: number; y: number }) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Warp the quad region of `source` into an upright canvas (bilinear sampling). */
export function warpQuad(source: CanvasImageSource & { width: number; height: number }, quadNorm: Quad, maxSide = 900): HTMLCanvasElement {
  const sw0 = (source as HTMLImageElement).naturalWidth || source.width;
  const sh0 = (source as HTMLImageElement).naturalHeight || source.height;
  const scale = Math.min(1, 1600 / Math.max(sw0, sh0));
  const sw = Math.max(1, Math.round(sw0 * scale));
  const sh = Math.max(1, Math.round(sh0 * scale));
  const sc = document.createElement("canvas");
  sc.width = sw;
  sc.height = sh;
  const sctx = sc.getContext("2d", { willReadFrequently: true })!;
  sctx.drawImage(source, 0, 0, sw, sh);
  const src = sctx.getImageData(0, 0, sw, sh).data;

  const q = quadNorm.map((p) => ({ x: p.x * sw, y: p.y * sh }));
  const wTop = dist(q[0], q[1]);
  const wBot = dist(q[3], q[2]);
  const hL = dist(q[0], q[3]);
  const hR = dist(q[1], q[2]);
  let ow = Math.max(wTop, wBot);
  let oh = Math.max(hL, hR);
  const f = Math.min(1, maxSide / Math.max(ow, oh));
  ow = Math.max(8, Math.round(ow * f));
  oh = Math.max(8, Math.round(oh * f));
  const H = homography(
    [
      { x: 0, y: 0 },
      { x: ow - 1, y: 0 },
      { x: ow - 1, y: oh - 1 },
      { x: 0, y: oh - 1 },
    ],
    q,
  );
  const out = document.createElement("canvas");
  out.width = ow;
  out.height = oh;
  const octx = out.getContext("2d")!;
  const img = octx.createImageData(ow, oh);
  const d = img.data;
  for (let y = 0; y < oh; y++) {
    for (let x = 0; x < ow; x++) {
      const den = H[6] * x + H[7] * y + H[8];
      const sx = (H[0] * x + H[1] * y + H[2]) / den;
      const sy = (H[3] * x + H[4] * y + H[5]) / den;
      const x0 = Math.floor(sx), y0 = Math.floor(sy);
      const o = (y * ow + x) * 4;
      if (x0 < 0 || y0 < 0 || x0 >= sw - 1 || y0 >= sh - 1) {
        d[o] = d[o + 1] = d[o + 2] = 255;
        d[o + 3] = 255;
        continue;
      }
      const fx = sx - x0, fy = sy - y0;
      const i00 = (y0 * sw + x0) * 4, i10 = i00 + 4, i01 = i00 + sw * 4, i11 = i01 + 4;
      for (let k = 0; k < 3; k++) {
        const top = src[i00 + k] * (1 - fx) + src[i10 + k] * fx;
        const bot = src[i01 + k] * (1 - fx) + src[i11 + k] * fx;
        d[o + k] = top * (1 - fy) + bot * fy;
      }
      d[o + 3] = 255;
    }
  }
  octx.putImageData(img, 0, 0);
  return out;
}

export function rotateCanvas(c: HTMLCanvasElement, deg: number): HTMLCanvasElement {
  const r = ((deg % 360) + 360) % 360;
  if (!r) return c;
  const out = document.createElement("canvas");
  out.width = r === 180 ? c.width : c.height;
  out.height = r === 180 ? c.height : c.width;
  const ctx = out.getContext("2d")!;
  ctx.translate(out.width / 2, out.height / 2);
  ctx.rotate((r * Math.PI) / 180);
  ctx.drawImage(c, -c.width / 2, -c.height / 2);
  return out;
}
