/**
 * One cluster, from the side of the person running it.
 *
 *     Dashboard -> My Clusters -> this cluster -> Listings | Orders
 *
 * Two tabs, because those are genuinely the two jobs: deciding what the cluster sells,
 * and moving what it has sold. A third tab for the roster sits alongside them because
 * splitting a bulk order is impossible without knowing who has hands free, and that
 * answer belongs next to the order that needs it rather than two screens away.
 *
 * "Live on ONDC" and "Live on Amazon" are read from the publications table - a channel
 * that actually accepted the listing - rather than from the listing's own status. A
 * product can be active here and rejected there, and the operator needs to see the
 * second one. Channels with nothing live are shown with a zero rather than hidden, so
 * the absence is visible instead of looking like the section failed to load.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Image, RefreshControl, Pressable } from 'react-native';
import { C, S, T, R } from '../theme';
import { Btn, Card, Divider, Pill, Row, Skeleton } from '../ui';
import { TopBar } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';
import { ScrollView } from 'react-native';

function money(n: number) {
  return `₹${Math.round(n).toLocaleString('en-IN')}`;
}

type Pane = 'listings' | 'orders' | 'roster';

/** The channels worth naming explicitly, in the order the operator cares about. */
const NAMED_CHANNELS = ['ondc', 'amazon_karigar', 'storefront', 'gem', 'shopify'];

function channelLabel(id: string) {
  return ({
    ondc: 'ONDC',
    amazon_karigar: 'Amazon Karigar',
    storefront: 'Kalakriti storefront',
    gem: 'GeM',
    shopify: 'Shopify',
  } as Record<string, string>)[id] || id;
}

function ProductRow({ card, onOpen }: { card: api.DashCard; onOpen: () => void }) {
  const { t } = useI18n();
  return (
    <Card onPress={onOpen}>
      <View style={{ flexDirection: 'row', gap: S.md, alignItems: 'center' }}>
        {card.imageUrl ? (
          <Image source={{ uri: card.imageUrl }}
                 style={{ width: 52, height: 52, borderRadius: R.md,
                          backgroundColor: C.bgAlt }} />
        ) : null}
        <View style={{ flex: 1, gap: 2 }}>
          <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}
                numberOfLines={1}>
            {card.title || t('appr.untitled')}
          </Text>
          <Text style={T.micro}>
            {card.price ? money(card.price) : t('appr.noPrice')}
            {card.quantity ? ` · ${card.quantity}` : ''}
          </Text>
        </View>
        <Pill text={card.status} />
      </View>
    </Card>
  );
}

export default function ClusterOps({
  clusterId, onBack, onOpenProduct, onTrack,
}: {
  clusterId: string;
  onBack: () => void;
  onOpenProduct: (id: string) => void;
  onTrack: (orderId: string) => void;
}) {
  const { t } = useI18n();
  const [pane, setPane] = useState<Pane>('listings');
  const [data, setData] = useState<api.ClusterView | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setData(await api.clusterView(clusterId));
      setErr('');
    } catch (e: any) {
      setErr(e?.message || 'Could not load this cluster.');
    } finally {
      setBusy(false);
    }
  }, [clusterId]);

  useEffect(() => { void load(); }, [load]);

  const tab = (key: Pane, label: string, count: number) => {
    const on = pane === key;
    return (
      <Pressable key={key} onPress={() => setPane(key)} style={{ flex: 1 }}
                 accessibilityRole="tab" accessibilityState={{ selected: on }}>
        <View style={{
          paddingVertical: S.sm, alignItems: 'center', gap: 2,
          borderBottomWidth: 2.5,
          borderBottomColor: on ? C.primary : 'transparent',
        }}>
          <Text style={{
            fontFamily: on ? 'Mukta_700Bold' : 'Mukta_500Medium',
            fontSize: 14, color: on ? C.primary : C.inkMid,
          }}>
            {label}
          </Text>
          <Text style={[T.micro, { fontSize: 11 }]}>{count}</Text>
        </View>
      </Pressable>
    );
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar
        title={data?.cluster.name || t('cops.title')}
        subtitle={data ? [data.cluster.craftCategory, data.cluster.district]
          .filter(Boolean).join(' · ') : undefined}
        onBack={onBack}
      />

      {data ? (
        <View style={{ flexDirection: 'row', borderBottomWidth: 1,
                       borderBottomColor: C.line, backgroundColor: C.surface }}>
          {tab('listings', t('cops.listings'), data.listings.total)}
          {tab('orders', t('cops.orders'), data.orders.active.length)}
          {tab('roster', t('cops.roster'), data.roster.length)}
        </View>
      ) : null}

      <ScrollView
        contentContainerStyle={{ padding: S.lg, paddingBottom: S.xl * 2, gap: S.md }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={busy} onRefresh={load}
                                        tintColor={C.primary} />}
      >
        {err ? (
          <Card tone="danger">
            <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
              {t('cops.errTitle')}
            </Text>
            <Text style={T.bodySoft}>{err}</Text>
            <Btn label={t('market.retry')} tone="tonal" onPress={load} />
          </Card>
        ) : null}

        {!data && !err ? (
          <><Skeleton h={90} r={14} /><Skeleton h={90} r={14} /></>
        ) : null}

        {/* ── listings ──────────────────────────────────────────────────── */}
        {data && pane === 'listings' ? (
          <>
            <Card tone="warn">
              <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                {t('cops.pendingReviews')}
              </Text>
              <Text style={[T.title, { fontSize: 24 }]}>
                {data.listings.pendingReviews.length}
              </Text>
              <Text style={T.bodySoft}>{t('cops.pendingWhy')}</Text>
            </Card>
            {data.listings.pendingReviews.map((c) => (
              <ProductRow key={c.id} card={c} onOpen={() => onOpenProduct(c.id)} />
            ))}

            <Divider />
            {NAMED_CHANNELS.map((ch) => {
              const rows = data.listings.byChannel[ch] || [];
              return (
                <Card key={ch} tone={rows.length ? 'money' : 'plain'}>
                  <View style={{ flexDirection: 'row', alignItems: 'center',
                                 gap: S.sm }}>
                    <Text style={[T.body, { flex: 1,
                                            fontFamily: 'Mukta_700Bold' }]}>
                      {t('cops.liveOn', { channel: channelLabel(ch) })}
                    </Text>
                    <Pill text={String(rows.length)}
                          tone={rows.length ? 'good' : undefined} />
                  </View>
                  {rows.length
                    ? rows.slice(0, 5).map((c) => (
                        <Text key={c.id} style={T.bodySoft} numberOfLines={1}>
                          • {c.title || c.id}
                        </Text>
                      ))
                    : (
                      <Text style={[T.micro, { fontSize: 11.5 }]}>
                        {t('cops.noneLiveThere')}
                      </Text>
                    )}
                </Card>
              );
            })}
          </>
        ) : null}

        {/* ── orders ────────────────────────────────────────────────────── */}
        {data && pane === 'orders' ? (
          <>
            <Text style={T.section}>{t('cops.activeOrders')}</Text>
            {data.orders.active.length === 0 ? (
              <Card tone="soft">
                <Text style={T.bodySoft}>{t('cops.noActiveOrders')}</Text>
              </Card>
            ) : data.orders.active.map((o) => (
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

            <Divider />
            <Text style={T.section}>{t('cops.splittingTasks')}</Text>
            <Text style={T.bodySoft}>{t('cops.splittingWhy')}</Text>
            {data.orders.splittingTasks.length === 0 ? (
              <Card tone="soft">
                <Text style={T.bodySoft}>{t('cops.noSplitting')}</Text>
              </Card>
            ) : data.orders.splittingTasks.map((x) => (
              <Card key={x.id} tone="indigo">
                <View style={{ flexDirection: 'row', alignItems: 'center',
                               gap: S.sm }}>
                  <Text style={[T.body, { flex: 1,
                                          fontFamily: 'Mukta_700Bold' }]}
                        numberOfLines={1}>
                    {x.organisation || x.buyer || t('cops.aBuyer')}
                  </Text>
                  <Pill text={x.status} />
                </View>
                <Row label={t('cops.unitsWanted')} value={String(x.quantity)} />
                {x.targetPrice ? (
                  <Row label={t('cops.targetPrice')}
                       value={money(x.targetPrice)} />
                ) : null}
                {x.neededBy ? (
                  <Row label={t('cops.neededBy')} value={x.neededBy} />
                ) : null}
                {x.message ? (
                  <Text style={T.bodySoft} numberOfLines={3}>{x.message}</Text>
                ) : null}
                {/* Capacity is the thing that decides whether this can be taken at
                    all, so the answer is put right here rather than a tab away. */}
                <Text style={[T.micro, { fontSize: 11.5 }]}>
                  {t('cops.capacityNow', {
                    n: data.roster.reduce((a, m) => a + m.capacityAvailable, 0),
                  })}
                </Text>
                <Btn label={t('cops.openRoster')} tone="ghost"
                     onPress={() => setPane('roster')} />
              </Card>
            ))}
          </>
        ) : null}

        {/* ── roster ────────────────────────────────────────────────────── */}
        {data && pane === 'roster' ? (
          <>
            <Card tone="money">
              <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                {t('cops.capacityTotal')}
              </Text>
              <Text style={[T.title, { fontSize: 26 }]}>
                {data.roster.reduce((a, m) => a + m.capacityAvailable, 0)}
              </Text>
              <Text style={T.bodySoft}>{t('cops.capacityWhy')}</Text>
            </Card>
            {data.roster.length === 0 ? (
              <Card tone="soft">
                <Text style={T.bodySoft}>{t('cops.noMembers')}</Text>
              </Card>
            ) : data.roster.map((m) => (
              <Card key={m.artisanId}>
                <View style={{ flexDirection: 'row', alignItems: 'center',
                               gap: S.sm }}>
                  <Text style={[T.body, { flex: 1,
                                          fontFamily: 'Mukta_600SemiBold' }]}
                        numberOfLines={1}>
                    {m.name || m.artisanId}
                  </Text>
                  {m.role ? <Pill text={m.role} /> : null}
                </View>
                <Row label={t('cops.free')} value={String(m.capacityAvailable)}
                     sub={t('cops.ofTotal', { n: m.capacityUnits })} />
                {m.capacityCommitted ? (
                  <Row label={t('cops.committed')}
                       value={String(m.capacityCommitted)} />
                ) : null}
              </Card>
            ))}
          </>
        ) : null}
      </ScrollView>
    </View>
  );
}
