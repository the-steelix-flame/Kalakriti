/**
 * Where the backend lives.
 *
 * This is build configuration, not a user setting. An artisan should never see a
 * server address, let alone be expected to type one - the earlier version put a
 * "Server address" field in Profile, which is developer plumbing leaking into a
 * product used by people who have never seen a URL.
 *
 * EXPO_PUBLIC_API_URL may name more than one backend, comma separated, best first:
 *
 *   EXPO_PUBLIC_API_URL="https://kalakriti-api.onrender.com,http://192.168.1.5:8000"
 *
 * The app probes them in order at startup and uses the first that answers. That exists
 * because of a real situation rather than a hypothetical one: the same APK has to work
 * on the venue wifi where the hosted backend is reachable, and in a room where only
 * the laptop on the local network is. Without it, moving between the two means a
 * fourteen-minute Gradle rebuild.
 *
 * Resolution order:
 *   1. EXPO_PUBLIC_API_URL, one or more, probed in order.
 *   2. On a development build, the LAN address Metro is already serving from, so a
 *      phone on the same Wi-Fi finds the laptop with nothing to configure.
 *   3. Web: localhost.
 *
 * If none produces a host, apiBase() returns '' and every call raises OfflineError
 * rather than firing a request with no protocol - which is what threw
 * `MalformedURLException: no protocol: /v1/analyze` on Android.
 */
import { Platform } from 'react-native';
import Constants from 'expo-constants';

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

/** Every backend this build knows about, best first. */
export function candidates(): string[] {
  const out: string[] = [];
  const env = process.env.EXPO_PUBLIC_API_URL;
  if (env) {
    for (const part of env.split(',')) {
      const clean = part.trim().replace(/\/+$/, '');
      if (clean) out.push(clean);
    }
  }
  if (Platform.OS === 'web') {
    if (!out.length) out.push('http://localhost:8000');
  } else {
    const host = metroHost();
    if (host) out.push(`http://${host}:${API_PORT}`);
  }
  return out;
}

/**
 * The one currently in use. Starts as the first candidate and is corrected by
 * `resolve()` once a probe has answered.
 */
let chosen: string | null = null;

export function apiBase(): string {
  if (chosen !== null) return chosen;
  const list = candidates();
  return list.length ? list[0] : '';
}

export const hasBackend = () => apiBase().length > 0;

/**
 * Probe the candidates and settle on the first that answers.
 *
 * Called once during boot. Each probe is given four seconds: long enough for a cold
 * serverless backend to wake, short enough that falling through two dead candidates
 * does not visibly delay the first screen. A failure here is not fatal - the first
 * candidate stays selected and ordinary requests will report being offline, which is
 * the same outcome as before this function existed.
 */
export async function resolve(): Promise<string> {
  const list = candidates();
  if (list.length <= 1) {
    chosen = list[0] ?? '';
    return chosen;
  }
  for (const base of list) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 4000);
      const r = await fetch(`${base}/health`, { signal: ctrl.signal });
      clearTimeout(timer);
      if (r.ok) {
        chosen = base;
        return base;
      }
    } catch { /* try the next one */ }
  }
  chosen = list[0];
  return chosen;
}

/** Which backend answered, for the deployment check in Profile. Not a setting. */
export const activeBackend = () => chosen;
