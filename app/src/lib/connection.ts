/**
 * How fast is this connection, really?
 *
 * Used to *propose* a mode, never to impose one. An artisan on a good connection who
 * prefers to fill the form herself should not be argued with, and a measurement is a
 * guess about the next minute anyway - the bar moves when she walks to the next room.
 *
 * The measurement is deliberately cheap: a timed request to /health, which is a few
 * hundred bytes. Downloading a test payload to find out that the connection is slow
 * would spend the artisan's data to tell her something she already knows.
 */
import * as Network from 'expo-network';
import { apiBase, hasBackend } from './config';

export type Speed = 'fast' | 'slow' | 'offline' | 'unknown';

export type Reading = {
  speed: Speed;
  latencyMs: number | null;
  cellular: boolean;
  generation: string | null;   // '2g' | '3g' | '4g' | '5g', when Android reports it
};

/** Above this, a five-minute upload request is not going to survive. */
const SLOW_LATENCY_MS = 1200;

export async function measure(): Promise<Reading> {
  const out: Reading = { speed: 'unknown', latencyMs: null, cellular: false,
                         generation: null };
  if (!hasBackend()) return { ...out, speed: 'offline' };

  try {
    const st = await Network.getNetworkStateAsync();
    out.cellular = st.type === Network.NetworkStateType.CELLULAR;
    if (!st.isConnected || st.isInternetReachable === false) {
      return { ...out, speed: 'offline' };
    }
  } catch { /* the timing below is the real test anyway */ }

  const t0 = Date.now();
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    const r = await fetch(`${apiBase()}/health`, { signal: ctrl.signal });
    clearTimeout(timer);
    if (!r.ok) return { ...out, speed: 'offline' };
    await r.text();
  } catch {
    // A timeout here is itself an answer: whatever this connection is, it will not
    // carry a multi-minute upload.
    return { ...out, speed: Date.now() - t0 >= 7500 ? 'slow' : 'offline' };
  }

  const ms = Date.now() - t0;
  return { ...out, latencyMs: ms, speed: ms > SLOW_LATENCY_MS ? 'slow' : 'fast' };
}

/**
 * Which mode to propose. The artisan can always override, and her choice is kept.
 *
 *   offline  manual - there is nothing to upload to, and asking her to wait for a
 *            server she cannot reach would be a lie. She can still type the whole
 *            listing; the AI buttons queue and run when signal returns.
 *   anything else  auto - the AI doing the work is the point of the app.
 *
 * Speed does not choose between auto and manual. It chooses *how* auto runs, which is
 * `useBackgroundJob` below.
 */
export function suggest(r: Reading): 'auto' | 'manual' {
  return r.speed === 'offline' ? 'manual' : 'auto';
}

/**
 * Within auto mode: hand the work to the server and let her close the app, rather
 * than holding a request open while she watches a progress bar.
 *
 * A five-minute HTTP request does not survive a 2G connection, and when it dies the
 * upload dies with it - she pays for the megabytes twice. So anything that is not
 * demonstrably fast goes through the job queue. Cellular is included even when it
 * times well, because a mobile connection that is quick right now is exactly the one
 * that drops halfway through.
 */
export function useBackgroundJob(r: Reading): boolean {
  return r.speed !== 'fast' || r.cellular;
}
