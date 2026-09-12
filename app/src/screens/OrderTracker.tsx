/**
 * One order's journey, and the buttons that move it.
 *
 * The tracker is drawn from the server's event history, not from a single status
 * column. Every completed step carries the time it happened and the name of the person
 * who recorded it, because that is the difference between a record and a decoration:
 * a settlement is computed from these stages, so "who said this was delivered" has to
 * be answerable months later.
 *
 * What this screen may do is decided by the server, not here. `canAdvanceTo` holds at
 * most one stage - the next one this viewer's role is allowed to enter - and the
 * button is built from it. So an artisan sees "Product ready" and never sees
 * "Delivered", and if the app got that wrong anyway the endpoint refuses it with a
 * sentence saying whose stage it is.
 *
 * The packaging video is real evidence, captured with the phone's own camera through
 * expo-image-picker. The system camera gives record, review and retake for free and
 * is far more reliable on a cheap phone than a custom recorder; the file is then
 * streamed to the same bucket the product photographs live in. Packaging cannot be
 * marked complete without it - the server enforces that, not just this screen.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Image, ScrollView, Pressable, RefreshControl, Linking,
         ActivityIndicator, Alert } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { C, S, T, R } from '../theme';
import { Btn, Card, Pill, Row, Skeleton, Divider } from '../ui';
import { TopBar } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

function money(n: number) {
  return `₹${Math.round(n).toLocaleString('en-IN')}`;
}

// Exactly the list transit.EXCEPTIONS accepts. Kept in the same order so the buttons
// cannot offer a type the endpoint would refuse, which would be a dead button.
const EXCEPTION_KINDS = [
  'damaged', 'missing', 'quantity_mismatch', 'quality_failed', 'pickup_delayed',
  'shipment_delayed', 'wrong_product', 'address_problem', 'packaging_problem',
] as const;

function when(iso: string | null) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('en-IN',
    { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
}

/* ──────────────────────────────────────────────────────────── one timeline step */

function Step({ step, last }: { step: api.TransitStep; last: boolean }) {
  const { t } = useI18n();
  const done = step.state === 'done';
  const now = step.state === 'current';

  const dot = done ? C.money : now ? C.primary : C.line;
  const ink = done ? C.inkMid : now ? C.ink : C.inkSoft;

  return (
    <View style={{ flexDirection: 'row', gap: S.md }}>
      {/* Rail: a filled dot for done, a ring for current, hollow for upcoming. The
          shape carries the meaning as well as the colour, so it still reads for
          somebody who cannot separate the greens from the greys. */}
      <View style={{ width: 22, alignItems: 'center' }}>
        <View style={{
          width: now ? 18 : 14, height: now ? 18 : 14, borderRadius: 99,
          backgroundColor: done ? dot : now ? C.primarySoft : C.surface,
          borderWidth: now ? 3 : 1.5, borderColor: dot,
          marginTop: 3,
        }} />
        {!last ? (
          <View style={{ flex: 1, width: 2, backgroundColor: done ? C.money : C.line,
                         marginVertical: 2, minHeight: 18 }} />
        ) : null}
      </View>

      <View style={{ flex: 1, paddingBottom: last ? 0 : S.md, gap: 2 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <Text style={[T.body, {
            flex: 1,
            fontFamily: now ? 'Mukta_700Bold' : done ? 'Mukta_600SemiBold'
                                                     : 'Mukta_500Medium',
            color: ink,
          }]}>
            {step.label}
          </Text>
          {now ? <Pill text={t('track.now')} /> : null}
        </View>

        {step.at ? (
          <Text style={[T.micro, { fontSize: 12 }]}>
            {when(step.at)}{step.by ? ` · ${step.by}` : ''}
          </Text>
        ) : step.state === 'upcoming' ? null : (
          // Past but never recorded. Saying so is better than inventing a timestamp
          // for a step whose event we do not have.
          <Text style={[T.micro, { fontSize: 12, fontStyle: 'italic' }]}>
            {t('track.noRecord')}
          </Text>
        )}

        {step.note ? (
          <Text style={[T.micro, { fontSize: 12, color: C.inkMid }]}>{step.note}</Text>
        ) : null}

        {step.mediaUrl ? (
          <Pressable onPress={() => Linking.openURL(api.localise(step.mediaUrl))} hitSlop={6}>
            <Text style={[T.micro, { fontSize: 12.5, color: C.indigoDeep,
                                     fontFamily: 'Mukta_600SemiBold' }]}>
              {t('track.viewProof')}
            </Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

/* ───────────────────────────────────────────────────────────────── the screen */

export default function OrderTracker({
  orderId, onBack,
}: { orderId: string; onBack: () => void }) {
  const { t } = useI18n();
  const [tl, setTl] = useState<api.Timeline | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [acting, setActing] = useState('');
  const [upPct, setUpPct] = useState(-1);
  const [flagging, setFlagging] = useState(false);
  const [flagBusy, setFlagBusy] = useState('');
  const alive = useRef(true);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setBusy(true);
    try {
      const r = await api.timeline(orderId);
      if (alive.current) { setTl(r); setErr(''); }
    } catch (e: any) {
      if (alive.current) setErr(e?.message || 'Could not load this order.');
    } finally {
      if (alive.current && !quiet) setBusy(false);
    }
  }, [orderId]);

  useEffect(() => {
    alive.current = true;
    void load();
    // Polling rather than sockets. The backend has no realtime channel, and inventing
    // one for this would be a large change to claim a small benefit. Thirty seconds is
    // enough for a parcel: the stages move in minutes and hours, not milliseconds, and
    // pull-to-refresh covers the impatient case.
    const timer = setInterval(() => void load(true), 30000);
    return () => { alive.current = false; clearInterval(timer); };
  }, [load]);

  async function advance(stage: string) {
    setActing(stage);
    try {
      const r = await api.advanceOrder(orderId, stage);
      setTl(r.timeline);
    } catch (e: any) {
      Alert.alert(t('track.cannot'), e?.message || 'That did not go through.');
    } finally {
      setActing('');
    }
  }

  /**
   * Flag a problem without moving the order.
   *
   * An exception is not a stage: a damaged parcel at the collection point is still at
   * the collection point, and advancing it would hide the problem behind progress. So
   * it is recorded alongside the timeline and shown above it, where somebody will
   * actually see it.
   */
  async function flag(kind: string) {
    setFlagBusy(kind);
    try {
      const r = await api.orderException(orderId, kind);
      setTl(r.timeline);
      setFlagging(false);
    } catch (e: any) {
      Alert.alert(t('track.cannot'), e?.message || 'That did not go through.');
    } finally {
      setFlagBusy('');
    }
  }

  /** Record with the phone's camera, then stream the file up. */
  async function recordPackaging() {
    try {
      const perm = await ImagePicker.requestCameraPermissionsAsync();
      if (!perm.granted) {
        Alert.alert(t('track.cameraNeeded'), t('track.cameraWhy'));
        return;
      }
      const shot = await ImagePicker.launchCameraAsync({
        mediaTypes: ['videos'],
        // Short on purpose. This is proof the parcel was sealed, not a film, and the
        // artisan is often paying for the megabytes.
        videoMaxDuration: 30,
        quality: 0.7,
      });
      if (shot.canceled || !shot.assets?.length) return;
      const asset = shot.assets[0];

      setUpPct(0);
      const out = await api.uploadPackagingVideo(orderId, asset.uri, {
        seconds: (asset.duration || 0) / 1000,
      });
      setTl(out.timeline);
      setUpPct(-1);
      Alert.alert(t('track.proofAdded'), t('track.proofAddedBody'));
    } catch (e: any) {
      setUpPct(-1);
      // The upload is the whole point of the step, so a failure has to be loud. A
      // silent one would let the artisan believe the evidence is attached when the
      // server has nothing, and packaging would then refuse to complete with no
      // explanation she could connect to this.
      Alert.alert(t('track.proofFailed'), e?.message || 'The upload did not finish.');
    }
  }

  if (err && !tl) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg }}>
        <TopBar title={t('track.title')} onBack={onBack} />
        <View style={{ padding: S.lg }}>
          <Card tone="danger">
            <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
              {t('track.errTitle')}
            </Text>
            <Text style={T.bodySoft}>{err}</Text>
            <Btn label={t('market.retry')} tone="tonal" onPress={() => load()} />
          </Card>
        </View>
      </View>
    );
  }

  if (!tl) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg }}>
        <TopBar title={t('track.title')} onBack={onBack} />
        <View style={{ padding: S.lg, gap: S.md }}>
          <Skeleton h={80} /><Skeleton h={22} w="60%" />
          <Skeleton h={200} />
        </View>
      </View>
    );
  }

  const next = tl.canAdvanceTo[0] || '';
  const nextLabel = tl.steps.find((s) => s.key === next)?.label || '';
  const inPackaging = tl.stage === 'packaging';
  const canRecord = tl.viewerRole === 'artisan' || tl.viewerRole === 'solo';

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar title={t('track.title')} subtitle={tl.orderId} onBack={onBack} />
      <ScrollView
        contentContainerStyle={{ padding: S.lg, paddingBottom: S.xl * 2, gap: S.md }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={busy} onRefresh={() => load()}
                                        tintColor={C.primary} />}
      >
        {/* What it is */}
        <Card>
          <View style={{ flexDirection: 'row', gap: S.md, alignItems: 'center' }}>
            {tl.product.imageUrl ? (
              <Image source={{ uri: tl.product.imageUrl }}
                     style={{ width: 62, height: 62, borderRadius: R.md,
                              backgroundColor: C.bgAlt }} />
            ) : null}
            <View style={{ flex: 1, gap: 2 }}>
              <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}
                    numberOfLines={2}>
                {tl.product.title || t('track.product')}
              </Text>
              <Text style={T.micro}>
                {t('track.qty', { n: tl.quantity })} · {money(tl.amount)}
              </Text>
            </View>
          </View>
          <Divider />
          {tl.artisan.name ? (
            <Row label={t('track.artisan')} value={tl.artisan.name} />
          ) : null}
          {tl.buyerName ? (
            <Row label={t('track.buyer')} value={tl.buyerName} />
          ) : null}
          <Row label={t('track.yourRole')} value={t(`track.role.${tl.viewerRole}`)} />
        </Card>

        {/* Where it is */}
        <Card tone={tl.cancelled ? 'danger' : 'soft'}>
          <Text style={T.label}>{t('track.current')}</Text>
          <Text style={[T.title, { fontSize: 21 }]}>{tl.stageLabel}</Text>
          {tl.updatedAt ? (
            <Text style={T.micro}>{t('track.updated')} {when(tl.updatedAt)}</Text>
          ) : null}
        </Card>

        {/* Exceptions come before the tracker: a damaged parcel is the most important
            thing on this screen and must not be below a fold. */}
        {tl.exceptions.length ? (
          <Card tone="warn">
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
              {t('track.problems', { n: tl.exceptions.length })}
            </Text>
            {tl.exceptions.map((x, i) => (
              <View key={i} style={{ gap: 2 }}>
                <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
                  {x.type.replace(/_/g, ' ')}
                </Text>
                {x.description ? (
                  <Text style={T.bodySoft}>{x.description}</Text>
                ) : null}
                <Text style={T.micro}>{when(x.at)}{x.by ? ` · ${x.by}` : ''}</Text>
              </View>
            ))}
          </Card>
        ) : null}

        {/* Packaging proof */}
        {tl.packagingVideoUrl ? (
          <Card tone="money">
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
              {t('track.proofOnFile')}
            </Text>
            <Text style={T.bodySoft}>{t('track.proofOnFileBody')}</Text>
            <Btn label={t('track.watch')} tone="indigo"
                 onPress={() => Linking.openURL(api.localise(tl.packagingVideoUrl))} />
            {canRecord ? (
              <Btn label={t('track.retake')} tone="ghost" onPress={recordPackaging} />
            ) : null}
          </Card>
        ) : canRecord && inPackaging ? (
          <Card tone="warn">
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
              {t('track.proofNeeded')}
            </Text>
            <Text style={T.bodySoft}>{t('track.proofNeededBody')}</Text>
            {upPct >= 0 ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                <ActivityIndicator color={C.primaryDeep} />
                <Text style={T.bodySoft}>{t('track.uploading')}</Text>
              </View>
            ) : (
              <Btn label={t('track.record')} tone="primary" large
                   onPress={recordPackaging} />
            )}
          </Card>
        ) : null}

        {/* The one thing this role may do next */}
        {next ? (
          <Btn
            label={t('track.moveTo', { stage: nextLabel })}
            tone="primary"
            large
            busy={acting === next}
            onPress={() => advance(next)}
          />
        ) : tl.viewerRole === 'guest' ? (
          <Card tone="soft">
            <Text style={T.bodySoft}>{t('track.readOnly')}</Text>
          </Card>
        ) : null}

        {/* Flagging a problem. Only offered to somebody who is a party to the order -
            the server refuses a guest anyway, and showing a button that always fails
            would be worse than not showing it. */}
        {tl.viewerRole !== 'guest' ? (
          flagging ? (
            <Card tone="warn">
              <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                {t('track.whatWrong')}
              </Text>
              {EXCEPTION_KINDS.map((k) => (
                <Btn key={k} label={t(`track.exc.${k}`)} tone="ghost"
                     busy={flagBusy === k} onPress={() => flag(k)} />
              ))}
              <Btn label={t('common.cancel')} tone="ghost"
                   onPress={() => setFlagging(false)} />
            </Card>
          ) : (
            <Btn label={t('track.reportProblem')} tone="ghost"
                 onPress={() => setFlagging(true)} />
          )
        ) : null}

        {/* The journey */}
        <Card>
          <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
            {t('track.journey')}
          </Text>
          <View style={{ marginTop: S.sm }}>
            {tl.steps.map((s, i) => (
              <Step key={s.key} step={s} last={i === tl.steps.length - 1} />
            ))}
          </View>
        </Card>

        {/* Everything that ever happened, newest last, nothing overwritten */}
        {tl.history.length ? (
          <Card tone="plain">
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
              {t('track.history')}
            </Text>
            {tl.history.map((h, i) => (
              <View key={i} style={{ gap: 1, paddingVertical: 3 }}>
                <Text style={[T.micro, { fontSize: 12.5, color: C.inkMid }]}>
                  {when(h.at)} · {h.detail || h.to || h.kind}
                  {h.by ? ` · ${h.by}` : ''}
                  {h.role ? ` (${h.role})` : ''}
                </Text>
              </View>
            ))}
          </Card>
        ) : null}
      </ScrollView>
    </View>
  );
}
