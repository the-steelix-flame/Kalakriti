/**
 * Where the backend lives.
 *
 * This is build configuration, not a user setting. An artisan should never see a
 * server address, let alone be expected to type one - the earlier version put a
 * "Server address" field in Profile, which is developer plumbing leaking into a
 * product used by people who have never seen a URL.
 *
 * Resolution order:
 *   1. EXPO_PUBLIC_API_URL, baked in at build time. This is how a release APK knows
 *      its backend, and how a teammate points a build at their own laptop.
 *   2. On a development build, the LAN address Metro is already serving from, so a
 *      phone on the same Wi-Fi finds the laptop with nothing to configure.
 *   3. Web: localhost.
 *
 * If none of these yields a host, apiBase() returns '' and every call raises
 * OfflineError rather than firing a request with no protocol - which is what threw
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

let resolved: string | null = null;

export function apiBase(): string {
  if (resolved !== null) return resolved;

  const env = process.env.EXPO_PUBLIC_API_URL;
  if (env) return (resolved = env.replace(/\/+$/, ''));

  if (Platform.OS === 'web') return (resolved = 'http://localhost:8000');

  const host = metroHost();
  if (host) return (resolved = `http://${host}:${API_PORT}`);

  // A release APK built without EXPO_PUBLIC_API_URL. Knowing we are offline is far
  // better than emitting a request that cannot be built.
  return (resolved = '');
}

export const hasBackend = () => apiBase().length > 0;
