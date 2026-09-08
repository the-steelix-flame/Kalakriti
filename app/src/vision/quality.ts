/**
 * On-device capture analysis. No network, no models, no native modules.
 *
 * Everything here runs on a small grayscale buffer (typically 160x120) taken from
 * the live preview, which is why it is fast enough to run continuously on a low-end
 * phone and works with the aeroplane mode on.
 *
 * What is measured, and how:
 *
 *   exposure    mean luminance + the share of clipped pixels. Separates "too dark"
 *               from "glare" - a washed-out highlight on a glazed pot is a very
 *               different problem from a dim room, and the advice differs.
 *   blur        variance of the Laplacian. The standard sharpness estimator: a
 *               sharp edge produces a large second derivative, a blurred one does
 *               not. Normalised by contrast so a flat dark scene is not mistaken
 *               for a blurry one.
 *   subject     Sobel gradient magnitude, accumulated into row and column energy
 *               profiles, then trimmed to the band holding the central mass of the
 *               energy. That gives a subject box without any model at all, which
 *               is what makes framing guidance work offline.
 *   clutter     edge energy outside the subject box relative to inside. A busy
 *               courtyard scores high; a cloth backdrop scores low.
 *   tilt        orientation histogram of strong gradients, folded to 0-90 degrees.
 *               A rectangular object photographed square has energy concentrated
 *               near 0 and 90; a tilted one shows a consistent offset.
 *
 * These are classical estimators rather than learned ones on purpose: they need no
 * download, run in a few milliseconds, and behave predictably enough to explain to
 * an artisan in one short sentence.
 */

export type Gray = { data: Uint8ClampedArray; w: number; h: number };

export type Box = { x: number; y: number; w: number; h: number };

export type Metrics = {
  brightness: number;      // 0..1 mean luminance
  contrast: number;        // 0..1 normalised std dev
  clipped: number;         // 0..1 share of blown-out pixels
  sharpness: number;       // 0..1, higher is sharper
  subject: Box | null;     // normalised 0..1
  coverage: number;        // subject area / frame area
  offCentre: number;       // 0..1 distance of subject centre from frame centre
  clutter: number;         // 0..1 background business
  tilt: number;            // degrees, 0..45
  touchesEdge: boolean;
};

/** RGBA (canvas / raw camera bytes) -> downscaled luma. */
export function toGray(rgba: Uint8ClampedArray, w: number, h: number,
                       targetW = 160): Gray {
  const scale = Math.max(1, Math.round(w / targetW));
  const ow = Math.floor(w / scale);
  const oh = Math.floor(h / scale);
  const out = new Uint8ClampedArray(ow * oh);
  for (let y = 0; y < oh; y++) {
    for (let x = 0; x < ow; x++) {
      const si = ((y * scale) * w + x * scale) * 4;
      // Rec. 601 luma - closer to perceived brightness than a plain mean.
      out[y * ow + x] =
        (rgba[si] * 0.299 + rgba[si + 1] * 0.587 + rgba[si + 2] * 0.114) | 0;
    }
  }
  return { data: out, w: ow, h: oh };
}

function stats(g: Gray) {
  let sum = 0, sumSq = 0, clipped = 0;
  const n = g.data.length;
  for (let i = 0; i < n; i++) {
    const v = g.data[i];
    sum += v; sumSq += v * v;
    if (v >= 250) clipped++;
  }
  const mean = sum / n;
  const varr = Math.max(sumSq / n - mean * mean, 0);
  return { mean, std: Math.sqrt(varr), clipped: clipped / n };
}

/** Variance of the Laplacian, normalised so contrast does not masquerade as focus. */
function sharpness(g: Gray, std: number): number {
  const { data, w, h } = g;
  let sum = 0, sumSq = 0, n = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const lap = -4 * data[i] + data[i - 1] + data[i + 1] + data[i - w] + data[i + w];
      sum += lap; sumSq += lap * lap; n++;
    }
  }
  if (!n) return 0;
  const mean = sum / n;
  const varr = Math.max(sumSq / n - mean * mean, 0);
  // Divide by contrast: a low-contrast scene has small second derivatives even in
  // perfect focus, and calling that "blurry" would send the artisan chasing nothing.
  const norm = varr / Math.max(std * std, 25);
  return Math.max(0, Math.min(1, norm / 3));
}

type EdgeField = { mag: Float32Array; w: number; h: number; total: number };

function sobel(g: Gray): EdgeField {
  const { data, w, h } = g;
  const mag = new Float32Array(w * h);
  let total = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const gx =
        -data[i - w - 1] - 2 * data[i - 1] - data[i + w - 1] +
        data[i - w + 1] + 2 * data[i + 1] + data[i + w + 1];
      const gy =
        -data[i - w - 1] - 2 * data[i - w] - data[i - w + 1] +
        data[i + w - 1] + 2 * data[i + w] + data[i + w + 1];
      const m = Math.sqrt(gx * gx + gy * gy);
      mag[i] = m; total += m;
    }
  }
  return { mag, w, h, total };
}

/** Trim a 1-D energy profile to the band holding `keep` of the total mass. */
function band(profile: Float32Array, keep: number): [number, number] {
  const n = profile.length;
  let total = 0;
  for (let i = 0; i < n; i++) total += profile[i];
  if (total <= 0) return [0, n - 1];
  const drop = (1 - keep) / 2 * total;
  let acc = 0, lo = 0, hi = n - 1;
  for (let i = 0; i < n; i++) { acc += profile[i]; if (acc >= drop) { lo = i; break; } }
  acc = 0;
  for (let i = n - 1; i >= 0; i--) { acc += profile[i]; if (acc >= drop) { hi = i; break; } }
  return [lo, Math.max(hi, lo + 1)];
}

function subjectBox(e: EdgeField): Box | null {
  if (e.total <= 0) return null;
  const col = new Float32Array(e.w);
  const row = new Float32Array(e.h);
  for (let y = 0; y < e.h; y++) {
    for (let x = 0; x < e.w; x++) {
      const m = e.mag[y * e.w + x];
      col[x] += m; row[y] += m;
    }
  }
  const [x0, x1] = band(col, 0.86);
  const [y0, y1] = band(row, 0.86);
  return {
    x: x0 / e.w, y: y0 / e.h,
    w: (x1 - x0) / e.w, h: (y1 - y0) / e.h,
  };
}

function clutterOutside(e: EdgeField, b: Box): number {
  const x0 = Math.floor(b.x * e.w), x1 = Math.ceil((b.x + b.w) * e.w);
  const y0 = Math.floor(b.y * e.h), y1 = Math.ceil((b.y + b.h) * e.h);
  let inside = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) inside += e.mag[y * e.w + x] || 0;
  }
  const outside = Math.max(e.total - inside, 0);
  const outArea = Math.max(e.w * e.h - (x1 - x0) * (y1 - y0), 1);
  const inArea = Math.max((x1 - x0) * (y1 - y0), 1);
  const outDensity = outside / outArea;
  const inDensity = inside / inArea;
  if (inDensity <= 0) return 1;
  return Math.max(0, Math.min(1, outDensity / inDensity));
}

/** Dominant edge orientation, folded into 0..45 degrees of skew from square. */
function tiltDegrees(g: Gray): number {
  const { data, w, h } = g;
  const bins = new Float32Array(90);
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const gx = data[i + 1] - data[i - 1];
      const gy = data[i + w] - data[i - w];
      const m = Math.sqrt(gx * gx + gy * gy);
      if (m < 40) continue;                       // ignore texture noise
      let a = (Math.atan2(gy, gx) * 180) / Math.PI;
      a = ((a % 180) + 180) % 180;                // 0..180
      bins[Math.min(89, Math.floor(a / 2))] += m;
    }
  }
  let best = 0, bestV = -1;
  for (let i = 0; i < 90; i++) if (bins[i] > bestV) { bestV = bins[i]; best = i; }
  if (bestV <= 0) return 0;
  const deg = best * 2;                            // 0..178
  const off = Math.min(deg % 90, 90 - (deg % 90)); // distance to the nearest axis
  return Math.round(off);
}

export function analyse(gray: Gray): Metrics {
  const { mean, std, clipped } = stats(gray);
  const e = sobel(gray);
  const box = subjectBox(e);
  const coverage = box ? box.w * box.h : 0;
  const offCentre = box
    ? Math.hypot((box.x + box.w / 2) - 0.5, (box.y + box.h / 2) - 0.5) * 2
    : 1;
  const touchesEdge = !!box &&
    (box.x <= 0.02 || box.y <= 0.02 || box.x + box.w >= 0.98 || box.y + box.h >= 0.98);

  return {
    brightness: mean / 255,
    contrast: Math.min(1, std / 80),
    clipped,
    sharpness: sharpness(gray, std),
    subject: box,
    coverage,
    offCentre: Math.min(1, offCentre),
    clutter: box ? clutterOutside(e, box) : 1,
    tilt: tiltDegrees(gray),
    touchesEdge,
  };
}
