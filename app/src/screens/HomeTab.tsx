import React, { useEffect, useState } from 'react';
import { View, Text, Image, Pressable } from 'react-native';
import * as Speech from 'expo-speech';
import { Card, Btn, Pill, Skeleton, money } from '../ui';
import { C, S, T, R } from '../theme';
import { Plus, Speaker, Trend, Users, Camera, Loom } from '../icons';
import { TabScreen } from '../nav/Shell';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

type TrendT = { title: string; detail: string; band: string; confidence: number };

/**
 * Home: one obvious action, then anything unfinished, then advice.
 *
 * The old version opened with a tall indigo gradient carrying invented earnings.
 * Both are gone - the band because it made a phone look like a dashboard, and the
 * numbers because they were fiction. What is left is real: the artisan's own drafts,
 * their account state, and the create button.
 */
export default function HomeTab({
  artisan, drafts, onNew, onResume, onSamuh, onLogin, onLanguage,
}: {
  artisan: api.Artisan | null;
  drafts: api.Listing[];
  onNew: () => void;
  onResume: (id: string) => void;
  onSamuh: () => void;
  onLogin: () => void;
  onLanguage: () => void;
}) {
  const { t, locale } = useI18n();
  const [trends, setTrends] = useState<TrendT[] | null>(null);

  useEffect(() => {
    let alive = true;
    api.trends('varanasi-handloom')
      .then((r) => { if (alive) setTrends(r.trends || []); })
      .catch(() => { if (alive) setTrends([]); });
    return () => { alive = false; };
  }, []);

  const name = artisan?.fullName?.trim();

  return (
    <TabScreen
      title={name ? `${t('home.greeting')}, ${name}` : t('home.greeting')}
      subtitle={artisan
        ? `+91 ${artisan.phone} · ${t('home.verified')}`
        : t('home.guest')}
      onLanguage={onLanguage}
    >
      <Btn
        label={t('home.addProduct')}
        sub={t('home.addProductSub')}
        icon={<Plus color={C.white} size={26} />}
        onPress={onNew}
        large
      />

      <Card tone="soft" onPress={() => {
        try { Speech.speak(t('home.helpSpoken'), { language: locale }); } catch {}
      }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.md }}>
          <View style={{
            width: 42, height: 42, borderRadius: R.pill, backgroundColor: C.surface,
            alignItems: 'center', justifyContent: 'center',
          }}>
            <Speaker color={C.primaryDeep} size={20} />
          </View>
          <Text style={[T.bodySoft, { flex: 1, color: C.primaryDeep }]}>
            {t('home.help')}
          </Text>
        </View>
      </Card>

      {drafts.length ? (
        <>
          <Text style={T.section}>{t('home.drafts')}</Text>
          {drafts.map((d) => (
            <Card key={d.id} onPress={() => onResume(d.id)}>
              <View style={{ flexDirection: 'row', gap: S.md, alignItems: 'center' }}>
                {d.imageUrl ? (
                  <Image source={{ uri: d.imageUrl }}
                         style={{ width: 58, height: 58, borderRadius: R.md,
                                  backgroundColor: C.bgAlt }} />
                ) : (
                  <View style={{ width: 58, height: 58, borderRadius: R.md,
                                 backgroundColor: C.bgAlt, alignItems: 'center',
                                 justifyContent: 'center' }}>
                    <Camera color={C.inkSoft} size={22} />
                  </View>
                )}
                <View style={{ flex: 1 }}>
                  <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}
                        numberOfLines={1}>
                    {d.titleHi || d.titleEn || t('home.noDraftName')}
                  </Text>
                  <Text style={[T.micro, { fontSize: 12 }]}>
                    {d.price ? money(d.price) : t('home.priceMissing')}
                  </Text>
                </View>
                <Pill text={t('home.resume')} tone="soft" />
              </View>
            </Card>
          ))}
        </>
      ) : null}

      {!artisan ? (
        <Btn label={t('home.login')} sub={t('home.loginSub')} tone="tonal"
             onPress={onLogin} />
      ) : null}

      {/* Group orders: the one thing a single artisan cannot do alone. */}
      <Card tone="money" onPress={onSamuh}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <Users color={C.moneyDeep} size={20} />
          <Text style={[T.micro, { color: C.moneyDeep, flex: 1 }]}>
            {t('samuh.badge')}
          </Text>
        </View>
        <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
          {t('samuh.headline')}
        </Text>
        <Text style={T.bodySoft}>{t('samuh.subtitle')}</Text>
      </Card>

      <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm,
                     marginTop: S.sm }}>
        <Loom color={C.ink} size={20} />
        <Text style={[T.section, { flex: 1 }]}>{t('home.trendTitle')}</Text>
      </View>
      <Text style={T.bodySoft}>{t('home.trendSub')}</Text>

      {trends === null ? (
        <Card><Skeleton h={20} w="70%" /><Skeleton h={14} /><Skeleton h={14} w="85%" /></Card>
      ) : trends.length === 0 ? null : trends.map((tr) => (
        <Card key={tr.title}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
            <Trend color={C.primaryDeep} size={18} />
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold', flex: 1 }]}>
              {tr.title}
            </Text>
            <Pill text={`${Math.round((tr.confidence ?? 0.7) * 100)}%`} tone="good" />
          </View>
          <Text style={[T.body, { color: C.money, fontFamily: 'Mukta_700Bold' }]}>
            {tr.band}
          </Text>
          <Text style={T.bodySoft}>{tr.detail}</Text>
        </Card>
      ))}

      {!drafts.length ? (
        <Card style={{ alignItems: 'center', paddingVertical: S.xl, gap: S.sm }}>
          <View style={{ width: 54, height: 54, borderRadius: R.pill,
                         backgroundColor: C.bgAlt, alignItems: 'center',
                         justifyContent: 'center' }}>
            <Camera color={C.inkSoft} size={24} />
          </View>
          <Text style={T.body}>{t('home.empty')}</Text>
          <Text style={[T.bodySoft, { textAlign: 'center' }]}>{t('home.emptySub')}</Text>
        </Card>
      ) : null}
    </TabScreen>
  );
}
