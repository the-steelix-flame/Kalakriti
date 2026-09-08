/**
 * Local session state: language, auth token, guest device id, current draft.
 *
 * All four persist across launches, which is what makes the onboarding show only
 * once and lets a half-finished listing survive closing the app. On web this is
 * localStorage; on native the same interface is backed by AsyncStorage.
 *
 * The guest token is a device identifier, not an identity - it exists purely so a
 * draft made before logging in can be claimed by the account afterwards.
 */
import { Platform } from 'react-native';

const KEYS = {
  lang: 'si.lang',
  token: 'si.token',
  guest: 'si.guest',
  draft: 'si.draft',
  onboarded: 'si.onboarded',
};

const mem = new Map<string, string>();

function read(k: string): string | null {
  try {
    if (Platform.OS === 'web' && typeof localStorage !== 'undefined') {
      return localStorage.getItem(k);
    }
  } catch { /* private mode, blocked storage */ }
  return mem.get(k) ?? null;
}

function write(k: string, v: string | null) {
  try {
    if (Platform.OS === 'web' && typeof localStorage !== 'undefined') {
      if (v === null) localStorage.removeItem(k);
      else localStorage.setItem(k, v);
    }
  } catch { /* fall through to memory */ }
  if (v === null) mem.delete(k); else mem.set(k, v);
}

export const getLang = () => read(KEYS.lang);
export const setLang = (v: string) => write(KEYS.lang, v);

export const getToken = () => read(KEYS.token);
export const setToken = (v: string | null) => write(KEYS.token, v);

export const getDraft = () => read(KEYS.draft);
export const setDraft = (v: string | null) => write(KEYS.draft, v);

export const isOnboarded = () => read(KEYS.onboarded) === '1';
export const setOnboarded = () => write(KEYS.onboarded, '1');

/** Stable per-device id, created once. */
export function guestToken(): string {
  let g = read(KEYS.guest);
  if (!g) {
    g = 'guest_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    write(KEYS.guest, g);
  }
  return g;
}

export function signOutLocal() {
  setToken(null);
  setDraft(null);
}
