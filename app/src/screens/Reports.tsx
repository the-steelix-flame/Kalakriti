/**
 * Reports: money that arrived, and money that is owed. Kept apart.
 *
 * There are no projections here and no growth percentages, deliberately. There is not
 * enough history in this system for either to mean anything, and a made-up trend line
 * is the fastest way to lose an artisan's trust in every other number on the screen.
 *
 * Revenue counts paid orders only. An unpaid order is a hope, and adding hopes to a
 * revenue figure is where a dashboard starts lying. Unpaid ones are counted on their
 * own line so the difference is visible rather than hidden.
 *
 * Payouts come from settlements, which only exist once a cluster has actually divided
 * a paid order between its members. When none exist the screen says so instead of
 * showing zero, because "nothing owed" and "never worked out" are different answers.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Image, RefreshControl } from 'react-native';
import { C, S, T, R } from '../theme';
import { Btn, Card, Divider, Pill, Row, Skeleton, StatusChip, money } from '../ui';
import { MetricGrid, MetricTile, Metric } from '../ui/Metric';
import { TabScreen } from '../nav/Shell';
import { useI18n } from '../i18n';
import { ago } from '../lib/ago';
import * as api from '../lib/api';

/** A plain number wrapped in the shape ui/Metric.tsx expects, for a figure that is
 *  always known here - Reports never shows "not computed" for its own arithmetic,
 *  only for payouts before any settlement exists, which is handled separately. */
function known(value: number): Metric {
  return { value, available: true, why: '', source: '' };
}

export default function Reports({
  onLanguage, onOrders,
}: { onLanguage: () => void; onOrders: () => void }) {
  const { t } = useI18n();
  const [data, setData] = useState<api.Reports | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setData(await api.reports());
      setErr('');
    } catch (e: any) {
      setErr(e?.message || 'Could not load the reports.');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const channels = data ? Object.entries(data.revenue.byChannel) : [];

  return (
    <TabScreen
      title={t('rep.title')}
      subtitle={t('rep.sub')}
      onLanguage={onLanguage}
      refreshControl={<RefreshControl refreshing={busy} onRefresh={load}
                                      tintColor={C.primary} />}
    >
      {err ? (
        <Card tone="danger">
          <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
            {t('rep.errTitle')}
          </Text>
          <Text style={T.bodySoft}>{err}</Text>
          <Btn label={t('market.retry')} tone="tonal" onPress={load} />
        </Card>
      ) : null}

      {!data && !err ? (
        <><Skeleton h={120} r={14} /><Skeleton h={120} r={14} /></>
      ) : null}

      {data ? (
        <>
          {/* ── money that arrived ─────────────────────────────────────────
              MetricGrid/MetricTile rather than a hand-built Card+Row: it is the
              same component MarketplaceDetail uses for its per-channel figures, so a
              number reads the same way - same size, same "why" affordance - wherever
              it appears in the app instead of Reports inventing its own look. */}
          <Text style={T.section}>{t('rep.revenue')}</Text>
          <MetricGrid>
            <MetricTile label={t('rep.revenue')} metric={known(data.revenue.gross)}
                        currency />
            <MetricTile label={t('rep.fromPaid', { n: data.revenue.paidOrders })}
                        metric={known(data.revenue.paidOrders)} />
            <MetricTile label={t('rep.unpaid')}
                        metric={known(data.revenue.unpaidOrders)} />
          </MetricGrid>
          {data.revenue.why ? (
            <Card tone="soft"><Text style={T.bodySoft}>{data.revenue.why}</Text></Card>
          ) : data.revenue.unpaidOrders ? (
            <Text style={[T.micro, { fontSize: 11.5 }]}>{t('rep.unpaidWhy')}</Text>
          ) : null}

          {channels.length ? (
            <Card>
              <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                {t('rep.byChannel')}
              </Text>
              {channels.map(([ch, row]) => (
                <Row key={ch} label={ch} value={money(row.amount)}
                     sub={t('rep.nOrders', { n: row.orders })} />
              ))}
            </Card>
          ) : null}

          {/* ── money that is owed ─────────────────────────────────────── */}
          <Card tone={data.payouts.available ? 'plain' : 'soft'}>
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
              {t('rep.payouts')}
            </Text>
            {data.payouts.available ? (
              <>
                <Row label={t('rep.pending')} value={money(data.payouts.pending)} />
                <Row label={t('rep.paidOut')} value={money(data.payouts.paid)} />
                <Row label={t('rep.settlements')}
                     value={String(data.payouts.settlements)} />
                <Divider />
                {data.payouts.rows.slice(0, 8).map((r) => (
                  <Row key={r.id}
                       label={`${r.orderId || r.id}`}
                       sub={`${t('rep.gross')} ${money(r.gross)} · `
                            + `${t('rep.commission')} ${money(r.commission)}`}
                       value={money(r.distributable)} />
                ))}
              </>
            ) : (
              <Text style={T.bodySoft}>{data.payouts.why}</Text>
            )}
          </Card>

          {/* ── what is on sale ────────────────────────────────────────── */}
          <Card>
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
              {t('rep.products')}
            </Text>
            <Row label={t('rep.live')} value={String(data.products.live)} />
            <Row label={t('rep.total')} value={String(data.products.total)} />
          </Card>

          {/* ── recent orders, drawn exactly like the Orders tab draws them ──────
              Same StatusChip, same status vocabulary (transit's stage keys), so an
              order compared side by side on the two screens looks like the same
              order rather than two screens that quietly disagree about it. */}
          {data.recentOrders.length ? (
            <>
              <Text style={T.section}>{t('rep.recentOrders')}</Text>
              {data.recentOrders.map((o) => (
                <Card key={o.id}>
                  <View style={{ flexDirection: 'row', alignItems: 'center',
                                gap: S.sm }}>
                    {o.imageUrl ? (
                      <Image source={{ uri: o.imageUrl }}
                             style={{ width: 40, height: 40, borderRadius: R.md,
                                      backgroundColor: C.bgAlt }} />
                    ) : null}
                    <View style={{ flex: 1 }}>
                      <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}
                            numberOfLines={1}>
                        {o.buyerName || t('ord.buyer')}
                      </Text>
                      <Text style={[T.micro, { fontSize: 12 }]}>
                        {o.title || o.id} · {ago(t, o.createdAt)}
                      </Text>
                    </View>
                    <StatusChip status={o.status} />
                  </View>
                  <Row label={t('ord.amount')} value={money(o.amount)} />
                </Card>
              ))}
            </>
          ) : null}

          <Btn label={t('rep.seeOrders')} tone="indigo" onPress={onOrders} />

          <Pill text={t('rep.noProjections')} />
        </>
      ) : null}
    </TabScreen>
  );
}
