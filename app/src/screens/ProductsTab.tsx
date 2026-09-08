import React, { useMemo, useState } from 'react';
import { View, Text, Image, Pressable, Linking } from 'react-native';
import { Card, Pill, StatusChip, Skeleton, money } from '../ui';
import { C, S, T, R } from '../theme';
import { Camera, Globe, Check } from '../icons';
import { TabScreen } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

/**
 * My Products: everything the artisan has made, grouped by where it is in its life.
 *
 * The filters mirror the real listing lifecycle stored in the database
 * (draft -> processing -> submitted -> published -> active -> sold), rather than
 * inventing categories, so a chip always corresponds to a state the backend can
 * actually be in.
 */
const FILTERS = [
  { key: 'all', label: 'prod.all', match: () => true },
  { key: 'draft', label: 'prod.draft', match: (s: string) => s === 'draft' || s === 'processing' },
  { key: 'published', label: 'prod.published', match: (s: string) => s === 'submitted' || s === 'published' },
  { key: 'active', label: 'prod.active', match: (s: string) => s === 'active' },
  { key: 'sold', label: 'prod.sold', match: (s: string) => s === 'sold' },
];

export default function ProductsTab({
  listings, loading, onOpen, onLanguage, onRefresh,
}: {
  listings: api.Listing[];
  loading: boolean;
  onOpen: (id: string) => void;
  onLanguage: () => void;
  onRefresh: () => void;
}) {
  const { t } = useI18n();
  const [filter, setFilter] = useState('all');

  const shown = useMemo(() => {
    const f = FILTERS.find((x) => x.key === filter) ?? FILTERS[0];
    return listings.filter((l) => f.match(l.status));
  }, [listings, filter]);

  return (
    <TabScreen title={t('prod.title')} onLanguage={onLanguage}
               right={<Pressable onPress={onRefresh} hitSlop={10}
                                 accessibilityLabel={t('create.refresh')}>
                        <Pill text={t('create.refresh')} />
                      </Pressable>}>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
        {FILTERS.map((f) => {
          const on = filter === f.key;
          const n = listings.filter(f.match as any).length;
          return (
            <Pressable key={f.key} onPress={() => setFilter(f.key)}
                       accessibilityLabel={t(f.label)}>
              <View style={{
                paddingHorizontal: 13, paddingVertical: 8, borderRadius: R.pill,
                backgroundColor: on ? C.ink : C.bgAlt,
              }}>
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
        <>
          <Card><Skeleton h={58} /></Card>
          <Card><Skeleton h={58} /></Card>
        </>
      ) : shown.length === 0 ? (
        <Card style={{ alignItems: 'center', paddingVertical: S.xl, gap: S.sm }}>
          <View style={{ width: 54, height: 54, borderRadius: R.pill,
                         backgroundColor: C.bgAlt, alignItems: 'center',
                         justifyContent: 'center' }}>
            <Camera color={C.inkSoft} size={24} />
          </View>
          <Text style={T.body}>
            {listings.length ? t('prod.emptyFiltered') : t('prod.empty')}
          </Text>
          {!listings.length ? (
            <Text style={[T.bodySoft, { textAlign: 'center' }]}>{t('prod.emptySub')}</Text>
          ) : null}
        </Card>
      ) : shown.map((l) => {
        const live = l.publications?.find((p) => p.status === 'published' && p.url);
        return (
          <Card key={l.id} onPress={() => onOpen(l.id)}>
            <View style={{ flexDirection: 'row', gap: S.md }}>
              {l.imageUrl ? (
                <Image source={{ uri: l.imageUrl }}
                       style={{ width: 72, height: 72, borderRadius: R.md,
                                backgroundColor: C.bgAlt }} />
              ) : (
                <View style={{ width: 72, height: 72, borderRadius: R.md,
                               backgroundColor: C.bgAlt, alignItems: 'center',
                               justifyContent: 'center' }}>
                  <Camera color={C.inkSoft} size={22} />
                </View>
              )}
              <View style={{ flex: 1, gap: 4 }}>
                <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]} numberOfLines={2}>
                  {l.titleHi || l.titleEn || t('home.noDraftName')}
                </Text>
                <Text style={[T.body, { color: C.money, fontFamily: 'Mukta_700Bold' }]}>
                  {l.price ? money(l.price) : t('home.priceMissing')}
                </Text>
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 5 }}>
                  <StatusChip status={l.status} />
                  {l.publications?.length ? (
                    <Pill text={t('prod.channels', { n: l.publications.length })}
                          tone="indigo" />
                  ) : null}
                </View>
              </View>
            </View>
            {live ? (
              <Pressable onPress={() => Linking.openURL(live.url)}
                         accessibilityLabel={t('prod.openListing')}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6,
                               marginTop: 4 }}>
                  <Globe color={C.indigo} size={15} />
                  <Text style={[T.micro, { color: C.indigo, flex: 1,
                                           textDecorationLine: 'underline' }]}
                        numberOfLines={1}>
                    {live.url}
                  </Text>
                </View>
              </Pressable>
            ) : null}
          </Card>
        );
      })}
    </TabScreen>
  );
}
