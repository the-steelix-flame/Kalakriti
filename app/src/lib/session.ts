/**
 * Session state: who is signed in, in what language, and what they were doing.
 *
 * Everything here persists across launches. That is the whole point - the previous
 * version kept these in memory on native, so closing the app reset the artisan to a
 * first-time user. See storage.ts for why the token is held separately.
 *
 * The public getters are synchronous because they are read during render. They serve
 * a mirror that `hydrate()` fills from disk once, before the first paint; writes go
 * to the mirror immediately and to disk in the background, so the UI never waits on
 * a keystore round-trip to know which tab to draw.
 */
import * as store from './storage';

const K = {
  lang: 'kk.lang',
  token: 'kk.token',              // secure
  guest: 'kk.guest',
  draft: 'kk.draft',
  onboarded: 'kk.onboarded',
  artisan: 'kk.artisan',          // cached profile, for offline first paint
  hideInsights: 'kk.hideInsights',
};

// Values from the previous build, so an existing install is not logged out or asked
// to pick its language again by an upgrade.
const LEGACY: Record<string, string> = {
  [K.lang]: 'si.lang', [K.token]: 'si.token', [K.guest]: 'si.guest',
  [K.draft]: 'si.draft', [K.onboarded]: 'si.onboarded',
};

const mirror = new Map<string, string>();
let hydrated = false;

function put(k: string, v: string | null, secure = false) {
  if (v === null) mirror.delete(k); else mirror.set(k, v);
  void (secure ? store.setSecure(k, v) : store.set(k, v));
}

const read = (k: string) => mirror.get(k) ?? null;

/**
 * Load persisted state into the mirror. Call once, and await it, before rendering
 * anything that branches on being logged in or onboarded.
 */
export async function hydrate(): Promise<void> {
  if (hydrated) return;
  const plain = [K.lang, K.guest, K.draft, K.onboarded, K.artisan, K.hideInsights];
  const [vals, token] = await Promise.all([
    Promise.all(plain.map((k) => store.get(k))),
    store.getSecure(K.token),
  ]);
  plain.forEach((k, i) => { if (vals[i] != null) mirror.set(k, vals[i] as string); });
  if (token) mirror.set(K.token, token);

  // One-time migration off the old `si.*` keys.
  const missing = [...plain, K.token].filter((k) => !mirror.has(k) && LEGACY[k]);
  if (missing.length) {
    const old = await Promise.all(missing.map((k) => store.get(LEGACY[k])));
    missing.forEach((k, i) => {
      const v = old[i];
      if (v != null) { mirror.set(k, v); put(k, v, k === K.token); }
    });
  }

  // A device id is needed before the first request so guest drafts have an owner.
  if (!mirror.has(K.guest)) {
    put(K.guest, 'guest_' + Math.random().toString(36).slice(2) + Date.now().toString(36));
  }
  hydrated = true;
}

export const isHydrated = () => hydrated;

export const getLang = () => read(K.lang);
export const setLang = (v: string) => put(K.lang, v);

export const getToken = () => read(K.token);
export const setToken = (v: string | null) => put(K.token, v, true);

export const getDraft = () => read(K.draft);
export const setDraft = (v: string | null) => put(K.draft, v);

export const isOnboarded = () => read(K.onboarded) === '1';
export const setOnboarded = () => put(K.onboarded, '1');

export const insightsHidden = () => read(K.hideInsights) === '1';
export const setInsightsHidden = (v: boolean) => put(K.hideInsights, v ? '1' : null);

/** Last known profile, so Home can greet the artisan before the network answers. */
export function getArtisan<T>(): T | null {
  const raw = read(K.artisan);
  if (!raw) return null;
  try { return JSON.parse(raw) as T; } catch { return null; }
}
export const setArtisan = (a: unknown | null) =>
  put(K.artisan, a ? JSON.stringify(a) : null);

/** Stable per-device id. Not an identity - it only lets a guest draft be claimed. */
export const guestToken = () => read(K.guest) ?? '';

/**
 * Sign out. The device id survives deliberately: it is not tied to the person, and
 * keeping it means drafts started before signing in are still reachable afterwards.
 */
export function signOutLocal() {
  setToken(null);
  setArtisan(null);
  setDraft(null);
}
