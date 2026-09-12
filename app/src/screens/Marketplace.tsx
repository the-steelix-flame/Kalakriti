/**
 * The Kalakriti shop, as a buyer sees it.
 *
 * This is the buyer-facing half of the platform, and it reads the same rows the
 * server-rendered shop at /market reads - `/v1/market` is literally the same query.
 * That matters: a pot that sells out or loses its photograph disappears from both at
 * once, instead of the app offering something the order endpoint will refuse.
 *
 * Nothing here is hardcoded. Every tile is a real listing, every price is the
 * artisan's own price, and the stock number is the column the order endpoint checks
 * and decrements. There is no second inventory in the app.
 *
 * Deliberately unauthenticated. A buyer browsing has no account, and requiring one
 * before they can see what is for sale is the fastest way to have no buyers.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Image, Pressable, ScrollView, RefreshControl,
         ActivityIndicator } from 'react-native';
import { C, S, T, R, shadow } from '../theme';
import { Btn, Card, Pill, Skeleton } from '../ui';
import { TopBar, TAB_BAR_HEIGHT } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

function money(n: number, cur = 'INR') {
  const sym = cur === 'INR' ? '₹' : '';
  return `${sym}${Math.round(n).toLocaleString('en-IN')}`;
}

/* ─────────────────────────────────────────────────────────── one product tile */

function Tile({ item, onOpen }: { item: api.MarketItem; onOpen: () => void }) {
  // A tile that fails to load its picture should still be a usable tile rather than a
  // grey hole, so the failure is tracked and replaced with the product's own name.
  const [broke, setBroke] = useState(false);
  const low = item.quantity > 0 && item.quantity <= 3;

  return (
    <Pressable
      onPress={onOpen}
      accessibilityRole="button"
      accessibilityLabel={`${item.title}, ${money(item.price, item.currency)}`}
      style={({ pressed }) => ({ flex: 1, opacity: pressed ? 0.75 : 1 })}
    >
      <View style={[{
        backgroundColor: C.surface, borderRadius: R.lg, borderWidth: 1,
        borderColor: C.line, overflow: 'hidden',
      }, shadow(1)]}>
        <View style={{ aspectRatio: 1, backgroundColor: C.bgAlt }}>
          {item.imageUrl && !broke ? (
            <Image
              source={{ uri: item.imageUrl }}
              onError={() => setBroke(true)}
              resizeMode="cover"
              style={{ width: '100%', height: '100%' }}
            />
          ) : (
            <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center',
                           padding: S.md }}>
              <Text style={[T.micro, { textAlign: 'center', color: C.inkSoft }]}
                    numberOfLines={3}>
                {item.title}
              </Text>
            </View>
          )}
          {low ? (
            <View style={{ position: 'absolute', top: 8, left: 8,
                           backgroundColor: C.goldSoft, borderRadius: R.pill,
                           paddingHorizontal: 9, paddingVertical: 4,
                           borderWidth: 1, borderColor: '#EFDFB4' }}>
              <Text style={{ fontFamily: 'Mukta_700Bold', fontSize: 11,
                             color: '#7A5B10' }}>
                {item.quantity} left
              </Text>
            </View>
          ) : null}
        </View>

        <View style={{ padding: S.md, gap: 4 }}>
          <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold', fontSize: 14.5 }]}
                numberOfLines={2}>
            {item.title}
          </Text>
          <Text style={[T.title, { fontSize: 17, color: C.primaryDeep }]}>
            {money(item.price, item.currency)}
          </Text>
          {item.maker ? (
            <Text style={[T.micro, { fontSize: 11.5 }]} numberOfLines={1}>
              {item.maker}
            </Text>
          ) : null}
        </View>
      </View>
    </Pressable>
  );
}

/* ───────────────────────────────────────────────────────────────── the screen */

export default function Marketplace({
  onBack, onOpen,
}: {
  onBack: () => void;
  onOpen: (id: string) => void;
}) {
  const { t } = useI18n();
  const [items, setItems] = useState<api.MarketItem[] | null>(null);
  const [cats, setCats] = useState<string[]>([]);
  const [cat, setCat] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (category: string) => {
    setBusy(true);
    setErr('');
    try {
      const r = await api.market('', category);
      setItems(r.items);
      // Categories come from the whole shop, not the filtered slice, or choosing one
      // would erase every other chip and leave no way back.
      if (!category) setCats(r.categories);
    } catch (e: any) {
      // The shop is the one screen a buyer sees first, so a failure here says what to
      // do rather than showing an empty shelf that looks like we have nothing to sell.
      setErr(e?.message || 'Could not reach the shop.');
      setItems(null);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void load(cat); }, [cat, load]);

  const bottomPad = TAB_BAR_HEIGHT + S.xl;

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar title={t('market.title')} subtitle={t('market.sub')} onBack={onBack} />

      {cats.length ? (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ paddingHorizontal: S.lg, paddingVertical: S.sm,
                                   gap: S.sm }}
          style={{ flexGrow: 0, borderBottomWidth: 1, borderBottomColor: C.line }}
        >
          {[{ key: '', label: t('market.all') },
            ...cats.map((c) => ({ key: c, label: c.split('>').pop()!.trim() }))]
            .map(({ key, label }) => {
              const on = cat === key;
              return (
                <Pressable key={key || 'all'} onPress={() => setCat(key)}
                           accessibilityRole="button"
                           accessibilityState={{ selected: on }}>
                  <View style={{
                    paddingHorizontal: 14, paddingVertical: 8, borderRadius: R.pill,
                    backgroundColor: on ? C.primary : C.surface,
                    borderWidth: 1, borderColor: on ? C.primary : C.line,
                  }}>
                    <Text style={{
                      fontFamily: on ? 'Mukta_700Bold' : 'Mukta_500Medium',
                      fontSize: 13.5, color: on ? C.white : C.inkMid,
                    }}>
                      {label}
                    </Text>
                  </View>
                </Pressable>
              );
            })}
        </ScrollView>
      ) : null}

      <ScrollView
        contentContainerStyle={{ padding: S.lg, paddingBottom: bottomPad, gap: S.md }}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl refreshing={busy && items !== null}
                          onRefresh={() => load(cat)} tintColor={C.primary} />
        }
      >
        {err ? (
          <Card tone="danger">
            <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
              {t('market.errTitle')}
            </Text>
            <Text style={T.bodySoft}>{err}</Text>
            <Btn label={t('market.retry')} tone="tonal" busy={busy}
                 onPress={() => load(cat)} />
          </Card>
        ) : null}

        {items === null && !err ? (
          <View style={{ gap: S.md }}>
            {[0, 1].map((r) => (
              <View key={r} style={{ flexDirection: 'row', gap: S.md }}>
                {[0, 1].map((c) => (
                  <View key={c} style={{ flex: 1, gap: 8 }}>
                    <Skeleton h={150} r={14} />
                    <Skeleton h={14} w="85%" />
                    <Skeleton h={14} w="45%" />
                  </View>
                ))}
              </View>
            ))}
          </View>
        ) : null}

        {items !== null && items.length === 0 && !err ? (
          <Card tone="soft">
            <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
              {cat ? t('market.emptyCat') : t('market.empty')}
            </Text>
            <Text style={T.bodySoft}>{t('market.emptyWhy')}</Text>
            {cat ? (
              <Btn label={t('market.all')} tone="tonal" onPress={() => setCat('')} />
            ) : null}
          </Card>
        ) : null}

        {items && items.length ? (
          <>
            <Pill text={t('market.count', { n: items.length })} />
            {/* Two columns, built by hand rather than with FlatList numColumns: this
                whole page is one ScrollView so the category rail scrolls away with
                the grid, and nesting a virtualised list inside that fights itself. */}
            {Array.from({ length: Math.ceil(items.length / 2) }).map((_, row) => (
              <View key={row} style={{ flexDirection: 'row', gap: S.md }}>
                {[items[row * 2], items[row * 2 + 1]].map((it, i) =>
                  it ? (
                    <Tile key={it.id} item={it} onOpen={() => onOpen(it.id)} />
                  ) : (
                    // Keeps the last odd tile at half width instead of stretching it.
                    <View key={`pad-${i}`} style={{ flex: 1 }} />
                  ))}
              </View>
            ))}
          </>
        ) : null}

        {busy && items !== null ? (
          <ActivityIndicator color={C.primary} style={{ marginTop: S.sm }} />
        ) : null}
      </ScrollView>
    </View>
  );
}
