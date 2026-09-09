/**
 * The "ask AI for this one field" button, and the sheet that shows what it suggested.
 *
 * This is the whole idea of manual mode in one component. Manual does not mean the AI
 * is switched off - every model the automatic path uses is still here. It means the
 * artisan starts it, one field at a time, and sees the suggestion before it lands.
 *
 * Two rules the component enforces:
 *
 *   1. Nothing is written until she taps Use this. The suggestion appears beside what
 *      she already has, so accepting is a comparison rather than a leap of faith.
 *   2. Whatever she typed is never quietly replaced. If the field already has content,
 *      the sheet shows both and she chooses.
 */
import React, { useState } from 'react';
import {
  View, Text, Pressable, Modal, ActivityIndicator, ScrollView, TextInput,
} from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { Card, Btn, Pill } from '../ui';
import { C, S, T, R, shadow } from '../theme';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

/**
 * A four-pointed spark. Drawn rather than imported because an app for artisans should
 * not ship another company's brand mark on its buttons - the shape reads as "AI"
 * without borrowing anybody's logo.
 */
export function Spark({ size = 16, color = C.indigo }: { size?: number; color?: string }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      <Path
        d="M12 2.5c.9 4.4 2.2 5.7 6.6 6.6-4.4.9-5.7 2.2-6.6 6.6-.9-4.4-2.2-5.7-6.6-6.6 4.4-.9 5.7-2.2 6.6-6.6z"
        fill={color}
      />
      <Path d="M18.5 15.2c.45 2.2 1.1 2.85 3.3 3.3-2.2.45-2.85 1.1-3.3 3.3-.45-2.2-1.1-2.85-3.3-3.3 2.2-.45 2.85-1.1 3.3-3.3z"
            fill={color} opacity={0.65} />
    </Svg>
  );
}

export function AiButton({
  listingId, field, current, onAccept, label, disabled,
}: {
  listingId: string | null;
  field: api.AssistField;
  current: string;
  onAccept: (value: string) => void;
  label?: string;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [value, setValue] = useState('');
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const [steer, setSteer] = useState('');

  async function run(instruction = '') {
    if (!listingId) { setErr(t('create.aiNeedsNet')); return; }
    setBusy(true); setErr(''); setValue('');
    try {
      const r = await api.assist({ listingId, field, instruction });
      const v = Array.isArray(r.value) ? r.value.join(', ') : String(r.value ?? '');
      setValue(v);
      setNote(r.note || '');
    } catch (e: any) {
      // 503 means the key is not set. Say that plainly rather than "something went
      // wrong" - it is a configuration problem somebody can actually fix.
      const msg = String(e?.message || e);
      const networkish = e instanceof api.OfflineError
        || /network request failed|fetch failed|connectexception|failed to connect|unable to resolve host|timed out|timeout|aborted|backend not reachable/i
             .test(msg);
      // No signal is not an error she caused, and it costs her nothing: the field
      // stays hers and she can ask again later.
      setErr(e?.status === 503 ? t('ai.notConfigured')
           : networkish ? t('create.aiNeedsNet')
           : msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Pressable
        disabled={disabled}
        onPress={() => { setOpen(true); run(); }}
        accessibilityRole="button"
        accessibilityLabel={label || t('ai.fillWithAi')}
        hitSlop={8}
        style={({ pressed }) => ({
          flexDirection: 'row', alignItems: 'center', gap: 5,
          paddingHorizontal: 10, paddingVertical: 6, borderRadius: R.pill,
          backgroundColor: C.indigoSoft, borderWidth: 1, borderColor: '#D6D2EE',
          opacity: disabled ? 0.45 : pressed ? 0.7 : 1,
        })}
      >
        <Spark size={14} />
        <Text style={{ fontFamily: 'Mukta_600SemiBold', fontSize: 12.5,
                       color: C.indigo }}>
          {label || t('ai.ai')}
        </Text>
      </Pressable>

      <Modal visible={open} transparent animationType="slide"
             onRequestClose={() => setOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(23,19,49,0.45)',
                       justifyContent: 'flex-end' }}>
          <View style={[{ backgroundColor: C.bg, borderTopLeftRadius: R.xl,
                          borderTopRightRadius: R.xl, padding: S.lg, gap: S.md,
                          maxHeight: '86%' }, shadow(3)]}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
              <Spark size={18} />
              <Text style={[T.section, { flex: 1 }]}>{t('ai.suggestion')}</Text>
              <Pressable onPress={() => setOpen(false)} hitSlop={12}
                         accessibilityLabel={t('common.close')}>
                <Pill text={t('common.close')} tone="soft" />
              </Pressable>
            </View>

            <ScrollView contentContainerStyle={{ gap: S.md }}
                        keyboardShouldPersistTaps="handled">
              {current?.trim() ? (
                <Card tone="soft">
                  <Text style={T.label}>{t('ai.whatYouWrote')}</Text>
                  <Text style={T.bodySoft}>{current}</Text>
                </Card>
              ) : null}

              {busy ? (
                <Card style={{ alignItems: 'center', paddingVertical: S.xl, gap: S.sm }}>
                  <ActivityIndicator color={C.indigo} />
                  <Text style={T.bodySoft}>{t('ai.thinking')}</Text>
                </Card>
              ) : err ? (
                <Card tone="danger">
                  <Text style={T.bodySoft}>{err}</Text>
                  <Btn label={t('common.retry')} tone="tonal" onPress={() => run(steer)} />
                </Card>
              ) : (
                <Card tone="indigo">
                  <Text style={T.label}>{t('ai.suggested')}</Text>
                  <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}>
                    {value || t('ai.nothing')}
                  </Text>
                  {note ? (
                    <Text style={[T.micro, { fontSize: 12 }]}>{note}</Text>
                  ) : null}
                </Card>
              )}

              {/* Steering, because the first answer is often nearly right. */}
              <View style={{ gap: 6 }}>
                <Text style={T.label}>{t('ai.askAgain')}</Text>
                <TextInput
                  value={steer}
                  onChangeText={setSteer}
                  placeholder={t('ai.askAgainHint')}
                  placeholderTextColor={C.inkSoft}
                  style={{
                    borderWidth: 1, borderColor: C.lineStrong, borderRadius: R.md,
                    padding: S.md, fontFamily: 'Mukta_400Regular', fontSize: 16,
                    color: C.ink, backgroundColor: C.surface,
                  }}
                />
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                  {['ai.shorter', 'ai.simpler', 'ai.moreDetail'].map((k) => (
                    <Pressable key={k} onPress={() => { setSteer(t(k)); run(t(k)); }}>
                      <Pill text={t(k)} tone="soft" />
                    </Pressable>
                  ))}
                </View>
              </View>
            </ScrollView>

            <Btn label={t('ai.useThis')} tone="indigo" large
                 disabled={busy || !value}
                 onPress={() => { onAccept(value); setOpen(false); }} />
            <Btn label={t('ai.keepMine')} tone="ghost" onPress={() => setOpen(false)} />
          </View>
        </View>
      </Modal>
    </>
  );
}
