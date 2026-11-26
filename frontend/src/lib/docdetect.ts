/**
 * Real-time document boundary detection for the live camera scanner.
 *
 * Pure TypeScript (no OpenCV.js download). Runs on a downscaled frame
 * (~320 px) a few times per second:
 *
 *   gray → blur → two region candidates
 *     A) bright paper vs darker background (Otsu threshold)
 *     B) region enclosed by strong edges (flood-fill the outside through non-edges)
 *   → largest component → convex hull → reduce to a quadrilateral
 *   → score by edge support along each side, area and corner angles.
 *
 * Also reports sharpness (variance of Laplacian) and brightness, used for
 * "hold steady" / "too dark" hints and to gate auto-capture.
 */

export type Pt = { x: number; y: number };
export type Quad = [Pt, Pt, Pt, Pt]; // top-left, top-right, bottom-right, bottom-left (normalised 0..1)

export interface Detection {
  quad: Quad | null;
  score: number;
  sharpness: number;
  brightness: number;
  method: "bright" | "edges" | null;
}

export function toGray(rgba: Uint8ClampedArray | Uint8Array, w: number, h: number): Float32Array {
  const g = new Float32Array(w * h);
  for (let i = 0, j = 0; i < g.length; i++, j += 4) g[i] = 0.299 * rgba[j] + 0.587 * rgba[j + 1] + 0.114 * rgba[j + 2];
  return g;
}

function boxBlur(src: Float32Array, w: number, h: number): Float32Array {
  const tmp = new Float32Array(w * h);
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    const r = y * w;
    for (let x = 0; x < w; x++) {
      tmp[r + x] = (src[r + (x > 0 ? x - 1 : 0)] + src[r + x] + src[r + (x < w - 1 ? x + 1 : x)]) / 3;
    }
  }
  for (let y = 0; y < h; y++) {
    const ym = (y > 0 ? y - 1 : 0) * w;
    const yp = (y < h - 1 ? y + 1 : y) * w;
    const r = y * w;
    for (let x = 0; x < w; x++) out[r + x] = (tmp[ym + x] + tmp[r + x] + tmp[yp + x]) / 3;
  }
  return out;
}

function otsu(img: Float32Array): number {
  const hist = new Float64Array(256);
  for (let i = 0; i < img.length; i++) hist[Math.min(255, Math.max(0, img[i] | 0))]++;
  const total = img.length;
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * hist[i];
  let sumB = 0;
  let wB = 0;
  let max = 0;
  let thr = 127;
  for (let t = 0; t < 256; t++) {
    wB += hist[t];
    if (!wB) continue;
    const wF = total - wB;
    if (!wF) break;
    sumB += t * hist[t];
    const mB = sumB / wB;
    const mF = (sum - sumB) / wF;
    const between = wB * wF * (mB - mF) * (mB - mF);
    if (between > max) {
      max = between;
      thr = t;
    }
  }
  return thr;
}

function sobel(img: Float32Array, w: number, h: number): Float32Array {
  const mag = new Float32Array(w * h);
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const gx = -img[i - w - 1] - 2 * img[i - 1] - img[i + w - 1] + img[i - w + 1] + 2 * img[i + 1] + img[i + w + 1];
      const gy = -img[i - w - 1] - 2 * img[i - w] - img[i - w + 1] + img[i + w - 1] + 2 * img[i + w] + img[i + w + 1];
      mag[i] = Math.sqrt(gx * gx + gy * gy);
    }
  }
  return mag;
}

function percentile(arr: Float32Array, p: number): number {
  const bins = 512;
  const maxV = 1450;
  const hist = new Uint32Array(bins);
  for (let i = 0; i < arr.length; i++) hist[Math.min(bins - 1, ((arr[i] / maxV) * bins) | 0)]++;
  const target = arr.length * p;
  let acc = 0;
  for (let i = 0; i < bins; i++) {
    acc += hist[i];
    if (acc >= target) return ((i + 0.5) / bins) * maxV;
  }
  return maxV;
}

function erode(mask: Uint8Array, w: number, h: number): Uint8Array {
  const out = new Uint8Array(w * h);
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      out[i] = mask[i] && mask[i - 1] && mask[i + 1] && mask[i - w] && mask[i + w] ? 1 : 0;
    }
  }
  return out;
}

/** Paper is bright and colourless: V - 1.2·S separates it from wood, fabric, coloured desks. */
function paperness(rgba: Uint8ClampedArray | Uint8Array, w: number, h: number): Float32Array {
  const p = new Float32Array(w * h);
  for (let i = 0, j = 0; i < p.length; i++, j += 4) {
    const r = rgba[j], g = rgba[j + 1], b = rgba[j + 2];
    const v = Math.max(r, g, b);
    const s = v - Math.min(r, g, b);
    p[i] = Math.max(0, v - 1.2 * s);
  }
  return p;
}

/**
 * Brightness contrast across each side (inside minus outside, 0..1). A real
 * paper edge has consistent contrast; a line through a textured desk has none,
 * however many edges the texture produces.
 */
function boundaryContrast(q: Quad, img: Float32Array, w: number, h: number): number[] {
  const cx = (q[0].x + q[1].x + q[2].x + q[3].x) / 4;
  const cy = (q[0].y + q[1].y + q[2].y + q[3].y) / 4;
  const off = Math.max(2, Math.round(Math.min(w, h) / 80));
  const out: number[] = [];
  for (let s = 0; s < 4; s++) {
    const a = q[s];
    const b = q[(s + 1) % 4];
    let nx = -(b.y - a.y);
    let ny = b.x - a.x;
    const len = Math.hypot(nx, ny) || 1;
    nx /= len;
    ny /= len;
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    if ((cx - mx) * nx + (cy - my) * ny < 0) {
      nx = -nx;
      ny = -ny;
    }
    let sum = 0, n = 0;
    for (let k = 1; k < 24; k++) {
      const t = k / 24;
      const px = a.x + (b.x - a.x) * t, py = a.y + (b.y - a.y) * t;
      const ix = Math.round(px + nx * off), iy = Math.round(py + ny * off);
      const ox = Math.round(px - nx * off), oy = Math.round(py - ny * off);
      if (ix < 0 || iy < 0 || ix >= w || iy >= h) continue;
      const inside = img[iy * w + ix];
      const outside = ox >= 0 && oy >= 0 && ox < w && oy < h ? img[oy * w + ox] : inside * 0.5; // frame border counts as background
      sum += inside - outside;
      n++;
    }
    out.push(n ? sum / n / 255 : 0);
  }
  return out;
}

function dilate(mask: Uint8Array, w: number, h: number): Uint8Array {
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      if (mask[i]) {
        out[i] = 1;
        if (x > 0) out[i - 1] = 1;
        if (x < w - 1) out[i + 1] = 1;
        if (y > 0) out[i - w] = 1;
        if (y < h - 1) out[i + w] = 1;
      }
    }
  }
  return out;
}

/** Label 4-connected components of `mask`; return row extremes of the largest one. */
function largestComponent(mask: Uint8Array, w: number, h: number): { area: number; pts: Pt[] } | null {
  const labels = new Int32Array(w * h);
  const stack = new Int32Array(w * h);
  let next = 0;
  let bestLabel = 0;
  let bestArea = 0;
  for (let start = 0; start < w * h; start++) {
    if (!mask[start] || labels[start]) continue;
    next++;
    let sp = 0;
    stack[sp++] = start;
    labels[start] = next;
    let area = 0;
    while (sp) {
      const i = stack[--sp];
      area++;
      const x = i % w;
      if (x > 0 && mask[i - 1] && !labels[i - 1]) { labels[i - 1] = next; stack[sp++] = i - 1; }
      if (x < w - 1 && mask[i + 1] && !labels[i + 1]) { labels[i + 1] = next; stack[sp++] = i + 1; }
      if (i >= w && mask[i - w] && !labels[i - w]) { labels[i - w] = next; stack[sp++] = i - w; }
      if (i < w * (h - 1) && mask[i + w] && !labels[i + w]) { labels[i + w] = next; stack[sp++] = i + w; }
    }
    if (area > bestArea) {
      bestArea = area;
      bestLabel = next;
    }
  }
  if (!bestLabel) return null;
  const pts: Pt[] = [];
  for (let y = 0; y < h; y++) {
    let minX = -1;
    let maxX = -1;
    for (let x = 0; x < w; x++) {
      if (labels[y * w + x] === bestLabel) {
        if (minX < 0) minX = x;
        maxX = x;
      }
    }
    if (minX >= 0) {
      pts.push({ x: minX, y });
      if (maxX !== minX) pts.push({ x: maxX + 1, y });
    }
  }
  return { area: bestArea, pts };
}

function cross(o: Pt, a: Pt, b: Pt) {
  return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

export function convexHull(points: Pt[]): Pt[] {
  const pts = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  if (pts.length < 3) return pts;
  const lower: Pt[] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: Pt[] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  upper.pop();
  lower.pop();
  return lower.concat(upper);
}

/** Repeatedly drop the hull vertex whose removal loses the least area. */
export function reduceToQuad(hull: Pt[]): Pt[] | null {
  const poly = [...hull];
  if (poly.length < 4) return null;
  while (poly.length > 4) {
    let minA = Infinity;
    let idx = 0;
    for (let i = 0; i < poly.length; i++) {
      const a = poly[(i - 1 + poly.length) % poly.length];
      const b = poly[i];
      const c = poly[(i + 1) % poly.length];
      const area = Math.abs(cross(a, b, c));
      if (area < minA) {
        minA = area;
        idx = i;
      }
    }
    poly.splice(idx, 1);
  }
  return poly;
}

export function orderQuad(pts: Pt[]): Quad {
  const bySum = [...pts].sort((a, b) => a.x + a.y - (b.x + b.y));
  const byDiff = [...pts].sort((a, b) => a.y - a.x - (b.y - b.x));
  return [bySum[0], byDiff[0], bySum[3], byDiff[3]];
}

export function quadArea(q: Pt[]): number {
  let a = 0;
  for (let i = 0; i < q.length; i++) {
    const p = q[i];
    const n = q[(i + 1) % q.length];
    a += p.x * n.y - n.x * p.y;
  }
  return Math.abs(a) / 2;
}

function angleAt(a: Pt, b: Pt, c: Pt) {
  const v1x = a.x - b.x, v1y = a.y - b.y, v2x = c.x - b.x, v2y = c.y - b.y;
  const cos = (v1x * v2x + v1y * v2y) / (Math.hypot(v1x, v1y) * Math.hypot(v2x, v2y) + 1e-9);
  return (Math.acos(Math.max(-1, Math.min(1, cos))) * 180) / Math.PI;
}

function edgeSupport(q: Quad, mag: Float32Array, w: number, h: number, thr: number): number {
  const sides: number[] = [];
  for (let s = 0; s < 4; s++) {
    const a = q[s];
    const b = q[(s + 1) % 4];
    let hits = 0;
    const n = 36;
    for (let k = 1; k < n; k++) {
      const t = k / n;
      const x = Math.round(a.x + (b.x - a.x) * t);
      const y = Math.round(a.y + (b.y - a.y) * t);
      let found = false;
      for (let dy = -2; dy <= 2 && !found; dy++) {
        for (let dx = -2; dx <= 2 && !found; dx++) {
          const xx = x + dx, yy = y + dy;
          if (xx >= 0 && yy >= 0 && xx < w && yy < h && mag[yy * w + xx] > thr) found = true;
        }
      }
      if (found) hits++;
    }
    sides.push(hits / (n - 1));
  }
  sides.sort((a, b) => a - b);
  return (sides[1] + sides[2] + sides[3]) / 3; // tolerate one weak side (e.g. paper edge against similar background)
}

function laplacianVariance(img: Float32Array, w: number, h: number): number {
  let n = 0, sum = 0, sum2 = 0;
  const x0 = Math.floor(w * 0.2), x1 = Math.floor(w * 0.8), y0 = Math.floor(h * 0.2), y1 = Math.floor(h * 0.8);
  for (let y = Math.max(1, y0); y < Math.min(h - 1, y1); y++) {
    for (let x = Math.max(1, x0); x < Math.min(w - 1, x1); x++) {
      const i = y * w + x;
      const v = img[i - 1] + img[i + 1] + img[i - w] + img[i + w] - 4 * img[i];
      sum += v;
      sum2 += v * v;
      n++;
    }
  }
  if (!n) return 0;
  const mean = sum / n;
  return sum2 / n - mean * mean;
}

/**
 * Snap each approximate corner to the hull point that projects furthest along the
 * corner's diagonal (centroid -> corner). Vertex-reduction alone can keep a point
 * half-way along a side as a "corner", which makes the outline jump between frames.
 */
export function refineCorners(approx: Quad, hull: Pt[]): Quad {
  const cx = (approx[0].x + approx[1].x + approx[2].x + approx[3].x) / 4;
  const cy = (approx[0].y + approx[1].y + approx[2].y + approx[3].y) / 4;
  return approx.map((c) => {
    let dx = c.x - cx;
    let dy = c.y - cy;
    const len = Math.hypot(dx, dy) || 1;
    dx /= len;
    dy /= len;
    let best = c;
    let bestProj = -Infinity;
    for (const p of hull) {
      const proj = (p.x - cx) * dx + (p.y - cy) * dy;
      if (proj > bestProj) {
        bestProj = proj;
        best = p;
      }
    }
    return { x: best.x, y: best.y };
  }) as Quad;
}

function candidateQuad(comp: { area: number; pts: Pt[] } | null, w: number, h: number): Quad | null {
  if (!comp || comp.area < w * h * 0.12) return null;
  const hull = convexHull(comp.pts);
  const reduced = reduceToQuad(hull);
  if (!reduced) return null;
  // two passes: the second uses the improved corners to aim the diagonals better
  const q1 = refineCorners(orderQuad(reduced), hull);
  return orderQuad(refineCorners(q1, hull));
}

export function expandQuad(q: Quad, amount: number, w = 1, h = 1): Quad {
  const cx = (q[0].x + q[1].x + q[2].x + q[3].x) / 4;
  const cy = (q[0].y + q[1].y + q[2].y + q[3].y) / 4;
  return q.map((p) => ({
    x: Math.min(w, Math.max(0, cx + (p.x - cx) * (1 + amount))),
    y: Math.min(h, Math.max(0, cy + (p.y - cy) * (1 + amount))),
  })) as Quad;
}

export function detectDocument(rgbaOrGray: Uint8ClampedArray | Uint8Array | Float32Array, w: number, h: number): Detection {
  const gray = rgbaOrGray instanceof Float32Array ? rgbaOrGray : toGray(rgbaOrGray, w, h);
  const blur = boxBlur(boxBlur(gray, w, h), w, h);
  let brightness = 0;
  for (let i = 0; i < blur.length; i++) brightness += blur[i];
  brightness /= blur.length;
  const sharpness = laplacianVariance(gray, w, h);
  const mag = sobel(blur, w, h);
  const hi = Math.max(40, percentile(mag, 0.88));
  const lo = hi * 0.5;

  const candidates: { quad: Quad; method: "bright" | "edges" }[] = [];

  // A) bright, colourless region (paper) — opened to cut thin bridges to textured backgrounds
  const isRgba = !(rgbaOrGray instanceof Float32Array);
  const pmap = isRgba ? boxBlur(boxBlur(paperness(rgbaOrGray as Uint8ClampedArray, w, h), w, h), w, h) : blur;
  const t = otsu(pmap);
  let bright: Uint8Array = new Uint8Array(w * h);
  for (let i = 0; i < pmap.length; i++) bright[i] = pmap[i] > t ? 1 : 0;
  bright = dilate(dilate(erode(erode(bright, w, h), w, h), w, h), w, h);
  const qa = candidateQuad(largestComponent(bright, w, h), w, h);
  if (qa) candidates.push({ quad: qa, method: "bright" });

  // B) area enclosed by strong edges
  const edges = new Uint8Array(w * h);
  for (let i = 0; i < mag.length; i++) edges[i] = mag[i] > hi ? 1 : 0;
  const ed = dilate(dilate(edges, w, h), w, h);
  const outside = new Uint8Array(w * h);
  const stack = new Int32Array(w * h);
  let sp = 0;
  const push = (i: number) => {
    if (!ed[i] && !outside[i]) {
      outside[i] = 1;
      stack[sp++] = i;
    }
  };
  for (let x = 0; x < w; x++) {
    push(x);
    push((h - 1) * w + x);
  }
  for (let y = 0; y < h; y++) {
    push(y * w);
    push(y * w + w - 1);
  }
  while (sp) {
    const i = stack[--sp];
    const x = i % w;
    if (x > 0) push(i - 1);
    if (x < w - 1) push(i + 1);
    if (i >= w) push(i - w);
    if (i < w * (h - 1)) push(i + w);
  }
  const inside = new Uint8Array(w * h);
  for (let i = 0; i < inside.length; i++) inside[i] = !outside[i] ? 1 : 0;
  const qb = candidateQuad(largestComponent(inside, w, h), w, h);
  if (qb) candidates.push({ quad: qb, method: "edges" });

  let best: Detection = { quad: null, score: 0, sharpness, brightness, method: null };
  for (const c of candidates) {
    const area = quadArea(c.quad) / (w * h);
    if (area < 0.12 || area > 0.985) continue;
    const q = c.quad;
    const angles = [0, 1, 2, 3].map((i) => angleAt(q[(i + 3) % 4], q[i], q[(i + 1) % 4]));
    if (Math.min(...angles) < 50 || Math.max(...angles) > 130) continue;
    const support = edgeSupport(q, mag, w, h, lo);
    if (support < 0.5) continue;
    const contrast = boundaryContrast(q, blur, w, h).sort((a, b) => b - a);
    const best3 = (contrast[0] + contrast[1] + contrast[2]) / 3;
    if (contrast[2] < 0.05 || best3 < 0.08) continue; // a side running through the background has no contrast
    const score = support * 0.4 + Math.min(best3, 0.5) * 1.2 + Math.min(area, 0.8) * 0.3;
    if (score > best.score) {
      const norm = q.map((p) => ({ x: p.x / w, y: p.y / h })) as Quad;
      best = { quad: expandQuad(norm, 0.012), score, sharpness, brightness, method: c.method };
    }
  }
  return best;
}

/** Max corner displacement between two quads (normalised units). */
export function quadDistance(a: Quad, b: Quad): number {
  let m = 0;
  for (let i = 0; i < 4; i++) m = Math.max(m, Math.hypot(a[i].x - b[i].x, a[i].y - b[i].y));
  return m;
}

export function lerpQuad(a: Quad, b: Quad, t: number): Quad {
  return a.map((p, i) => ({ x: p.x + (b[i].x - p.x) * t, y: p.y + (b[i].y - p.y) * t })) as Quad;
}

export const FULL_QUAD: Quad = [
  { x: 0, y: 0 },
  { x: 1, y: 0 },
  { x: 1, y: 1 },
  { x: 0, y: 1 },
];
