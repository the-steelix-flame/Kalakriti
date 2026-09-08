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
  Speaker, Home as HomeIcon,
} from '../icons';
import * as api from '../lib/api';
import * as session from '../lib/session';
import AuthSheet from './AuthSheet';
import GuidedCamera from './GuidedCamera';
import { ShotPlan } from './shotPlan';
import { CATEGORIES, CategoryKey } from '../vision/guidance';
import { useI18n } from '../i18n';
import { listen, isSupported, LANGS, Listener } from '../lib/speech';

/**
 * The whole listing flow on ONE page.
 *
 * Sections appear below one another as they are completed and are never unmounted,
 * so scrolling back always shows exactly what was entered. All state lives in this
 * component and is mirrored to the backend on edit, so progress survives a reload
 * too - the draft row is the source of truth, not component state.
 *
 * Every AI-produced value lands in an editable Field and stays editable until the
 * artisan presses Publish. Nothing locks.
 */

const STEPS = [
  'create.step.photo', 'create.step.detect', 'create.step.info',
  'create.step.price', 'create.step.channels', 'create.step.live',
];

export default function Create({
  onHome, resumeId,
}: { onHome: () => void; resumeId?: string | null }) {
  // ── form state (the single source of truth while editing) ────────────────
  const [rawUri, setRawUri] = useState<string | null>(null);
  const [listingId, setListingId] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<api.AnalyzeOut | null>(null);
  const [busy, setBusy] = useState<string>('');
  const [err, setErr] = useState('');

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
  const insets = useSafeAreaInsets();
  const [lang, setLang] = useState(locale);
  useEffect(() => { setLang(locale); }, [locale]);
  const recognizer = useRef<Listener | null>(null);

  const [chans, setChans] = useState<api.Channel[]>([]);
  const [picked, setPicked] = useState<string[]>(['storefront']);
  const [pubs, setPubs] = useState<api.Publication[]>([]);
  const [live, setLive] = useState<api.Listing | null>(null);
  const [orders, setOrders] = useState<api.Order[]>([]);
  const [passport, setPassport] = useState<any>(null);

  const [camOpen, setCamOpen] = useState(false);
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

  useEffect(() => { api.channels().then((r) => setChans(r.channels)).catch(() => {}); }, []);

  useEffect(() => {
    api.bootstrap()
      .then((b) => { setArtisan(b.artisan); setReadiness(b.marketplaceReadiness); })
      .catch(() => {});
  }, []);

  // Resume a draft left behind by a previous session. Everything the artisan had -
  // images, generated copy, price, chosen channels - comes back from the row.
  useEffect(() => {
    const id = resumeId || session.getDraft();
    if (!id || listingId) return;
    api.getListing(id).then((l) => {
      setListingId(l.id);
      setTitleEn(l.titleEn); setTitleHi(l.titleHi);
      setDescEn(l.descEn); setDescHi(l.descHi);
      setCategory(l.category); setHsn(l.hsn);
      if (l.price) setPriceStr(String(l.price));
      setQtyStr(String(l.quantity || 1));
      setTranscript(l.transcript || '');
      if (l.channelsSelected?.length) setPicked(l.channelsSelected);
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
    }).catch(() => session.setDraft(null));
  }, [resumeId]);

  useEffect(() => { if (listingId) session.setDraft(listingId); }, [listingId]);

  // Mirror edits to the backend so a refresh never loses work. Debounced so typing
  // does not fire a request per keystroke.
  const saveTimer = useRef<any>(null);
  const persist = useCallback((patch: Partial<api.Listing>) => {
    if (!listingId) return;
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      api.updateListing(listingId, patch).catch(() => {});
    }, 700);
  }, [listingId]);

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
      if (!r.canceled && r.assets?.[0]) {
        setRawUri(r.assets[0].uri);
        runAnalyze(r.assets[0].uri);
      }
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  }

  function onShotCaptured(uri: string, key: string) {
    planRef.current.add(uri, key);
    setShots([...planRef.current.captured]);
    const first = planRef.current.captured.length === 1;
    if (first) {
      setRawUri(uri);
      setCamOpen(false);
      runAnalyze(uri);
      // Say why another photo helps, without forcing one.
      setShotHint(planRef.current.nextShotKey());
    } else {
      setCamOpen(false);
      setShotHint(planRef.current.nextShotKey());
    }
  }

  async function runAnalyze(uri: string) {
    setBusy(t('create.analysing'));
    setErr('');
    try {
      const out = await api.analyze(uri, {
        transcript, lang, background: 'studio', listingId: listingId ?? undefined,
      });
      setAnalysis(out);
      setListingId(out.listingId);
      const d = out.detected || {};
      // Fill only empty fields - never overwrite something already edited.
      setTitleEn((v) => v || String(d.title ?? ''));
      setDescEn((v) => v || String(d.description ?? ''));
      setCategory((v) => v || String(d.category ?? ''));
      setHsn((v) => v || String(d.hsn ?? ''));
      if (typeof d.price === 'number') setPriceStr((v) => v || String(d.price));
      api.mintPassport({ rawHash: out.rawHash, ops: out.ops, artisanId: 'ART-UP-VNS-4471' })
        .then(setPassport).catch(() => {});
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy('');
    }
  }

  /* ── step 3: voice + copy ────────────────────────────────────────────── */

  function startRec() {
    setPartial('');
    setRecording(true);
    recognizer.current = listen({
      lang,
      onPartial: setPartial,
      onFinal: (t) => { setRecording(false); setTranscript(t); setPartial(''); },
      onError: (m) => setErr(String(m)),
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
        listingId: listingId ?? undefined,
      });
      if (out.ok === false) { setErr(out.error || 'copy failed'); return; }
      setTitleEn(out.titleEn ?? titleEn);
      setTitleHi(out.titleHi ?? titleHi);
      setDescEn(out.descEn ?? descEn);
      setDescHi(out.descHi ?? descHi);
      setCategory(out.category ?? category);
      setHsn(out.hsn ?? hsn);
    } catch (e: any) {
      setErr(String(e?.message || e));
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
        listingId: listingId ?? undefined,
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
      setErr(String(e?.message || e));
    } finally { setBusy(''); }
  }

  /* ── step 5: publish ─────────────────────────────────────────────────── */

  async function doPublish() {
    if (!listingId) return;
    setBusy(t('create.publishing'));
    setErr('');
    setBlocked({});
    try {
      await api.updateListing(listingId, {
        titleEn, titleHi, descEn, descHi, category, hsn,
        price: parseFloat(priceStr) || 0, quantity: parseInt(qtyStr, 10) || 1,
        channelsSelected: picked,
      } as any);
      const out = await api.publishListing(listingId, picked);
      setPubs(out.publications);
      setLive(out.listing);
      const st = await api.listingStatus(listingId);
      setOrders(st.orders);
      session.setDraft(null);
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
        setErr(String(e?.message || e));
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

  const st = (i: number): 'done' | 'active' | 'locked' =>
    step > i ? 'done' : step === i ? 'active' : 'locked';

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
            const active = step === i;
            return (
              <View key={stepKey} style={{ flex: 1, gap: 5 }}>
                <View style={{
                  height: 4, borderRadius: 99,
                  backgroundColor: done ? C.money
                    : active ? C.primary : C.line,
                }} />
                <Text style={[T.micro, {
                  fontSize: 11,
                  color: done || active ? C.ink : C.inkSoft,
                }]} numberOfLines={1}>{t(stepKey)}</Text>
              </View>
            );
          })}
        </View>
      </View>

      <ScrollView ref={scroller}
                  contentContainerStyle={{ padding: S.lg, paddingBottom: 140, gap: S.lg }}
                  showsVerticalScrollIndicator={false}>

        {err ? (
          <Card tone="danger">
            <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'center' }}>
              <Warning color={C.danger} size={20} />
              <Text style={[T.bodySoft, { flex: 1, color: C.ink }]}>{err}</Text>
            </View>
          </Card>
        ) : null}

        {/* ── 1. photo ─────────────────────────────────────────────────── */}
        <Section n={1} title={t('create.step.photo')} subtitle={t('create.photoSub')} state={st(0) === 'locked' ? 'active' : st(0)}>
          <Card style={{ padding: S.sm }}>
            {analysis?.imageUrl || rawUri ? (
              <Image source={{ uri: analysis?.imageUrl || rawUri! }}
                     style={{ width: '100%', aspectRatio: 1, borderRadius: R.lg,
                              backgroundColor: C.bgAlt }} resizeMode="contain" />
            ) : (
              <View style={{ aspectRatio: 1, borderRadius: R.lg, borderWidth: 2,
                             borderStyle: 'dashed', borderColor: C.lineStrong,
                             alignItems: 'center', justifyContent: 'center', gap: S.md,
                             backgroundColor: C.bgAlt }}>
                <Camera color={C.primary} size={40} />
                <Text style={T.body}>{t('create.takePhoto')}</Text>
                <Text style={[T.bodySoft, { textAlign: 'center', paddingHorizontal: S.lg }]}>
                  {t('create.takePhotoHint')}
                </Text>
              </View>
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
          {rawUri && analysis ? (
            <Btn label={t('create.reanalyse')} tone="ghost"
                 icon={<Sparkle color={C.ink} size={20} />}
                 onPress={() => runAnalyze(rawUri)} />
          ) : null}
        </Section>

        {busy ? (
          <Card tone="soft">
            <Pill text={busy} icon={<Sparkle color={C.primaryDeep} size={14} />} />
            <Skeleton h={14} /><Skeleton h={14} w="80%" /><Skeleton h={14} w="55%" />
          </Card>
        ) : null}

        {/* ── 2. detection + OCR ───────────────────────────────────────── */}
        {analysis ? (
          <Section n={2} title={t('create.detectTitle')}
                   subtitle={t('create.detectSub', { n: analysis.ocr?.boxes?.length ?? 0, ms: analysis.ms })}
                   state={st(1)}>
            {analysis.ocr?.boxes?.length ? (
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
            ) : (
              <Card tone="warn">
                <Text style={T.bodySoft}>{t('create.ocrNone')}</Text>
              </Card>
            )}

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

            {Object.keys(analysis.suggestions || {}).length ? (
              <Card tone="warn">
                <Text style={T.label}>{t('create.lowConfidence')}</Text>
                {Object.entries(analysis.suggestions).map(([k, v]) => (
                  <Text key={k} style={T.bodySoft}>{k}: {String(v)}</Text>
                ))}
              </Card>
            ) : null}

            <Card>
              <Text style={T.label}>{t('create.whatHappened')}</Text>
              {analysis.ops.map((o, i) => (
                <View key={i} style={{ flexDirection: 'row', gap: S.sm, alignItems: 'center' }}>
                  <Check color={C.money} size={14} />
                  <Text style={[T.micro, { flex: 1, fontSize: 12 }]}>{o}</Text>
                </View>
              ))}
            </Card>
          </Section>
        ) : null}

        {/* ── 3. editable details ──────────────────────────────────────── */}
        {analysis ? (
          <Section n={3} title={t('create.step.info')} subtitle={t('create.infoSub')} state={st(2)}>
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
              <Btn label={t('create.writeWithAI')} tone="tonal"
                   icon={<Sparkle color={C.primaryDeep} size={20} />}
                   busy={busy === t('create.writing')} onPress={writeCopy} />
            </Card>

            <Card>
              <Field label={t('create.nameLocal')} value={titleHi} onChange={setTitleHi}
                     placeholder={t('create.namePlaceholder')} />
              <Field label="Name (English)" value={titleEn} onChange={setTitleEn}
                     confidence={conf.title} placeholder="Product title" />
              <Field label={t('create.descLocal')} value={descHi} onChange={setDescHi} multiline
                     placeholder={t('create.descPlaceholder')} />
              <Field label="Description (English)" value={descEn} onChange={setDescEn}
                     multiline confidence={conf.description} />
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
        ) : null}

        {/* ── 4. price ─────────────────────────────────────────────────── */}
        {hasInfo ? (
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
        ) : null}

        {/* ── 5. channels + preview ────────────────────────────────────── */}
        {hasPrice ? (
          <Section n={5} title="कहाँ बेचना है" subtitle={t('create.channelsSub')} state={st(4)}>
            {chans.map((c) => {
              const on = picked.includes(c.id);
              return (
                <Pressable key={c.id} onPress={() => setPicked((p) =>
                  p.includes(c.id) ? p.filter((x) => x !== c.id) : [...p, c.id])}>
                  <View style={[{ borderRadius: R.lg, padding: S.md, borderWidth: 1.5,
                                  borderColor: on ? C.money : C.line,
                                  backgroundColor: on ? C.moneySoft : C.surface,
                                  flexDirection: 'row', alignItems: 'center', gap: S.md }]}>
                    <View style={{ width: 40, height: 40, borderRadius: R.md,
                                   backgroundColor: on ? C.money : C.bgAlt,
                                   alignItems: 'center', justifyContent: 'center' }}>
                      {on ? <Check color={C.white} size={20} />
                          : <Globe color={C.inkSoft} size={18} />}
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>{c.name}</Text>
                      <Text style={[T.micro, { fontSize: 12 }]}>{c.note}</Text>
                    </View>
                    {c.configured
                      ? <Pill text={t('create.ready')} tone="good" />
                      : <Pill text={t('create.setupPending')} tone="warn" />}
                  </View>
                </Pressable>
              );
            })}
            {chans.some((c) => picked.includes(c.id) && !c.configured) ? (
              <Card tone="warn">
                <Text style={T.bodySoft}>
                  कुछ चुने हुए चैनल अभी सेटअप नहीं हैं। भेजने की कोशिश होगी और असली
                  नतीजा नीचे दिखेगा — झूठा "सफल" कभी नहीं दिखाया जाएगा।
                </Text>
              </Card>
            ) : null}

            <Card>
              <Text style={T.label}>{t('create.review')}</Text>
              <View style={{ flexDirection: 'row', gap: S.md }}>
                {analysis?.imageUrl ? (
                  <Image source={{ uri: analysis.imageUrl }}
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

            <Btn label={`${picked.length} जगह भेजिए`} tone="money" large
                 icon={<Arrow color={C.white} size={22} />}
                 busy={busy === t('create.publishing')} disabled={!picked.length}
                 onPress={doPublish} />
          </Section>
        ) : null}

        {/* ── 6. live status ───────────────────────────────────────────── */}
        {pubs.length ? (
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
                  <Pressable onPress={() => Linking.openURL(p.url)}>
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
                    <QRCode value={passport.id} size={72} color={C.indigoDeep}
                            backgroundColor={C.white} />
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
    </View>
  );
}
