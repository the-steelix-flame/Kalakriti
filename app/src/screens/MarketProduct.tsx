/**
 * One product in the shop, and the order that comes out of it.
 *
 * The buyer's half of the business model ends here:
 *
 *     buyer -> marketplace -> cluster owner (seller of record) -> artisan
 *
 * So the screen names both parties. `seller` is who takes the money and holds the
 * GSTIN - a cluster owner when the product is sold through a cluster, the artisan
 * herself when it is not - and `maker` is always the person whose hands made it.
 * Collapsing the two would hide the entire point of the platform.
 *
 * Stock is the server's number, not ours. The quantity stepper is capped at what
 * `/v1/market/{id}` reported, but the order endpoint checks and decrements stock
 * inside the same transaction that writes the row, so two buyers racing for the last
 * pot cannot both win. A 400 back from it is a real answer to show, not a bug to
 * paper over - which is why the failure path here prints what the server said.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Image, ScrollView, Pressable, Linking } from 'react-native';
import { C, S, T, R, shadow } from '../theme';
import { Btn, Card, Field, Pill, Row, Skeleton, Divider } from '../ui';
import { TopBar } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

function money(n: number, cur = 'INR') {
  const sym = cur === 'INR' ? '₹' : '';
  return `${sym}${Math.round(n).toLocaleString('en-IN')}`;
}

/* ───────────────────────────────────────────────────────── quantity stepper */

function Stepper({
  value, max, onChange,
}: { value: number; max: number; onChange: (n: number) => void }) {
  const { t } = useI18n();
  const btn = (label: string, to: number, enabled: boolean) => (
    <Pressable
      onPress={() => enabled && onChange(to)}
      disabled={!enabled}
      accessibilityRole="button"
      accessibilityLabel={label === '−' ? t('market.less') : t('market.more')}
      hitSlop={8}
      style={({ pressed }) => ({
        width: 46, height: 46, borderRadius: R.md,
        alignItems: 'center', justifyContent: 'center',
        backgroundColor: enabled ? C.surface : C.bgAlt,
        borderWidth: 1, borderColor: enabled ? C.lineStrong : C.line,
        opacity: pressed ? 0.6 : 1,
      })}
    >
      <Text style={{ fontFamily: 'Mukta_700Bold', fontSize: 21,
                     color: enabled ? C.ink : C.inkSoft, marginTop: -2 }}>
        {label}
      </Text>
    </Pressable>
  );
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.md }}>
      {btn('−', value - 1, value > 1)}
      <Text style={[T.title, { fontSize: 22, minWidth: 40, textAlign: 'center' }]}>
        {value}
      </Text>
      {btn('+', value + 1, value < max)}
      <Text style={[T.micro, { flex: 1 }]}>
        {t('market.inStock', { n: max })}
      </Text>
    </View>
  );
}

/* ───────────────────────────────────────────────────────────────── the screen */

export default function MarketProduct({
  id, onBack, onOrdered,
}: {
  id: string;
  onBack: () => void;
  /** Handed the new order id so the caller can show its tracker. */
  onOrdered?: (orderId: string) => void;
}) {
  const { t } = useI18n();
  const [item, setItem] = useState<api.MarketDetail | null>(null);
  const [err, setErr] = useState('');
  const [qty, setQty] = useState(1);

  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [address, setAddress] = useState('');
  const [buying, setBuying] = useState(false);
  const [orderErr, setOrderErr] = useState('');
  const [placed, setPlaced] = useState<{ id: string; amount: number;
                                         upiLink?: string } | null>(null);

  const load = useCallback(async () => {
    setErr('');
    try {
      const r = await api.marketItem(id);
      setItem(r);
      setQty((q) => Math.min(Math.max(q, 1), Math.max(r.quantity, 1)));
    } catch (e: any) {
      setErr(e?.message || 'Could not load this product.');
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const ready = name.trim().length > 1 && phone.trim().length >= 10
                && address.trim().length > 8;

  async function buy() {
    if (!item) return;
    setBuying(true);
    setOrderErr('');
    try {
      const r = await api.placeOrder({
        listingId: item.id,
        buyerName: name.trim(),
        buyerPhone: phone.trim(),
        address: address.trim(),
        quantity: qty,
      });
      setPlaced({ id: r.id, amount: r.amount, upiLink: r.payLink });
      // The stock the server just decremented is now stale on this screen, and a
      // buyer who taps again should see the real remaining count rather than the one
      // they bought against.
      void load();
    } catch (e: any) {
      setOrderErr(e?.message || 'The order did not go through.');
    } finally {
      setBuying(false);
    }
  }

  if (err) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg }}>
        <TopBar title={t('market.product')} onBack={onBack} />
        <View style={{ padding: S.lg }}>
          <Card tone="danger">
            <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
              {t('market.gone')}
            </Text>
            <Text style={T.bodySoft}>{err}</Text>
            <Btn label={t('market.retry')} tone="tonal" onPress={load} />
            <Btn label={t('market.backToShop')} tone="ghost" onPress={onBack} />
          </Card>
        </View>
      </View>
    );
  }

  if (!item) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg }}>
        <TopBar title={t('market.product')} onBack={onBack} />
        <View style={{ padding: S.lg, gap: S.md }}>
          <Skeleton h={240} r={14} />
          <Skeleton h={22} w="70%" />
          <Skeleton h={18} w="40%" />
          <Skeleton h={60} />
        </View>
      </View>
    );
  }

  const total = (item.price || 0) * qty;

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar title={t('market.product')} subtitle={item.maker || undefined}
              onBack={onBack} />
      <ScrollView
        contentContainerStyle={{ padding: S.lg, paddingBottom: S.xl * 2, gap: S.md }}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
      >
        {item.imageUrl ? (
          <View style={[{ borderRadius: R.lg, overflow: 'hidden',
                          backgroundColor: C.bgAlt, borderWidth: 1,
                          borderColor: C.line }, shadow(1)]}>
            <Image source={{ uri: item.imageUrl }} resizeMode="cover"
                   style={{ width: '100%', aspectRatio: 1 }} />
          </View>
        ) : null}

        <View style={{ gap: 6 }}>
          <Text style={[T.title, { fontSize: 22 }]}>{item.title}</Text>
          {item.titleHi && item.titleHi !== item.title ? (
            <Text style={[T.body, { color: C.inkMid }]}>{item.titleHi}</Text>
          ) : null}
          <Text style={[T.title, { fontSize: 26, color: C.primaryDeep }]}>
            {money(item.price, item.currency)}
          </Text>
        </View>

        {/* Who made it, and who is selling it. Both, always - see the file header. */}
        <Card tone="soft">
          <Row label={t('market.maker')} value={item.maker || '—'} />
          {item.seller && item.seller !== item.maker ? (
            <Row
              label={item.sellerKind === 'cluster'
                ? t('market.soldByCluster') : t('market.soldBy')}
              value={item.seller}
            />
          ) : null}
          {item.makerCluster ? (
            <Row label={t('market.craftCluster')} value={item.makerCluster} />
          ) : null}
        </Card>

        {item.description ? (
          <Card>
            <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
              {t('market.about')}
            </Text>
            <Text style={T.bodySoft}>{item.description}</Text>
            {item.descriptionHi ? (
              <>
                <Divider />
                <Text style={T.bodySoft}>{item.descriptionHi}</Text>
              </>
            ) : null}
          </Card>
        ) : null}

        <Card>
          {item.category ? (
            <Row label={t('market.category')} value={item.category} />
          ) : null}
          {item.hsn ? <Row label="HSN" value={item.hsn} /> : null}
          {item.weightG ? (
            <Row label={t('market.weight')} value={`${item.weightG} g`} />
          ) : null}
          {item.dimensionsCm?.length ? (
            <Row
              label={t('market.size')}
              value={`${item.dimensionsCm.length} × ${item.dimensionsCm.breadth} × `
                     + `${item.dimensionsCm.height} cm`}
            />
          ) : null}
          {/* Built from api.API.url, which is the base the app actually resolved, so
              this one needs no localise() - it was never the server's baked
              PUBLIC_BASE_URL to begin with. */}
          {item.passportId ? (
            <Pressable onPress={() => Linking.openURL(
              `${api.API.url}/passport/${item.passportId}`)}>
              <Text style={[T.micro, { color: C.indigoDeep,
                                       fontFamily: 'Mukta_600SemiBold' }]}>
                {t('market.passport')}
              </Text>
            </Pressable>
          ) : null}
        </Card>

        {/* ── the order ──────────────────────────────────────────────────── */}

        {placed ? (
          <Card tone="money">
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
              {t('market.placed')}
            </Text>
            <Row label={t('market.orderId')} value={placed.id} />
            <Row label={t('market.total')} value={money(placed.amount, item.currency)} />
            <Text style={T.bodySoft}>{t('market.placedNext')}</Text>
            {placed.upiLink ? (
              <Btn label={t('market.payUpi')} tone="money"
                   onPress={() => Linking.openURL(placed.upiLink!)} />
            ) : null}
            {onOrdered ? (
              <Btn label={t('market.track')} tone="indigo"
                   onPress={() => onOrdered(placed.id)} />
            ) : null}
            <Btn label={t('market.backToShop')} tone="ghost" onPress={onBack} />
          </Card>
        ) : item.quantity <= 0 ? (
          <Card tone="warn">
            <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
              {t('market.soldOut')}
            </Text>
            <Text style={T.bodySoft}>{t('market.soldOutWhy')}</Text>
          </Card>
        ) : (
          <>
            <Card>
              <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
                {t('market.howMany')}
              </Text>
              <Stepper value={qty} max={item.quantity} onChange={setQty} />
              <Divider />
              <Row label={t('market.total')} value={money(total, item.currency)} />
            </Card>

            <Card>
              <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
                {t('market.whereTo')}
              </Text>
              <Field label={t('market.yourName')} value={name} onChange={setName}
                     placeholder={t('market.yourNameHint')} />
              <Field label={t('market.yourPhone')} value={phone} onChange={setPhone}
                     numeric
                     placeholder={t('market.yourPhoneHint')} />
              <Field label={t('market.yourAddress')} value={address}
                     onChange={setAddress} multiline
                     placeholder={t('market.yourAddressHint')} />
            </Card>

            {orderErr ? (
              <Card tone="danger">
                <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
                  {t('market.orderFailed')}
                </Text>
                {/* The server's own sentence. "not enough stock" means somebody else
                    bought it while this form was being filled in, and that is worth
                    saying plainly rather than translating into a generic error. */}
                <Text style={T.bodySoft}>{orderErr}</Text>
              </Card>
            ) : null}

            <Btn
              label={t('market.placeOrder')}
              sub={`${qty} × ${item.title.slice(0, 22)} · ${money(total, item.currency)}`}
              tone="primary"
              large
              busy={buying}
              disabled={!ready || buying}
              onPress={buy}
            />
            {!ready ? (
              <Text style={[T.micro, { textAlign: 'center' }]}>
                {t('market.needDetails')}
              </Text>
            ) : null}
          </>
        )}
      </ScrollView>
    </View>
  );
}
