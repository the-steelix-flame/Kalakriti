import React, { useEffect, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { Card, Btn, Pill, Row, Field, Divider } from '../ui';
import { C, S, T, R } from '../theme';
import { Users, Check, Warning, Globe, Shield } from '../icons';
import { TabScreen } from '../nav/Shell';
import { useI18n, LANGUAGES } from '../i18n';
import * as api from '../lib/api';
import { apiBase, setApiBase, describeBackend } from '../lib/config';

/**
 * Profile: who the artisan is, where they ship from, which marketplaces are ready,
 * and the two settings that actually matter on a phone (language and server).
 *
 * The server field is here rather than buried in a separate screen because a release
 * APK cannot infer a LAN address, and being able to fix it on the device is the
 * difference between a working demo and a dead one.
 */
export default function ProfileTab({
  artisan, readiness, onLanguage, onLogin, onLogout, onEditProfile, onRefresh,
}: {
  artisan: api.Artisan | null;
  readiness: api.Readiness[];
  onLanguage: () => void;
  onLogin: () => void;
  onLogout: () => void;
  onEditProfile: () => void;
  onRefresh: () => void;
}) {
  const { t, native } = useI18n();
  const [url, setUrl] = useState(apiBase());
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => { setUrl(apiBase()); }, [artisan]);

  async function testServer() {
    setChecking(true); setResult(null);
    setApiBase(url || null);
    try {
      const h = await api.health();
      setResult({ ok: true, text: t('prof.connected', { info: h?.llm ?? 'ok' }) });
      onRefresh();
    } catch {
      setResult({ ok: false, text: t('prof.notConnected') });
    } finally { setChecking(false); }
  }

  const pickup = artisan?.addresses?.find((a) => a.kind === 'pickup');

  return (
    <TabScreen title={t('prof.title')} onLanguage={onLanguage}>
      {artisan ? (
        <Card>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.md }}>
            <View style={{ width: 52, height: 52, borderRadius: R.pill,
                           backgroundColor: C.primary, alignItems: 'center',
                           justifyContent: 'center' }}>
              <Text style={{ fontFamily: 'Mukta_800ExtraBold', fontSize: 22,
                             color: C.white }}>
                {(artisan.fullName || artisan.phone || '?').charAt(0)}
              </Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                {artisan.fullName || t('prof.artisanInfo')}
              </Text>
              <Text style={[T.micro, { fontSize: 12.5 }]}>+91 {artisan.phone}</Text>
            </View>
            <Pill text={artisan.phoneVerified ? t('prof.verified') : t('prof.notVerified')}
                  tone={artisan.phoneVerified ? 'good' : 'warn'} />
          </View>
          {artisan.businessName ? (
            <Row label={t('auth.businessName')} value={artisan.businessName} />
          ) : null}
          <Btn label={t('prof.editProfile')} tone="ghost" onPress={onEditProfile} />
        </Card>
      ) : (
        <Card style={{ alignItems: 'center', paddingVertical: S.xl, gap: S.md }}>
          <View style={{ width: 54, height: 54, borderRadius: R.pill,
                         backgroundColor: C.bgAlt, alignItems: 'center',
                         justifyContent: 'center' }}>
            <Users color={C.inkSoft} size={24} />
          </View>
          <Text style={T.body}>{t('prof.notSignedIn')}</Text>
          <Text style={[T.bodySoft, { textAlign: 'center' }]}>
            {t('prof.notSignedInSub')}
          </Text>
          <Btn label={t('home.login')} tone="money" onPress={onLogin} />
        </Card>
      )}

      {artisan ? (
        <Card>
          <Text style={T.label}>{t('prof.addresses')}</Text>
          {pickup ? (
            <>
              <Text style={T.bodySoft}>
                {[pickup.line1, pickup.line2, pickup.city, pickup.state, pickup.pincode]
                  .filter(Boolean).join(', ')}
              </Text>
              <Pill text={t('field.pickup_address')} tone="indigo" />
            </>
          ) : (
            <>
              <Text style={T.bodySoft}>{t('prof.noAddress')}</Text>
              <Btn label={t('prof.addAddress')} tone="tonal" onPress={onEditProfile} />
            </>
          )}
        </Card>
      ) : null}

      {artisan && readiness.length ? (
        <Card>
          <Text style={T.label}>{t('prof.marketplaces')}</Text>
          {readiness.map((r) => (
            <View key={r.channel} style={{ flexDirection: 'row', alignItems: 'center',
                                           gap: S.sm, paddingVertical: 6 }}>
              {r.ready ? <Check color={C.money} size={17} />
                       : <Warning color={C.gold} size={17} />}
              <Text style={[T.bodySoft, { flex: 1, color: C.ink }]}>{r.channel}</Text>
              {r.ready ? <Pill text={t('create.ready')} tone="good" />
                       : <Pill text={t('auth.nLeft', { n: r.missing.length })}
                               tone="warn" />}
            </View>
          ))}
        </Card>
      ) : null}

      <Card onPress={onLanguage}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.md }}>
          <Globe color={C.inkMid} size={20} />
          <View style={{ flex: 1 }}>
            <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
              {t('common.language')}
            </Text>
            <Text style={[T.micro, { fontSize: 12 }]}>{t('prof.languageNote')}</Text>
          </View>
          <Pill text={native} />
        </View>
      </Card>

      <Card>
        <Text style={T.label}>{t('prof.serverAddress')}</Text>
        <Field label="" value={url} onChange={setUrl}
               placeholder="http://10.0.0.5:8000" />
        <Text style={[T.micro, { fontSize: 12 }]}>
          {t('prof.serverNow', { url: describeBackend() || t('common.offline') })}
        </Text>
        <Btn label={checking ? t('common.checking') : t('prof.testServer')}
             tone="tonal" busy={checking} onPress={testServer} />
        {result ? (
          <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'center' }}>
            {result.ok ? <Check color={C.money} size={17} />
                       : <Warning color={C.danger} size={17} />}
            <Text style={[T.bodySoft, { flex: 1,
                  color: result.ok ? C.moneyDeep : C.danger }]}>{result.text}</Text>
          </View>
        ) : null}
        <Divider />
        <Text style={[T.micro, { fontSize: 12 }]}>{t('prof.serverNote')}</Text>
      </Card>

      <Card tone="indigo">
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <Shield color={C.indigo} size={18} />
          <Text style={[T.label, { color: C.indigo, flex: 1 }]}>
            {t('prof.offlineTitle')}
          </Text>
        </View>
        {['prof.offlineCamera', 'prof.offlineQuality', 'prof.offlineFraming',
          'prof.offlineDrafts'].map((k) => (
          <View key={k} style={{ flexDirection: 'row', gap: S.sm, alignItems: 'center' }}>
            <Check color={C.indigo} size={14} />
            <Text style={[T.bodySoft, { flex: 1, color: C.indigo }]}>{t(k)}</Text>
          </View>
        ))}
        <Text style={[T.micro, { fontSize: 12, color: C.indigo }]}>
          {t('prof.onlineNote')}
        </Text>
      </Card>

      {artisan ? (
        <Btn label={t('prof.logout')} tone="ghost" onPress={onLogout} />
      ) : null}
    </TabScreen>
  );
}
