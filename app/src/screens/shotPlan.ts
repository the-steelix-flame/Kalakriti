/**
 * Multi-shot capture plan.
 *
 * One photograph is rarely enough to sell a handmade object: a buyer wants the whole
 * piece, the detail that justifies the price, and - for a listing that has to prove
 * it is handmade - the texture or the maker's mark. The plan is per category, and
 * the app says *why* each further shot is wanted rather than just demanding another.
 *
 * Only the first shot is required; everything after it is optional, because an
 * artisan who has taken one good photo should never be blocked from listing.
 */
import { CATEGORIES, CategoryKey } from '../vision/guidance';

export type Shot = { key: string; en: string };

export type CapturedShot = { key: string; uri: string };

export class ShotPlan {
  readonly shots: Shot[];
  index = 0;
  captured: CapturedShot[] = [];

  constructor(readonly category: CategoryKey = 'general') {
    this.shots = CATEGORIES[category].shots;
  }

  get total() { return this.shots.length; }

  current(): Shot {
    return this.shots[Math.min(this.index, this.shots.length - 1)];
  }

  /** The key of the next shot, for the caller to translate. */
  nextShotKey(): string | null {
    const next = this.shots[this.index];
    if (!next || this.index === 0) return null;
    return next.key;
  }

  add(uri: string, key?: string) {
    this.captured.push({ key: key || this.current().key, uri });
    if (this.index < this.shots.length - 1) this.index += 1;
  }

  get done() { return this.captured.length >= 1; }
  get complete() { return this.captured.length >= this.shots.length; }

  /** The shot the listing's main image comes from. */
  primary(): string | null {
    return this.captured[0]?.uri ?? null;
  }

  reset() { this.index = 0; this.captured = []; }
}
