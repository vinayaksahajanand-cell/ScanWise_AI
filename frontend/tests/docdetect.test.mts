// Run: npm test   (Node's built-in test runner with TypeScript type stripping)
import assert from "node:assert/strict";
import { test } from "node:test";

import { detectDocument, type Pt } from "../src/lib/docdetect.ts";

function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296;
  };
}

function inside(q: Pt[], x: number, y: number) {
  for (let i = 0; i < 4; i++) {
    const a = q[i], b = q[(i + 1) % 4];
    if ((b.x - a.x) * (y - a.y) - (b.y - a.y) * (x - a.x) < 0) return false;
  }
  return true;
}

/** Synthetic camera frame: textured dark desk + bright paper (quad) with text lines. */
function frame(w: number, h: number, quad: Pt[] | null, opts: { bg?: number; paper?: number; seed?: number } = {}) {
  const r = rng(opts.seed ?? 7);
  const g = new Float32Array(w * h);
  const bg = opts.bg ?? 70;
  const paper = opts.paper ?? 228;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let v = bg + 25 * Math.sin(x / 17) * Math.cos(y / 23) + (r() - 0.5) * 18;
      if (quad && inside(quad, x, y)) {
        v = paper + (r() - 0.5) * 8;
        // text lines inside the paper (parametrise by position within the quad's bbox)
        const ty = (y - quad[0].y) / (quad[3].y - quad[0].y);
        const tx = (x - quad[0].x) / (quad[1].x - quad[0].x);
        if (ty > 0.12 && ty < 0.85 && Math.floor(ty * 40) % 3 === 0 && tx > 0.1 && tx < 0.85 && r() > 0.25) v = 45;
      }
      g[y * w + x] = Math.max(0, Math.min(255, v));
    }
  }
  return g;
}

function assertNear(found: Pt[] | null, truth: Pt[], w: number, h: number, tol = 0.035) {
  assert.ok(found, "expected a document to be detected");
  for (let i = 0; i < 4; i++) {
    const dx = found![i].x - truth[i].x / w;
    const dy = found![i].y - truth[i].y / h;
    assert.ok(Math.hypot(dx, dy) < tol, `corner ${i} off by ${Math.hypot(dx, dy).toFixed(3)} (found ${JSON.stringify(found![i])}, truth ${truth[i].x / w},${truth[i].y / h})`);
  }
}

test("detects a perspective-skewed invoice on a dark desk", () => {
  const w = 320, h = 427;
  const quad = [{ x: 62, y: 58 }, { x: 262, y: 74 }, { x: 281, y: 371 }, { x: 40, y: 352 }];
  const d = detectDocument(frame(w, h, quad), w, h);
  assertNear(d.quad, quad, w, h);
  assert.ok(d.score > 0.6, `score ${d.score}`);
});

test("detects a rotated page", () => {
  const w = 320, h = 427;
  const c = { x: 160, y: 213 };
  const a = (20 * Math.PI) / 180;
  const corners = [[-95, -135], [95, -135], [95, 135], [-95, 135]].map(([x, y]) => ({
    x: c.x + x * Math.cos(a) - y * Math.sin(a),
    y: c.y + x * Math.sin(a) + y * Math.cos(a),
  }));
  // order: tl,tr,br,bl after rotation by sums/diffs
  const bySum = [...corners].sort((p, q) => p.x + p.y - (q.x + q.y));
  const byDiff = [...corners].sort((p, q) => p.y - p.x - (q.y - q.x));
  const truth = [bySum[0], byDiff[0], bySum[3], byDiff[3]];
  const d = detectDocument(frame(w, h, corners, { seed: 3 }), w, h);
  assertNear(d.quad, truth, w, h, 0.045);
});

test("returns no quad when there is no document", () => {
  const w = 320, h = 240;
  const d = detectDocument(frame(w, h, null, { seed: 11 }), w, h);
  assert.equal(d.quad, null);
});

test("camera frame: invoice on a textured, unevenly lit wooden desk", async () => {
  const { readFileSync } = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const rgba = new Uint8ClampedArray(readFileSync(fileURLToPath(new URL("./fixtures/camera_invoice_320x240.rgba", import.meta.url))));
  const w = 320, h = 240;
  // ground truth from the generator (1280x960 frame), scaled to 320x240
  const truth = [{ x: 372, y: 92 }, { x: 905, y: 128 }, { x: 948, y: 880 }, { x: 318, y: 846 }].map((p) => ({ x: p.x / 4, y: p.y / 4 }));
  const d = detectDocument(rgba, w, h);
  assertNear(d.quad, truth, w, h, 0.035);
  assert.equal(d.method, "bright");
});

test("reports sharpness and brightness", () => {
  const w = 160, h = 120;
  const d = detectDocument(frame(w, h, null, { bg: 20 }), w, h);
  assert.ok(d.brightness < 60);
  assert.ok(d.sharpness >= 0);
});
