/**
 * The app's data layer: local-first reads, queued writes, one place that knows
 * whether we are online.
 *
 * How a screen sees it:
 *
 *   1. On mount it gets whatever was cached from last time, immediately. No spinner
 *      on a screen we already have an answer for.
 *   2. A refresh is fired in the background. When it lands the screen re-renders with
 *      server data and `stale` clears.
 *   3. If the refresh fails, the cached rows stay on screen and `offline` goes true,
 *      so the UI can say "showing what we had at 4:10pm" instead of going blank.
 *
 * Writes go through `edit()`, which is optimistic: the local copy changes at once and
 * the change is queued. Online, the queue drains within a moment. Offline, it waits,
 * and drains when connectivity returns. The artisan is never blocked from working and
 * never loses an edit to a dropped connection.
 */
import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import { AppState } from 'react-native';
import * as Network from 'expo-network';

import * as api from './api';
import * as cache from './cache';
import * as session from './session';
import * as sync from './sync';
import { hasBackend } from './config';

type Ctx = {
  ready: boolean;                     // session hydrated; safe to decide onboarding
  online: boolean;
  offline: boolean;
  pendingWrites: number;
  conflicts: sync.Conflict[];

  artisan: api.Artisan | null;
  readiness: api.Readiness[];
  /** Uploads this server is still working through, and finished ones not yet seen. */
  jobs: api.Job[];
  jobsWorking: number;
  jobsReady: number;
  summary: api.Summary | null;
  cards: api.Card[];
  orders: api.Order[];
  enquiries: api.Enquiry[];
  insights: api.Insights | null;

  loading: boolean;
  lastSync: number | null;

  refresh: (opts?: { silent?: boolean }) => Promise<void>;
  refreshJobs: () => Promise<void>;
  dismissJob: (id: string) => Promise<void>;
  refreshOrders: () => Promise<void>;
  editListing: (id: string, fields: Record<string, any>,
                baseUpdatedAt?: string | null) => Promise<void>;
  setArtisan: (a: api.Artisan | null) => void;
  signOut: () => Promise<void>;
  dismissConflict: (opId: string, field: string) => Promise<void>;
};

const StoreCtx = createContext<Ctx>(null as any);
export const useStore = () => useContext(StoreCtx);

async function isOnline(): Promise<boolean> {
  if (!hasBackend()) return false;
  try {
    const st = await Network.getNetworkStateAsync();
    // `isInternetReachable` is undefined on some Android builds; a connected
    // interface is the best signal we have there, and a failed fetch corrects us.
    return !!st.isConnected && st.isInternetReachable !== false;
  } catch {
    return true;                       // assume reachable and let the request decide
  }
}

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [online, setOnline] = useState(true);
  const [offline, setOffline] = useState(false);
  const [pendingWrites, setPending] = useState(0);
  const [conflicts, setConflicts] = useState<sync.Conflict[]>([]);

  const [artisan, setArtisanState] = useState<api.Artisan | null>(null);
  const [readiness, setReadiness] = useState<api.Readiness[]>([]);
  const [summary, setSummary] = useState<api.Summary | null>(null);
  const [cards, setCards] = useState<api.Card[]>([]);
  const [orders, setOrders] = useState<api.Order[]>([]);
  const [enquiries, setEnquiries] = useState<api.Enquiry[]>([]);
  const [insights, setInsights] = useState<api.Insights | null>(null);
  const [jobs, setJobs] = useState<api.Job[]>([]);
  const [jobsWorking, setJobsWorking] = useState(0);
  const [jobsReady, setJobsReady] = useState(0);
  const [loading, setLoading] = useState(false);
  const [lastSync, setLastSync] = useState<number | null>(null);

  const inflight = useRef(false);

  /* ------------------------------------------------------------ boot */

  useEffect(() => {
    (async () => {
      await session.hydrate();
      await sync.load();
      setPending(sync.pending());
      setConflicts(await sync.conflicts());

      // Paint from cache before anything touches the network. This is what makes the
      // app usable on a train, and what makes a cold start feel instant.
      const cached = session.getArtisan<api.Artisan>();
      if (cached) setArtisanState(cached);
      const [cs, co, csum, cin, cen] = await Promise.all([
        cache.read<api.Card[]>(cache.K.listings),
        cache.read<api.Order[]>(cache.K.orders),
        cache.read<api.Summary>(cache.K.bootstrap),
        cache.read<api.Insights>(cache.K.insights),
        cache.read<api.Enquiry[]>('enquiries'),
      ]);
      if (cs) { setCards(cs.data); setLastSync(cs.fetchedAt); }
      if (co) setOrders(co.data);
      if (csum) setSummary(csum.data);
      if (cin) setInsights(cin.data);
      if (cen) setEnquiries(cen.data);

      setReady(true);
    })();
  }, []);

  useEffect(() => sync.subscribe(() => setPending(sync.pending())), []);

  /* -------------------------------------------------------- the queue */

  const send: sync.Sender = useCallback(async (op) => {
    try {
      if (op.kind === 'listing.patch') {
        const res = await api.updateListing(op.target, {
          ...op.fields, baseUpdatedAt: op.baseUpdatedAt,
        } as any);
        const cs = (res as any).conflicts as
          { field: string; mine: any; theirs: any }[] | undefined;
        if (cs?.length) {
          return { ok: false, conflicts: cs.map((c) => ({ op, ...c })) };
        }
        return { ok: true };
      }
      if (op.kind === 'profile.patch') { await api.updateProfile(op.fields); return { ok: true }; }
      if (op.kind === 'address.put') { await api.saveAddress(op.fields as any); return { ok: true }; }
      if (op.kind === 'order.patch') {
        await api.updateOrder(op.target, op.fields);
        return { ok: true };
      }
      return { ok: false, error: `unknown op ${op.kind}` };
    } catch (e: any) {
      // Offline, or the server is unreachable: keep the op and try again later.
      if (e instanceof api.OfflineError) return { ok: false, retry: true };
      if (e?.name === 'AbortError' || e?.message === 'Network request failed') {
        return { ok: false, retry: true };
      }
      // A 5xx is worth retrying; a 4xx means the server will never accept this.
      if (e instanceof api.ApiError && e.status >= 500) return { ok: false, retry: true };
      return { ok: false, error: String(e?.message || e) };
    }
  }, []);

  const drain = useCallback(async () => {
    if (!sync.pending()) return;
    const r = await sync.flush(send);
    if (r.sent) setConflicts(await sync.conflicts());
  }, [send]);

  /* ------------------------------------------------------------ fetch */

  const refresh = useCallback(async (opts?: { silent?: boolean }) => {
    if (inflight.current) return;
    inflight.current = true;
    if (!opts?.silent) setLoading(true);
    try {
      const up = await isOnline();
      setOnline(up);
      if (!up) { setOffline(true); return; }

      // Any queued edit goes out before we read, or the read would immediately
      // overwrite the artisan's own unsent change with the older server copy.
      await drain();

      const [b, sm, ls] = await Promise.allSettled([
        api.bootstrap(), api.summary(), api.listListings(),
      ]);

      if (b.status === 'fulfilled') {
        setArtisanState(b.value.artisan);
        session.setArtisan(b.value.artisan);
        setReadiness(b.value.marketplaceReadiness || []);
        // Language chosen on another device, if we do not have one locally yet.
        const lang = b.value.artisan?.language;
        if (lang && !session.getLang()) session.setLang(lang);
      }
      if (sm.status === 'fulfilled') {
        setSummary(sm.value);
        await cache.write(cache.K.bootstrap, sm.value);
      }
      if (ls.status === 'fulfilled') {
        setCards(ls.value.cards || []);
        await cache.write(cache.K.listings, ls.value.cards || []);
        setLastSync(Date.now());
      }
      const anyOk = [b, sm, ls].some((r) => r.status === 'fulfilled');
      setOffline(!anyOk);
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, [drain]);

  /**
   * Poll the server-side uploads.
   *
   * Kept separate from the main refresh and deliberately small - it runs on a timer
   * while something is in flight, sometimes on a connection billed by the megabyte,
   * so it fetches statuses without results and pulls a result only when one is done.
   */
  const refreshJobs = useCallback(async () => {
    try {
      const r = await api.listJobs();
      setJobs(r.jobs || []);
      setJobsWorking(r.working || 0);
      setJobsReady(r.ready || 0);
      // A finished job means there is a new draft; bring the product list in line so
      // it appears without the artisan having to pull to refresh.
      if ((r.ready || 0) > 0) {
        const ls = await api.listListings().catch(() => null);
        if (ls) {
          setCards(ls.cards || []);
          await cache.write(cache.K.listings, ls.cards || []);
        }
      }
    } catch { /* offline: the card keeps showing the last known state */ }
  }, []);

  const dismissJob = useCallback(async (id: string) => {
    try { await api.markJobSeen(id); } catch { /* it will be marked on the next poll */ }
    setJobs((js) => js.map((j) => (j.id === id ? { ...j, seen: true } : j)));
    setJobsReady((n) => Math.max(0, n - 1));
  }, []);

  /**
   * While work is in flight, check every 15 seconds. Nothing in flight, no polling -
   * a timer that runs forever on a metered connection is somebody's money.
   */
  useEffect(() => {
    if (!ready) return;
    if (!jobsWorking) return;
    const id = setInterval(() => { refreshJobs(); }, 15000);
    return () => clearInterval(id);
  }, [ready, jobsWorking, refreshJobs]);

  const refreshOrders = useCallback(async () => {
    try {
      const [o, e, i] = await Promise.allSettled([
        api.listOrders(), api.listEnquiries(), api.insights(),
      ]);
      if (o.status === 'fulfilled') {
        setOrders(o.value.orders || []);
        await cache.write(cache.K.orders, o.value.orders || []);
      }
      if (e.status === 'fulfilled') {
        setEnquiries(e.value.enquiries || []);
        await cache.write('enquiries', e.value.enquiries || []);
      }
      if (i.status === 'fulfilled') {
        setInsights(i.value);
        await cache.write(cache.K.insights, i.value);
      }
    } catch { /* cached rows stay on screen */ }
  }, []);

  useEffect(() => {
    if (ready) { refresh(); refreshOrders(); refreshJobs(); }
  }, [ready]);

  /* Coming back to the foreground is the usual moment connectivity returns. */
  useEffect(() => {
    const sub = AppState.addEventListener('change', (st) => {
      if (st === 'active' && ready) { refresh({ silent: true }); refreshJobs(); }
    });
    return () => sub.remove();
  }, [ready, refresh]);

  /* A light retry while writes are stuck, so a returning signal is noticed. */
  useEffect(() => {
    if (!pendingWrites) return;
    const id = setInterval(() => { drain(); }, 20000);
    return () => clearInterval(id);
  }, [pendingWrites, drain]);

  /* ------------------------------------------------------------ writes */

  const editListing = useCallback(async (
    id: string, fields: Record<string, any>, baseUpdatedAt?: string | null,
  ) => {
    // Optimistic: the card changes now, so the artisan sees her own edit whether or
    // not there is a signal.
    setCards((cs) => cs.map((c) => (c.id === id ? { ...c, ...cardPatch(fields) } : c)));
    await sync.enqueue({
      kind: 'listing.patch', target: id, fields,
      baseUpdatedAt: baseUpdatedAt ?? null,
    });
    setPending(sync.pending());
    await drain();
  }, [drain]);

  const setArtisan = useCallback((a: api.Artisan | null) => {
    setArtisanState(a);
    session.setArtisan(a);
  }, []);

  const signOut = useCallback(async () => {
    try { await api.logout(); } catch { /* the local session goes either way */ }
    session.signOutLocal();
    await sync.reset();
    await cache.clear();
    setArtisanState(null);
    setCards([]); setOrders([]); setEnquiries([]); setSummary(null);
    setJobs([]); setJobsWorking(0); setJobsReady(0);
    setConflicts([]);
  }, []);

  const dismissConflict = useCallback(async (opId: string, field: string) => {
    await sync.clearConflict(opId, field);
    setConflicts(await sync.conflicts());
  }, []);

  const value = useMemo<Ctx>(() => ({
    ready, online, offline, pendingWrites, conflicts,
    artisan, readiness, summary, cards, orders, enquiries, insights,
    jobs, jobsWorking, jobsReady,
    loading, lastSync,
    refresh, refreshOrders, refreshJobs, dismissJob,
    editListing, setArtisan, signOut, dismissConflict,
  }), [ready, online, offline, pendingWrites, conflicts, artisan, readiness, summary,
       cards, orders, enquiries, insights, jobs, jobsWorking, jobsReady,
       loading, lastSync, refresh, refreshOrders, refreshJobs, dismissJob,
       editListing, setArtisan, signOut, dismissConflict]);

  return <StoreCtx.Provider value={value}>{children}</StoreCtx.Provider>;
}

/** Only the card fields an edit can touch, so an optimistic update stays truthful. */
function cardPatch(fields: Record<string, any>): Partial<api.Card> {
  const out: Partial<api.Card> = {};
  if (fields.price != null) out.price = fields.price;
  if (fields.quantity != null) out.quantity = fields.quantity;
  if (fields.category != null) out.category = fields.category;
  if (fields.titleHi || fields.titleEn) out.title = fields.titleHi || fields.titleEn;
  out.updatedAt = new Date().toISOString();
  return out;
}
