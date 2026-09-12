/**
 * The operations dashboard: five headline numbers and four shortcuts.
 *
 * This sits at the top of the first tab, above the artisan's own content rather than
 * instead of it. Both audiences use the same account - the person running a cluster is
 * usually also making things - so replacing one view with the other would take
 * something away from whichever of the two they were doing at the time.
 *
 * Every tile and every shortcut navigates. There are no decorative cards here: a
 * number somebody cannot open is a number they cannot act on, and the whole point of
 * the screen is deciding what to do next.
 *
 * A metric that the server could not compute shows a dash and its reason on tap,
 * rather than a zero. Pending payouts is the one that matters: zero owed and never
 * calculated look identical on a card, and only one of them means nothing to do.
 */
import React from 'react';
import { View, Text, Pressable } from 'react-native';
import { C, S, T, R, shadow } from '../theme';
import { Card, Pill, Skeleton } from '../ui';
import { Tag, Rupee, Users, Check, Trend, Warning } from '../icons';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

function money(n: number) {
  return `₹${Math.round(n).toLocaleString('en-IN')}`;
}

/* ─────────────────────────────────────────────────────────────── metric tile */

function Tile({
  label, metric, money: asMoney, tone, onPress,
}: {
  label: string;
  metric?: api.DashMetric;
  money?: boolean;
  tone: 'primary' | 'money' | 'indigo' | 'gold';
  onPress: () => void;
}) {
  const { t } = useI18n();
  const ink = { primary: C.primaryDeep, money: C.moneyDeep,
                indigo: C.indigoDeep, gold: '#7A5B10' }[tone];
  const bg = { primary: C.primarySoft, money: C.moneySoft,
               indigo: C.indigoSoft, gold: C.goldSoft }[tone];

  const unknown = metric ? !metric.available : false;
  const shown = !metric ? '—'
    : unknown ? '—'
    : asMoney ? money(metric.value || 0)
    : String(metric.value ?? 0);

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`${label}: ${shown}`}
      style={({ pressed }) => ({
        flexGrow: 1, flexBasis: '30%', minWidth: 104,
        opacity: pressed ? 0.72 : 1,
      })}
    >
      <View style={[{
        backgroundColor: bg, borderRadius: R.lg, padding: S.md,
        borderWidth: 1, borderColor: C.line, gap: 2, minHeight: 78,
        justifyContent: 'center',
      }, shadow(1)]}>
        <Text style={[T.title, { fontSize: asMoney ? 19 : 23, color: ink }]}
              numberOfLines={1}>
          {shown}
        </Text>
        <Text style={[T.micro, { fontSize: 11.5, color: C.inkMid }]}
              numberOfLines={2}>
          {label}
        </Text>
        {unknown ? (
          <Text style={[T.micro, { fontSize: 10.5, color: C.inkSoft }]}>
            {t('ops.notComputed')}
          </Text>
        ) : null}
      </View>
    </Pressable>
  );
}

/* ──────────────────────────────────────────────────────────── quick action */

function Action({
  label, sub, icon, onPress,
}: { label: string; sub?: string; icon: React.ReactNode; onPress: () => void }) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => ({ flexGrow: 1, flexBasis: '44%', minWidth: 150,
                                 opacity: pressed ? 0.72 : 1 })}
    >
      <View style={[{
        backgroundColor: C.surface, borderRadius: R.lg, padding: S.md,
        borderWidth: 1, borderColor: C.line, gap: 6, minHeight: 84,
      }, shadow(1)]}>
        <View style={{ width: 34, height: 34, borderRadius: R.md,
                       backgroundColor: C.bgAlt, alignItems: 'center',
                       justifyContent: 'center' }}>
          {icon}
        </View>
        <Text style={[T.body, { fontFamily: 'Mukta_700Bold', fontSize: 14 }]}
              numberOfLines={2}>
          {label}
        </Text>
        {sub ? (
          <Text style={[T.micro, { fontSize: 11 }]} numberOfLines={1}>{sub}</Text>
        ) : null}
      </View>
    </Pressable>
  );
}

/* ───────────────────────────────────────────────────────────────── the block */

export default function OpsHeader({
  metrics, loading, onPendingReviews, onLiveProducts, onActiveOrders,
  onPayouts, onRevenue, onBulkSplitting, onRoster, onClusters,
}: {
  metrics: api.DashboardMetrics | null;
  loading: boolean;
  onPendingReviews: () => void;
  onLiveProducts: () => void;
  onActiveOrders: () => void;
  onPayouts: () => void;
  onRevenue: () => void;
  onBulkSplitting: () => void;
  onRoster: () => void;
  onClusters: () => void;
}) {
  const { t } = useI18n();

  if (loading && !metrics) {
    return (
      <View style={{ gap: S.sm }}>
        <Text style={T.section}>{t('ops.overview')}</Text>
        <View style={{ flexDirection: 'row', gap: S.sm }}>
          <Skeleton h={78} r={14} /><Skeleton h={78} r={14} /><Skeleton h={78} r={14} />
        </View>
      </View>
    );
  }

  return (
    <View style={{ gap: S.sm }}>
      <Text style={T.section}>{t('ops.overview')}</Text>

      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: S.sm }}>
        <Tile label={t('ops.pendingReviews')} metric={metrics?.pendingReviews}
              tone="gold" onPress={onPendingReviews} />
        <Tile label={t('ops.liveProducts')} metric={metrics?.liveProducts}
              tone="money" onPress={onLiveProducts} />
        <Tile label={t('ops.activeOrders')} metric={metrics?.activeOrders}
              tone="indigo" onPress={onActiveOrders} />
        <Tile label={t('ops.pendingPayouts')} metric={metrics?.pendingPayouts}
              money tone="primary" onPress={onPayouts} />
        <Tile label={t('ops.totalRevenue')} metric={metrics?.totalRevenue}
              money tone="money" onPress={onRevenue} />
      </View>

      <Text style={[T.section, { marginTop: S.sm }]}>{t('ops.quickActions')}</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: S.sm }}>
        <Action label={t('ops.bulkSplitting')} sub={t('ops.bulkSplittingSub')}
                icon={<Users color={C.indigo} size={19} />}
                onPress={onBulkSplitting} />
        <Action label={t('ops.roster')} sub={t('ops.rosterSub')}
                icon={<Check color={C.money} size={19} />}
                onPress={onRoster} />
        <Action label={t('ops.myClusters')} sub={t('ops.myClustersSub')}
                icon={<Tag color={C.primary} size={19} />}
                onPress={onClusters} />
        <Action label={t('ops.listingReviews')} sub={t('ops.listingReviewsSub')}
                icon={<Warning color={C.gold} size={19} />}
                onPress={onPendingReviews} />
      </View>

      {/* Said once, here, rather than on every tile: the numbers above are what this
          person is answerable for, which is not the same as everything in the app. */}
      {metrics?.isOperator ? (
        <Pill text={t('ops.scopedOperator', { n: metrics.clustersOwned })} />
      ) : metrics ? (
        <Pill text={t('ops.scopedMaker')} />
      ) : null}
    </View>
  );
}
