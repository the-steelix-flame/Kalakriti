/**
 * Approvals: everything made but not yet selling, and the one action each row needs.
 *
 * "Needs review" is two different jobs and the screen refuses to blur them. A row with
 * no name or no photograph needs the details finishing, and the only useful button is
 * "open it". A row that is complete but still a draft needs publishing, and that button
 * does the publishing here rather than sending somebody back through six steps of the
 * create flow to press the same thing.
 *
 * The split is computed on the server, from what is actually missing on each row, so
 * this screen cannot disagree with the dashboard number that led here.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Image, RefreshControl, Pressable, Alert } from 'react-native';
import { C, S, T, R } from '../theme';
import { Btn, Card, Pill, Skeleton } from '../ui';
import { TabScreen } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

function money(n: number) {
  return `₹${Math.round(n).toLocaleString('en-IN')}`;
}

function Row({
  card, action, onOpen, busy,
}: {
  card: api.DashCard;
  action?: { label: string; onPress: () => void };
  onOpen: () => void;
  busy?: boolean;
}) {
  const { t } = useI18n();
  return (
    <Card onPress={onOpen}>
      <View style={{ flexDirection: 'row', gap: S.md, alignItems: 'center' }}>
        {card.imageUrl ? (
          <Image source={{ uri: card.imageUrl }}
                 style={{ width: 56, height: 56, borderRadius: R.md,
                          backgroundColor: C.bgAlt }} />
        ) : (
          <View style={{ width: 56, height: 56, borderRadius: R.md,
                         backgroundColor: C.bgAlt, alignItems: 'center',
                         justifyContent: 'center' }}>
            <Text style={[T.micro, { fontSize: 10 }]}>{t('appr.noPhoto')}</Text>
          </View>
        )}
        <View style={{ flex: 1, gap: 2 }}>
          <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}
                numberOfLines={1}>
            {card.title || t('appr.untitled')}
          </Text>
          <Text style={T.micro}>
            {card.price ? money(card.price) : t('appr.noPrice')}
            {card.quantity ? ` · ${card.quantity}` : ''}
          </Text>
          {card.missing?.length ? (
            <Text style={[T.micro, { fontSize: 11.5, color: '#7A5B10' }]}>
              {t('appr.missing')}: {card.missing.join(', ')}
            </Text>
          ) : null}
        </View>
      </View>
      {action ? (
        <Btn label={action.label} tone="money" busy={busy} onPress={action.onPress} />
      ) : null}
    </Card>
  );
}

export default function Approvals({
  onLanguage, onOpen,
}: {
  onLanguage: () => void;
  onOpen: (listingId: string) => void;
}) {
  const { t } = useI18n();
  const [data, setData] = useState<
    { needsDetails: api.DashCard[]; readyToPublish: api.DashCard[]; total: number } | null
  >(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [publishing, setPublishing] = useState('');

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setData(await api.dashPendingReviews());
      setErr('');
    } catch (e: any) {
      setErr(e?.message || 'Could not load approvals.');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function publish(id: string, title: string) {
    setPublishing(id);
    try {
      // The storefront needs no GST and no marketplace account, so it is the channel
      // that always works and the right default for a one-tap publish here. Anything
      // else is a decision that belongs in the create flow with its own screen.
      await api.publishListing(id, ['storefront']);
      await load();
      Alert.alert(t('appr.published'), t('appr.publishedBody', { title }));
    } catch (e: any) {
      Alert.alert(t('appr.publishFailed'), e?.message || 'It did not publish.');
    } finally {
      setPublishing('');
    }
  }

  return (
    <TabScreen
      title={t('appr.title')}
      subtitle={data ? t('appr.sub', { n: data.total }) : undefined}
      onLanguage={onLanguage}
      refreshControl={<RefreshControl refreshing={busy} onRefresh={load}
                                      tintColor={C.primary} />}
    >
      {err ? (
        <Card tone="danger">
          <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
            {t('appr.errTitle')}
          </Text>
          <Text style={T.bodySoft}>{err}</Text>
          <Btn label={t('market.retry')} tone="tonal" onPress={load} />
        </Card>
      ) : null}

      {!data && !err ? (
        <>
          <Skeleton h={90} r={14} /><Skeleton h={90} r={14} />
        </>
      ) : null}

      {data && data.total === 0 ? (
        <Card tone="money">
          <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
            {t('appr.allClear')}
          </Text>
          <Text style={T.bodySoft}>{t('appr.allClearBody')}</Text>
        </Card>
      ) : null}

      {data?.readyToPublish.length ? (
        <>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
            <Text style={[T.section, { flex: 1 }]}>{t('appr.readyTitle')}</Text>
            <Pill text={String(data.readyToPublish.length)} tone="good" />
          </View>
          <Text style={T.bodySoft}>{t('appr.readyWhy')}</Text>
          {data.readyToPublish.map((c) => (
            <Row key={c.id} card={c} onOpen={() => onOpen(c.id)}
                 busy={publishing === c.id}
                 action={{ label: t('appr.publishNow'),
                           onPress: () => publish(c.id, c.title) }} />
          ))}
        </>
      ) : null}

      {data?.needsDetails.length ? (
        <>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
            <Text style={[T.section, { flex: 1 }]}>{t('appr.detailsTitle')}</Text>
            <Pill text={String(data.needsDetails.length)} tone="warn" />
          </View>
          <Text style={T.bodySoft}>{t('appr.detailsWhy')}</Text>
          {data.needsDetails.map((c) => (
            <Row key={c.id} card={c} onOpen={() => onOpen(c.id)} />
          ))}
        </>
      ) : null}
    </TabScreen>
  );
}
