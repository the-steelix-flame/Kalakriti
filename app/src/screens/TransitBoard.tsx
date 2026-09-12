/**
 * Transit operations: how many orders are sitting at each stage, and which ones.
 *
 * The point of this screen is finding the pile-up. Eighteen stages in a row of counts
 * makes "four orders stuck waiting for pickup" visible in a way a flat order list never
 * does, and every count opens exactly those orders rather than a filtered guess.
 *
 * Stages with nothing in them are still shown, greyed. Hiding them would make the
 * journey look shorter than it is and would move the remaining tiles around every time
 * a parcel advanced, which is exactly the wrong behaviour for a screen somebody is
 * scanning for a number that changed.
 *
 * Scoped by the server to orders this person is answerable for. There is no global
 * board, because somebody else's order book is not theirs to read.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Image, ScrollView, Pressable, RefreshControl } from 'react-native';
import { C, S, T, R, shadow } from '../theme';
import { Btn, Card, Pill, Skeleton } from '../ui';
import { TopBar } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

function money(n: number) {
  return `₹${Math.round(n).toLocaleString('en-IN')}`;
}

/** Who owns the stage, which is also how urgent it is to the person looking. */
const TONE: Record<string, { bg: string; ink: string }> = {
  artisan: { bg: C.primarySoft, ink: C.primaryDeep },
  ops: { bg: C.indigoSoft, ink: C.indigoDeep },
  system: { bg: C.bgAlt, ink: C.inkMid },
};

export default function TransitBoard({
  onBack, onTrack,
}: { onBack: () => void; onTrack: (orderId: string) => void }) {
  const { t } = useI18n();
  const [data, setData] = useState<Awaited<
    ReturnType<typeof api.transitBoard>> | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState('');

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setData(await api.transitBoard());
      setErr('');
    } catch (e: any) {
      setErr(e?.message || 'Could not load the board.');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const shown = data
    ? (stage ? data.orders.filter((o) => o.stage === stage) : data.orders)
    : [];

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar
        title={t('board.title')}
        subtitle={data ? t('board.sub', { n: data.total }) : undefined}
        onBack={onBack}
      />
      <ScrollView
        contentContainerStyle={{ padding: S.lg, paddingBottom: S.xl * 2, gap: S.md }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={busy} onRefresh={load}
                                        tintColor={C.primary} />}
      >
        {err ? (
          <Card tone="danger">
            <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
              {t('board.errTitle')}
            </Text>
            <Text style={T.bodySoft}>{err}</Text>
            <Btn label={t('market.retry')} tone="tonal" onPress={load} />
          </Card>
        ) : null}

        {!data && !err ? (
          <><Skeleton h={120} r={14} /><Skeleton h={80} r={14} /></>
        ) : null}

        {data ? (
          <>
            <Text style={T.section}>{t('board.byStage')}</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: S.sm }}>
              {data.stages.map((s) => {
                const on = stage === s.key;
                const empty = s.count === 0;
                const tone = TONE[s.actor] || TONE.system;
                return (
                  <Pressable
                    key={s.key}
                    onPress={() => setStage(on ? '' : s.key)}
                    accessibilityRole="button"
                    accessibilityState={{ selected: on }}
                    accessibilityLabel={`${s.label}: ${s.count}`}
                    style={({ pressed }) => ({
                      flexGrow: 1, flexBasis: '30%', minWidth: 100,
                      opacity: pressed ? 0.7 : 1,
                    })}
                  >
                    <View style={[{
                      borderRadius: R.lg, padding: S.md, minHeight: 70,
                      justifyContent: 'center', gap: 2,
                      backgroundColor: empty ? C.surface : tone.bg,
                      borderWidth: on ? 2 : 1,
                      borderColor: on ? C.primary : C.line,
                    }, empty ? null : shadow(1)]}>
                      <Text style={[T.title, {
                        fontSize: 21,
                        color: empty ? C.inkSoft : tone.ink,
                      }]}>
                        {s.count}
                      </Text>
                      <Text style={[T.micro, { fontSize: 11,
                                               color: empty ? C.inkSoft : C.inkMid }]}
                            numberOfLines={2}>
                        {s.label}
                      </Text>
                    </View>
                  </Pressable>
                );
              })}
            </View>

            {data.cancelled ? (
              <Pill text={t('board.cancelled', { n: data.cancelled })} tone="danger" />
            ) : null}

            <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
              <Text style={[T.section, { flex: 1 }]}>
                {stage
                  ? t('board.atStage', {
                      stage: data.stages.find((s) => s.key === stage)?.label || stage,
                    })
                  : t('board.allOrders')}
              </Text>
              {stage ? (
                <Pressable onPress={() => setStage('')} hitSlop={8}>
                  <Text style={[T.micro, { color: C.primaryDeep,
                                           fontFamily: 'Mukta_600SemiBold' }]}>
                    {t('board.clear')}
                  </Text>
                </Pressable>
              ) : null}
            </View>

            {shown.length === 0 ? (
              <Card tone="soft">
                <Text style={T.bodySoft}>
                  {stage ? t('board.noneAtStage') : t('board.noOrders')}
                </Text>
              </Card>
            ) : shown.map((o) => (
              <Card key={o.orderId} onPress={() => onTrack(o.orderId)}>
                <View style={{ flexDirection: 'row', gap: S.md,
                               alignItems: 'center' }}>
                  {o.imageUrl ? (
                    <Image source={{ uri: o.imageUrl }}
                           style={{ width: 48, height: 48, borderRadius: R.md,
                                    backgroundColor: C.bgAlt }} />
                  ) : null}
                  <View style={{ flex: 1, gap: 2 }}>
                    <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}
                          numberOfLines={1}>
                      {o.title || o.orderId}
                    </Text>
                    <Text style={T.micro}>
                      {o.quantity} · {money(o.amount)}
                      {o.buyerName ? ` · ${o.buyerName}` : ''}
                    </Text>
                  </View>
                  <Pill text={o.stageLabel} />
                </View>
              </Card>
            ))}
          </>
        ) : null}
      </ScrollView>
    </View>
  );
}
