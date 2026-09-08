/**
 * Where the backend lives.
 *
 * This file exists because of a real crash: on Android the base URL resolved to an
 * empty string, so every call became fetch('/v1/analyze') with no host and Android
 * threw `MalformedURLException: no protocol`. Web worked because a relative URL
 * resolves against the page origin; a native app has no origin to resolve against.
 *
 * Resolution order:
 *   1. A URL the artisan typed in Settings (persisted on the device).
 *   2. EXPO_PUBLIC_API_URL baked in at build time.
 *   3. On a dev build, the LAN address Metro is already serving from - so a phone
 *      on the same Wi-Fi finds the laptop automatically, with nothing to type.
 *   4. Web: localhost.
 *
 * If none of these produce a host, `apiBase()` returns '' and callers must treat the
 * backend as offline rather than firing a malformed request.
 */
import { Platform } from 'react-native';
import Constants from 'expo-constants';

const KEY = 'si.apiBase';
let override: string | null = null;

function readStored(): string | null {
  try {
    if (Platform.OS === 'web' && typeof localStorage !== 'undefined') {
      return localStorage.getItem(KEY);
    }
  } catch { /* blocked storage */ }
  return override;
}

export function setApiBase(url: string | null) {
  const clean = url ? url.trim().replace(/\/+$/, '') : null;
  override = clean;
  try {
    if (Platform.OS === 'web' && typeof localStorage !== 'undefined') {
      if (clean) localStorage.setItem(KEY, clean);
      else localStorage.removeItem(KEY);
    }
  } catch { /* memory only */ }
}

/** The host Metro is served from, e.g. "192.168.1.5:8081" on a dev build. */
function metroHost(): string | null {
  const c: any = Constants as any;
  const hostUri: string | undefined =
    c?.expoConfig?.hostUri ||
    c?.expoGoConfig?.debuggerHost ||
    c?.manifest2?.extra?.expoClient?.hostUri ||
    c?.manifest?.debuggerHost;
  if (!hostUri) return null;
  const host = String(hostUri).split(':')[0];
  return host && host !== 'localhost' && host !== '127.0.0.1' ? host : null;
}

const API_PORT = process.env.EXPO_PUBLIC_API_PORT || '8000';

export function apiBase(): string {
  const stored = readStored();
  if (stored) return stored;

  const env = process.env.EXPO_PUBLIC_API_URL;
  if (env) return env.replace(/\/+$/, '');

  if (Platform.OS === 'web') return 'http://localhost:8000';

  const host = metroHost();
  if (host) return `http://${host}:${API_PORT}`;

  // A release APK with nothing configured. Better to know we are offline than to
  // emit a request that cannot be built.
  return '';
}

export const hasBackend = () => apiBase().length > 0;

/**
 * Shown in Profile so the artisan can see what the app is actually talking to.
 * Returns the URL, or an empty string when nothing is configured - the caller
 * decides how to phrase "offline" in the artisan's own language.
 */
export function describeBackend(): string {
  return apiBase();
}
