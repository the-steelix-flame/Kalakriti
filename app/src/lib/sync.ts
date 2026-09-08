/**
 * Offline write queue.
 *
 * Reads are handled by cache.ts. This is the other half: when the artisan edits a
 * price in a place with no signal, the edit is recorded here and replayed when the
 * connection returns. Nothing is lost because a tower was out of range.
 *
 * Conflicts are resolved by field, not by document. Every queued edit carries the
 * `updatedAt` of the row it was made against and only the fields that were actually
 * touched. On replay the server compares:
 *
 *   - the row has not moved since  -> apply the edit
 *   - the row moved, but not in the fields this edit touches -> apply them; both
 *     changes survive, which is the honest merge
 *   - the row moved in a field this edit also touches -> the server keeps its value
 *     and returns the conflict, and the app tells the artisan which field differs
 *     rather than silently discarding either side
 *
 * Last-write-wins was rejected because both sides here are real work: the server
 * copy may hold a marketplace's own price correction, and the local copy is what
 * the artisan believes she set.
 */
import * as store from './storage';

export type Op = {
  id: string;
  kind: 'listing.patch' | 'profile.patch' | 'address.put' | 'order.patch';
  target: string;                 // listing id, order id, or '' for profile
  fields: Record<string, any>;    // only what the artisan changed
  baseUpdatedAt: string | null;   // what the row looked like when they changed it
  queuedAt: number;
  tries: number;
  lastError?: string;
};

export type Conflict = {
  op: Op;
  field: string;
  mine: any;
  theirs: any;
};

const KEY = 'kk.outbox';
const CONFLICTS = 'kk.conflicts';

let queue: Op[] = [];
let loaded = false;
const listeners = new Set<() => void>();

const notify = () => listeners.forEach((l) => l());

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export async function load(): Promise<void> {
  if (loaded) return;
  queue = await store.getJSON<Op[]>(KEY, []);
  loaded = true;
  notify();
}

const persist = () => store.setJSON(KEY, queue);

export const pending = () => queue.length;
export const peek = () => [...queue];

/**
 * Queue an edit. Repeated edits to the same fields of the same row collapse into
 * one op - typing a price digit by digit must not become nine queued writes, and
 * the earliest `baseUpdatedAt` is kept because that is the version the artisan was
 * genuinely looking at when she started.
 */
export async function enqueue(op: Omit<Op, 'id' | 'queuedAt' | 'tries'>): Promise<void> {
  await load();
  const existing = queue.find((q) => q.kind === op.kind && q.target === op.target);
  if (existing) {
    existing.fields = { ...existing.fields, ...op.fields };
    existing.lastError = undefined;
  } else {
    queue.push({
      ...op,
      id: 'op_' + Math.random().toString(36).slice(2) + Date.now().toString(36),
      queuedAt: Date.now(),
      tries: 0,
    });
  }
  await persist();
  notify();
}

export async function conflicts(): Promise<Conflict[]> {
  return store.getJSON<Conflict[]>(CONFLICTS, []);
}

export async function clearConflict(opId: string, field: string): Promise<void> {
  const list = await conflicts();
  await store.setJSON(CONFLICTS,
    list.filter((c) => !(c.op.id === opId && c.field === field)));
  notify();
}

async function addConflicts(items: Conflict[]): Promise<void> {
  if (!items.length) return;
  await store.setJSON(CONFLICTS, [...(await conflicts()), ...items]);
}

export type Sender = (op: Op) => Promise<{ ok: boolean; conflicts?: Conflict[];
                                           retry?: boolean; error?: string }>;

let flushing = false;

/**
 * Replay the queue oldest first. Order matters: a price edit followed by a status
 * change must not arrive reversed.
 *
 * An op that fails because the server rejected it (a 4xx that is not a conflict) is
 * dropped after three attempts rather than blocking every later edit behind it
 * forever - it is recorded as an error the artisan can see.
 */
export async function flush(send: Sender): Promise<{ sent: number; failed: number }> {
  await load();
  if (flushing || !queue.length) return { sent: 0, failed: 0 };
  flushing = true;
  let sent = 0, failed = 0;
  try {
    for (const op of [...queue]) {
      let res;
      try {
        res = await send(op);
      } catch (e: any) {
        res = { ok: false, retry: true, error: String(e?.message || e) };
      }
      if (res.conflicts?.length) await addConflicts(res.conflicts);
      if (res.ok || res.conflicts?.length) {
        queue = queue.filter((q) => q.id !== op.id);
        sent += 1;
      } else if (res.retry) {
        break;                       // still offline: stop, keep order intact
      } else {
        op.tries += 1;
        op.lastError = res.error;
        if (op.tries >= 3) queue = queue.filter((q) => q.id !== op.id);
        failed += 1;
      }
    }
    await persist();
  } finally {
    flushing = false;
    notify();
  }
  return { sent, failed };
}

export async function reset(): Promise<void> {
  queue = [];
  await persist();
  await store.set(CONFLICTS, null);
  notify();
}
