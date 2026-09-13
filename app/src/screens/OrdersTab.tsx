/**
 * Orders, with the filters and sorts the brief asks for.
 *
 * Filters follow the real order lifecycle stored in the database, so a chip always
 * corresponds to a state the backend can actually be in - there is no "Processing"
 * chip that quietly means several different things.
 *
 * The "book the courier" action appears only on a paid order, because that is the
 * precondition the backend enforces. Offering it earlier would be a button that
 * always fails, which teaches the artisan the app is unreliable.
 */
import React, { useMemo, useState } from 'react';
import { View, Text, Pressable, Linking, RefreshControl, Alert } from 'react-native';
import { Card, Btn, Pill, Row, StatusChip, Skeleton, Divider, money } from '../ui';
import { FilterBar, applyList, byDate, byNum, FilterDef, SortDef } from '../ui/Filters';
import { C, S, T, R } from '../theme';
import { Rupee, Globe, Warning } from '../icons';
import { TabScreen } from '../nav/Shell';
import { useI18n } from '../i18n';
import { ago } from '../lib/ago';
import * as api from '../lib/api';
import { recordAndUploadPackagingVideo } from '../lib/packagingVideo';
import { isPending, isPreparing, isInTransit, isDelivered, isCancelled, isReturned }
  from '../lib/orderStages';

// Bucketed through lib/orderStages.ts rather than a bare list of strings, so this
// chip means the same thing here and in Reports regardless of whether the order was
// last touched by checkout's original created/paid/shipped vocabulary or by the
// transit tracker's finer-grained one.
const FILTERS: FilterDef<api.Order>[] = [
  { key: 'all', label: 'prod.all', match: () => true },
  { key: 'pending', label: 'ord.filterPending', match: (o) => isPending(o.status) },
  { key: 'paid', label: 'ord.filterPaid',
    match: (o) => o.status === 'paid' || (o.paymentStatus === 'paid'
                    && o.status === 'confirmed') },
  { key: 'processing', label: 'ord.filterProcessing', match: (o) => isPreparing(o.status) },
  { key: 'shipped', label: 'ord.filterShipped', match: (o) => isInTransit(o.status) },
  { key: 'delivered', label: 'ord.filterDelivered', match: (o) => isDelivered(o.status) },
  { key: 'cancelled', label: 'ord.filterCancelled', match: (o) => isCancelled(o.status) },
  { key: 'returned', label: 'ord.filterReturned', match: (o) => isReturned(o.status) },
];

const SORTS: SortDef<api.Order>[] = [
  { key: 'newest', label: 'sort.newest', cmp: byDate((o) => o.createdAt, 1) },
  { key: 'oldest', label: 'sort.oldest', cmp: byDate((o) => o.createdAt, -1) },
  { key: 'valueHigh', label: 'sort.valueHigh', cmp: byNum((o) => o.amount, 1) },
  { key: 'valueLow', label: 'sort.valueLow', cmp: byNum((o) => o.amount, -1) },
];

export default function OrdersTab({
  orders, loading, artisan, offline, onLanguage, onRefresh, onShip, onLogin,
  onOpenProduct, onTrack, onBoard,
}: {
  orders: api.Order[];
  loading: boolean;
  artisan: api.Artisan | null;
  offline: boolean;
  onLanguage: () => void;
  onRefresh: () => void;
  onShip: (id: string) => Promise<void>;
  onLogin: () => void;
  onOpenProduct?: (id: string) => void;
  /** Opens this order's tracker: the stage it is at, who moved it there, and the one
   *  button this person is allowed to press next. */
  onTrack?: (orderId: string) => void;
  /** Opens the transit board: stage counts across every order, which is the view that
   *  answers "where is everything" rather than "what is this one doing". */
  onBoard?: () => void;
}) {
  const { t } = useI18n();
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('newest');
  const [shipping, setShipping] = useState<string | null>(null);
  const [advancing, setAdvancing] = useState<string | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);

  const shown = useMemo(
    () => applyList(orders, FILTERS, filter, SORTS, sort),
    [orders, filter, sort]);

  /**
   * Move an order to whatever the server says is next for THIS caller, on THIS order.
   *
   * `o.nextStage` already came from the same permission rule the tracker uses
   * (transit.next_stage_for), so there is nothing to decide here about who is
   * allowed to do what - only whether the request succeeded. An artisan viewing her
   * own orders sees her own next stage (packaging, ready for pickup); the cluster
   * owner viewing theirs sees the logistics stages. Neither can see a button for a
   * stage that belongs to the other, because the server never sends one.
   */
  async function advanceOrder(o: api.Order) {
    if (!o.nextStage) return;
    setAdvancing(o.id);
    try {
      await api.advanceOrder(o.id, o.nextStage);
      onRefresh();
    } catch (e: any) {
      Alert.alert(t('track.cannot'), e?.message || 'That did not go through.');
    } finally {
      setAdvancing(null);
    }
  }

  /** Same camera-and-upload flow the tracker offers, reachable directly from the
   *  list so packaging a parcel does not require opening a second screen first. */
  async function recordPackaging(o: api.Order) {
    setUploading(o.id);
    const r = await recordAndUploadPackagingVideo(o.id);
    setUploading(null);
    if (r.ok) {
      onRefresh();
      Alert.alert(t('track.proofAdded'), t('track.proofAddedBody'));
      return;
    }
    if (r.reason === 'permission') {
      Alert.alert(t('track.cameraNeeded'), t('track.cameraWhy'));
    } else if (r.reason === 'error') {
      Alert.alert(t('track.proofFailed'), r.message);
    }
  }

  // An order belongs to an account. A guest has no account, so there is genuinely
  // nothing to show - and saying why is better than an empty list.
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
    <TabScreen
      title={t('ord.title')}
      subtitle={offline ? t('sync.offline') : undefined}
      onLanguage={onLanguage}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={onRefresh}
                                      tintColor={C.primary} />}
    >
      {/* Above the list, because "where is everything" is the question somebody opens
          this tab holding, and a flat list cannot answer it. */}
      {onBoard ? (
        <Btn label={t('ord.openBoard')} sub={t('ord.openBoardSub')} tone="indigo"
             onPress={onBoard} />
      ) : null}

      <FilterBar items={orders} filters={FILTERS} filter={filter} onFilter={setFilter}
                 sorts={SORTS} sort={sort} onSort={setSort} />

      {loading && !orders.length ? (
        <><Card><Skeleton h={54} /></Card><Card><Skeleton h={54} /></Card></>
      ) : shown.length === 0 ? (
        <Card style={{ alignItems: 'center', paddingVertical: S.xl, gap: S.sm }}>
          <View style={{ width: 54, height: 54, borderRadius: R.pill,
                         backgroundColor: C.bgAlt, alignItems: 'center',
                         justifyContent: 'center' }}>
            <Rupee color={C.inkSoft} size={24} />
          </View>
          <Text style={T.body}>
            {orders.length ? t('prod.emptyFiltered') : t('ord.empty')}
          </Text>
          {!orders.length ? (
            <Text style={[T.bodySoft, { textAlign: 'center' }]}>{t('ord.emptySub')}</Text>
          ) : null}
        </Card>
      ) : shown.map((o) => {
        const canShip = o.paymentStatus === 'paid'
          && ['paid', 'confirmed', 'packed'].includes(o.status);
        return (
          <Card key={o.id}
                onPress={onOpenProduct ? () => onOpenProduct(o.listingId) : undefined}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
              <View style={{ flex: 1 }}>
                <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]} numberOfLines={1}>
                  {o.buyerName || t('ord.buyer')}
                </Text>
                <Text style={[T.micro, { fontSize: 12 }]}>
                  {t('ord.from', { name: o.channel })} · {ago(t, o.createdAt)}
                </Text>
              </View>
              <StatusChip status={o.status} />
            </View>

            {/* The direct action, right at the top: this is what somebody opening
                the tab holding a parcel actually wants to do, and it should not be
                a fourth thing to find after amount, quantity and payment status. */}
            {o.needsPackagingProof ? (
              <Btn label={uploading === o.id ? t('track.uploading')
                                              : t('ord.recordPackaging')}
                   tone="primary" busy={uploading === o.id}
                   onPress={() => recordPackaging(o)} />
            ) : o.nextStage ? (
              <Btn label={t('ord.markAs', { stage: o.nextStageLabel || o.nextStage })}
                   tone="primary" busy={advancing === o.id}
                   onPress={() => advanceOrder(o)} />
            ) : null}

            <Row label={t('ord.amount')} value={money(o.amount)} strong />
            <Row label={t('ord.quantity')} value={String(o.quantity)} />
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

            {/* The tracker is the primary action on an order now. Shipping is one
                stage inside the journey rather than the only thing to do with it. */}
            {onTrack ? (
              <Btn label={t('ord.track')} tone="indigo"
                   onPress={() => onTrack(o.id)} />
            ) : null}

            {canShip ? (
              <Btn label={shipping === o.id ? t('ord.shipping') : t('ord.ship')}
                   tone="money" busy={shipping === o.id}
                   onPress={async () => {
                     setShipping(o.id);
                     try { await onShip(o.id); } finally { setShipping(null); }
                   }} />
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
