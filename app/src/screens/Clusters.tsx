/**
 * Clusters: find one, read its terms, join it, leave it.
 *
 * This is the screen the removed "Samuh" screen should have been. That one drew a
 * fourteen-member consortium out of a hardcoded array and a Join button that set a
 * boolean. Every number on this screen comes from the database, and where there is no
 * number yet it says so in words rather than showing a zero - a cluster nobody has
 * rated is not a cluster rated badly.
 *
 * The ordering of this screen is the argument. Commission, who carries the GST, and
 * which marketplaces the work will actually reach are all above the Join button, not
 * behind it. The addendum is explicit that visible terms are the whole difference
 * between this and the middleman it replaces, and a term you have to tap to find is
 * not visible.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView, Pressable, Alert } from 'react-native';
import { Card, Btn, Pill, Row, Divider, Skeleton, Field } from '../ui';
import { C, S, T, R } from '../theme';
import { Users, Rupee, Warning, Shield, Tag } from '../icons';
import { TopBar } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

type Tab = 'mine' | 'browse';

function pct(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/* ───────────────────────────────────────────────────────── terms, in full */

function Terms({ c }: { c: api.Cluster }) {
  const { t } = useI18n();
  return (
    <Card tone="indigo" style={{ gap: S.sm }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
        <Shield color={C.indigo} size={18} />
        <Text style={[T.label, { color: C.indigoDeep, flex: 1 }]}>
          {t('clu.beforeYouJoin')}
        </Text>
      </View>
      <Text style={[T.micro, { color: C.inkMid }]}>
        {'• ' + t('clu.terms1')}
      </Text>
      <Text style={[T.micro, { color: C.inkMid }]}>
        {'• ' + t('clu.terms2', { pct: pct(c.commissionPct) })}
      </Text>
      <Text style={[T.micro, { color: C.inkMid }]}>
        {'• ' + t('clu.terms3')}
      </Text>
    </Card>
  );
}

/* ───────────────────────────────────────────────────── one cluster's card */

function ClusterCard({
  c, onJoin, onLeave, busy, expanded, onToggle,
}: {
  c: api.Cluster;
  onJoin: (c: api.Cluster) => void;
  onLeave: (c: api.Cluster) => void;
  busy: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { t } = useI18n();
  const joined = c.viewerMembership?.status === 'active';
  const live = c.platforms.filter((p) => p.live);

  return (
    <Card style={{ gap: S.md }}>
      <Pressable onPress={onToggle} accessibilityRole="button">
        <View style={{ gap: 4 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
            <Text style={[T.body, { fontFamily: 'Mukta_800ExtraBold', flex: 1 }]}>
              {c.name}
            </Text>
            {joined ? <Pill text={t('clu.joined')} tone="good" /> : null}
          </View>
          <Text style={[T.micro, { color: C.inkSoft }]}>
            {[c.craftCategory, c.district, c.state].filter(Boolean).join(' · ')}
          </Text>
        </View>
      </Pressable>

      {/* The three numbers that decide whether to join, before anything else. */}
      <View style={{ flexDirection: 'row', gap: S.sm, flexWrap: 'wrap' }}>
        <Pill text={t('clu.commission', { pct: pct(c.commissionPct) })} tone="warn"
              icon={<Rupee color={C.gold} size={13} />} />
        <Pill text={t('clu.members', { n: c.memberCount })} tone="indigo"
              icon={<Users color={C.indigo} size={13} />} />
        {c.capacityAvailableUnits > 0 ? (
          <Pill text={t('clu.capacity', { n: c.capacityAvailableUnits })} tone="soft" />
        ) : null}
      </View>

      {/* Who actually carries the tax liability. */}
      {c.ownerName ? (
        <Text style={[T.micro, { color: C.inkMid }]}>
          {t('clu.owner', { name: c.ownerName })}
          {c.ownerGstinLast4 ? '  ·  ' + t('clu.gstin', { last4: c.ownerGstinLast4 }) : ''}
        </Text>
      ) : null}

      {expanded ? (
        <>
          <Divider />

          {/* Where the work actually ends up. A channel the owner is not ready for
              is listed as not connected rather than quietly omitted. */}
          <View style={{ gap: 6 }}>
            <Text style={[T.label, {}]}>{t('clu.platforms')}</Text>
            {live.length === 0 ? (
              <Text style={[T.micro, { color: C.inkSoft }]}>
                {t('clu.platformsNone')}
              </Text>
            ) : null}
            <View style={{ flexDirection: 'row', gap: S.sm, flexWrap: 'wrap' }}>
              {c.platforms.map((p) => (
                <Pill key={p.channel} text={p.live ? p.name : `${p.name} — ${t('clu.notLive')}`}
                      tone={p.live ? 'good' : 'soft'} />
              ))}
            </View>
          </View>

          {/* Rating, or an honest statement that there is none. Never zero stars. */}
          <View style={{ gap: 6 }}>
            <Text style={[T.label, {}]}>{t('clu.rating')}</Text>
            {c.rating.available ? (
              <>
                <Row label={t('clu.paidOnTime')} value={`${c.rating.paidOnTime} / 5`} />
                <Row label={t('clu.commissionFair')}
                     value={`${c.rating.commissionFair} / 5`} />
                <Row label={t('clu.ordersRegular')}
                     value={`${c.rating.ordersRegular} / 5`} />
              </>
            ) : (
              <Text style={[T.micro, { color: C.inkSoft }]}>
                {c.rating.why || t('clu.ratingNone')}
              </Text>
            )}
          </View>

          {/* What it has actually paid, which is the only claim that matters. */}
          <View style={{ gap: 6 }}>
            <Text style={[T.label, {}]}>{t('clu.payouts')}</Text>
            <Text style={[T.micro, { color: C.inkSoft }]}>
              {c.payouts.available
                ? t('clu.payoutAvg', { amount: `₹${c.payouts.averagePerArtisan}` })
                : t('clu.payoutsNone')}
            </Text>
          </View>

          <Card tone="warn" style={{ gap: 2 }}>
            <Text style={[T.label, { color: C.gold }]}>{t('clu.commissionWhat')}</Text>
            <Text style={[T.micro, { color: C.inkMid }]}>{t('clu.commissionWhy')}</Text>
          </Card>

          {!joined ? <Terms c={c} /> : null}

          {joined ? (
            <Btn label={t('clu.leave')} tone="ghost" busy={busy}
                 onPress={() => onLeave(c)} />
          ) : (
            <Btn label={t('clu.join')} tone="primary" busy={busy}
                 onPress={() => onJoin(c)} />
          )}
        </>
      ) : null}
    </Card>
  );
}

/* ────────────────────────────────────────────────────────────── the screen */

export default function Clusters({
  onBack, onLanguage, onChanged,
}: {
  onBack: () => void;
  onLanguage: () => void;
  onChanged?: () => void;
}) {
  const { t } = useI18n();
  const [tab, setTab] = useState<Tab>('mine');
  const [mine, setMine] = useState<api.Cluster[]>([]);
  const [owned, setOwned] = useState<api.Cluster[]>([]);
  const [open, setOpen] = useState<api.Cluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState('');
  const [expanded, setExpanded] = useState('');
  const [code, setCode] = useState('');
  const [codeBusy, setCodeBusy] = useState(false);
  const [codeErr, setCodeErr] = useState('');
  const [found, setFound] = useState<api.Cluster | null>(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setErr('');
    try {
      const [m, b] = await Promise.all([
        api.myClusters().catch(() => ({ clusters: [], owned: [] })),
        api.browseClusters(),
      ]);
      setMine(m.clusters || []);
      setOwned(m.owned || []);
      setOpen(b.clusters || []);
      if (!(m.clusters || []).length) setTab('browse');
    } catch (e: any) {
      setErr(e?.message || 'Could not load clusters');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const checkCode = async () => {
    setCodeBusy(true);
    setCodeErr('');
    setFound(null);
    try {
      setFound(await api.clusterByCode(code));
    } catch (e: any) {
      setCodeErr(e?.message || t('clu.codeBad'));
    } finally {
      setCodeBusy(false);
    }
  };

  const join = async (c: api.Cluster) => {
    setBusyId(c.id);
    try {
      await api.joinCluster(c.id);
      setFound(null);
      setCode('');
      await load();
      setTab('mine');
      onChanged?.();
    } catch (e: any) {
      Alert.alert('', e?.message || 'Could not join');
    } finally {
      setBusyId('');
    }
  };

  const leave = (c: api.Cluster) => {
    Alert.alert(c.name, t('clu.leaveConfirm'), [
      { text: '✕', style: 'cancel' },
      {
        text: t('clu.leave'),
        style: 'destructive',
        onPress: async () => {
          setBusyId(c.id);
          try {
            await api.leaveCluster(c.id);
            await load();
            onChanged?.();
          } catch (e: any) {
            Alert.alert('', e?.message || 'Could not leave');
          } finally {
            setBusyId('');
          }
        },
      },
    ]);
  };

  const list = tab === 'mine' ? mine : open;

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar title={t('clu.title')} onBack={onBack} onLanguage={onLanguage} />
      <ScrollView contentContainerStyle={{ padding: S.lg, paddingBottom: 120, gap: S.md }}
                  showsVerticalScrollIndicator={false}>

        <Text style={[T.micro, { color: C.inkMid }]}>{t('clu.explain')}</Text>

        {/* Two tabs, not a filter bar: "mine" and "all of them" are different
            questions, and an artisan with one cluster should land on it. */}
        <View style={{ flexDirection: 'row', gap: S.sm }}>
          {(['mine', 'browse'] as Tab[]).map((k) => (
            <Pressable key={k} onPress={() => setTab(k)} style={{ flex: 1 }}>
              <View style={{
                paddingVertical: 12, borderRadius: R.pill, alignItems: 'center',
                backgroundColor: tab === k ? C.indigo : C.surface,
                borderWidth: 1, borderColor: tab === k ? C.indigo : C.line,
              }}>
                <Text style={[T.label, { color: tab === k ? C.white : C.inkMid }]}>
                  {k === 'mine' ? t('clu.mine') : t('clu.browse')}
                </Text>
              </View>
            </Pressable>
          ))}
        </View>

        {err ? (
          <Card tone="danger" style={{ flexDirection: 'row', gap: S.sm }}>
            <Warning color={C.danger} size={18} />
            <Text style={[T.micro, { color: C.danger, flex: 1 }]}>{err}</Text>
          </Card>
        ) : null}

        {/* Join by code sits on the browse tab, because the realistic path is a
            field officer onboarding somebody in person, not a search. */}
        {tab === 'browse' ? (
          <Card style={{ gap: S.sm }}>
            <Text style={[T.label, {}]}>{t('clu.joinByCode')}</Text>
            <Field label="" value={code} onChange={(v) => { setCode(v); setFound(null); }}
                   placeholder={t('clu.codePlaceholder')} />
            <Btn label={t('clu.codeCheck')} tone="tonal" busy={codeBusy}
                 onPress={checkCode} disabled={code.trim().length < 4} />
            {codeErr ? (
              <Text style={[T.micro, { color: C.danger }]}>{codeErr}</Text>
            ) : null}
            {found ? (
              <ClusterCard c={found} expanded onToggle={() => {}}
                           onJoin={join} onLeave={leave} busy={busyId === found.id} />
            ) : null}
          </Card>
        ) : null}

        {/* A cluster this artisan owns is theirs to run, not to join. */}
        {tab === 'mine' && owned.length ? (
          <View style={{ gap: S.sm }}>
            <Text style={[T.label, { color: C.inkSoft }]}>{t('cd.title')}</Text>
            {owned.map((c) => (
              <Card key={c.id} tone="money" style={{ gap: 4 }}>
                <Text style={[T.body, { fontFamily: 'Mukta_800ExtraBold' }]}>{c.name}</Text>
                <Text style={[T.micro, { color: C.inkMid }]}>
                  {t('clu.members', { n: c.memberCount })}
                  {'   ·   '}
                  {t('cd.invite')}: {c.inviteCode}
                </Text>
              </Card>
            ))}
          </View>
        ) : null}

        {loading ? (
          <>
            <Skeleton h={120} /><Skeleton h={120} />
          </>
        ) : list.length === 0 ? (
          <Card style={{ gap: 6, alignItems: 'center', paddingVertical: S.xl }}>
            <Users color={C.inkSoft} size={28} />
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
              {tab === 'mine' ? t('clu.none') : t('clu.empty')}
            </Text>
            <Text style={[T.micro, { color: C.inkSoft, textAlign: 'center' }]}>
              {tab === 'mine' ? t('clu.noneSub') : t('clu.emptySub')}
            </Text>
          </Card>
        ) : (
          list.map((c) => (
            <ClusterCard
              key={c.id} c={c}
              expanded={expanded === c.id}
              onToggle={() => setExpanded(expanded === c.id ? '' : c.id)}
              onJoin={join} onLeave={leave} busy={busyId === c.id}
            />
          ))
        )}
      </ScrollView>
    </View>
  );
}
