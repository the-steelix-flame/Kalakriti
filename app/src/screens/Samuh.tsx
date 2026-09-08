import React, { useState } from 'react';
import { View, Text } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Screen, Card, Btn, Pill, Row, Divider, money } from '../ui';
import { C, GRAD, S, T, R, shadow } from '../theme';
import { Users, Check, Shield, Warning } from '../icons';
import { useI18n } from '../i18n';

/**
 * SAMUH — cluster order aggregation.
 *
 * The problem no cataloguing app addresses: a B2B buyer wants 500 units in 45 days,
 * a single weaver can make 40, so the order goes to a trader who subcontracts the
 * cluster at a fraction of the price. Digitising one artisan's catalogue does not
 * change that at all - it just gives the middleman a better photo.
 *
 * Here the backend forms an ad-hoc consortium: matches artisans by capacity, craft and
 * quality history, splits the quantity, issues one consolidated quotation, and splits
 * payment on delivery through escrow. The cluster becomes a supplier that can actually
 * accept the order.
 */
const MEMBERS = [
  { name: 'Sunita Devi', v: 'ART-4471', units: 40, done: 12, you: true },
  { name: 'ART 4402', v: 'ART-4402', units: 45, done: 20, you: false },
  { name: 'ART 4418', v: 'ART-4418', units: 38, done: 9, you: false },
  { name: 'ART 4431', v: 'ART-4431', units: 52, done: 31, you: false },
  { name: 'ART 4447', v: 'ART-4447', units: 35, done: 5, you: false },
];

export default function Samuh({ onBack }: { onBack: () => void }) {
  const { t } = useI18n();
  const [joined, setJoined] = useState(false);

  const total = 500;
  const committed = 210 + 175;      // this consortium plus nine other members
  const pct = Math.round((committed / total) * 100);

  return (
    <Screen title={t('samuh.title')} subtitle={t('samuh.subtitle')} onBack={onBack}>
      <LinearGradient colors={GRAD.indigo} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                      style={[{ borderRadius: R.xl, padding: S.lg, gap: S.sm }, shadow(3)]}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <Users color={C.primaryLite} size={20} />
          <Text style={[T.micro, { color: C.primaryLite, flex: 1 }]}>{t('samuh.badge')}</Text>
        </View>
        <Text style={[T.title, { color: C.white }]}>{t('samuh.headline')}</Text>
        <Text style={[T.bodySoft, { color: 'rgba(255,255,255,0.8)' }]}>{t('samuh.buyer')}</Text>

        <View style={{ marginTop: S.sm, gap: 6 }}>
          <View style={{ flexDirection: 'row' }}>
            <Text style={[T.micro, { color: 'rgba(255,255,255,0.7)', flex: 1 }]}>{t('samuh.filled')}</Text>
            <Text style={[T.micro, { color: C.white }]}>{committed} / {total}</Text>
          </View>
          <View style={{ height: 10, borderRadius: 99,
                         backgroundColor: 'rgba(255,255,255,0.18)' }}>
            <View style={{ height: 10, borderRadius: 99, width: `${pct}%`,
                           backgroundColor: C.primaryLite }} />
          </View>
        </View>
      </LinearGradient>

      <Card tone="danger">
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <Warning color={C.danger} size={20} />
          <Text style={[T.micro, { color: C.danger }]}>{t('samuh.whyAlone')}</Text>
        </View>
        <Text style={T.bodySoft}>{t('samuh.whyAloneBody')}<Text style={{ fontFamily: 'Mukta_700Bold', color: C.ink }}>
            {' '}{t('samuh.whyAloneStrong')}
          </Text>
        </Text>
      </Card>

      <Card>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <Users color={C.inkMid} size={20} />
          <Text style={[T.section, { flex: 1 }]}>{t('samuh.members', { n: 14 })}</Text>
          <Pill text={t('samuh.splitBy')} tone="indigo" />
        </View>
        <Text style={T.bodySoft}>{t('samuh.splitNote')}</Text>
        <Divider />
        {MEMBERS.map((m) => {
          const you = m.you;
          return (
            <View key={m.v} style={{ paddingVertical: 9, gap: 6 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                <View style={{
                  width: 36, height: 36, borderRadius: R.pill,
                  backgroundColor: you ? C.primary : C.bgAlt,
                  alignItems: 'center', justifyContent: 'center',
                }}>
                  <Text style={[T.body, {
                    fontFamily: 'Mukta_700Bold', fontSize: 15,
                    color: you ? C.white : C.inkMid,
                  }]}>{m.name.charAt(0)}</Text>
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={[T.bodySoft, { color: C.ink }]}>
                    {m.name}{you ? `  ·  ${t('samuh.you')}` : ''}
                  </Text>
                  <Text style={[T.micro, { fontSize: 12 }]}>{t('samuh.units', { n: m.units })}</Text>
                </View>
                <Text style={[T.micro, { color: C.money }]}>{m.done}/{m.units}</Text>
              </View>
              <View style={{ height: 6, borderRadius: 99, backgroundColor: C.bgAlt }}>
                <View style={{
                  height: 6, borderRadius: 99,
                  width: `${Math.round((m.done / m.units) * 100)}%`,
                  backgroundColor: you ? C.primary : C.money,
                }} />
              </View>
            </View>
          );
        })}
        <Text style={[T.micro, { marginTop: 4 }]}>{t('samuh.andMore')}</Text>
      </Card>

      <Card tone="money">
        <Text style={T.label}>{t('samuh.yourShare')}</Text>
        <Row label={t('samuh.youMake')} value={t('samuh.units', { n: 40 })} />
        <Row label={t('samuh.perPiece')} value={money(5200)} />
        <Divider />
        <Row label={t('samuh.payout')} value={money(208000)} strong />
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm,
                       marginTop: S.sm }}>
          <Shield color={C.moneyDeep} size={18} />
          <Text style={[T.bodySoft, { flex: 1, fontSize: 15 }]}>{t('samuh.escrow')}</Text>
        </View>
      </Card>

      {joined ? (
        <Card tone="money" style={{ alignItems: 'center', paddingVertical: S.xl, gap: S.sm }}>
          <View style={{
            width: 64, height: 64, borderRadius: R.pill, backgroundColor: C.money,
            alignItems: 'center', justifyContent: 'center',
          }}>
            <Check color={C.white} size={34} />
          </View>
          <Text style={T.section}>{t('samuh.joined')}</Text>
          <Text style={[T.bodySoft, { textAlign: 'center' }]}>{t('samuh.joinedSub')}</Text>
        </Card>
      ) : (
        <Btn label={t('samuh.join')} sub={t('samuh.joinSub', { units: 40, days: 45 })}
             tone="money" large icon={<Users color={C.white} size={24} />}
             onPress={() => setJoined(true)} />
      )}
    </Screen>
  );
}
