import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Image, Pressable, ScrollView, Linking } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import * as Speech from 'expo-speech';
import QRCode from 'react-native-qrcode-svg';
import {
  Card, Btn, Pill, Row, Skeleton, Divider, Field, Section, StatusChip, money,
} from '../ui';
import { C, GRAD, S, T, R, shadow } from '../theme';
import {
  Camera, Gallery, Mic, Sparkle, Shield, Check, Arrow, Tag, Rupee, Globe, Warning,
  Speaker, Home as HomeIcon, Users,
} from '../icons';
import * as api from '../lib/api';
import * as session from '../lib/session';
import AuthSheet from './AuthSheet';
import GuidedCamera from './GuidedCamera';
import { ShotPlan } from './shotPlan';
import { CATEGORIES, CategoryKey } from '../vision/guidance';
import { useI18n } from '../i18n';
import { useStore } from '../lib/store';
import * as connection from '../lib/connection';
import { AiButton, Spark } from '../ui/AiButton';
import ModeSheet from './ModeSheet';
import { listen, isSupported, requestPermission, LANGS, Listener }
  from '../lib/speech';
import * as drafts from '../lib/drafts';
import * as cache from '../lib/cache';

/**
 * The whole listing flow on ONE page.
 *
 * Every section is rendered from the moment there is a photograph, and the timeline
 * at the top is tappable, so going back to change something already done is one tap
 * and never loses anything. Nothing here locks.
 *
 * The listing is created on the phone, not on the server. `draftId` is a local id
 * that exists the instant a photograph is taken, with no network involved; the
 * photograph itself is copied into the app's own storage. `listingId` is the
 * backend's id and stays null until a backend has actually accepted it. Nothing in
 * this screen waits on that. The previous version could only make a listing by
 * uploading it first, which meant that on a phone with no signal - the phone this
 * app exists for - taking a photograph produced a red error and no way forward.
 *
 * Every AI-produced value lands in an editable Field and stays editable until the
 * artisan presses Publish. Nothing locks.
 */

const STEPS = [
  'create.step.photo', 'create.step.detect', 'create.step.info',
  'create.step.price', 'create.step.clusters', 'create.step.live',
];


/**
 * A form field with an "ask the AI for this one" button in its label row.
 *
 * The button sits beside the label rather than inside the input, so it never covers
 * what she is typing and is reachable with a thumb on a small screen.
 */
function FieldWithAi({
  label, value, onChange, listingId, field, multiline, confidence, placeholder, numeric,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  listingId: string | null;
  field: api.AssistField;
  multiline?: boolean;
  confidence?: number;
  placeholder?: string;
  numeric?: boolean;
}) {
  return (
    <View style={{ gap: 4 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
        <Text style={[T.label, { flex: 1 }]}>{label}</Text>
        <AiButton listingId={listingId} field={field} current={value}
                  onAccept={onChange} />
      </View>
      <Field label="" value={value} onChange={onChange} multiline={multiline}
             confidence={confidence} placeholder={placeholder} numeric={numeric} />
    </View>
  );
}

export default function Create({
  onHome, resumeId, onClusters,
}: { onHome: () => void; resumeId?: string | null;
     /** Opens the cluster browser. Optional so this screen still renders for a caller
      *  that has nowhere to send her - the picker then just shows the storefront. */
     onClusters?: () => void }) {
  // ── form state (the single source of truth while editing) ────────────────
  const [rawUri, setRawUri] = useState<string | null>(null);
  /** The draft on this phone. Exists from the first photograph, offline or not. */
  const [draftId, setDraftId] = useState<string | null>(null);
  /*
    The same id, held where it can be read back immediately.

    `setDraftId` is a React state update, so within the same tick `draftId` is still
    whatever it was when this closure was created - null, on the pass that has just
    made a draft. `ensureDraft` read that stale null, decided no draft existed, and
    made a second one. Two rows for one photograph, both nagging her to finish.

    Every "do I already have a draft?" question reads the ref. The state is still
    there because rendering needs it; it is just not the answer to that question.
  */
  const draftIdRef = useRef<string | null>(null);
  /** The backend's id, once a backend has seen it. Null is a normal state. */
  const [listingId, setListingId] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<api.AnalyzeOut | null>(null);
  const [busy, setBusy] = useState<string>('');
  const [err, setErr] = useState('');
  /** A one-line confirmation, e.g. after AI fills the blanks. */
  const [notice, setNotice] = useState('');

  const [titleEn, setTitleEn] = useState('');
  const [titleHi, setTitleHi] = useState('');
  const [descEn, setDescEn] = useState('');
  const [descHi, setDescHi] = useState('');
  const [category, setCategory] = useState('');
  const [hsn, setHsn] = useState('');
  const [priceStr, setPriceStr] = useState('');
  const [qtyStr, setQtyStr] = useState('1');
  const [materialStr, setMaterialStr] = useState('1400');
  const [daysStr, setDaysStr] = useState('3');
  const [advice, setAdvice] = useState<api.PriceAdvice | null>(null);

  // The breakdown is real editable state, not a rendering of the server response.
  // Previously it was `advice.breakdown.map(...)` printed as static rows, so editing
  // the price left the explanation showing stale numbers with no way to correct it.
  // `breakdownDirty` is what protects a hand-edited breakdown from being clobbered
  // the next time the AI is asked for a price.
  const [breakdown, setBreakdown] = useState<{ id: string; label: string; amount: string }[]>([]);
  const [breakdownDirty, setBreakdownDirty] = useState(false);
  const [rationale, setRationale] = useState('');
  const [rationaleDirty, setRationaleDirty] = useState(false);

  const [transcript, setTranscript] = useState('');
  const [partial, setPartial] = useState('');
  const [recording, setRecording] = useState(false);
  const { t, locale } = useI18n();
  const store = useStore();
  /** The row version the current edits were made against. */
  const baseVersion = useRef<string | null>(null);
  /** The server id, readable straight after it is set. State would be a frame late. */
  const listingIdRef = useRef<string | null>(null);

  /**
   * How this listing gets made.
   *
   *   auto    the photo goes to the server and the AI fills everything in
   *   manual  the artisan writes it, and asks for AI per field when she wants it
   *
   * Unset means she has never chosen; the sheet opens on the first photograph and
   * proposes one based on the connection. Her choice is remembered after that.
   */
  const [mode, setModeState] = useState<session.Mode>(session.getMode() ?? 'auto');
  const [modeAsk, setModeAsk] = useState(false);
  const [handedOff, setHandedOff] = useState(false);   // sent to the server as a job
  const [aiBusy, setAiBusy] = useState('');
  /** Held while the mode sheet is open, so the answer resumes the capture. */
  const pendingPhoto = useRef<string | null>(null);
  const insets = useSafeAreaInsets();
  const [lang, setLang] = useState(locale);
  useEffect(() => { setLang(locale); }, [locale]);
  const recognizer = useRef<Listener | null>(null);

  const [chans, setChans] = useState<api.Channel[]>([]);

  // Which cluster sells this product. `null` clusters means "not loaded yet", which
  // is a different thing from an empty list - an artisan who belongs to no cluster
  // needs to be told that, not shown a spinner for ever.
  const [clusters, setClusters] = useState<api.Cluster[] | null>(null);
  const [clusterId, setClusterId] = useState<string>('');
  const [savingCluster, setSavingCluster] = useState(false);
  const [clusterErr, setClusterErr] = useState('');
  const [picked, setPicked] = useState<string[]>(['storefront']);
  const [pubs, setPubs] = useState<api.Publication[]>([]);
  const [live, setLive] = useState<api.Listing | null>(null);
  const [orders, setOrders] = useState<api.Order[]>([]);
  const [passport, setPassport] = useState<any>(null);

  const [camOpen, setCamOpen] = useState(false);
  const [viewStep, setViewStep] = useState(0);
  /** Is the listing's picture the AI's version, or hers untouched? */
  const [aiImage, setAiImage] = useState(true);
  const [craft, setCraft] = useState<CategoryKey>('general');
  const planRef = useRef(new ShotPlan('general'));
  const [shots, setShots] = useState<{ key: string; uri: string }[]>([]);
  const [shotHint, setShotHint] = useState<string | null>(null);

  const [authOpen, setAuthOpen] = useState(false);
  const [authReason, setAuthReason] = useState('');
  const [artisan, setArtisan] = useState<api.Artisan | null>(null);
  const [readiness, setReadiness] = useState<api.Readiness[]>([]);
  const [blocked, setBlocked] = useState<Record<string, string[]>>({});

  const scroller = useRef<ScrollView>(null);
  /** Where each numbered section starts, so a tap on the timeline can go there. */
  const sectionY = useRef<Record<number, number>>({});

  useEffect(() => { if (draftId) drafts.patch(draftId, { craft, mode }); },
            [craft, mode, draftId]);

  /**
   * Go to a step.
   *
   * The timeline used to be decoration: four coloured bars that told her where she
   * was and gave her no way to get back to anything. Once a step was done it was
   * gone. Now every step is a target, everything below stays on screen and editable,
   * and nothing is recalculated or discarded by moving between them.
   */
  const goToStep = useCallback((i: number) => {
    const y = sectionY.current[i];
    scroller.current?.scrollTo({ y: Math.max(0, (y ?? 0) - 8), animated: true });
  }, []);

  const markSection = (i: number) => (e: any) => {
    sectionY.current[i] = e.nativeEvent.layout.y;
  };

  /**
   * Which section is on screen right now.
   *
   * Kept apart from `step`, which is about what is *finished*. The timeline used to
   * show only completion, so scrolling through the page moved nothing and the five
   * Continue buttons were the only thing that advanced it - a button per section on
   * one scrolling page, which is two ways of doing the same thing and one too many.
   *
   * Now the page just scrolls. The bar follows the scroll, and a finished step stays
   * green whether she is looking at it or not.
   */
  const onScroll = useCallback((e: any) => {
    const y = e.nativeEvent.contentOffset.y + 90;   // a little below the header
    const ys = sectionY.current;
    let at = 0;
    // Keyed by section index rather than a list, because sections mount as they
    // become reachable and the gaps matter - a missing key means "not on screen yet".
    for (let i = 0; i < STEPS.length; i++) {
      const top = ys[i];
      if (typeof top === 'number' && top <= y) at = i;
    }
    setViewStep(at);
  }, []);

  // Where she can sell is a fact about the deployment, not about this minute's
  // connection, so the last real answer is kept and shown when there is no signal.
  // Without this the whole "where to sell" step vanished the moment the phone lost
  // the network, taking the artisan's channel choices with it.
  useEffect(() => {
    let alive = true;
    cache.read<api.Channel[]>(cache.K.channels).then((c) => {
      if (alive && c?.data?.length) setChans((cur) => (cur.length ? cur : c.data));
    });
    api.channels()
      .then((r) => {
        if (!alive) return;
        setChans(r.channels);
        cache.write(cache.K.channels, r.channels);
      })
      .catch(() => { /* the cached list is already on screen */ });
    return () => { alive = false; };
  }, []);

  // The clusters this artisan may sell through: the ones she has joined, plus any she
  // owns. Signed out there are none, and the picker then offers only her storefront -
  // which is correct, because joining a cluster requires an account.
  useEffect(() => {
    let alive = true;
    if (!artisan) { setClusters([]); return () => { alive = false; }; }
    api.myClusters()
      .then((r) => {
        if (!alive) return;
        // Owned first: a cluster creator listing her own work almost always means the
        // cluster she runs, and it should not be buried under ones she joined.
        const seen = new Set<string>();
        const all = [...(r.owned || []), ...(r.clusters || [])]
          .filter((c) => (seen.has(c.id) ? false : (seen.add(c.id), true)));
        setClusters(all);
      })
      .catch(() => { if (alive) setClusters([]); });
    return () => { alive = false; };
  }, [artisan]);

  /**
   * Choose the cluster, and save it before anything else can go wrong.
   *
   * Written straight through to the server rather than held until publish, because
   * this is the field that decides who the seller of record is. Leaving it in local
   * state until the end means a crash loses the one answer that determines whose
   * GSTIN the sale happens under.
   */
  async function pickCluster(id: string) {
    const was = clusterId;
    setClusterId(id);
    setClusterErr('');
    const lid = listingIdRef.current ?? listingId;
    if (!lid) return;                 // nothing to attach it to yet; saved on create
    setSavingCluster(true);
    try {
      await api.updateListing(lid, { clusterId: id });
    } catch (e: any) {
      // The server refuses a cluster she is not a member of. Rolling the selection
      // back is the honest response: leaving it highlighted would show her a seller
      // of record that is not actually recorded.
      setClusterId(was);
      setClusterErr(e?.message || 'That cluster did not accept this product.');
    } finally {
      setSavingCluster(false);
    }
  }

  useEffect(() => {
    api.bootstrap()
      .then((b) => { setArtisan(b.artisan); setReadiness(b.marketplaceReadiness); })
      .catch(() => {});
  }, []);

  // Resume a draft left behind by a previous session. Everything the artisan had -
  // images, generated copy, price, chosen channels - comes back from the row.
  useEffect(() => {
    const id = resumeId || session.getDraft();
    if (!id || listingId || draftId) return;

    // A local draft restores entirely from this phone - no request, no waiting, and
    // it works with the radio off. This is the ordinary path.
    if (drafts.isLocalId(id)) {
      drafts.get(id).then((d) => {
        if (!d) { session.setDraft(null); return; }
        setDraftId(d.id);
        setRawUri(d.imageUri);
        if (d.craft) { setCraft(d.craft as CategoryKey); planRef.current = new ShotPlan(d.craft as CategoryKey); }
        if (d.shots?.length) {
          for (const sh of d.shots) planRef.current.add(sh.uri, sh.key);
          setShots([...planRef.current.captured]);
        }
        const f = d.fields || {};
        setTitleEn(f.titleEn ?? ''); setTitleHi(f.titleHi ?? '');
        setDescEn(f.descEn ?? ''); setDescHi(f.descHi ?? '');
        setCategory(f.category ?? ''); setHsn(f.hsn ?? '');
        if (f.price) setPriceStr(String(f.price));
        if (f.quantity) setQtyStr(String(f.quantity));
        if (f.transcript) setTranscript(String(f.transcript));
        if (Array.isArray(f.channelsSelected) && f.channelsSelected.length) {
          setPicked(f.channelsSelected);
        }
        if (typeof f.clusterId === 'string') setClusterId(f.clusterId);
        if (Array.isArray(f.priceBreakdown) && f.priceBreakdown.length) {
          setBreakdown(f.priceBreakdown.map((b: any, i: number) => ({
            id: `bd_local_${i}`, label: String(b.label ?? ''),
            amount: String(b.amount ?? ''),
          })));
          setBreakdownDirty(true);
        }
        if (f.priceRationale) { setRationale(String(f.priceRationale)); setRationaleDirty(true); }
        if (d.serverId) { listingIdRef.current = d.serverId; setListingId(d.serverId); }
        draftIdRef.current = d.id;
      });
      return;
    }

    api.getListing(id).then((l) => {
      setListingId(l.id);
      setTitleEn(l.titleEn); setTitleHi(l.titleHi);
      setDescEn(l.descEn); setDescHi(l.descHi);
      setCategory(l.category); setHsn(l.hsn);
      if (l.price) setPriceStr(String(l.price));
      setQtyStr(String(l.quantity || 1));
      setTranscript(l.transcript || '');
      if (l.channelsSelected?.length) setPicked(l.channelsSelected);
      // Whose GSTIN this sells under, restored with everything else.
      setClusterId(l.clusterId || '');
      const savedBd = (l.attributes as any)?.priceBreakdown;
      if (Array.isArray(savedBd) && savedBd.length) {
        setBreakdown(savedBd.map((b: any, i: number) => ({
          id: `bd_saved_${i}`, label: String(b.label ?? ''),
          amount: String(b.amount ?? ''),
        })));
        setBreakdownDirty(true);          // it is the artisan's, do not overwrite
      }
      const savedRat = (l.attributes as any)?.priceRationale;
      if (savedRat) { setRationale(String(savedRat)); setRationaleDirty(true); }
      if (l.imageUrl) {
        setRawUri(l.imageUrl);
        setAnalysis({
          listingId: l.id, imageUrl: l.imageUrl, rawHash: l.rawHash,
          ops: l.enhanceOps || [], ms: 0,
          ocr: { ok: true, boxes: l.ocr?.boxes || [], text: l.ocr?.text || '' },
          detected: {}, suggestions: l.vision?.suggestions || {},
          confidence: l.vision?.confidence || {}, notes: l.vision?.notes || '',
          ocrUsed: l.vision?.ocrUsed || [], source: 'resumed', listing: l,
        } as api.AnalyzeOut);
      }
      if (l.publications?.length) setPubs(l.publications);
      // Remember which version these edits are being made against, so an edit that
      // sits in the offline queue can be merged rather than blindly replayed.
      baseVersion.current = l.updatedAt ?? null;
      listingIdRef.current = l.id;
      // Give the server row a home on this phone too, so the next time she opens it
      // there is something to show before any request has finished.
      drafts.create({ serverId: l.id, imageUri: l.imageUrl || null,
                      syncState: 'synced' }).then((d) => setDraftId(d.id));
    }).catch(() => { /* keep the id: it may just be a bad moment for the network */ });
  }, [resumeId]);

  // What "resume this" points at is the local draft, because that is the copy that
  // is always there. The server id travels inside it.
  useEffect(() => { if (draftId) session.setDraft(draftId); }, [draftId]);

  // Mirror edits so a refresh never loses work. Debounced, because typing must not
  // fire a request per keystroke.
  //
  // This goes through the write queue rather than calling the API directly. The old
  // version did `api.updateListing(...).catch(() => {})`, which meant that an edit
  // made with no signal was swallowed silently - the artisan would fill in a price on
  // a patchy connection, see nothing wrong, and find it gone. Queued, the edit is
  // held on the device and replayed when the connection returns.
  const saveTimer = useRef<any>(null);
  const persist = useCallback((patch: Partial<api.Listing>) => {
    if (!listingId) return;
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      store.editListing(listingId, patch as Record<string, any>, baseVersion.current);
    }, 700);
  }, [listingId, store]);

  // Everything typed is written to the local draft on a short debounce. This is the
  // save that always succeeds; the server mirror below is the one that may have to
  // wait for a signal.
  const localTimer = useRef<any>(null);
  useEffect(() => {
    if (!draftId) return;
    clearTimeout(localTimer.current);
    localTimer.current = setTimeout(() => {
      drafts.patchFields(draftId, {
        titleEn, titleHi, descEn, descHi, category, hsn, transcript,
        price: parseFloat(priceStr) || 0,
        quantity: parseInt(qtyStr, 10) || 1,
        channelsSelected: picked,
        priceBreakdown: breakdown.map((b) => ({
          label: b.label, amount: parseFloat(b.amount) || 0 })),
        priceRationale: rationale,
      });
    }, 400);
    return () => clearTimeout(localTimer.current);
  }, [titleEn, titleHi, descEn, descHi, category, hsn, priceStr, qtyStr, picked,
      breakdown, rationale, transcript, draftId]);

  useEffect(() => {
    if (!listingId) return;
    persist({
      titleEn, titleHi, descEn, descHi, category, hsn,
      price: parseFloat(priceStr) || 0,
      quantity: parseInt(qtyStr, 10) || 1,
      channelsSelected: picked,
      attributes: {
        ...(analysis?.listing?.attributes || {}),
        priceBreakdown: breakdown.map((b) => ({
          label: b.label, amount: parseFloat(b.amount) || 0 })),
        priceRationale: rationale,
      },
    } as any);
  }, [titleEn, titleHi, descEn, descHi, category, hsn, priceStr, qtyStr, picked,
      breakdown, rationale, listingId, persist]);

  /* ── step 1: photo ───────────────────────────────────────────────────── */

  /** Gallery still uses the picker; the camera now uses our own guided viewfinder. */
  async function pick(mode: 'camera' | 'library') {
    setErr('');
    if (mode === 'camera') { setCamOpen(true); return; }
    try {
      const r = await ImagePicker.launchImageLibraryAsync({ quality: 0.9 });
      if (!r.canceled && r.assets?.[0]) await handlePhoto(r.assets[0].uri);
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  }

  function onShotCaptured(uri: string, key: string) {
    planRef.current.add(uri, key);
    setShots([...planRef.current.captured]);
    const first = planRef.current.captured.length === 1;
    setCamOpen(false);
    // Say why another photo helps, without forcing one.
    setShotHint(planRef.current.nextShotKey());
    if (first) void handlePhoto(uri);
    else if (draftId) void drafts.patch(draftId, { shots: planRef.current.captured });
  }

  /**
   * A network failure is not the artisan's problem and must not look like one.
   *
   * Anything that is plainly the connection - no backend configured, a refused
   * socket, a timeout - becomes a calm line saying the work is safe on the phone.
   * Everything else is a real error and is shown as one. The old code printed
   * `fetch failed: java.net.ConnectException` in a red box, which tells an artisan
   * nothing and tells her, wrongly, that she has lost her work.
   */
  function reportSoft(e: any) {
    const msg = String(e?.message || e);
    const networkish = e instanceof api.OfflineError
      || /network request failed|fetch failed|connectexception|failed to connect|unable to resolve host|timed out|timeout|aborted|enotfound|econnrefused|backend not reachable/i
           .test(msg);
    if (networkish) { setErr(''); setNotice(t('create.savedOnPhone')); }
    else setErr(msg);
  }

  /**
   * What happens to a photograph, which depends on the connection.
   *
   * On a good connection the pipeline runs inline and the artisan watches it. On
   * anything slower the photograph is handed to the server as a job and she is free
   * to leave - a five-minute HTTP request does not survive 2G, and when it dies she
   * pays for the upload twice.
   *
   * In manual mode nothing is sent for analysis at all. The photograph is attached to
   * the draft so the AI buttons have something to look at, and she writes the listing.
   */
  async function handlePhoto(uri: string) {
    setErr('');
    // Step one, always, before anything touches the network: the photograph is
    // copied somewhere permanent and a draft exists on this phone. From here on
    // every section of this screen has something to work with, whatever the
    // connection does.
    const kept = await drafts.keepPhoto(uri);
    setRawUri(kept);
    const id = await ensureDraft(kept);

    if (!session.getMode()) { pendingPhoto.current = kept; setModeAsk(true); return; }
    if (mode === 'manual') { void attachOnly(kept); return; }

    const reading = await connection.measure();
    if (connection.useBackgroundJob(reading)) { void sendAsJob(kept); return; }
    void runAnalyze(kept);
    return id;
  }

  /** Create the local draft if there isn't one, and keep it current. */
  async function ensureDraft(imageUri?: string | null): Promise<string> {
    const existing = draftIdRef.current ?? draftId;
    if (existing) {
      await drafts.patch(existing, {
        craft, mode, ...(imageUri ? { imageUri } : {}),
        shots: planRef.current.captured,
      });
      return existing;
    }
    const d = await drafts.create({
      craft, mode, imageUri: imageUri ?? null, shots: planRef.current.captured,
    });
    // The ref first, and before any await, so a second caller in the same tick sees
    // it. This ordering is the entire fix.
    draftIdRef.current = d.id;
    setDraftId(d.id);
    session.setDraft(d.id);
    return d.id;
  }

  /**
   * Manual mode: keep the photograph, run nothing.
   *
   * The draft is created with the image so /v1/assist has context, but no model is
   * called until she taps an AI button. That is the promise manual mode makes.
   */
  async function attachOnly(uri: string) {
    setBusy(t('create.saving'));
    setErr('');
    try {
      const out = await api.analyze(uri, {
        transcript, lang, background: 'none', listingId: listingIdRef.current ?? listingId ?? undefined,
        skipModels: true,
      } as any);
      setAnalysis(out);
      await adoptServerId(out.listingId);
      // Manual mode is signed too: its operation log says that nothing was run.
      if (out.passport) setPassport(out.passport);
    } catch (e: any) {
      // The draft is already on the phone. Failing to mirror it is a delay, not a
      // loss, and she carries on writing either way.
      reportSoft(e);
    } finally {
      setBusy('');
    }
  }

  /** Remember the backend's id on the local draft, so the two are tied together. */
  async function adoptServerId(id: string) {
    listingIdRef.current = id;
    setListingId(id);
    const local = draftIdRef.current ?? draftId ?? (await ensureDraft(rawUri));
    await drafts.patch(local, { serverId: id, syncState: 'synced' });
  }

  /** Auto mode on a slow connection: upload once, walk away. */
  async function sendAsJob(uri: string) {
    setBusy(t('job.sent'));
    setErr('');
    try {
      const b64 = await api.toBase64(uri);
      const job = await api.createJob({
        imageBase64: b64, transcript, lang, background: 'studio',
        listingId: listingIdRef.current ?? listingId ?? undefined,
      });
      setHandedOff(true);
      store.refreshJobs();
      // She does not have to stay here. Home carries the progress card.
      setTimeout(() => onHome(), 1800);
      return job;
    } catch (e: any) {
      reportSoft(e);
    } finally {
      setBusy('');
    }
  }

  /**
   * Send the photograph up.
   *
   * `plain` means keep her photograph exactly as it is and run no image models. It
   * is the undo for the AI's work on the picture: the same row is rewritten with the
   * original, so what a buyer sees is what she took. Idempotency in /v1/analyze is
   * what makes that safe to call twice - before it, each call made a new listing.
   */
  async function runAnalyze(uri: string, opts?: { plain?: boolean }) {
    const plain = !!opts?.plain;
    setBusy(t(plain ? 'create.restoring' : 'create.analysing'));
    setErr('');
    try {
      const out = await api.analyze(uri, {
        transcript, lang,
        background: plain ? 'none' : 'studio',
        skipModels: plain,
        listingId: listingIdRef.current ?? listingId ?? undefined,
      } as any);
      setAiImage(!plain);
      setAnalysis(out);
      await adoptServerId(out.listingId);
      const d = out.detected || {};
      // Fill only empty fields - never overwrite something already edited.
      setTitleEn((v) => v || String(d.title ?? ''));
      setDescEn((v) => v || String(d.description ?? ''));
      setCategory((v) => v || String(d.category ?? ''));
      setHsn((v) => v || String(d.hsn ?? ''));
      if (typeof d.price === 'number') setPriceStr((v) => v || String(d.price));
      // The passport now comes back with the analysis, signed on the server against
      // this artisan's own row. The app used to mint it in a second call with a
      // hard-coded maker id, and the result was never stored anywhere.
      if (out.passport) setPassport(out.passport);
    } catch (e: any) {
      reportSoft(e);
    } finally {
      setBusy('');
    }
  }

  /**
   * "Carry on with AI from here."
   *
   * Runs the remaining steps and applies only what is still blank. Anything she has
   * typed is left exactly as she typed it - the server is told what she wrote so the
   * suggestions stay consistent with it rather than contradicting her.
   */
  async function continueWithAi() {
    setAiBusy('continue');
    setErr('');
    try {
      // There may be a draft that no backend has seen yet - that is the normal state
      // offline. Hand it over first; if that cannot happen, say so plainly.
      if (!listingIdRef.current && rawUri) await attachOnly(rawUri);
      const id = listingIdRef.current;
      if (!id) {
        setNotice(rawUri ? t('create.aiNeedsNet') : t('ai.needsPhotoFirst'));
        return;
      }
      const r = await api.assistContinue({ listingId: id, from_step: 'catalog' });
      const cat = r.suggestions?.catalog || {};
      setTitleEn((v) => v || String(cat.titleEn ?? ''));
      setTitleHi((v) => v || String(cat.titleHi ?? ''));
      setDescEn((v) => v || String(cat.descEn ?? ''));
      setDescHi((v) => v || String(cat.descHi ?? ''));
      setCategory((v) => v || String(cat.category ?? ''));
      setHsn((v) => v || String(cat.hsn ?? ''));
      const pr = r.suggestions?.price;
      if (pr && typeof pr.suggested === 'number') {
        setPriceStr((v) => v || String(pr.suggested));
        if (Array.isArray(pr.breakdown) && !breakdownDirty) {
          setBreakdown(pr.breakdown.map((b: any, i: number) => ({
            id: `ai${i}`, label: String(b.label), amount: String(b.amount),
          })));
        }
        if (!rationaleDirty && pr.rationale) setRationale(String(pr.rationale));
      }
      setNotice(t('ai.continueDone'));
    } catch (e: any) {
      if (e?.status === 503) setErr(t('ai.notConfigured'));
      else reportSoft(e);
    } finally {
      setAiBusy('');
    }
  }

  /* ── step 3: voice + copy ────────────────────────────────────────────── */

  async function startRec() {
    setErr('');
    setPartial('');
    // Ask before opening the mic, and stop here if refused. Starting a recogniser
    // without permission fails with a code nobody can act on.
    const allowed = await requestPermission();
    if (!allowed) {
      setErr(t('create.micDenied'));
      return;
    }
    setRecording(true);
    recognizer.current = listen({
      lang,
      onPartial: setPartial,
      onFinal: (tx) => { setRecording(false); setTranscript(tx); setPartial(''); },
      // An error clears the recording state and says what happened. Nothing is
      // written to the transcript - a guess here becomes the product description.
      onError: (m) => { setRecording(false); setPartial(''); setErr(String(m)); },
    });
  }
  function stopRec() {
    setRecording(false);
    recognizer.current?.stop();
    recognizer.current = null;
  }

  async function writeCopy() {
    setBusy(t('create.writing'));
    setErr('');
    try {
      const out = await api.catalog({
        transcript, lang, detected: analysis?.detected, ocrText: analysis?.ocr?.text,
        listingId: listingIdRef.current ?? listingId ?? undefined,
      });
      if (out.ok === false) { setErr(out.error || 'copy failed'); return; }
      setTitleEn(out.titleEn ?? titleEn);
      setTitleHi(out.titleHi ?? titleHi);
      setDescEn(out.descEn ?? descEn);
      setDescHi(out.descHi ?? descHi);
      setCategory(out.category ?? category);
      setHsn(out.hsn ?? hsn);
    } catch (e: any) {
      reportSoft(e);
    } finally { setBusy(''); }
  }

  /* ── step 4: price ───────────────────────────────────────────────────── */

  const bdTotal = breakdown.reduce((n, r) => n + (parseFloat(r.amount) || 0), 0);
  const priceNum = parseFloat(priceStr) || 0;
  const bdMismatch = breakdown.length > 0 && Math.abs(bdTotal - priceNum) >= 1;

  function editLine(id: string, patch: Partial<{ label: string; amount: string }>) {
    setBreakdownDirty(true);
    setBreakdown((rows) => rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }
  function addLine() {
    setBreakdownDirty(true);
    setBreakdown((rows) => [...rows,
      { id: `bd_${Date.now()}_${rows.length}`, label: '', amount: '' }]);
  }
  function removeLine(id: string) {
    setBreakdownDirty(true);
    setBreakdown((rows) => rows.filter((r) => r.id !== id));
  }

  /** Reconcile, but only ever on an explicit tap - never silently. */
  function priceFromBreakdown() {
    setPriceStr(String(Math.round(bdTotal)));
  }
  function balanceBreakdown() {
    const diff = Math.round(priceNum - bdTotal);
    if (!diff) return;
    setBreakdownDirty(true);
    setBreakdown((rows) => {
      const i = rows.findIndex((r) => r.label === t('create.profit')
                                   || /profit/i.test(r.label));
      if (i >= 0) {
        const next = [...rows];
        next[i] = { ...next[i],
                    amount: String((parseFloat(next[i].amount) || 0) + diff) };
        return next;
      }
      return [...rows, { id: `bd_${Date.now()}`, label: t('create.profit'),
                         amount: String(diff) }];
    });
  }

  async function runPrice() {
    setBusy(t('create.pricing'));
    setErr('');
    try {
      const a = await api.suggestPrice({
        catalog: { titleEn, descEn, category, hsn },
        detected: analysis?.detected,
        materialCost: parseFloat(materialStr) || 0,
        days: parseFloat(daysStr) || 1,
        listingId: listingIdRef.current ?? listingId ?? undefined,
      });
      setAdvice(a);
      setPriceStr((v) => v || String(a.suggested));
      // Seed the explanation only if the artisan has not written their own.
      if (!breakdownDirty) {
        setBreakdown((a.breakdown || []).map((b, i) => ({
          id: `bd_ai_${i}`, label: b.label, amount: String(Math.round(b.amount)),
        })));
      }
      if (!rationaleDirty) setRationale(a.rationale || '');
    } catch (e: any) {
      reportSoft(e);
    } finally { setBusy(''); }
  }

  /* ── step 5: publish ─────────────────────────────────────────────────── */

  async function doPublish() {
    // Publishing genuinely needs the server: a listing has to exist somewhere a
    // buyer can reach. What must not happen is the button doing nothing at all,
    // which is what `if (!listingId) return` did on every offline tap. Try to hand
    // the draft over first, and if that cannot happen, say why and keep the work.
    if (!listingId) {
      setBusy(t('create.publishing'));
      try {
        if (rawUri) await attachOnly(rawUri);
      } finally { setBusy(''); }
      if (!listingIdRef.current) {
        if (draftId) await drafts.patchFields(draftId, { pendingPublish: picked });
        setErr('');
        setNotice(t('create.publishNeedsNet'));
        return;
      }
    }
    setBusy(t('create.publishing'));
    setErr('');
    setBlocked({});
    const id = listingIdRef.current || listingId!;
    try {
      await api.updateListing(id, {
        titleEn, titleHi, descEn, descHi, category, hsn,
        price: parseFloat(priceStr) || 0, quantity: parseInt(qtyStr, 10) || 1,
        channelsSelected: picked,
      } as any);
      const out = await api.publishListing(id, picked);
      setPubs(out.publications);
      setLive(out.listing);
      const st = await api.listingStatus(id);
      setOrders(st.orders);
      if (draftId) await drafts.patch(draftId, { syncState: 'synced' });
      session.setDraft(null);
      setTimeout(() => goToStep(5), 300);
    } catch (e: any) {
      // These two are not failures so much as "one more step". The listing stays
      // exactly as it is underneath the sheet.
      if (e instanceof api.ApiError && e.status === 401) {
        setAuthReason(t('auth.loginToPublish'));
        setAuthOpen(true);
      } else if (e instanceof api.ApiError && e.status === 428) {
        setBlocked(e.detail?.blocked || {});
        setAuthReason(t('auth.needMore'));
        setAuthOpen(true);
      } else {
        reportSoft(e);
        if (draftId) await drafts.patchFields(draftId, { pendingPublish: picked });
      }
    } finally { setBusy(''); }
  }

  async function refreshStatus() {
    if (!listingId) return;
    try {
      const st = await api.listingStatus(listingId);
      setPubs(st.publications);
      setOrders(st.orders);
      setLive((l) => (l ? { ...l, status: st.status } : l));
    } catch { /* offline */ }
  }
  useEffect(() => {
    if (!live) return;
    const t = setInterval(refreshStatus, 8000);
    return () => clearInterval(t);
  }, [live, listingId]);

  /* ── derived progress ────────────────────────────────────────────────── */

  const hasPhoto = !!rawUri;
  const hasDetect = !!analysis;
  const hasInfo = titleEn.trim().length > 2 || titleHi.trim().length > 2;
  const hasPrice = (parseFloat(priceStr) || 0) > 0;
  const isLive = !!live;
  const step =
    isLive ? 6 : hasPrice && picked.length ? 5 : hasPrice ? 4 : hasInfo ? 3 :
    hasDetect ? 2 : hasPhoto ? 1 : 0;

  /**
   * A section is either finished or open. Never locked.
   *
   * `Section` hides its children when locked, which is how the create screen used to
   * end in a dead end: with no analysis there was no step 2, so there was no step 3,
   * and the artisan was left looking at her photograph with nothing to press.
   */
  const st = (i: number): 'done' | 'active' | 'locked' => (step > i ? 'done' : 'active');

  const conf = analysis?.confidence || {};

  if (camOpen) {
    return (
      <GuidedCamera
        category={craft}
        plan={planRef.current}
        onCapture={onShotCaptured}
        onClose={() => setCamOpen(false)}
      />
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      {/* ── persistent timeline ─────────────────────────────────────────── */}
      <View style={{ paddingTop: insets.top + 8, paddingHorizontal: S.lg,
                     paddingBottom: S.md, backgroundColor: C.bg,
                     borderBottomWidth: 1, borderBottomColor: C.line }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <Pressable onPress={onHome} hitSlop={10} style={{
            width: 42, height: 42, borderRadius: R.pill, alignItems: 'center',
            justifyContent: 'center', backgroundColor: C.bgAlt }}>
            <HomeIcon color={C.ink} size={20} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={[T.title, { fontSize: 21 }]}>{t('create.title')}</Text>
            <Text style={[T.micro, { fontSize: 12.5 }]}>
              {listingId ? t('create.draft', { id: listingId.slice(4, 12) })
                       : t('create.subtitle')}
            </Text>
          </View>
          <Text style={[T.micro, { color: C.primary }]}>
            {Math.min(step, STEPS.length)}/{STEPS.length}
          </Text>
        </View>

        <View style={{ flexDirection: 'row', gap: 5, marginTop: S.lg }}>
          {STEPS.map((stepKey, i) => {
            const done = step > i;
            const active = viewStep === i;
            return (
              <Pressable key={stepKey} onPress={() => goToStep(i)} hitSlop={8}
                         accessibilityRole="button"
                         accessibilityLabel={t(stepKey)}
                         style={{ flex: 1, gap: 5, paddingVertical: 4 }}>
                <View style={{
                  height: 4, borderRadius: 99,
                  backgroundColor: done ? C.money
                    : active ? C.primary : C.line,
                }} />
                <Text style={[T.micro, {
                  fontSize: 11,
                  color: done || active ? C.ink : C.inkSoft,
                }]} numberOfLines={1}>{t(stepKey)}</Text>
              </Pressable>
            );
          })}
        </View>
        <Text style={[T.micro, { fontSize: 11, marginTop: 4, color: C.inkSoft }]}>
          {t('create.tapStepToGoBack')}
        </Text>
      </View>

      <ScrollView ref={scroller}
                  contentContainerStyle={{ padding: S.lg, paddingBottom: 140, gap: S.lg }}
                  showsVerticalScrollIndicator={false}
                  onScroll={onScroll}
                  scrollEventThrottle={64}>

        {err ? (
          <Card tone="danger">
            <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'center' }}>
              <Warning color={C.danger} size={20} />
              <Text style={[T.bodySoft, { flex: 1, color: C.ink }]}>{err}</Text>
            </View>
          </Card>
        ) : null}

        {notice ? (
          <Card tone="money">
            <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'center' }}>
              <Check color={C.money} size={20} />
              <Text style={[T.bodySoft, { flex: 1, color: C.ink }]}>{notice}</Text>
            </View>
          </Card>
        ) : null}

        {/* How this listing is being made, and a one-tap way to change it. Visible
            rather than buried in settings: the choice changes what the app does with
            her photograph, so she should be able to see which one is active. */}
        <Pressable onPress={() => setModeAsk(true)} accessibilityRole="button"
                   accessibilityLabel={t('mode.change')}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm,
                         paddingVertical: 6 }}>
            <Spark size={14} color={mode === 'auto' ? C.indigo : C.inkSoft} />
            <Text style={[T.micro, { flex: 1, fontSize: 12.5 }]}>
              {t('mode.current', {
                what: mode === 'auto' ? t('mode.auto') : t('mode.manual') })}
            </Text>
            <Pill text={t('common.edit')} tone="soft" />
          </View>
        </Pressable>

        {/* ── 1. photo ─────────────────────────────────────────────────── */}
        <View onLayout={markSection(0)}>
        <Section n={1} title={t('create.step.photo')} subtitle={t('create.photoSub')} state={st(0)}>
          <Card style={{ padding: S.sm }}>
            {rawUri || analysis?.imageUrl ? (
              // The file on this phone comes first. The server's copy is a URL on a
              // machine that may not be reachable, and when it was preferred the
              // photograph she had just taken showed as an empty grey square.
              // Whichever version is actually on the listing. Preferring the local
              // file unconditionally meant the enhanced picture - the one buyers
              // would see - was never on screen at all.
              <Image source={{ uri: (aiImage && analysis?.imageUrl)
                                    || rawUri || analysis?.imageUrl }}
                     style={{ width: '100%', aspectRatio: 1, borderRadius: R.lg,
                              backgroundColor: C.bgAlt }} resizeMode="contain" />
            ) : (
              // The box is the button. It is the largest thing on the screen, it has
              // a camera drawn in the middle of it and it says "take a photo" - so
              // tapping it and having nothing happen is the app telling somebody they
              // guessed wrong, when they had not.
              <Pressable onPress={() => pick('camera')} accessibilityRole="button"
                         accessibilityLabel={t('create.takePhoto')}
                         style={({ pressed }) => ({ opacity: pressed ? 0.7 : 1 })}>
                <View style={{ aspectRatio: 1, borderRadius: R.lg, borderWidth: 2,
                               borderStyle: 'dashed', borderColor: C.lineStrong,
                               alignItems: 'center', justifyContent: 'center', gap: S.md,
                               backgroundColor: C.bgAlt }}>
                  <Camera color={C.primary} size={40} />
                  <Text style={T.body}>{t('create.takePhoto')}</Text>
                  <Text style={[T.bodySoft, { textAlign: 'center',
                                              paddingHorizontal: S.lg }]}>
                    {t('create.takePhotoHint')}
                  </Text>
                </View>
              </Pressable>
            )}
          </Card>
          <Card>
            <Text style={T.label}>{t('create.craftQuestion')}</Text>
            <Text style={[T.micro, { fontSize: 12 }]}>{t('create.craftHint')}</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
              {(Object.keys(CATEGORIES) as CategoryKey[]).map((k) => {
                const on = craft === k;
                return (
                  <Pressable key={k} onPress={() => {
                    setCraft(k);
                    planRef.current = new ShotPlan(k);
                    setShots([]);
                  }}>
                    <View style={{ paddingHorizontal: 14, paddingVertical: 9,
                                   borderRadius: R.pill,
                                   backgroundColor: on ? C.ink : C.bgAlt }}>
                      <Text style={{ fontFamily: 'Mukta_600SemiBold', fontSize: 14,
                                     color: on ? C.white : C.inkMid }}>
                        {t(`craft.${k}`)}
                      </Text>
                    </View>
                  </Pressable>
                );
              })}
            </View>
          </Card>

          <View style={{ flexDirection: 'row', gap: S.md }}>
            <Btn label={t('create.camera')} icon={<Camera color={C.white} size={20} />}
                 onPress={() => pick('camera')} style={{ flex: 1 }} />
            <Btn label={t('create.gallery')} tone="ghost" icon={<Gallery color={C.ink} size={20} />}
                 onPress={() => pick('library')} style={{ flex: 1 }} />
          </View>

          {shotHint ? (
            <Card tone="soft">
              <Text style={T.bodySoft}>
                {t('shot.nextReason', { what: t(`shot.${craft}.${shotHint}`) })}
              </Text>
              <Btn label={t('create.morePhotos')} tone="tonal"
                   icon={<Camera color={C.primaryDeep} size={20} />}
                   onPress={() => setCamOpen(true)} />
            </Card>
          ) : null}

          {shots.length > 1 ? (
            <Card>
              <Text style={T.label}>{t('create.photosTaken', { n: shots.length })}</Text>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: S.sm }}>
                {shots.map((sh, i) => (
                  <Image key={i} source={{ uri: sh.uri }}
                         style={{ width: 68, height: 68, borderRadius: R.md,
                                  backgroundColor: C.bgAlt }} />
                ))}
              </View>
            </Card>
          ) : null}
          {/*
            What the AI did to the photograph, and how to undo it.

            This was invisible before. The screen always drew the local file, so she
            never saw the enhanced version at all - while the enhanced version was
            the one going to the marketplace. She could not tell what had been done,
            and could not refuse it.

            Both are now shown side by side with the live one marked, and either can
            be made the one that ships.
          */}
          {rawUri && analysis ? (
            <Card style={{ gap: S.md }}>
              <Text style={T.label}>{t('create.whichPhoto')}</Text>

              <View style={{ flexDirection: 'row', gap: S.md }}>
                {([
                  { on: aiImage, uri: analysis.imageUrl,
                    label: t('create.aiVersion'), plain: false },
                  { on: !aiImage, uri: rawUri,
                    label: t('create.myVersion'), plain: true },
                ]).map((opt) => (
                  <Pressable key={opt.label} style={{ flex: 1 }}
                             disabled={opt.on || !!busy}
                             onPress={() => runAnalyze(rawUri, { plain: opt.plain })}>
                    <View style={{ gap: 6, borderRadius: R.lg, padding: 6,
                                   borderWidth: opt.on ? 2 : 1,
                                   borderColor: opt.on ? C.primary : C.line,
                                   backgroundColor: opt.on ? C.primarySoft : C.surface }}>
                      <Image source={{ uri: opt.uri }}
                             style={{ width: '100%', aspectRatio: 1,
                                      borderRadius: R.md, backgroundColor: C.bgAlt }}
                             resizeMode="cover" />
                      <View style={{ flexDirection: 'row', alignItems: 'center',
                                     gap: 4 }}>
                        <Text style={[T.micro, { flex: 1, fontSize: 12,
                                                 color: opt.on ? C.primaryDeep
                                                               : C.inkMid }]}>
                          {opt.label}
                        </Text>
                        {opt.on ? <Check color={C.primary} size={14} /> : null}
                      </View>
                    </View>
                  </Pressable>
                ))}
              </View>

              {/* Exactly what was done, in her language, one line each. The edit log
                  was always recorded and never shown. */}
              {aiImage && (analysis.ops || []).length ? (
                <View style={{ gap: 2 }}>
                  {(analysis.ops || []).map((op) => (
                    <Text key={op} style={[T.micro, { fontSize: 12 }]}>
                      {'• '}{t(`op.${op}` as any) !== `op.${op}`
                        ? t(`op.${op}` as any) : op}
                    </Text>
                  ))}
                </View>
              ) : null}

              <Text style={[T.micro, { fontSize: 11.5, color: C.inkSoft }]}>
                {aiImage ? t('create.aiVersionNote') : t('create.myVersionNote')}
              </Text>

              <Btn label={t('create.reanalyse')} tone="ghost"
                   icon={<Sparkle color={C.ink} size={20} />}
                   busy={!!busy}
                   onPress={() => runAnalyze(rawUri, { plain: !aiImage })} />
            </Card>
          ) : null}

        </Section>
        </View>

        {busy ? (
          <Card tone="soft">
            <Pill text={busy} icon={<Sparkle color={C.primaryDeep} size={14} />} />
            <Skeleton h={14} /><Skeleton h={14} w="80%" /><Skeleton h={14} w="55%" />
          </Card>
        ) : null}

        {/* ── 2. detection + OCR ───────────────────────────────────────── */}
        {hasPhoto ? (
          <View onLayout={markSection(1)}>
          <Section n={2} title={t('create.detectTitle')}
                   subtitle={analysis
                     ? t('create.detectSub', { n: analysis.ocr?.boxes?.length ?? 0, ms: analysis.ms })
                     : mode === 'manual' ? t('create.detectSkipped')
                     // Busy is not offline, and saying "no connection" while a
                     // request is in flight is the app lying about its own state.
                     // Against the deployed backend this step takes minutes, so this
                     // caption was on screen for the whole time it was working.
                     : busy ? t('create.detectWorking')
                     : err ? err
                                         : t('create.detectOffline')}
                   state={st(1)}>
            {!analysis ? (
              // No analysis is a legitimate state, not a failure. In manual mode it
              // is the promise being kept - nothing has looked at her photograph. In
              // auto mode with no signal it is simply not done yet. Either way the
              // step is complete enough to move past.
              <Card tone="soft">
                <Text style={T.bodySoft}>
                  {mode === 'manual' ? t('create.detectSkipped')
                   : busy ? t('create.detectWorking')
                   : err ? err
                   : t('create.detectOffline')}
                </Text>
                <View style={{ flexDirection: 'row', gap: S.sm }}>
                  {rawUri ? (
                    <Btn label={t('create.detectRetry')} tone="tonal" style={{ flex: 1 }}
                         icon={<Sparkle color={C.primaryDeep} size={18} />}
                         busy={!!busy} onPress={() => runAnalyze(rawUri)} />
                  ) : null}
                </View>
              </Card>
            ) : null}
            {analysis?.ocr?.boxes?.length ? (
              <Card>
                <Text style={T.label}>{t('create.ocrRead')}</Text>
                {analysis.ocr.boxes.slice(0, 8).map((b, i) => (
                  <View key={i} style={{ flexDirection: 'row', gap: S.sm,
                                         alignItems: 'center' }}>
                    <Pill text={`${Math.round(b.confidence * 100)}%`}
                          tone={b.confidence >= 0.9 ? 'good' : 'warn'} />
                    <Text style={[T.bodySoft, { flex: 1, color: C.ink }]}>{b.text}</Text>
                  </View>
                ))}
              </Card>
            ) : analysis ? (
              <Card tone="warn">
                <Text style={T.bodySoft}>{t('create.ocrNone')}</Text>
              </Card>
            ) : null}

            {analysis ? (
            <Card>
              <Text style={T.label}>{t('create.aiFound')}</Text>
              {Object.entries(analysis.detected || {}).map(([k, v]) => (
                <View key={k} style={{ flexDirection: 'row', gap: S.sm,
                                       alignItems: 'flex-start', paddingVertical: 3 }}>
                  <Text style={[T.micro, { width: 92, color: C.inkSoft }]}>{k}</Text>
                  <Text style={[T.bodySoft, { flex: 1, color: C.ink }]} numberOfLines={3}>
                    {Array.isArray(v) ? v.join(', ') : String(v)}
                  </Text>
                  {conf[k] != null ? (
                    <Pill text={`${Math.round(conf[k] * 100)}%`}
                          tone={conf[k] >= 0.8 ? 'good' : 'warn'} />
                  ) : null}
                </View>
              ))}
              {analysis.notes ? (
                <>
                  <Divider />
                  <Text style={[T.micro, { fontSize: 12 }]}>{analysis.notes}</Text>
                </>
              ) : null}
            </Card>
            ) : null}

            {analysis && Object.keys(analysis.suggestions || {}).length ? (
              <Card tone="warn">
                <Text style={T.label}>{t('create.lowConfidence')}</Text>
                {Object.entries(analysis!.suggestions).map(([k, v]) => (
                  <Text key={k} style={T.bodySoft}>{k}: {String(v)}</Text>
                ))}
              </Card>
            ) : null}

            {analysis ? (
            <Card>
              <Text style={T.label}>{t('create.whatHappened')}</Text>
              {analysis.ops.map((o, i) => (
                <View key={i} style={{ flexDirection: 'row', gap: S.sm, alignItems: 'center' }}>
                  <Check color={C.money} size={14} />
                  <Text style={[T.micro, { flex: 1, fontSize: 12 }]}>{o}</Text>
                </View>
              ))}
            </Card>
            ) : null}

          </Section>
          </View>
        ) : null}

        {/* ── 3. editable details ──────────────────────────────────────── */}
        {hasPhoto ? (
          <View onLayout={markSection(2)}>
          <Section n={3} title={t('create.step.info')} subtitle={t('create.infoSub')} state={st(2)}>
            {/*
              In manual mode nothing has been analysed, so the one-tap escape hatch is
              offered up front: fill everything still empty, leave everything she has
              typed exactly as it is.
            */}
            {mode === 'manual' ? (
              <Card tone="indigo">
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                  <Spark size={17} />
                  <Text style={[T.body, { flex: 1, fontFamily: 'Mukta_700Bold' }]}>
                    {t('ai.continueFromHere')}
                  </Text>
                </View>
                <Text style={T.bodySoft}>{t('ai.continueSub')}</Text>
                <Btn label={t('ai.continueFromHere')} tone="indigo"
                     busy={aiBusy === 'continue'} onPress={continueWithAi} />
                <Text style={[T.micro, { fontSize: 11.5 }]}>{t('ai.editable')}</Text>
              </Card>
            ) : null}
            <Card>
              <Text style={T.label}>{t('create.speakOptional')}</Text>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                {LANGS.map((l) => (
                  <Pressable key={l.code} onPress={() => setLang(l.code)}>
                    <View style={{ paddingHorizontal: 14, paddingVertical: 8,
                                   borderRadius: R.pill,
                                   backgroundColor: lang === l.code ? C.ink : C.bgAlt }}>
                      <Text style={{ fontFamily: 'Mukta_600SemiBold', fontSize: 14,
                                     color: lang === l.code ? C.white : C.inkMid }}>
                        {l.label}
                      </Text>
                    </View>
                  </Pressable>
                ))}
              </View>
              <Pressable onPressIn={startRec} onPressOut={stopRec}
                         accessibilityLabel={t('create.holdToSpeak')}>
                <LinearGradient colors={recording ? ['#E5533D', '#B3261E'] : GRAD.primary}
                                start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                                style={[{ borderRadius: R.lg, paddingVertical: 16,
                                          flexDirection: 'row', alignItems: 'center',
                                          justifyContent: 'center', gap: S.sm }, shadow(1)]}>
                  <Mic color={C.white} size={24} />
                  <Text style={{ fontFamily: 'Mukta_700Bold', fontSize: 17, color: C.white }}>
                    {recording ? t('create.listening') : t('create.holdToSpeak')}
                  </Text>
                </LinearGradient>
              </Pressable>
              {partial ? <Text style={T.bodySoft}>{partial}</Text> : null}
              {transcript ? (
                <Card tone="soft"><Text style={T.body}>{transcript}</Text></Card>
              ) : null}
              {!isSupported() ? (
                <Pill text={t('create.noMic')} tone="warn" />
              ) : null}
              {/* No fabricated transcript any more: if the recogniser cannot run,
                  the AI button below is the way forward, and it works from the
                  photograph alone. */}
              <Btn label={t('create.writeWithAI')} tone="tonal"
                   icon={<Sparkle color={C.primaryDeep} size={20} />}
                   busy={busy === t('create.writing')} onPress={writeCopy} />
            </Card>

            {/*
              Every field carries its own AI button. This is what makes manual mode a
              choice and not a lesser version of the app: the same models are here,
              and she decides which field and when. Each suggestion is shown next to
              what she already wrote and nothing is saved until she accepts it.
            */}
            <Card>
              <FieldWithAi label={t('create.nameLocal')} value={titleHi}
                           onChange={setTitleHi} listingId={listingId} field="titleHi"
                           placeholder={t('create.namePlaceholder')} />
              <FieldWithAi label="Name (English)" value={titleEn} onChange={setTitleEn}
                           listingId={listingId} field="title"
                           confidence={conf.title} placeholder="Product title" />
              <FieldWithAi label={t('create.descLocal')} value={descHi}
                           onChange={setDescHi} multiline listingId={listingId}
                           field="descriptionHi"
                           placeholder={t('create.descPlaceholder')} />
              <FieldWithAi label="Description (English)" value={descEn}
                           onChange={setDescEn} multiline listingId={listingId}
                           field="description" confidence={conf.description} />
              {descHi ? (
                <Btn label={t('create.listenCheck')} tone="ghost"
                     icon={<Speaker color={C.ink} size={20} />}
                     onPress={() => Speech.speak(descHi, { language: 'hi-IN' })} />
              ) : null}
            </Card>

            <Card>
              <Field label={t('create.category')} value={category} onChange={setCategory}
                     confidence={conf.category} placeholder="A > B > C" />
              <Field label={t('create.hsn')} value={hsn} onChange={setHsn} numeric
                     confidence={conf.hsn} placeholder={t('create.hsnHint')} />
              <Field label={t('create.quantity')} value={qtyStr} onChange={setQtyStr} numeric />
            </Card>

          </Section>
          </View>
        ) : null}

        {/* ── 4. price ─────────────────────────────────────────────────── */}
        {hasPhoto ? (
          <View onLayout={markSection(3)}>
          <Section n={4} title={t('create.step.price')} subtitle={t('create.priceSub')} state={st(3)}>
            <Card>
              <Field label={t('create.materialCost')} value={materialStr}
                     onChange={setMaterialStr} numeric suffix="₹" />
              <Field label={t('create.daysTaken')} value={daysStr} onChange={setDaysStr} numeric />
              <Btn label={t('create.getFairPrice')} tone="tonal"
                   icon={<Rupee color={C.primaryDeep} size={20} />}
                   busy={busy.includes(t('create.step.price'))} onPress={runPrice} />
            </Card>

            {advice ? (
              <>
                <Card tone="money">
                  <Text style={T.label}>{t('create.floorLabel')}</Text>
                  <Text style={[T.hero, { color: C.moneyDeep }]}>{money(advice.floor)}</Text>
                  <Text style={T.bodySoft}>
                    {t('create.floorNote')}
                  </Text>
                </Card>
                {advice.underpricedWarning ? (
                  <Card tone="danger">
                    <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'center' }}>
                      <Warning color={C.danger} size={18} />
                      <Text style={[T.micro, { color: C.danger }]}>{t('create.warning')}</Text>
                    </View>
                    <Text style={T.bodySoft}>{advice.underpricedWarning}</Text>
                  </Card>
                ) : null}
              </>
            ) : null}

            <Card>
              <Field label={t('create.sellingPrice')} value={priceStr} onChange={setPriceStr}
                     numeric suffix="₹" confidence={conf.price}
                     source={analysis?.detected?.price ? 'OCR' : undefined} />
              {advice && priceNum > 0 && priceNum < advice.floor ? (
                <Pill text={t('create.belowFloor', { amount: money(advice.floor) })}
                      tone="danger" />
              ) : null}
            </Card>

            {/* The explanation is editable, and stays editable, alongside the price. */}
            <Card>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                <Text style={[T.label, { flex: 1 }]}>{t('create.howMade')}</Text>
                {breakdownDirty ? <Pill text={t('create.yoursBadge')} tone="good" />
                                : advice ? <Pill text={t('create.aiBadge')} /> : null}
              </View>

              {breakdown.length === 0 ? (
                <Text style={T.bodySoft}>{t('create.breakdownEmpty')}</Text>
              ) : null}

              {breakdown.map((row) => (
                <View key={row.id} style={{ flexDirection: 'row', gap: S.sm,
                                            alignItems: 'flex-end' }}>
                  <View style={{ flex: 1.6 }}>
                    <Field label="" value={row.label}
                           onChange={(v) => editLine(row.id, { label: v })}
                           placeholder={t('create.linePlaceholder')} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Field label="" value={row.amount} numeric suffix="₹"
                           onChange={(v) => editLine(row.id, { amount: v })}
                           placeholder="0" />
                  </View>
                  <Pressable onPress={() => removeLine(row.id)} hitSlop={10}
                             accessibilityLabel={t('common.delete')}
                             style={{ paddingBottom: 16, paddingHorizontal: 4 }}>
                    <Text style={{ fontSize: 22, color: C.inkSoft }}>×</Text>
                  </Pressable>
                </View>
              ))}

              <Btn label={t('create.addLine')} tone="ghost" onPress={addLine} />

              <Divider />
              <Row label={t('create.sum')} value={money(bdTotal)} />
              <Row label={t('create.sellingPrice')} value={money(priceNum)} strong />

              {bdMismatch ? (
                <Card tone="warn" style={{ marginTop: S.sm }}>
                  <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'center' }}>
                    <Warning color={C.gold} size={18} />
                    <Text style={[T.bodySoft, { flex: 1 }]}>
                      जोड़ {money(bdTotal)} है, पर कीमत {money(priceNum)} लिखी है —
                      अंतर {money(Math.abs(bdTotal - priceNum))}।
                    </Text>
                  </View>
                  <Text style={[T.micro, { fontSize: 12 }]}>{t('create.mismatchNote')}</Text>
                  <View style={{ flexDirection: 'row', gap: S.sm }}>
                    <Btn label={t('create.setPriceToSum')} tone="ghost" style={{ flex: 1 }}
                         onPress={priceFromBreakdown} />
                    <Btn label={t('create.putDiffInProfit')} tone="ghost" style={{ flex: 1 }}
                         onPress={balanceBreakdown} />
                  </View>
                </Card>
              ) : breakdown.length ? (
                <Pill text={t('create.matched')} tone="good"
                      icon={<Check color={C.moneyDeep} size={13} />} />
              ) : null}

              <Divider />
              <Field label={t('create.explainYourself')}
                     value={rationale} multiline
                     onChange={(v) => { setRationale(v); setRationaleDirty(true); }}
                     placeholder={t('create.explainPlaceholder')} />
            </Card>

          </Section>
          </View>
        ) : null}

        {/* ── 5. channels + preview ────────────────────────────────────── */}
        {hasPhoto ? (
          <View onLayout={markSection(4)}>
          <Section n={5} title={t('create.step.clusters')} subtitle={t('create.clustersSub')}
                   state={st(4)}>
            {/* Which cluster sells this product.

                This replaced a row of marketplace cards - ONDC, GeM, Amazon Karigar,
                Shopify - each reading "Setup pending". Those were not the artisan's
                decision to make: a marketplace seller account needs a GSTIN she does
                not have, which is the entire reason the cluster exists. The cluster
                owner holds the GST and is the seller of record, so choosing a cluster
                is the real, actionable version of the same step, and the marketplace
                plumbing sits behind it where it belongs.

                Optional on purpose. No cluster means the product sells on her own
                storefront, which already works and needs nobody's permission. */}
            {clusters === null ? (
              <Card tone="soft">
                <Text style={T.bodySoft}>{t('create.clustersLoading')}</Text>
              </Card>
            ) : clusters.length === 0 ? (
              <Card tone="soft">
                <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
                  {t('create.noClusters')}
                </Text>
                <Text style={T.bodySoft}>{t('create.noClustersWhy')}</Text>
                {onClusters ? (
                  <Btn label={t('create.findCluster')} tone="tonal"
                       onPress={onClusters} />
                ) : null}
              </Card>
            ) : (
              <>
                {[{ id: '', name: t('create.ownStorefront'),
                    note: t('create.ownStorefrontNote'), commissionPct: 0,
                    owner: '' },
                  ...clusters.map((c) => ({
                    id: c.id, name: c.name,
                    note: [c.craftCategory, c.district].filter(Boolean).join(' · '),
                    commissionPct: c.commissionPct,
                    owner: c.viewerIsOwner ? t('create.yours') : c.ownerName,
                  }))].map((c) => {
                  const on = (clusterId || '') === c.id;
                  return (
                    <Pressable
                      key={c.id || 'storefront'}
                      onPress={() => pickCluster(c.id)}
                      accessibilityRole="button"
                      accessibilityState={{ selected: on }}
                      disabled={savingCluster}
                    >
                      <View style={{
                        borderRadius: R.lg, padding: S.md, borderWidth: 1.5,
                        borderColor: on ? C.money : C.line,
                        backgroundColor: on ? C.moneySoft : C.surface,
                        flexDirection: 'row', alignItems: 'center', gap: S.md,
                        opacity: savingCluster ? 0.6 : 1,
                      }}>
                        <View style={{ width: 40, height: 40, borderRadius: R.md,
                                       backgroundColor: on ? C.money : C.bgAlt,
                                       alignItems: 'center', justifyContent: 'center' }}>
                          {on ? <Check color={C.white} size={20} />
                              : <Users color={C.inkSoft} size={18} />}
                        </View>
                        <View style={{ flex: 1, gap: 1 }}>
                          <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}
                                numberOfLines={1}>
                            {c.name}
                          </Text>
                          {c.note ? (
                            <Text style={[T.micro, { fontSize: 12 }]} numberOfLines={1}>
                              {c.note}
                            </Text>
                          ) : null}
                          {c.owner ? (
                            <Text style={[T.micro, { fontSize: 11.5 }]}
                                  numberOfLines={1}>
                              {t('create.clusterOwner')}: {c.owner}
                            </Text>
                          ) : null}
                        </View>
                        {/* The commission is the number that decides whether this is
                            worth it to her, so it is on the card rather than a tap
                            away. */}
                        {c.id ? (
                          <Pill text={`${c.commissionPct}%`}
                                tone={on ? 'good' : undefined} />
                        ) : null}
                      </View>
                    </Pressable>
                  );
                })}
                {clusterErr ? (
                  <Card tone="danger">
                    <Text style={T.bodySoft}>{clusterErr}</Text>
                  </Card>
                ) : null}
              </>
            )}

            <Card>
              <Text style={T.label}>{t('create.review')}</Text>
              <View style={{ flexDirection: 'row', gap: S.md }}>
                {rawUri || analysis?.imageUrl ? (
                  <Image source={{ uri: rawUri || analysis!.imageUrl }}
                         style={{ width: 92, height: 92, borderRadius: R.md,
                                  backgroundColor: C.bgAlt }} />
                ) : null}
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]} numberOfLines={2}>
                    {titleHi || titleEn}
                  </Text>
                  <Text style={[T.section, { color: C.money }]}>
                    {money(parseFloat(priceStr) || 0)}
                  </Text>
                  <Text style={[T.micro, { fontSize: 12 }]}>
                    {category} · HSN {hsn} · {qtyStr} नग
                  </Text>
                </View>
              </View>
            </Card>

            {Object.keys(blocked).length ? (
              <Card tone="warn">
                <Text style={T.label}>{t('create.blockedTitle')}</Text>
                {Object.entries(blocked).map(([ch, fields]) => (
                  <Text key={ch} style={T.bodySoft}>
                    {chans.find((c) => c.id === ch)?.name || ch}: {fields.join(', ')}
                  </Text>
                ))}
              </Card>
            ) : null}

            {!artisan ? (
              <Card tone="indigo">
                <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'flex-start' }}>
                  <Shield color={C.indigo} size={20} />
                  <Text style={[T.bodySoft, { flex: 1, color: C.indigo }]}>{t('create.loginNeededBanner')}</Text>
                </View>
              </Card>
            ) : null}

            <Btn label={t('create.sendTo', { n: picked.length })} tone="money" large
                 icon={<Arrow color={C.white} size={22} />}
                 busy={busy === t('create.publishing')}
                 disabled={!picked.length || !hasPrice}
                 onPress={doPublish} />
            {!hasPrice ? (
              <Text style={[T.micro, { fontSize: 12 }]}>{t('create.priceFirst')}</Text>
            ) : null}
          </Section>
          </View>
        ) : null}

        {/* ── 6. live status ───────────────────────────────────────────── */}
        {pubs.length ? (
          <View onLayout={markSection(5)}>
          <Section n={6} title={t('create.whereItWent')} subtitle={t('create.realStatus')} state="active">
            {pubs.map((p) => (
              <Card key={p.id}
                    tone={p.status === 'published' ? 'money'
                        : p.status === 'failed' ? 'danger' : 'plain'}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                  <Text style={[T.body, { fontFamily: 'Mukta_700Bold', flex: 1 }]}>
                    {chans.find((c) => c.id === p.channel)?.name || p.channel}
                  </Text>
                  <StatusChip status={p.status} />
                </View>
                {p.url ? (
                  <Pressable onPress={() => Linking.openURL(api.localise(p.url))}>
                    <Text style={[T.bodySoft, { color: C.indigo,
                                                textDecorationLine: 'underline' }]}>
                      {p.url}
                    </Text>
                  </Pressable>
                ) : null}
                {p.externalId ? (
                  <Text style={[T.micro, { fontSize: 12 }]}>ID: {p.externalId}</Text>
                ) : null}
                {p.submittedAt ? (
                  <Text style={[T.micro, { fontSize: 12 }]}>
                    {new Date(p.submittedAt).toLocaleString('en-IN')}
                  </Text>
                ) : null}
                {p.error ? (
                  <Text style={[T.micro, { fontSize: 12, color: C.danger }]}>{p.error}</Text>
                ) : null}
              </Card>
            ))}

            {live ? (
              <Card>
                <Text style={T.label}>{t('create.listingStatus')}</Text>
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                  {['draft', 'processing', 'submitted', 'published', 'active', 'sold']
                    .map((f) => {
                      const order = ['draft', 'processing', 'submitted', 'published',
                                     'active', 'sold'];
                      const on = order.indexOf(f) <= order.indexOf(live.status);
                      return (
                        <View key={f} style={{
                          paddingHorizontal: 11, paddingVertical: 6, borderRadius: R.pill,
                          backgroundColor: on ? C.money : C.bgAlt }}>
                          <Text style={{ fontFamily: 'Mukta_600SemiBold', fontSize: 12,
                                         color: on ? C.white : C.inkSoft }}>{f}</Text>
                        </View>
                      );
                    })}
                </View>
              </Card>
            ) : null}

            {passport ? (
              <LinearGradient colors={GRAD.indigo} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                              style={[{ borderRadius: R.xl, padding: S.lg, gap: S.md },
                                      shadow(2)]}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                  <Shield color={C.primaryLite} size={20} />
                  <Text style={[T.micro, { color: C.primaryLite, flex: 1 }]}>{t('create.passport')}</Text>
                </View>
                <View style={{ flexDirection: 'row', gap: S.lg, alignItems: 'center' }}>
                  <View style={{ backgroundColor: C.white, padding: 7, borderRadius: R.md }}>
                    {/* The verification URL, not the bare id. A camera app can open a
                        URL; scanning "KK-BNS-2026-4F2A19C0D3" opened nothing at all. */}
                    <QRCode value={passport.verifyUrl || passport.id} size={72}
                            color={C.indigoDeep} backgroundColor={C.white} />
                  </View>
                  <View style={{ flex: 1, gap: 2 }}>
                    <Text style={[T.body, { fontFamily: 'Mukta_800ExtraBold',
                                            color: C.white }]}>{passport.id}</Text>
                    <Text style={[T.micro, { color: 'rgba(255,255,255,0.7)', fontSize: 11 }]}
                          numberOfLines={1}>{passport.rawHash}</Text>
                    <Text style={[T.micro, { color: 'rgba(255,255,255,0.7)', fontSize: 11 }]}
                          numberOfLines={1}>{passport.signature}</Text>
                  </View>
                </View>
                {passport.verifyUrl ? (
                  <Pressable onPress={() => Linking.openURL(api.localise(passport.verifyUrl))}>
                    <Text style={[T.micro, { color: C.primaryLite, fontSize: 12 }]}>
                      {t('create.passportVerify')}
                    </Text>
                  </Pressable>
                ) : null}
              </LinearGradient>
            ) : null}

            <Card>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                <Text style={[T.label, { flex: 1 }]}>ऑर्डर ({orders.length})</Text>
                <Pressable onPress={refreshStatus}><Pill text={t('create.refresh')} /></Pressable>
              </View>
              {orders.length === 0 ? (
                <Text style={T.bodySoft}>
                  अभी कोई ऑर्डर नहीं। ऊपर के लिंक पर कोई खरीदेगा तो यहाँ अपने आप दिखेगा।
                </Text>
              ) : orders.map((o) => (
                <View key={o.id} style={{ paddingVertical: 8, gap: 4 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                    <Text style={[T.bodySoft, { flex: 1, color: C.ink }]}>
                      {o.buyerName} · {money(o.amount)}
                    </Text>
                    <StatusChip status={o.status} />
                  </View>
                  <Text style={[T.micro, { fontSize: 12 }]}>
                    {o.id} · भुगतान {o.paymentStatus}
                    {o.trackingId ? ` · ${o.courier} ${o.trackingId}` : ''}
                  </Text>
                </View>
              ))}
            </Card>

            <Btn label={t('nav.home')} tone="ghost" icon={<HomeIcon color={C.ink} size={20} />}
                 onPress={onHome} />
          </Section>
          </View>
        ) : null}
      </ScrollView>

      <AuthSheet
        visible={authOpen}
        requiredFor={picked}
        reason={authReason}
        onClose={() => setAuthOpen(false)}
        onAuthed={async (a) => {
          setArtisan(a);
          try {
            const rd = await api.readiness();
            setReadiness(rd.readiness);
          } catch { /* keep what we have */ }
          setAuthOpen(false);
          // Return to precisely where they were and finish the action they asked for.
          doPublish();
        }}
      />

      {/*
        Asked on the first photograph, and reachable from the banner at the top after
        that. The photograph she has already taken is held while she answers, so the
        question never costs her a retake.
      */}
      <ModeSheet
        visible={modeAsk}
        onClose={() => setModeAsk(false)}
        onChoose={(m) => {
          setModeState(m);
          const held = pendingPhoto.current;
          pendingPhoto.current = null;
          if (held) {
            if (m === 'manual') attachOnly(held);
            else connection.measure().then(async (r) => {
              if (connection.useBackgroundJob(r)) await sendAsJob(held);
              else await runAnalyze(held);
            });
          }
        }}
      />
    </View>
  );
}
