/**
 * Turns capture metrics into ONE instruction.
 *
 * The hardest part of this is not detection, it is restraint. An artisan holding a
 * phone over a pot cannot act on five simultaneous corrections; showing "too dark,
 * tilted, cluttered, move closer" at once is the same as showing nothing. So the
 * checks are ordered by how much they actually ruin a listing photo, and only the
 * first failing one is spoken.
 *
 * Ordering rationale: an unusable frame beats a badly composed one. A dark or
 * blurred photo cannot be rescued later; a slightly off-centre one can be cropped.
 * Clutter comes last because background removal will handle most of it anyway.
 *
 * Thresholds live per category because the right answer genuinely differs: jewellery
 * is photographed close and small in frame, a saree is photographed whole and fills
 * it, and glazed pottery legitimately carries specular highlights that would be
 * flagged as glare on cloth.
 */
import { Metrics } from './quality';

export type Verdict = {
  ok: boolean;
  code: string;
  en: string;   // developer-facing only; the UI translates `code`
  severity: 'good' | 'warn' | 'bad';
};

export type CategoryKey =
  | 'textile' | 'pottery' | 'jewellery' | 'woodwork' | 'metalwork' | 'general';

type Rules = {
  minCoverage: number;
  maxCoverage: number;
  maxClipped: number;
  minSharp: number;
  maxTilt: number;
  maxClutter: number;
  /** Extra shots worth having for this craft, in the order they are requested. */
  shots: { key: string; en: string }[];
};

export const CATEGORIES: Record<CategoryKey, Rules> = {
  textile: {
    // A fabric should fill the frame; its value is in the weave and the border.
    minCoverage: 0.30, maxCoverage: 0.95, maxClipped: 0.02,
    minSharp: 0.30, maxTilt: 12, maxClutter: 0.75,
    shots: [
      { key: 'full', en: 'The whole fabric, spread flat' },
      { key: 'border', en: 'Close-up of the border' },
      { key: 'weave', en: 'Close-up of the weave' },
    ],
  },
  pottery: {
    // Glaze produces real specular highlights; being strict here cries wolf.
    minCoverage: 0.18, maxCoverage: 0.80, maxClipped: 0.06,
    minSharp: 0.28, maxTilt: 10, maxClutter: 0.70,
    shots: [
      { key: 'full', en: 'The whole piece, from the front' },
      { key: 'side', en: 'From the side' },
      { key: 'detail', en: 'Close-up of the painting' },
    ],
  },
  jewellery: {
    // Small objects, photographed close. A low coverage bar would be wrong here.
    minCoverage: 0.10, maxCoverage: 0.70, maxClipped: 0.05,
    minSharp: 0.40, maxTilt: 15, maxClutter: 0.60,
    shots: [
      { key: 'full', en: 'The whole piece' },
      { key: 'detail', en: 'Close-up of the work' },
      { key: 'clasp', en: 'The clasp or joint' },
    ],
  },
  woodwork: {
    minCoverage: 0.22, maxCoverage: 0.88, maxClipped: 0.03,
    minSharp: 0.30, maxTilt: 10, maxClutter: 0.72,
    shots: [
      { key: 'full', en: 'The whole object' },
      { key: 'carve', en: 'Close-up of the carving' },
      { key: 'grain', en: 'The wood grain' },
    ],
  },
  metalwork: {
    // Polished metal reflects everything; the glare bar has to be generous.
    minCoverage: 0.18, maxCoverage: 0.82, maxClipped: 0.09,
    minSharp: 0.30, maxTilt: 12, maxClutter: 0.70,
    shots: [
      { key: 'full', en: 'The whole object' },
      { key: 'detail', en: 'Close-up of the work' },
    ],
  },
  general: {
    minCoverage: 0.18, maxCoverage: 0.88, maxClipped: 0.04,
    minSharp: 0.30, maxTilt: 12, maxClutter: 0.72,
    shots: [
      { key: 'full', en: 'The whole product' },
      { key: 'detail', en: 'Close-up of the detail' },
      { key: 'label', en: 'Label or price tag' },
    ],
  },
};

const V = (code: string, _en: string,
           severity: Verdict['severity'] = 'bad'): Verdict =>
  // hi/en are kept in the call sites for readability, but the screens
  // translate `code` through the catalogue (guide.*) so every language works.
  ({ ok: false, code, en: _en, severity });

export function judge(m: Metrics, cat: CategoryKey = 'general'): Verdict {
  const r = CATEGORIES[cat];

  // 1. Nothing usable in frame at all.
  if (!m.subject || m.coverage < 0.02) {
    return V('empty', 'Place the product inside the frame');
  }

  // 2. Light. An underexposed or blown frame cannot be repaired afterwards.
  if (m.brightness < 0.16) {
    return V('dark', 'Too dark - move to a brighter place');
  }
  if (m.clipped > r.maxClipped && m.brightness > 0.72) {
    return V('glare', 'Too much glare - turn away from the direct light');
  }
  if (m.brightness > 0.93) {
    return V('bright', 'Far too bright');
  }

  // 3. Focus. Checked after light because a dark frame reads as blurry anyway.
  if (m.sharpness < r.minSharp) {
    return V('blur', 'The photo is blurry - hold the phone still');
  }

  // 4. Framing.
  if (m.coverage > r.maxCoverage || m.touchesEdge) {
    return V('close', 'warn');
  }
  if (m.coverage < r.minCoverage) {
    return V('far', 'warn');
  }
  if (m.offCentre > 0.34) {
    return V('offcentre', 'warn');
  }
  if (m.tilt > r.maxTilt) {
    return V('tilt', 'warn');
  }

  // 5. Background, last: matting removes most of it anyway.
  if (m.clutter > r.maxClutter) {
    return V('clutter', 'warn');
  }

  return { ok: true, code: 'good', en: 'Perfect. Take the photo.',
           severity: 'good' };
}

/** Steady the advice: a single noisy frame should not flip the instruction. */
export class Smoother {
  private recent: string[] = [];
  private last: Verdict | null = null;

  constructor(private window = 5, private need = 3) {}

  push(v: Verdict): Verdict {
    this.recent.push(v.code);
    if (this.recent.length > this.window) this.recent.shift();
    const counts = new Map<string, number>();
    for (const c of this.recent) counts.set(c, (counts.get(c) || 0) + 1);
    if ((counts.get(v.code) || 0) >= this.need || !this.last) this.last = v;
    return this.last;
  }

  /** Only let the shutter arm when the frame has been good for a few frames running. */
  steadyGood(): boolean {
    return this.recent.length >= this.need &&
      this.recent.slice(-this.need).every((c) => c === 'good');
  }

  reset() { this.recent = []; this.last = null; }
}
