/**
 * One product: what it is, where it went, and what happened there.
 *
 * Tapping a product used to open the editor, which meant there was nowhere to see a
 * product's life - only somewhere to change it. This screen is the missing middle:
 *
 *     My Products -> this -> one marketplace -> that marketplace's orders
 *
 * Marketplace figures are deliberately not fetched here. Each one costs a round trip
 * to a rate-limited API, and five of them would make opening a product slow enough to
 * feel broken. This screen shows what we already hold; the marketplace screen fetches
 * live numbers for the one channel it is showing.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Image, Pressable, Linking, ActivityIndicator } from 'react-native';
import { Card, Pill, Btn, Row, Divider, StatusChip, Skeleton, money } from '../ui';
import { C, S, T, R } from '../theme';
import { Camera, Globe, Rupee, Warning, Tag } from '../icons';
import { TopBar } from '../nav/Shell';
import { ScrollView } from 'react-native';
import { useI18n } from '../i18n';
import { ago } from '../lib/ago';
import * as api from '../lib/api';
import * as cache from '../lib/cache';

export default function ProductDetail({
  listingId, onBack, onEdit, onOpenMarketplace, onPublish,
}: {
  listingId: string;
  onBack: () => void;
  onEdit: (id: string) => void;
  onOpenMarketplace: (listingId: string, channel: string) => void;
  onPublish: (id: string) => void;
}) {
  const { t } = useI18n();
  const [d, setD] = useState<api.ProductDetail | null>(null);
  const [stale, setStale] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    // Cached copy first, so re-opening a product is instant and works with no signal.
    const c = await cache.read<api.ProductDetail>(cache.K.listing(listingId));
    if (c) { setD(c.data); setStale(true); }
    try {
      const fresh = await api.productDetail(listingId);
      setD(fresh);
      setStale(false);
      setErr(null);
      await cache.write(cache.K.listing(listingId), fresh);
    } catch (e: any) {
      if (!c) setErr(e?.message || String(e));
      setStale(true);
    }
  }, [listingId]);

  useEffect(() => { load(); }, [load]);

  if (!d) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg }}>
        <TopBar title={t('pd.title')} onBack={onBack} />
        <View style={{ padding: S.lg, gap: S.md }}>
          {err ? (
            <Card tone="danger">
              <Text style={T.body}>{err}</Text>
              <Btn label={t('common.retry')} tone="tonal" onPress={load} />
            </Card>
          ) : (
            <><Card><Skeleton h={180} /></Card><Card><Skeleton h={90} /></Card></>
          )}
        </View>
      </View>
    );
  }

  const l = d.listing;
  const card = d.card;
  const attrs = Object.entries(l.attributes || {})
    .filter(([, v]) => v != null && String(v).trim() !== '');
  const breakdown: { label: string; amount: number }[] =
    (l.attributes as any)?.breakdown || [];

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar
        title={card.title || t('pd.title')}
        subtitle={stale ? t('sync.asOf', { ago: ago(t, card.updatedAt) }) : undefined}
        onBack={onBack}
      />
      <ScrollView contentContainerStyle={{ padding: S.lg, gap: S.md, paddingBottom: 60 }}
                  showsVerticalScrollIndicator={false}>

        {l.imageUrl ? (
          <Image source={{ uri: l.imageUrl }}
                 style={{ width: '100%', aspectRatio: 1, borderRadius: R.xl,
                          backgroundColor: C.bgAlt }} />
        ) : (
          <View style={{ width: '100%', aspectRatio: 1.6, borderRadius: R.xl,
                         backgroundColor: C.bgAlt, alignItems: 'center',
                         justifyContent: 'center' }}>
            <Camera color={C.inkSoft} size={30} />
          </View>
        )}

        <View style={{ gap: 6 }}>
          <Text style={[T.title, { fontSize: 22 }]}>{card.title}</Text>
          <Text style={{ fontFamily: 'Mukta_800ExtraBold', fontSize: 30, color: C.money }}>
            {card.price ? money(card.price) : t('home.priceMissing')}
          </Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 5 }}>
            <StatusChip status={card.status} />
            {card.quantity > 0
              ? <Pill text={t('prod.inStock', { n: card.quantity })} tone="soft" />
              : <Pill text={t('prod.outOfStock')} tone="warn" />}
            {card.updatedAt
              ? <Pill text={t('prod.updated', { ago: ago(t, card.updatedAt) })}
                      tone="soft" />
              : null}
          </View>
        </View>

        {/* Money the artisan has actually received, from paid orders only. */}
        {card.sold > 0 ? (
          <Card tone="money">
            <Row label={t('prod.soldCount', { n: card.sold })}
                 value={money(card.revenue)} strong />
            <Text style={[T.micro, { fontSize: 12 }]}>{t('pd.totalEarned')}</Text>
          </Card>
        ) : null}

        {/* ── where it is listed ─────────────────────────────────────────── */}
        <Text style={T.section}>{t('pd.whereListed')}</Text>
        {d.marketplaces.length === 0 ? (
          <Card>
            <Text style={T.bodySoft}>{t('pd.notListed')}</Text>
            <Btn label={t('pd.sendNow')} tone="tonal" onPress={() => onPublish(l.id)} />
          </Card>
        ) : d.marketplaces.map((m) => (
          <Card key={m.id} onPress={() => onOpenMarketplace(l.id, m.channel)}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
              <View style={{ width: 38, height: 38, borderRadius: R.pill,
                             backgroundColor: C.indigoSoft, alignItems: 'center',
                             justifyContent: 'center' }}>
                <Globe color={C.indigo} size={18} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                  {m.channelName}
                </Text>
                <Text style={[T.micro, { fontSize: 12 }]}>
                  {m.orderCount
                    ? t('prod.ordersCount', { n: m.orderCount })
                    : (m.submittedAt ? ago(t, m.submittedAt) : '')}
                </Text>
              </View>
              <StatusChip status={m.status} />
              <Text style={{ fontSize: 22, color: C.inkSoft, marginTop: -3 }}>›</Text>
            </View>
            {m.error ? (
              <View style={{ flexDirection: 'row', gap: 6, alignItems: 'flex-start' }}>
                <Warning color={C.danger} size={15} />
                <Text style={[T.micro, { flex: 1, color: C.danger }]} numberOfLines={3}>
                  {m.error}
                </Text>
              </View>
            ) : null}
          </Card>
        ))}

        {/* ── the product itself ─────────────────────────────────────────── */}
        <Text style={T.section}>{t('pd.details')}</Text>
        <Card>
          {l.descHi || l.descEn ? (
            <Text style={T.bodySoft}>{l.descHi || l.descEn}</Text>
          ) : null}
          <Divider />
          {card.category ? <Row label={t('pd.category')} value={card.category} /> : null}
          {l.hsn ? <Row label={t('pd.hsn')} value={l.hsn} /> : null}
          <Row label={t('pd.stock')} value={String(card.quantity)} />
          {l.lengthCm ? (
            <Row label={t('pd.size')}
                 value={`${l.lengthCm} × ${l.breadthCm} × ${l.heightCm} cm`} />
          ) : null}
          {l.weightG ? <Row label={t('pd.weight')} value={`${l.weightG} g`} /> : null}
          {attrs.map(([k, v]) => (
            k === 'breakdown' ? null : (
              <Row key={k} label={k}
                   value={Array.isArray(v) ? v.join(', ') : String(v)} />
            )
          ))}
        </Card>

        {breakdown.length ? (
          <Card>
            <Text style={T.label}>{t('pd.priceBreakdown')}</Text>
            {breakdown.map((b, i) => (
              <Row key={i} label={b.label} value={money(b.amount)} />
            ))}
            <Divider />
            <Row label={t('create.price')} value={money(card.price)} strong />
          </Card>
        ) : null}

        <Btn label={t('pd.edit')} tone="tonal" onPress={() => onEdit(l.id)} />

        {/* ── orders across all channels ─────────────────────────────────── */}
        <Text style={T.section}>{t('pd.orders')}</Text>
        {d.orders.length === 0 ? (
          <Card><Text style={T.bodySoft}>{t('pd.noOrders')}</Text></Card>
        ) : d.orders.map((o) => (
          <Card key={o.id}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
              <Rupee color={C.money} size={18} />
              <View style={{ flex: 1 }}>
                <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                  {o.buyerName || o.id}
                </Text>
                <Text style={[T.micro, { fontSize: 12 }]}>
                  {t('ord.from', { name: o.channel })} · {ago(t, o.createdAt)}
                </Text>
              </View>
              <Text style={[T.body, { color: C.money, fontFamily: 'Mukta_700Bold' }]}>
                {money(o.amount)}
              </Text>
            </View>
            <View style={{ flexDirection: 'row', gap: 5, flexWrap: 'wrap' }}>
              <StatusChip status={o.status} />
              <StatusChip status={o.paymentStatus} />
            </View>
          </Card>
        ))}

        {/* ── audit trail ────────────────────────────────────────────────── */}
        {d.events.length ? (
          <>
            <Text style={T.section}>{t('pd.history')}</Text>
            <Card>
              {d.events.slice(0, 12).map((e, i) => (
                <View key={e.id ?? i} style={{ flexDirection: 'row', gap: S.sm,
                                               paddingVertical: 5 }}>
                  <Text style={[T.micro, { fontSize: 11.5, width: 74 }]}>
                    {ago(t, e.at)}
                  </Text>
                  <Text style={[T.micro, { flex: 1, fontSize: 12.5, color: C.inkMid }]}>
                    {e.to ? `${e.from || '·'} → ${e.to}` : ''} {e.detail || e.kind}
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
