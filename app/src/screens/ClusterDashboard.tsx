/**
 * The Cluster Creator's side: create a cluster, hand out the invite code, see who
 * joined and how much they can actually make.
 *
 * Two screens' worth of work in one file because they are the same screen at two
 * moments: before a cluster exists it is a form, after it exists it is a dashboard.
 * Splitting them would mean routing between two states of one thing.
 *
 * The invite code is rendered large, as a QR code *and* as readable characters. Both
 * are needed and for different people: the QR for a phone that can scan, the letters
 * for a field officer reading it down a phone line to somebody who will write it on
 * paper. The alphabet excludes look-alike characters for exactly that second case.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView, Alert } from 'react-native';
import QRCode from 'react-native-qrcode-svg';
import { Card, Btn, Pill, Row, Divider, Skeleton, Field } from '../ui';
import { C, S, T, R } from '../theme';
import { Users, Shield, Warning, Plus } from '../icons';
import { TopBar } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

export default function ClusterDashboard({
  onBack, onLanguage, onChanged, onGoodsReceipts,
}: {
  onBack: () => void;
  onLanguage: () => void;
  onChanged?: () => void;
  onGoodsReceipts?: (clusterId: string) => void;
}) {
  const { t } = useI18n();
  const [owned, setOwned] = useState<api.Cluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const [name, setName] = useState('');
  const [craft, setCraft] = useState('');
  const [district, setDistrict] = useState('');
  const [state, setState] = useState('');
  const [commission, setCommission] = useState('8');
  const [maxUnits, setMaxUnits] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const m = await api.myClusters();
      const full = await Promise.all(
        (m.owned || []).map((c) => api.getCluster(c.id).catch(() => c)));
      setOwned(full);
    } catch (e: any) {
      setErr(e?.message || 'Could not load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const create = async () => {
    setBusy(true);
    setErr('');
    try {
      await api.createCluster({
        name: name.trim(),
        craftCategory: craft.trim(),
        commissionPct: Number(commission) || 0,
        maxOrderUnits: Number(maxUnits) || 0,
        district: district.trim(),
        state: state.trim(),
      });
      setName(''); setCraft(''); setDistrict(''); setState(''); setMaxUnits('');
      await load();
      onChanged?.();
    } catch (e: any) {
      // The server's `why` is written to be shown, so show it rather than a code.
      setErr(e?.message || 'Could not create the cluster');
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar title={t('cd.title')} onBack={onBack} onLanguage={onLanguage} />
      <ScrollView contentContainerStyle={{ padding: S.lg, paddingBottom: 120, gap: S.md }}
                  showsVerticalScrollIndicator={false}>

        {err ? (
          <Card tone="danger" style={{ flexDirection: 'row', gap: S.sm }}>
            <Warning color={C.danger} size={18} />
            <Text style={[T.micro, { color: C.danger, flex: 1 }]}>{err}</Text>
          </Card>
        ) : null}

        {loading ? <Skeleton h={160} /> : null}

        {owned.map((c) => {
          const members = c.members?.filter((m) => m.status === 'active') || [];
          const capacity = members.reduce((n, m) => n + (m.capacityAvailable || 0), 0);
          return (
            <Card key={c.id} style={{ gap: S.md }}>
              <View style={{ gap: 2 }}>
                <Text style={[T.body, { fontFamily: 'Mukta_800ExtraBold' }]}>{c.name}</Text>
                <Text style={[T.micro, { color: C.inkSoft }]}>
                  {[c.craftCategory, c.district, c.state].filter(Boolean).join(' · ')}
                </Text>
              </View>

              <View style={{ flexDirection: 'row', gap: S.sm, flexWrap: 'wrap' }}>
                <Pill text={t('clu.commission', { pct: c.commissionPct })} tone="warn" />
                <Pill text={t('clu.members', { n: members.length })} tone="indigo" />
              </View>

              {/* The invite code, both ways at once. */}
              <Card tone="indigo" style={{ gap: S.md, alignItems: 'center' }}>
                <Text style={[T.label, { color: C.indigoDeep }]}>{t('cd.invite')}</Text>
                <View style={{ backgroundColor: C.white, padding: 10, borderRadius: R.md }}>
                  <QRCode value={c.inviteCode} size={120} color={C.indigoDeep}
                          backgroundColor={C.white} />
                </View>
                <Text style={{
                  fontFamily: 'Mukta_800ExtraBold', fontSize: 30, letterSpacing: 6,
                  color: C.indigoDeep,
                }}>
                  {c.inviteCode}
                </Text>
                <Text style={[T.micro, { color: C.inkMid, textAlign: 'center' }]}>
                  {t('cd.inviteSub')}
                </Text>
              </Card>

              <Divider />

              {onGoodsReceipts ? (
                <Btn label={t('grn.title')} tone="indigo"
                     sub={t('grn.sub')}
                     onPress={() => onGoodsReceipts(c.id)} />
              ) : null}

              <Text style={[T.label, {}]}>{t('cd.memberList')}</Text>
              {members.length === 0 ? (
                <Text style={[T.micro, { color: C.inkSoft }]}>{t('cd.noMembers')}</Text>
              ) : (
                <>
                  {members.map((m) => (
                    <Row key={m.id}
                         label={m.name || m.phone}
                         value={`${m.capacityAvailable} / ${m.capacityUnits}`} />
                  ))}
                  <Text style={[T.micro, { color: C.inkSoft }]}>
                    {t('cd.totalCapacity', { n: capacity, m: members.length })}
                  </Text>
                </>
              )}
            </Card>
          );
        })}

        {/* Creating another cluster is legitimate - a manager may run one per craft. */}
        <Card style={{ gap: S.md }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
            <Plus color={C.primary} size={18} />
            <Text style={[T.label, { flex: 1 }]}>{t('cd.create')}</Text>
          </View>

          <Card tone="warn" style={{ flexDirection: 'row', gap: S.sm }}>
            <Shield color={C.gold} size={18} />
            <Text style={[T.micro, { color: C.inkMid, flex: 1 }]}>
              {t('role.clusterSub')}
            </Text>
          </Card>

          <Field label={t('cd.name')} value={name} onChange={setName}
                 placeholder={t('cd.namePlaceholder')} />
          <Field label={t('cd.craft')} value={craft} onChange={setCraft} />
          <View style={{ flexDirection: 'row', gap: S.md }}>
            <View style={{ flex: 1 }}>
              <Field label={t('cd.district')} value={district} onChange={setDistrict} />
            </View>
            <View style={{ flex: 1 }}>
              <Field label={t('cd.state')} value={state} onChange={setState} />
            </View>
          </View>
          <View style={{ flexDirection: 'row', gap: S.md }}>
            <View style={{ flex: 1 }}>
              <Field label={t('cd.commissionPct')} value={commission}
                     onChange={setCommission} numeric suffix="%" />
            </View>
            <View style={{ flex: 1 }}>
              <Field label={t('cd.maxUnits')} value={maxUnits} onChange={setMaxUnits}
                     numeric />
            </View>
          </View>

          <Text style={[T.micro, { color: C.inkSoft }]}>{t('clu.commissionWhy')}</Text>

          <Btn label={t('cd.createBtn')} tone="primary" busy={busy}
               disabled={!name.trim()} onPress={create} />
        </Card>
      </ScrollView>
    </View>
  );
}
