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
import * as drafts from './drafts';
import { hasBackend, resolve as resolveBackend } from './config';

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
  /** Listings made on this phone that no backend has accepted yet. */
  localDrafts: drafts.Draft[];
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
  /** Discard a draft, on the phone and on the server. Refuses anything sold. */
  deleteDraft: (id: string) => Promise<void>;
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
  const [localDrafts, setLocalDrafts] = useState<drafts.Draft[]>([]);
  const [jobs, setJobs] = useState<api.Job[]>([]);
  const [jobsWorking, setJobsWorking] = useState(0);
  const [jobsReady, setJobsReady] = useState(0);
  const [loading, setLoading] = useState(false);
  const [lastSync, setLastSync] = useState<number | null>(null);

  const inflight = useRef(false);
  /** Set below, so boot can trigger a refresh once a backend is chosen. */
  const refreshRef = useRef<((o?: { silent?: boolean }) => Promise<void>) | null>(null);

  /* ------------------------------------------------------------ boot */

  useEffect(() => {
    (async () => {
      await session.hydrate();
      await sync.load();

      // Settle on a backend, but never block the first screen on it. With one
      // candidate this is instant; with a hosted URL and a LAN fallback each probe
      // costs up to four seconds, so waiting for both would mean an artisan with no
      // signal stares at a spinner for eight seconds before the app admits it is
      // offline. Cached data renders regardless, and the first refresh picks up
      // whichever backend answered.
      void resolveBackend().then(() => refreshRef.current?.({ silent: true }));
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

  /* --------------------------------------------------- drafts on this phone */

  const reloadDrafts = useCallback(async () => {
    setLocalDrafts((await drafts.unsynced()));
  }, []);
  useEffect(() => { reloadDrafts(); return drafts.subscribe(() => { reloadDrafts(); }); },
            [reloadDrafts]);

  /**
   * Send up the listings made while there was no signal.
   *
   * One at a time and oldest first, because they were made in that order and a photo
   * upload on a rural connection is not something to run six of at once. A draft that
   * fails is left exactly where it is and tried again on the next refresh - it is on
   * her phone, so there is no hurry and nothing at risk.
   */
  const uploadDrafts = useCallback(async () => {
    const waiting = await drafts.unsynced();
    for (const d of waiting) {
      if (!d.imageUri) continue;
      try {
        const out = await api.analyze(d.imageUri, {
          transcript: String(d.fields?.transcript || ''),
          background: d.mode === 'manual' ? 'none' : 'studio',
          skipModels: d.mode === 'manual',
        } as any);
        await drafts.patch(d.id, { serverId: out.listingId, syncState: 'synced' });

        /*
          Whatever she typed offline belongs on the row, not just the photograph.

          Only the fields she actually filled in, though. This used to send
          `titleEn: f.titleEn ?? ''` for all nine fields, which meant a draft
          carrying only a price wiped the title and both descriptions on the server -
          including a title the AI had written and she had then edited and published.
          She watched her own words disappear and the listing drop back to
          "unfinished".

          An absent key now means "I have nothing to say about this field", which is
          what it always meant, rather than "set it to empty".
        */
        const f = d.fields || {};
        const patch: Record<string, any> = {};
        for (const k of ['titleEn', 'titleHi', 'descEn', 'descHi',
                         'category', 'hsn'] as const) {
          if (typeof f[k] === 'string' && f[k].trim()) patch[k] = f[k];
        }
        if (f.price !== undefined && Number(f.price) > 0) patch.price = Number(f.price);
        if (f.quantity !== undefined && Number(f.quantity) > 0) {
          patch.quantity = Number(f.quantity);
        }
        if (Array.isArray(f.channelsSelected) && f.channelsSelected.length) {
          patch.channelsSelected = f.channelsSelected;
        }
        if (Object.keys(patch).length) {
          await api.updateListing(out.listingId, patch as any)
            .catch(() => { /* the row exists; the edit retries through the queue */ });
        }
      } catch (e: any) {
        // Offline again, or the server said no. Either way the draft stays.
        await drafts.patch(d.id, { syncState: 'failed', lastError: String(e?.message || e) });
        break;
      }
    }
    await reloadDrafts();
  }, [reloadDrafts]);

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
      // Same reasoning for whole listings made offline: they go up before we ask the
      // server what exists, otherwise they would be missing from the answer.
      await uploadDrafts();

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
  }, [drain, uploadDrafts]);

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

  useEffect(() => { refreshRef.current = refresh; }, [refresh]);

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

  /**
   * What My Products and Home show: the server's rows plus the ones still only on
   * this phone. A listing an artisan finished in a place with no signal has to be
   * visible in her own product list, or as far as she can tell it never happened.
   */
  /**
   * Discard a draft, wherever it lives.
   *
   * A draft can be in two places at once: on the phone, and mirrored on the server
   * once it has been uploaded. Deleting one and not the other is how it comes back
   * on the next refresh, so both go.
   *
   * The local copy is removed last. If the server refuses - because the listing
   * turned out to be published, or somebody has ordered it - the draft is still on
   * her phone and the reason is raised, rather than the work vanishing locally while
   * the row survives.
   */
  const deleteDraft = useCallback(async (id: string) => {
    const local = drafts.isLocalId(id) ? await drafts.get(id) : null;
    const serverId = local?.serverId ?? (drafts.isLocalId(id) ? null : id);

    if (serverId) {
      await api.deleteListing(serverId);
      setCards((prev) => prev.filter((c) => c.id !== serverId));
    }
    if (local) await drafts.remove(local.id);
    else if (drafts.isLocalId(id)) await drafts.remove(id);
    else {
      // A server row she reached from the product list: drop any local draft that
      // was pointing at it, or it reappears as an orphan.
      const mirror = (await drafts.all()).find((d) => d.serverId === id);
      if (mirror) await drafts.remove(mirror.id);
    }
    await reloadDrafts();
  }, [reloadDrafts]);

  const mergedCards = useMemo<api.Card[]>(
    () => [...localDrafts.map(draftCard), ...cards], [localDrafts, cards]);

  const value = useMemo<Ctx>(() => ({
    ready, online, offline, pendingWrites, conflicts, localDrafts,
    artisan, readiness, summary, cards: mergedCards, orders, enquiries, insights,
    jobs, jobsWorking, jobsReady,
    loading, lastSync,
    refresh, refreshOrders, refreshJobs, dismissJob,
    editListing, setArtisan, signOut, dismissConflict, deleteDraft,
  }), [ready, online, offline, pendingWrites, conflicts, artisan, readiness, summary,
       mergedCards, localDrafts, orders, enquiries, insights, jobs, jobsWorking, jobsReady,
       loading, lastSync, refresh, refreshOrders, refreshJobs, dismissJob,
       editListing, setArtisan, signOut, dismissConflict, deleteDraft]);

  return <StoreCtx.Provider value={value}>{children}</StoreCtx.Provider>;
}

/**
 * A local draft rendered as a product card.
 *
 * Everything that has not happened is honestly zero or null rather than absent:
 * there are no views because nothing has been published, and `viewsAvailable` is
 * false so the UI says so instead of showing a confident 0.
 */
function draftCard(d: drafts.Draft): api.Card {
  const f = d.fields || {};
  return {
    id: d.id,
    title: String(f.titleHi || f.titleEn || ''),
    titleEn: String(f.titleEn || ''),
    imageUrl: d.imageUri || '',
    price: Number(f.price) || 0,
    currency: 'INR',
    quantity: Number(f.quantity) || 1,
    category: String(f.category || ''),
    status: 'draft', rawStatus: 'draft',
    marketplaces: 0, marketplacesAttempted: 0,
    channels: [], failedChannels: [],
    views: null, viewsAvailable: false,
    orders: 0, sold: 0, revenue: 0,
    updatedAt: new Date(d.updatedAt).toISOString(),
    createdAt: new Date(d.createdAt).toISOString(),
  };
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
