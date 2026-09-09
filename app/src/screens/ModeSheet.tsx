/**
 * Choosing between letting the AI do the work and filling the form yourself.
 *
 * The app measures the connection and proposes one, but it never decides. A
 * measurement is a guess about the next minute - the bar moves when she walks to the
 * next room - and an artisan who prefers to type should not be argued with by a
 * latency reading.
 *
 * Both options are described by what happens to *her*, not by what the software does.
 * "Take one photo, we do the rest and tell you when it is ready" is a promise she can
 * check. "Asynchronous server-side processing" is not.
 */
import React, { useEffect, useState } from 'react';
import { View, Text, Modal, Pressable, ScrollView } from 'react-native';
import { Card, Btn, Pill, Divider } from '../ui';
import { Spark } from '../ui/AiButton';
import { C, S, T, R, shadow } from '../theme';
import { Camera, Warning, Check } from '../icons';
import { useI18n } from '../i18n';
import * as session from '../lib/session';
import * as connection from '../lib/connection';

function Option({
  title, sub, detail, icon, selected, recommended, onPress,
}: {
  title: string; sub: string; detail: string; icon: React.ReactNode;
  selected: boolean; recommended: boolean; onPress: () => void;
}) {
  const { t } = useI18n();
  return (
    <Pressable onPress={onPress} accessibilityRole="radio"
               accessibilityState={{ selected }}>
      <View style={{
        backgroundColor: selected ? C.indigoSoft : C.surface,
        borderRadius: R.lg, borderWidth: selected ? 2 : 1,
        borderColor: selected ? C.indigo : C.line,
        padding: S.lg, gap: 6,
      }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <View style={{ width: 38, height: 38, borderRadius: R.pill,
                         backgroundColor: selected ? C.surface : C.bgAlt,
                         alignItems: 'center', justifyContent: 'center' }}>
            {icon}
          </View>
          <Text style={[T.body, { flex: 1, fontFamily: 'Mukta_700Bold' }]}>{title}</Text>
          {selected ? <Check color={C.indigo} size={20} /> : null}
        </View>
        <Text style={T.bodySoft}>{sub}</Text>
        {recommended ? <Pill text={t('mode.recommended')} tone="good" /> : null}
        {selected ? (
          <>
            <Divider />
            <Text style={[T.micro, { fontSize: 12.5 }]}>{detail}</Text>
          </>
        ) : null}
      </View>
    </Pressable>
  );
}

export default function ModeSheet({
  visible, onClose, onChoose,
}: {
  visible: boolean;
  onClose: () => void;
  onChoose?: (m: session.Mode) => void;
}) {
  const { t } = useI18n();
  const [mode, setMode] = useState<session.Mode>(session.getMode() ?? 'auto');
  const [reading, setReading] = useState<connection.Reading | null>(null);

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    connection.measure().then((r) => {
      if (!alive) return;
      setReading(r);
      // Only pre-select from the measurement when she has never chosen. Her own
      // choice always outranks a latency number.
      if (!session.getMode()) setMode(connection.suggest(r));
    });
    return () => { alive = false; };
  }, [visible]);

  const suggested = reading ? connection.suggest(reading) : null;

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: 'rgba(23,19,49,0.45)',
                     justifyContent: 'flex-end' }}>
        <View style={[{ backgroundColor: C.bg, borderTopLeftRadius: R.xl,
                        borderTopRightRadius: R.xl, padding: S.lg, gap: S.md,
                        maxHeight: '90%' }, shadow(3)]}>
          <Text style={T.title}>{t('mode.title')}</Text>
          <Text style={T.bodySoft}>{t('mode.sub')}</Text>

          {reading?.speed === 'slow' ? (
            <Card tone="warn">
              <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'flex-start' }}>
                <Warning color={C.gold} size={17} />
                <View style={{ flex: 1 }}>
                  <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                    {t('mode.slowNotice')}
                  </Text>
                  <Text style={T.bodySoft}>{t('mode.slowNoticeSub')}</Text>
                </View>
              </View>
            </Card>
          ) : reading?.speed === 'offline' ? (
            <Card tone="soft">
              <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                {t('mode.offlineNotice')}
              </Text>
              <Text style={T.bodySoft}>{t('mode.offlineNoticeSub')}</Text>
            </Card>
          ) : null}

          <ScrollView contentContainerStyle={{ gap: S.md }}>
            <Option
              title={t('mode.auto')} sub={t('mode.autoSub')} detail={t('mode.autoDetail')}
              icon={<Spark size={18} color={mode === 'auto' ? C.indigo : C.inkSoft} />}
              selected={mode === 'auto'} recommended={suggested === 'auto'}
              onPress={() => setMode('auto')}
            />
            <Option
              title={t('mode.manual')} sub={t('mode.manualSub')}
              detail={t('mode.manualDetail')}
              icon={<Camera size={18} color={mode === 'manual' ? C.indigo : C.inkSoft} />}
              selected={mode === 'manual'} recommended={suggested === 'manual'}
              onPress={() => setMode('manual')}
            />
          </ScrollView>

          <Btn label={t('common.continue')} tone="indigo" large onPress={() => {
            session.setMode(mode);
            onChoose?.(mode);
            onClose();
          }} />
        </View>
      </View>
    </Modal>
  );
}
