/**
 * Local read cache: the last known good copy of everything the app displays.
 *
 * The rule is local-first for *reading*. A screen renders the cached rows straight
 * away and marks itself stale, then replaces them when the server answers. An
 * artisan on a train opens My Products and sees her products, not a spinner that
 * never resolves; the server stays the source of truth whenever it can be reached.
 *
 * Cached rows are never treated as authoritative for money or status decisions -
 * they carry `fetchedAt` so the UI can say "as of 3 hours ago" instead of implying
 * the number is live.
 */
import * as store from './storage';

export type Cached<T> = { data: T; fetchedAt: number };

const P = 'kk.cache.';

export async function read<T>(key: string): Promise<Cached<T> | null> {
  const raw = await store.get(P + key);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.fetchedAt === 'number') return parsed as Cached<T>;
  } catch { /* corrupt entry: treat as absent */ }
  return null;
}

export async function write<T>(key: string, data: T): Promise<void> {
  await store.set(P + key, JSON.stringify({ data, fetchedAt: Date.now() }));
}

export async function clear(): Promise<void> {
  await store.removeMany(await store.keys(P));
}

/** Cache keys, in one place so nothing drifts. */
export const K = {
  bootstrap: 'bootstrap',
  listings: 'listings',
  orders: 'orders',
  channels: 'channels',
  insights: 'insights',
  listing: (id: string) => `listing.${id}`,
  publication: (id: string) => `pub.${id}`,
};

/** Human-scale age, for "updated N ago" labels. Returns minutes. */
export const ageMinutes = (c: Cached<unknown> | null) =>
  c ? Math.max(0, Math.round((Date.now() - c.fetchedAt) / 60000)) : null;
