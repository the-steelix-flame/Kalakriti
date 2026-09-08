import React, { useState } from 'react';
import { View, Text, Pressable, ScrollView } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Speech from 'expo-speech';
import { Card, Btn } from '../ui';
import { C, S, T, R, shadow } from '../theme';
import { Check, Shield, Rupee, Users, Speaker, Arrow } from '../icons';
import { useI18n, LANGUAGES, Lang } from '../i18n';

/**
 * First launch: choose a language, then log in or carry on as a guest.
 *
 * Language comes first and is required, because everything after it depends on the
 * artisan being able to read the screen. Choosing one applies it immediately - the
 * heading and the buttons on this very screen change under the tap, which is the
 * clearest possible confirmation for someone who cannot read the language names.
 *
 * Login is deliberately optional. The app has to be useful before it asks for anything.
 */
const WHY_LOGIN = [
  { icon: Rupee, key: 'onb.whyMoney' },
  { icon: Shield, key: 'onb.whyProof' },
  { icon: Users, key: 'onb.whyGroup' },
];

export default function Onboarding({
  onDone,
}: { onDone: (opts: { language: string; wantsLogin: boolean }) => void }) {
  const { t, lang, setLang, locale } = useI18n();
  const insets = useSafeAreaInsets();
  const [step, setStep] = useState<'lang' | 'choice'>('lang');
  const [touched, setTouched] = useState(false);

  function choose(code: Lang) {
    setLang(code);
    setTouched(true);
    const meta = LANGUAGES.find((l) => l.code === code);
    try {
      Speech.speak(meta?.native ?? '', { language: meta?.speech ?? 'hi-IN' });
    } catch { /* no voice installed for this language */ }
  }

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <View style={{ paddingTop: insets.top + 20, paddingHorizontal: S.lg,
                     paddingBottom: S.md }}>
        <Text style={[T.display, { fontSize: 30 }]}>{t('app.name')}</Text>
        <Text style={T.bodySoft}>
          {step === 'lang' ? t('onb.chooseLanguage') : t('onb.howToStart')}
        </Text>
      </View>

      <ScrollView contentContainerStyle={{
                    padding: S.lg, paddingTop: 0,
                    paddingBottom: insets.bottom + S.xxl, gap: S.lg,
                  }}
                  showsVerticalScrollIndicator={false}>
        {step === 'lang' ? (
          <>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: S.md }}>
              {LANGUAGES.map((l) => {
                const on = lang === l.code && touched;
                return (
                  <Pressable key={l.code} onPress={() => choose(l.code)}
                             accessibilityLabel={l.english} style={{ width: '47%' }}>
                    <View style={[{
                      borderRadius: R.lg, paddingVertical: S.lg, paddingHorizontal: S.md,
                      borderWidth: 2, alignItems: 'center', gap: 2,
                      borderColor: on ? C.money : C.line,
                      backgroundColor: on ? C.moneySoft : C.surface,
                    }, on ? null : shadow(1)]}>
                      <Text style={[T.title, { fontSize: 23 }]}>{l.native}</Text>
                      <Text style={[T.micro, { fontSize: 12 }]}>{l.english}</Text>
                      {on ? (
                        <View style={{ marginTop: 4 }}>
                          <Check color={C.money} size={19} />
                        </View>
                      ) : null}
                    </View>
                  </Pressable>
                );
              })}
            </View>

            <Btn label={t('common.continue')} icon={<Arrow color={C.white} size={22} />}
                 large disabled={!touched} onPress={() => setStep('choice')} />
            <Text style={[T.bodySoft, { textAlign: 'center' }]}>
              {touched ? t('onb.chooseLanguageHint') : t('onb.pickFirst')}
            </Text>
          </>
        ) : (
          <>
            <Card tone="soft" onPress={() => {
              try { Speech.speak(t('onb.spoken'), { language: locale }); } catch {}
            }}>
              <View style={{ flexDirection: 'row', gap: S.md, alignItems: 'center' }}>
                <Speaker color={C.primaryDeep} size={22} />
                <Text style={[T.bodySoft, { flex: 1, color: C.primaryDeep }]}>
                  {t('onb.listen')}
                </Text>
              </View>
            </Card>

            <Card>
              <Text style={T.section}>{t('onb.whyAccount')}</Text>
              {WHY_LOGIN.map(({ icon: Icon, key }) => (
                <View key={key} style={{ flexDirection: 'row', gap: S.md,
                                         alignItems: 'flex-start', paddingVertical: 6 }}>
                  <View style={{ marginTop: 2 }}><Icon color={C.money} size={20} /></View>
                  <Text style={[T.bodySoft, { flex: 1 }]}>{t(key)}</Text>
                </View>
              ))}
            </Card>

            <Btn label={t('onb.createAccount')} sub={t('onb.createAccountSub')}
                 tone="money" large
                 onPress={() => onDone({ language: lang, wantsLogin: true })} />

            <Btn label={t('onb.skip')} sub={t('onb.skipSub')} tone="ghost"
                 onPress={() => onDone({ language: lang, wantsLogin: false })} />

            <Card tone="warn">
              <Text style={T.bodySoft}>{t('onb.guestNote')}</Text>
            </Card>
          </>
        )}
      </ScrollView>
    </View>
  );
}
