/**
 * Key-value persistence that actually survives the process dying.
 *
 * The bug this replaces: the old module wrote to localStorage on web and to an
 * in-memory Map everywhere else. On a phone that meant language, session token and
 * onboarding flag lived only as long as the process. Swipe the app out of recents
 * and the artisan was a brand new user again - which is exactly what was reported.
 *
 * Two backends, chosen by what the value is rather than by convenience:
 *
 *   secure()  - expo-secure-store, i.e. the Android Keystore / iOS Keychain. Only
 *               the session token goes here. It is a bearer credential; a rooted
 *               device or another app reading AsyncStorage's plain JSON file must
 *               not come away with a usable login.
 *   plain()   - AsyncStorage. Language, onboarding flag, device id, cached rows.
 *               Losing these is an inconvenience, not a breach, and the volume
 *               (cached listings) is far past what SecureStore is built for.
 *
 * Reads are async, so everything the UI needs at first paint is loaded once during
 * boot into a synchronous mirror. `hydrate()` must finish before the first render
 * decides whether to show onboarding, otherwise the app briefly believes it has no
 * user and flashes the language picker at somebody who chose Gujarati weeks ago.
 */
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';

const web = Platform.OS === 'web';

/* ---------------------------------------------------------------- plain KV */

export async function get(key: string): Promise<string | null> {
  try {
    if (web) return typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null;
    return await AsyncStorage.getItem(key);
  } catch { return null; }          // private mode, blocked storage, corrupt file
}

export async function set(key: string, value: string | null): Promise<void> {
  try {
    if (web) {
      if (typeof localStorage === 'undefined') return;
      if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
      return;
    }
    if (value === null) await AsyncStorage.removeItem(key);
    else await AsyncStorage.setItem(key, value);
  } catch { /* a failed write must never crash the app */ }
}

export async function getJSON<T>(key: string, fallback: T): Promise<T> {
  const raw = await get(key);
  if (!raw) return fallback;
  try { return JSON.parse(raw) as T; } catch { return fallback; }
}

export const setJSON = (key: string, value: unknown) => set(key, JSON.stringify(value));

export async function keys(prefix: string): Promise<string[]> {
  try {
    if (web) {
      if (typeof localStorage === 'undefined') return [];
      return Object.keys(localStorage).filter((k) => k.startsWith(prefix));
    }
    return (await AsyncStorage.getAllKeys()).filter((k) => k.startsWith(prefix));
  } catch { return []; }
}

export async function removeMany(ks: string[]): Promise<void> {
  if (!ks.length) return;
  try {
    if (web) { ks.forEach((k) => localStorage.removeItem(k)); return; }
    await AsyncStorage.multiRemove(ks);
  } catch { /* best effort */ }
}

/* ------------------------------------------------------------- secure KV */

/**
 * SecureStore has no web implementation. On web we fall back to localStorage and
 * say so plainly rather than pretending: a browser tab has no keystore, and the
 * honest position is that the web build is a development and demo surface.
 */
export async function getSecure(key: string): Promise<string | null> {
  try {
    if (web) return typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null;
    return await SecureStore.getItemAsync(key);
  } catch { return null; }
}

export async function setSecure(key: string, value: string | null): Promise<void> {
  try {
    if (web) {
      if (typeof localStorage === 'undefined') return;
      if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
      return;
    }
    if (value === null) await SecureStore.deleteItemAsync(key);
    else await SecureStore.setItemAsync(key, value, {
      keychainAccessible: SecureStore.WHEN_UNLOCKED,
    });
  } catch { /* keystore unavailable: the user simply has to log in again */ }
}

export const secureAvailable = () => !web;
