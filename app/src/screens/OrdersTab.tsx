import React, { useMemo, useState } from 'react';
import { View, Text, Pressable, Linking } from 'react-native';
import { Card, Btn, Pill, Row, StatusChip, Skeleton, Divider, money } from '../ui';
import { C, S, T, R } from '../theme';
import { Rupee, Globe, Warning } from '../icons';
import { TabScreen } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

/**
 * Orders, grouped by the stage the artisan actually has to act on.
 *
 * The "book the courier" action only appears for a paid order, because that is the
 * real precondition the backend enforces - offering it earlier would be a button
 * that always fails.
 */
const FILTERS = [
  { key: 'new', label: 'ord.new',
    match: (o: api.Order) => ['created', 'payment_pending', 'paid'].includes(o.status) },
  { key: 'processing', label: 'ord.processing',
    match: (o: api.Order) => ['confirmed', 'packed'].includes(o.status) },
  { key: 'shipped', label: 'ord.shipped',
    match: (o: api.Order) => ['shipped', 'out_for_delivery'].includes(o.status) },
  { key: 'delivered', label: 'ord.delivered',
    match: (o: api.Order) => ['delivered', 'completed'].includes(o.status) },
  { key: 'returns', label: 'ord.returns',
    match: (o: api.Order) => ['cancelled', 'refunded', 'failed'].includes(o.status) },
];

export default function OrdersTab({
  orders, loading, artisan, onLanguage, onRefresh, onShip, onLogin,
}: {
  orders: api.Order[];
  loading: boolean;
  artisan: api.Artisan | null;
  onLanguage: () => void;
  onRefresh: () => void;
  onShip: (id: string) => Promise<void>;
  onLogin: () => void;
}) {
  const { t } = useI18n();
  const [filter, setFilter] = useState('new');
  const [shipping, setShipping] = useState<string | null>(null);

  const shown = useMemo(() => {
    const f = FILTERS.find((x) => x.key === filter) ?? FILTERS[0];
    return orders.filter(f.match);
  }, [orders, filter]);

  if (!artisan) {
    return (
      <TabScreen title={t('ord.title')} onLanguage={onLanguage}>
        <Card style={{ alignItems: 'center', paddingVertical: S.xl, gap: S.md }}>
          <View style={{ width: 54, height: 54, borderRadius: R.pill,
                         backgroundColor: C.bgAlt, alignItems: 'center',
                         justifyContent: 'center' }}>
            <Rupee color={C.inkSoft} size={24} />
          </View>
          <Text style={[T.body, { textAlign: 'center' }]}>{t('ord.needsLogin')}</Text>
          <Btn label={t('home.login')} tone="money" onPress={onLogin} />
        </Card>
      </TabScreen>
    );
  }

  return (
    <TabScreen title={t('ord.title')} onLanguage={onLanguage}
               right={<Pressable onPress={onRefresh} hitSlop={10}
                                 accessibilityLabel={t('create.refresh')}>
                        <Pill text={t('create.refresh')} />
                      </Pressable>}>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
        {FILTERS.map((f) => {
          const on = filter === f.key;
          const n = orders.filter(f.match).length;
          return (
            <Pressable key={f.key} onPress={() => setFilter(f.key)}
                       accessibilityLabel={t(f.label)}>
              <View style={{ paddingHorizontal: 13, paddingVertical: 8,
                             borderRadius: R.pill,
                             backgroundColor: on ? C.ink : C.bgAlt }}>
                <Text style={{ fontFamily: 'Mukta_600SemiBold', fontSize: 13.5,
                               color: on ? C.white : C.inkMid }}>
                  {t(f.label)}{n ? ` · ${n}` : ''}
                </Text>
              </View>
            </Pressable>
          );
        })}
      </View>

      {loading ? (
        <><Card><Skeleton h={54} /></Card><Card><Skeleton h={54} /></Card></>
      ) : shown.length === 0 ? (
        <Card style={{ alignItems: 'center', paddingVertical: S.xl, gap: S.sm }}>
          <View style={{ width: 54, height: 54, borderRadius: R.pill,
                         backgroundColor: C.bgAlt, alignItems: 'center',
                         justifyContent: 'center' }}>
            <Rupee color={C.inkSoft} size={24} />
          </View>
          <Text style={T.body}>{t('ord.empty')}</Text>
          <Text style={[T.bodySoft, { textAlign: 'center' }]}>{t('ord.emptySub')}</Text>
        </Card>
      ) : shown.map((o) => {
        const canShip = o.paymentStatus === 'paid'
          && ['paid', 'confirmed', 'packed'].includes(o.status);
        return (
          <Card key={o.id}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
              <Text style={[T.body, { fontFamily: 'Mukta_700Bold', flex: 1 }]}
                    numberOfLines={1}>
                {o.buyerName || t('ord.buyer')}
              </Text>
              <StatusChip status={o.status} />
            </View>
            <Row label={t('ord.amount')} value={money(o.amount)} strong />
            <Row label={t('ord.payment')} value={o.paymentStatus} />
            {o.buyerPhone ? <Row label={t('prof.phone')} value={o.buyerPhone} /> : null}
            {o.trackingId ? (
              <>
                <Divider />
                <Row label={t('ord.tracking')}
                     value={`${o.courier || ''} ${o.trackingId}`.trim()} />
                {o.trackingUrl ? (
                  <Pressable onPress={() => Linking.openURL(o.trackingUrl)}>
                    <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
                      <Globe color={C.indigo} size={15} />
                      <Text style={[T.micro, { color: C.indigo, flex: 1,
                                               textDecorationLine: 'underline' }]}
                            numberOfLines={1}>{o.trackingUrl}</Text>
                    </View>
                  </Pressable>
                ) : null}
              </>
            ) : null}
            {canShip ? (
              <Btn
                label={shipping === o.id ? t('ord.shipping') : t('ord.ship')}
                tone="money"
                busy={shipping === o.id}
                onPress={async () => {
                  setShipping(o.id);
                  try { await onShip(o.id); } finally { setShipping(null); }
                }}
              />
            ) : null}
            {o.paymentStatus !== 'paid' && o.status !== 'cancelled' ? (
              <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
                <Warning color={C.gold} size={15} />
                <Text style={[T.micro, { flex: 1, fontSize: 12 }]}>
                  {t('create.payment', { status: o.paymentStatus })}
                </Text>
              </View>
            ) : null}
          </Card>
        );
      })}
    </TabScreen>
  );
}
