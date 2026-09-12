/**
 * Recording what physically arrived at the dispatch point.
 *
 * Used standing up, often outdoors, by somebody counting pots out of a sack while
 * the person who made them waits. So the form is three taps and two numbers, the
 * member is picked from a list rather than typed, and the accepted count is shown
 * live as the numbers change - because the one thing the artisan standing there
 * wants to know is how many of hers counted.
 *
 * The rejected field is separate from the received field and not a status dropdown.
 * "Twenty came, two cracked" is the normal case, and a pass/fail toggle would force
 * whoever is logging it to round that to a lie in one direction or the other.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView, Pressable, Alert } from 'react-native';
import { Card, Btn, Pill, Row, Divider, Skeleton, Field, StatTile } from '../ui';
import { C, S, T, R } from '../theme';
import { Users, Check, Warning, Tag } from '../icons';
import { TopBar } from '../nav/Shell';
import { useI18n } from '../i18n';
import { ago } from '../lib/ago';
import * as api from '../lib/api';

function qualityTone(q: api.GoodsReceipt['qualityStatus']):
    'good' | 'warn' | 'danger' | 'soft' {
  return q === 'pass' ? 'good' : q === 'partial' ? 'warn'
    : q === 'reject' ? 'danger' : 'soft';
}

export default function GoodsReceipts({
  clusterId, enquiryId, onBack, onLanguage,
}: {
  clusterId: string;
  enquiryId?: string;
  onBack: () => void;
  onLanguage: () => void;
}) {
  const { t } = useI18n();
  const [cluster, setCluster] = useState<api.Cluster | null>(null);
  const [rows, setRows] = useState<api.GoodsReceipt[]>([]);
  const [tally, setTally] = useState<api.Tally | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const [who, setWho] = useState('');
  const [received, setReceived] = useState('');
  const [rejected, setRejected] = useState('');
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [c, g] = await Promise.all([
        api.getCluster(clusterId),
        api.listReceipts(clusterId, { enquiryId }),
      ]);
      setCluster(c);
      setRows(g.receipts || []);
      setTally(g.tally || null);
    } catch (e: any) {
      setErr(e?.message || 'Could not load');
    } finally {
      setLoading(false);
    }
  }, [clusterId, enquiryId]);

  useEffect(() => { load(); }, [load]);

  const members = (cluster?.members || []).filter((m) => m.status === 'active');
  const recN = Number(received) || 0;
  const rejN = Number(rejected) || 0;
  const accepted = Math.max(recN - rejN, 0);
  const valid = !!who && recN > 0 && rejN >= 0 && rejN <= recN;

  const save = async () => {
    setBusy(true);
    setErr('');
    try {
      const out = await api.logReceipt(clusterId, {
        artisanId: who,
        quantityReceived: recN,
        quantityRejected: rejN,
        enquiryId,
        note: note.trim(),
      });
      setTally(out.tally);
      setReceived(''); setRejected(''); setNote(''); setWho('');
      await load();
    } catch (e: any) {
      setErr(e?.message || 'Could not save');
    } finally {
      setBusy(false);
    }
  };

  const voidRow = (g: api.GoodsReceipt) => {
    Alert.alert(t('grn.voidThis'), t('grn.voidKept'), [
      { text: '✕', style: 'cancel' },
      {
        text: t('grn.voidThis'),
        style: 'destructive',
        onPress: async () => {
          try {
            await api.voidReceipt(g.id, 'cancelled from the app');
            await load();
          } catch (e: any) {
            Alert.alert('', e?.message || 'Could not cancel');
          }
        },
      },
    ]);
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar title={t('grn.title')} subtitle={cluster?.name}
              onBack={onBack} onLanguage={onLanguage} />
      <ScrollView contentContainerStyle={{ padding: S.lg, paddingBottom: 120, gap: S.md }}
                  showsVerticalScrollIndicator={false}>

        <Text style={[T.micro, { color: C.inkMid }]}>{t('grn.explain')}</Text>

        {err ? (
          <Card tone="danger" style={{ flexDirection: 'row', gap: S.sm }}>
            <Warning color={C.danger} size={18} />
            <Text style={[T.micro, { color: C.danger, flex: 1 }]}>{err}</Text>
          </Card>
        ) : null}

        {/* The running total, first, because it is what the room wants to know. */}
        {tally ? (
          <Card tone="indigo" style={{ gap: S.md }}>
            <Text style={[T.label, { color: C.indigoDeep }]}>{t('grn.tally')}</Text>
            <View style={{ flexDirection: 'row', gap: S.sm }}>
              <StatTile label={t('grn.tallyReceived')} value={String(tally.received)} />
              <StatTile label={t('grn.tallyAccepted')} value={String(tally.accepted)} />
              <StatTile label={t('grn.tallyRejected')} value={String(tally.rejected)} />
            </View>
            {tally.targetKnown ? (
              <Text style={[T.micro, { color: C.inkMid }]}>
                {t('grn.target', { n: tally.target ?? 0 })}
                {'   ·   '}
                {tally.complete
                  ? t('grn.complete')
                  : t('grn.shortfall', { n: tally.shortfall ?? 0 })}
              </Text>
            ) : (
              // Never a zero shortfall against an unknown target.
              <Text style={[T.micro, { color: C.inkSoft }]}>{t('grn.noTarget')}</Text>
            )}
          </Card>
        ) : null}

        {/* ── log a delivery ─────────────────────────────────────────────── */}
        <Card style={{ gap: S.md }}>
          <Text style={[T.label, {}]}>{t('grn.log')}</Text>

          <View style={{ gap: 6 }}>
            <Text style={[T.label, { color: C.inkSoft }]}>{t('grn.who')}</Text>
            {members.length === 0 ? (
              <Text style={[T.micro, { color: C.inkSoft }]}>{t('cd.noMembers')}</Text>
            ) : (
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: S.sm }}>
                {members.map((m) => {
                  const on = who === m.artisanId;
                  return (
                    <Pressable key={m.artisanId} onPress={() => setWho(m.artisanId)}>
                      <View style={{
                        paddingVertical: 12, paddingHorizontal: 14,
                        borderRadius: R.pill, minHeight: 48, justifyContent: 'center',
                        backgroundColor: on ? C.primary : C.surface,
                        borderWidth: on ? 0 : 1, borderColor: C.line,
                      }}>
                        <Text style={[T.label, { color: on ? C.white : C.ink }]}>
                          {m.name || m.phone}
                        </Text>
                      </View>
                    </Pressable>
                  );
                })}
              </View>
            )}
          </View>

          <View style={{ flexDirection: 'row', gap: S.md }}>
            <View style={{ flex: 1 }}>
              <Field label={t('grn.received')} value={received}
                     onChange={setReceived} numeric />
            </View>
            <View style={{ flex: 1 }}>
              <Field label={t('grn.rejected')} value={rejected}
                     onChange={setRejected} numeric />
            </View>
          </View>
          <Text style={[T.micro, { color: C.inkSoft }]}>{t('grn.rejectedHelp')}</Text>

          {/* What she will actually be paid for, updating as the numbers change. */}
          {recN > 0 ? (
            <Card tone={rejN > 0 ? 'warn' : 'money'} style={{ gap: 2 }}>
              <Text style={[T.body, { fontFamily: 'Mukta_800ExtraBold',
                                      color: rejN > 0 ? C.gold : C.moneyDeep }]}>
                {t('grn.accepted', { n: accepted })}
              </Text>
              {rejN > 0 ? (
                <Text style={[T.micro, { color: C.inkMid }]}>
                  {t('grn.rejectedN', { n: rejN })}
                </Text>
              ) : null}
            </Card>
          ) : null}

          <Field label={t('grn.note')} value={note} onChange={setNote}
                 placeholder={t('grn.notePlaceholder')} multiline />

          <Btn label={t('grn.save')} tone="primary" busy={busy} disabled={!valid}
               onPress={save} />
        </Card>

        {/* ── per person ─────────────────────────────────────────────────── */}
        {tally && tally.byArtisan.length ? (
          <Card style={{ gap: S.sm }}>
            <Text style={[T.label, {}]}>{t('grn.perPerson')}</Text>
            {tally.byArtisan.map((e) => (
              <Row key={e.artisanId}
                   label={`${e.name || e.phone}${e.stillInCluster ? '' : ' · ' + t('grn.left')}`}
                   value={`${e.accepted}${e.rejected ? `  (−${e.rejected})` : ''}`} />
            ))}
          </Card>
        ) : null}

        {/* ── history ────────────────────────────────────────────────────── */}
        <Text style={[T.label, { color: C.inkSoft }]}>{t('grn.history')}</Text>
        {loading ? (
          <Skeleton h={90} />
        ) : rows.length === 0 ? (
          <Card style={{ gap: 6, alignItems: 'center', paddingVertical: S.xl }}>
            <Tag color={C.inkSoft} size={26} />
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>{t('grn.none')}</Text>
            <Text style={[T.micro, { color: C.inkSoft, textAlign: 'center' }]}>
              {t('grn.noneSub')}
            </Text>
          </Card>
        ) : (
          rows.map((g) => {
            const name = tally?.byArtisan.find((e) => e.artisanId === g.artisanId)?.name
              || members.find((m) => m.artisanId === g.artisanId)?.name
              || g.artisanId;
            const voided = g.qualityStatus === 'void';
            return (
              <Card key={g.id} style={{ gap: 6, opacity: voided ? 0.55 : 1 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                  <Text style={[T.body, { fontFamily: 'Mukta_700Bold', flex: 1 }]}>
                    {name}
                  </Text>
                  <Pill text={t(`grn.${g.qualityStatus}` as any)}
                        tone={qualityTone(g.qualityStatus)} />
                </View>
                <Text style={[T.micro, { color: C.inkMid }]}>
                  {t('grn.accepted', { n: g.quantityAccepted })}
                  {g.quantityRejected
                    ? `   ·   ${t('grn.rejectedN', { n: g.quantityRejected })}` : ''}
                  {g.receivedAt ? `   ·   ${ago(t, g.receivedAt)}` : ''}
                </Text>
                {g.note ? (
                  <Text style={[T.micro, { color: C.inkSoft }]}>{g.note}</Text>
                ) : null}
                {!voided ? (
                  <Pressable onPress={() => voidRow(g)} hitSlop={8}>
                    <Text style={[T.micro, { color: C.danger }]}>{t('grn.voidThis')}</Text>
                  </Pressable>
                ) : null}
              </Card>
            );
          })
        )}
      </ScrollView>
    </View>
  );
}
