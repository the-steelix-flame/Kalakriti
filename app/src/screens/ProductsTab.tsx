/**
 * My Products: the product management centre.
 *
 * Each row now carries the whole lifecycle rather than a status word: where it is
 * listed, how many people looked, how many sold, what came in, when it last changed.
 * Those come from `listing_card` on the backend, which counts real rows - orders that
 * were actually paid, views that were actually served.
 *
 * Two states here do not exist as database columns and are derived, because the column
 * would mislead:
 *
 *   partial  - live on some marketplaces, failed on others. Stored as "published",
 *              which hides the half that needs fixing.
 *   failed   - submitted, and every channel rejected it. Also stored as "published"
 *              in the naive reading; it is the opposite of published.
 *
 * Tapping a product opens its detail page. It used to open the editor, which meant
 * there was no way to look at a product without being invited to change it.
 */
import React, { useMemo, useState } from 'react';
import { View, Text, Image, Pressable, RefreshControl } from 'react-native';
import { Card, Pill, StatusChip, Skeleton, money } from '../ui';
import { FilterBar, applyList, byDate, byNum, FilterDef, SortDef } from '../ui/Filters';
import { C, S, T, R } from '../theme';
import { Camera, Globe, Warning, Trend } from '../icons';
import { TabScreen } from '../nav/Shell';
import { useI18n } from '../i18n';
import { ago } from '../lib/ago';
import * as api from '../lib/api';

const FILTERS: FilterDef<api.Card>[] = [
  { key: 'all', label: 'prod.all', match: () => true },
  { key: 'draft', label: 'prod.draft',
    match: (c) => c.status === 'draft' || c.status === 'processing' },
  { key: 'published', label: 'prod.published',
    match: (c) => ['published', 'submitted', 'approved'].includes(c.status) },
  { key: 'active', label: 'prod.active', match: (c) => c.status === 'active' },
  { key: 'partial', label: 'prod.partial', match: (c) => c.status === 'partial' },
  { key: 'failed', label: 'prod.failed', match: (c) => c.status === 'failed' },
  { key: 'sold', label: 'prod.sold', match: (c) => c.status === 'sold' },
];

const SORTS: SortDef<api.Card>[] = [
  { key: 'newest', label: 'sort.newest', cmp: byDate((c) => c.updatedAt, 1) },
  { key: 'oldest', label: 'sort.oldest', cmp: byDate((c) => c.updatedAt, -1) },
  { key: 'priceHigh', label: 'sort.priceHigh', cmp: byNum((c) => c.price, 1) },
  { key: 'priceLow', label: 'sort.priceLow', cmp: byNum((c) => c.price, -1) },
  { key: 'viewed', label: 'sort.mostViewed', cmp: byNum((c) => c.views, 1) },
  { key: 'sold', label: 'sort.mostSold', cmp: byNum((c) => c.sold, 1) },
];

export default function ProductsTab({
  cards, loading, offline, lastSync, onOpen, onLanguage, onRefresh,
}: {
  cards: api.Card[];
  loading: boolean;
  offline: boolean;
  lastSync: number | null;
  onOpen: (id: string) => void;
  onLanguage: () => void;
  onRefresh: () => void;
}) {
  const { t } = useI18n();
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('newest');

  const shown = useMemo(
    () => applyList(cards, FILTERS, filter, SORTS, sort),
    [cards, filter, sort]);

  return (
    <TabScreen
      title={t('prod.title')}
      subtitle={offline && lastSync ? t('sync.asOf', { ago: ago(t, lastSync) }) : undefined}
      onLanguage={onLanguage}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={onRefresh}
                                      tintColor={C.primary} />}
    >
      <FilterBar items={cards} filters={FILTERS} filter={filter} onFilter={setFilter}
                 sorts={SORTS} sort={sort} onSort={setSort} />

      {loading && !cards.length ? (
        <><Card><Skeleton h={72} /></Card><Card><Skeleton h={72} /></Card></>
      ) : shown.length === 0 ? (
        <Card style={{ alignItems: 'center', paddingVertical: S.xl, gap: S.sm }}>
          <View style={{ width: 54, height: 54, borderRadius: R.pill,
                         backgroundColor: C.bgAlt, alignItems: 'center',
                         justifyContent: 'center' }}>
            <Camera color={C.inkSoft} size={24} />
          </View>
          <Text style={T.body}>
            {cards.length ? t('prod.emptyFiltered') : t('prod.empty')}
          </Text>
          {!cards.length ? (
            <Text style={[T.bodySoft, { textAlign: 'center' }]}>{t('prod.emptySub')}</Text>
          ) : null}
        </Card>
      ) : shown.map((c) => (
        <Card key={c.id} onPress={() => onOpen(c.id)}>
          <View style={{ flexDirection: 'row', gap: S.md }}>
            {c.imageUrl ? (
              <Image source={{ uri: c.imageUrl }}
                     style={{ width: 76, height: 76, borderRadius: R.md,
                              backgroundColor: C.bgAlt }} />
            ) : (
              <View style={{ width: 76, height: 76, borderRadius: R.md,
                             backgroundColor: C.bgAlt, alignItems: 'center',
                             justifyContent: 'center' }}>
                <Camera color={C.inkSoft} size={22} />
              </View>
            )}
            <View style={{ flex: 1, gap: 4 }}>
              <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]} numberOfLines={2}>
                {c.title || t('home.noDraftName')}
              </Text>
              <Text style={[T.body, { color: C.money, fontFamily: 'Mukta_700Bold' }]}>
                {c.price ? money(c.price) : t('home.priceMissing')}
              </Text>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 5 }}>
                <StatusChip status={c.status} />
                {c.marketplaces ? (
                  <Pill tone="indigo"
                        text={c.marketplaces === 1
                          ? t('prod.onMarketplaces', { n: 1 })
                          : t('prod.onMarketplacesPlural', { n: c.marketplaces })} />
                ) : c.status !== 'draft' ? (
                  <Pill text={t('prod.notSentYet')} tone="soft" />
                ) : null}
                {c.quantity <= 0 ? <Pill text={t('prod.outOfStock')} tone="warn" /> : null}
              </View>
            </View>
          </View>

          {/* Counted facts only. `views` is null wherever nothing counts views, and
              then the chip is simply absent rather than showing a zero. */}
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 5, marginTop: 2 }}>
            {c.viewsAvailable && c.views != null ? (
              <Pill text={t('prod.viewsCount', { n: c.views })} tone="soft" />
            ) : null}
            {c.sold ? <Pill text={t('prod.soldCount', { n: c.sold })} tone="good" /> : null}
            {c.orders ? (
              <Pill text={t('prod.ordersCount', { n: c.orders })} tone="soft" />
            ) : null}
            {c.revenue ? <Pill text={money(c.revenue)} tone="good" /> : null}
            {c.updatedAt ? (
              <Pill text={t('prod.updated', { ago: ago(t, c.updatedAt) })} tone="soft" />
            ) : null}
          </View>

          {c.failedChannels.length ? (
            <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
              <Warning color={C.danger} size={15} />
              <Text style={[T.micro, { flex: 1, color: C.danger }]} numberOfLines={1}>
                {t('home.actPublishFailed', { what: c.failedChannels.join(', ') })}
              </Text>
            </View>
          ) : null}
        </Card>
      ))}
    </TabScreen>
  );
}
