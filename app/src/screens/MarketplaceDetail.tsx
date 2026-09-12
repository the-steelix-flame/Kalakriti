/**
 * One product on one marketplace.
 *
 * This is the screen where honesty costs something, so it is worth being explicit
 * about the rule: every figure below either came from that platform's API just now,
 * or is shown as a dash with the platform's reason. Nothing is estimated.
 *
 * That means this screen is often mostly dashes, and it should be. Amazon gives a
 * seller application no per-listing view count without Brand Registry; ONDC has no
 * concept of a view at all; Shopify keeps traffic behind an analytics scope custom
 * apps rarely get. An artisan deciding whether to keep paying for a channel deserves
 * to know that the channel tells her nothing, rather than being handed a number
 * somebody invented to fill the space.
 *
 * The one place with full figures is her own storefront, because we serve that page
 * and count the requests ourselves.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, Pressable, Linking, ScrollView, ActivityIndicator, RefreshControl,
} from 'react-native';
import { Card, Pill, Btn, Row, Divider, StatusChip, money } from '../ui';
import { MetricTile, MetricGrid } from '../ui/Metric';
import { C, S, T, R } from '../theme';
import { Globe, Warning, Rupee, Shield } from '../icons';
import { TopBar } from '../nav/Shell';
import { useI18n } from '../i18n';
import { ago } from '../lib/ago';
import * as api from '../lib/api';

export default function MarketplaceDetail({
  listingId, channel, onBack, onOpenOrder,
}: {
  listingId: string;
  channel: string;
  onBack: () => void;
  onOpenOrder?: (orderId: string) => void;
}) {
  const { t } = useI18n();
  const [d, setD] = useState<{ marketplace: api.MarketplaceRow; orders: api.Order[];
                               events: any[] } | null>(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const fresh = await api.marketplaceDetail(listingId, channel);
      setD(fresh);
      setCheckedAt(Date.now());
      setErr(null);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [listingId, channel]);

  useEffect(() => { load(); }, [load]);

  const name = d?.marketplace.channelName || channel;

  if (!d) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg }}>
        <TopBar title={t('mp.title', { name })} onBack={onBack} />
        <View style={{ padding: S.lg, gap: S.md, alignItems: 'center' }}>
          {err ? (
            <Card tone="danger" style={{ alignSelf: 'stretch' }}>
              <Text style={T.body}>{err}</Text>
              <Btn label={t('common.retry')} tone="tonal" onPress={load} />
            </Card>
          ) : (
            <>
              <ActivityIndicator color={C.primary} />
              <Text style={T.bodySoft}>{t('mp.fetchingLive', { name })}</Text>
            </>
          )}
        </View>
      </View>
    );
  }

  const m = d.marketplace;
  const st = m.stats;
  // Whether this platform shares buyer identity at all. Our own storefront does,
  // because the buyer filled in our own form; a third party generally does not.
  const buyersKnown = d.orders.some((o) => o.buyerName || o.buyerPhone);

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar
        title={t('mp.title', { name })}
        subtitle={checkedAt ? t('mp.lastSync') + ' · ' + ago(t, checkedAt) : undefined}
        onBack={onBack}
      />
      <ScrollView
        contentContainerStyle={{ padding: S.lg, gap: S.md, paddingBottom: 60 }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={busy} onRefresh={load}
                                        tintColor={C.primary} />}
      >
        <Card>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
            <Globe color={C.indigo} size={20} />
            <Text style={[T.section, { flex: 1 }]}>{name}</Text>
            <StatusChip status={m.status} />
          </View>
          <Divider />
          {m.externalId ? <Row label={t('mp.listingId')} value={m.externalId} /> : null}
          {m.submittedAt ? <Row label={t('mp.created')} value={ago(t, m.submittedAt)} /> : null}
          {m.url ? (
            <Pressable onPress={() => Linking.openURL(api.localise(m.url))}
                       accessibilityLabel={t('mp.openPage')}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6,
                             paddingVertical: 8 }}>
                <Globe color={C.indigo} size={16} />
                <Text style={[T.micro, { flex: 1, color: C.indigo,
                                         textDecorationLine: 'underline' }]}
                      numberOfLines={2}>{m.url}</Text>
              </View>
            </Pressable>
          ) : null}
        </Card>

        {/* The platform's own complaint, verbatim. Not softened, because the exact
            wording is what tells a developer or a support desk what to fix. */}
        {m.error ? (
          <Card tone="danger">
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
              <Warning color={C.danger} size={18} />
              <Text style={[T.label, { color: C.danger }]}>
                {t('mp.problem', { name })}
              </Text>
            </View>
            <Text style={T.bodySoft}>{m.error}</Text>
          </Card>
        ) : null}

        <Text style={T.section}>{t('mp.figures', { name })}</Text>
        <MetricGrid>
          <MetricTile label={t('mp.metricViews')} metric={st?.views} />
          <MetricTile label={t('mp.metricWatchers')} metric={st?.watchers} />
          <MetricTile label={t('mp.metricSold')} metric={st?.sold} />
          <MetricTile label={t('mp.metricOrders')} metric={st?.orders} />
          <MetricTile label={t('mp.metricInventory')} metric={st?.inventory} />
          <MetricTile label={t('mp.metricRevenue')} metric={st?.revenue} currency />
        </MetricGrid>

        {/* ── orders on this channel ─────────────────────────────────────── */}
        <Text style={T.section}>{t('mp.buyers')}</Text>
        {d.orders.length === 0 ? (
          <Card>
            <Text style={T.bodySoft}>
              {st?.orders?.available === false
                ? t('mp.noBuyerData', { name })
                : t('pd.noOrders')}
            </Text>
          </Card>
        ) : d.orders.map((o) => (
          <Card key={o.id} onPress={onOpenOrder ? () => onOpenOrder(o.id) : undefined}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
              <View style={{ flex: 1 }}>
                <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                  {o.buyerName || t('ord.orderId') + ' ' + o.id.slice(-6)}
                </Text>
                <Text style={[T.micro, { fontSize: 12 }]}>
                  {ago(t, o.createdAt)} · {t('ord.quantity')} {o.quantity}
                </Text>
              </View>
              <Text style={[T.body, { color: C.money, fontFamily: 'Mukta_700Bold' }]}>
                {money(o.amount)}
              </Text>
            </View>
            <View style={{ flexDirection: 'row', gap: 5, flexWrap: 'wrap' }}>
              <StatusChip status={o.status} />
              <StatusChip status={o.paymentStatus} />
              {o.courier ? <Pill text={o.courier} tone="indigo" /> : null}
            </View>
            {o.buyerPhone ? (
              <Row label={t('ord.buyer')} value={`${o.buyerName} · ${o.buyerPhone}`} />
            ) : null}
            {o.trackingUrl ? (
              <Pressable onPress={() => Linking.openURL(o.trackingUrl)}>
                <Text style={[T.micro, { color: C.indigo,
                                         textDecorationLine: 'underline' }]}>
                  {o.trackingId || o.trackingUrl}
                </Text>
              </Pressable>
            ) : null}
          </Card>
        ))}

        {!buyersKnown && d.orders.length ? (
          <Card tone="soft">
            <View style={{ flexDirection: 'row', gap: S.sm }}>
              <Shield color={C.indigo} size={16} />
              <Text style={[T.micro, { flex: 1 }]}>{t('mp.noBuyerData', { name })}</Text>
            </View>
          </Card>
        ) : null}

        {d.events.length ? (
          <>
            <Text style={T.section}>{t('pd.history')}</Text>
            <Card>
              {d.events.slice(0, 10).map((e, i) => (
                <View key={e.id ?? i} style={{ flexDirection: 'row', gap: S.sm,
                                               paddingVertical: 5 }}>
                  <Text style={[T.micro, { fontSize: 11.5, width: 74 }]}>
                    {ago(t, e.at)}
                  </Text>
                  <Text style={[T.micro, { flex: 1, fontSize: 12.5, color: C.inkMid }]}>
                    {e.detail || e.kind}
                  </Text>
                </View>
              ))}
            </Card>
          </>
        ) : null}
      </ScrollView>
    </View>
  );
}
