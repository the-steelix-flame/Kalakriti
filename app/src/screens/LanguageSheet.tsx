import React from 'react';
import { View, Text, Modal, Pressable, ScrollView } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Speech from 'expo-speech';
import { C, S, T, R } from '../theme';
import { Check } from '../icons';
import { useI18n, LANGUAGES, Lang } from '../i18n';
import * as api from '../lib/api';

/**
 * The language switcher, reachable from the top-right of every main screen.
 *
 * Selecting a language applies it immediately (the provider sits above the tree),
 * persists it to the device, and - when the artisan is signed in - saves it to their
 * profile so a new phone starts in the right language.
 */
export default function LanguageSheet({
  visible, onClose,
}: { visible: boolean; onClose: () => void }) {
  const { lang, setLang, t } = useI18n();
  const insets = useSafeAreaInsets();

  function choose(code: Lang) {
    setLang(code);
    const meta = LANGUAGES.find((l) => l.code === code);
    try {
      // Speak the confirmation in the language just chosen, so a user who cannot
      // read the list still gets feedback that the tap registered.
      Speech.speak(meta?.native ?? '', { language: meta?.speech ?? 'hi-IN' });
    } catch { /* no TTS voice installed */ }
    api.updateProfile({ language: code }).catch(() => { /* guest, or offline */ });
    onClose();
  }

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={{ flex: 1, backgroundColor: 'rgba(23,19,49,0.4)' }}
                 onPress={onClose} accessibilityLabel={t('common.close')} />
      <View style={{
        backgroundColor: C.surface,
        borderTopLeftRadius: R.xxl, borderTopRightRadius: R.xxl,
        paddingBottom: insets.bottom + S.lg,
        maxHeight: '80%',
      }}>
        <View style={{ alignItems: 'center', paddingTop: S.md }}>
          <View style={{ width: 42, height: 4, borderRadius: 99,
                         backgroundColor: C.lineStrong }} />
        </View>
        <View style={{ padding: S.lg, paddingBottom: S.sm }}>
          <Text style={T.section}>{t('common.language')}</Text>
          <Text style={[T.micro, { fontSize: 12.5 }]}>{t('prof.languageNote')}</Text>
        </View>
        <ScrollView contentContainerStyle={{ paddingHorizontal: S.lg, gap: S.sm }}>
          {LANGUAGES.map((l) => {
            const on = lang === l.code;
            return (
              <Pressable key={l.code} onPress={() => choose(l.code)}
                         accessibilityLabel={l.english}>
                <View style={{
                  flexDirection: 'row', alignItems: 'center', gap: S.md,
                  paddingVertical: 14, paddingHorizontal: S.md,
                  borderRadius: R.lg, borderWidth: 1.5,
                  borderColor: on ? C.primary : C.line,
                  backgroundColor: on ? C.primarySoft : C.surface,
                }}>
                  <View style={{ flex: 1 }}>
                    <Text style={[T.body, { fontFamily: 'Mukta_700Bold', fontSize: 19 }]}>
                      {l.native}
                    </Text>
                    <Text style={[T.micro, { fontSize: 12 }]}>{l.english}</Text>
                  </View>
                  {on ? <Check color={C.primary} size={22} /> : null}
                </View>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>
    </Modal>
  );
}
