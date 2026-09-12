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
import { View, Text, RefreshControl } from 'react-native';
import { C, S, T } from '../theme';
import { Btn, Card, Divider, Pill, Row, Skeleton } from '../ui';
import { TabScreen } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

function money(n: number) {
  return `₹${Math.round(n).toLocaleString('en-IN')}`;
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
          {/* ── money that arrived ─────────────────────────────────────── */}
          <Card tone="money">
            <Text style={T.label}>{t('rep.revenue')}</Text>
            <Text style={[T.title, { fontSize: 28, color: C.moneyDeep }]}>
              {money(data.revenue.gross)}
            </Text>
            <Text style={T.micro}>
              {t('rep.fromPaid', { n: data.revenue.paidOrders })}
            </Text>
            {data.revenue.why ? (
              <Text style={T.bodySoft}>{data.revenue.why}</Text>
            ) : null}
            {data.revenue.unpaidOrders ? (
              <>
                <Divider />
                <Row label={t('rep.unpaid')}
                     value={String(data.revenue.unpaidOrders)} />
                <Text style={[T.micro, { fontSize: 11.5 }]}>
                  {t('rep.unpaidWhy')}
                </Text>
              </>
            ) : null}
          </Card>

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

          <Btn label={t('rep.seeOrders')} tone="indigo" onPress={onOrders} />

          <Pill text={t('rep.noProjections')} />
        </>
      ) : null}
    </TabScreen>
  );
}
