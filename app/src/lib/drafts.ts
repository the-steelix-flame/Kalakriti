/**
 * Listings that live on the phone.
 *
 * Until now a listing could only be created by the backend: the photograph was
 * uploaded, /v1/analyze returned a row, and every section of the create screen was
 * rendered only if that row existed. On a phone with no signal - which is the phone
 * this app is for - taking a photograph produced a red error and nothing else. No
 * fields, no continue button, no way forward. The laptop was a hard dependency for
 * the app's central act.
 *
 * A draft here is the artisan's own work, held in her own storage, complete on its
 * own. The server is where it goes later, not where it comes from. `serverId` is
 * filled in when a backend has accepted it; until then it is null and nothing
 * anywhere waits on it.
 *
 * The photograph is copied out of the camera's cache directory into the app's
 * documents directory. Android empties the cache without warning, and a draft whose
 * picture has evaporated is worse than no draft.
 */
import { Directory, File, Paths } from 'expo-file-system';
import * as store from './storage';

export type SyncState = 'local' | 'synced' | 'failed';

export type Draft = {
  id: string;
  /** The backend's id for this listing, once one exists. */
  serverId: string | null;
  craft: string;
  mode: 'auto' | 'manual';
  /** A file:// uri inside our own documents directory. */
  imageUri: string | null;
  shots: { key: string; uri: string }[];
  /** Everything the artisan typed: titleEn, descHi, price, quantity, ... */
  fields: Record<string, any>;
  createdAt: number;
  updatedAt: number;
  syncState: SyncState;
  lastError?: string;
};

const KEY = 'kk.drafts';
const PHOTOS = 'kalakriti-photos';

let cachedList: Draft[] | null = null;
const listeners = new Set<() => void>();

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
const notify = () => listeners.forEach((l) => l());

export async function all(): Promise<Draft[]> {
  if (!cachedList) cachedList = await store.getJSON<Draft[]>(KEY, []);
  return cachedList;
}

async function write(next: Draft[]): Promise<void> {
  cachedList = next;
  await store.setJSON(KEY, next);
  notify();
}

export async function get(id: string): Promise<Draft | null> {
  return (await all()).find((d) => d.id === id) ?? null;
}

/** Local ids are visibly different from server ids so nothing can confuse the two. */
export const isLocalId = (id: string | null | undefined) =>
  !!id && id.startsWith('ld_');

const newId = () =>
  'ld_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

export async function create(init: Partial<Draft> = {}): Promise<Draft> {
  const now = Date.now();
  const d: Draft = {
    id: newId(),
    serverId: null,
    craft: 'general',
    mode: 'auto',
    imageUri: null,
    shots: [],
    fields: {},
    createdAt: now,
    updatedAt: now,
    syncState: 'local',
    ...init,
  };
  await write([d, ...(await all())]);
  return d;
}

export async function patch(id: string, changes: Partial<Draft>): Promise<Draft | null> {
  const list = await all();
  const i = list.findIndex((d) => d.id === id);
  if (i < 0) return null;
  const next = [...list];
  next[i] = { ...next[i], ...changes, updatedAt: Date.now() };
  await write(next);
  return next[i];
}

/** Merge into `fields` rather than replacing it, so a partial save keeps the rest. */
export async function patchFields(
  id: string, fields: Record<string, any>,
): Promise<Draft | null> {
  const d = await get(id);
  if (!d) return null;
  return patch(id, { fields: { ...d.fields, ...fields } });
}

export async function remove(id: string): Promise<void> {
  const d = await get(id);
  await write((await all()).filter((x) => x.id !== id));
  if (d?.imageUri) { try { new File(d.imageUri).delete(); } catch {} }
}

/**
 * Copy a just-taken photograph somewhere permanent and return the new uri.
 *
 * The camera and the image picker both hand back a path in the cache directory,
 * which Android is free to empty at any time. If the copy fails for any reason the
 * original uri is returned unchanged - a picture that might disappear later is
 * still better than losing it now.
 */
export async function keepPhoto(uri: string): Promise<string> {
  try {
    const dir = new Directory(Paths.document, PHOTOS);
    if (!dir.exists) dir.create({ intermediates: true });
    const ext = (uri.split('?')[0].match(/\.(jpe?g|png|webp|heic)$/i)?.[1] || 'jpg')
      .toLowerCase();
    const dest = new File(dir, `${Date.now()}_${Math.random().toString(36).slice(2, 8)}.${ext}`);
    new File(uri).copy(dest);
    return dest.uri;
  } catch {
    return uri;
  }
}

/** Drafts still waiting for a backend, oldest first - the order they should go up in. */
export async function unsynced(): Promise<Draft[]> {
  return (await all())
    .filter((d) => !d.serverId)
    .sort((a, b) => a.createdAt - b.createdAt);
}
