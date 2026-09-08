import React, { useEffect, useRef } from 'react';
import {
  View, Text, Pressable, ScrollView, ActivityIndicator, Animated, Easing,
  ViewStyle, TextStyle, StyleSheet, Platform,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { C, GRAD, S, T, R, shadow } from './theme';
import { Back } from './icons';

/* ------------------------------------------------------------------ Button */

type BtnTone = 'primary' | 'indigo' | 'money' | 'tonal' | 'ghost';

export function Btn({
  label, sub, icon, onPress, tone = 'primary', busy, disabled, style, large,
}: {
  label: string; sub?: string; icon?: React.ReactNode; onPress?: () => void;
  tone?: BtnTone; busy?: boolean; disabled?: boolean; style?: ViewStyle; large?: boolean;
}) {
  const press = useRef(new Animated.Value(0)).current;
  const scale = press.interpolate({ inputRange: [0, 1], outputRange: [1, 0.97] });
  const to = (v: number) =>
    Animated.timing(press, { toValue: v, duration: 110, useNativeDriver: true }).start();

  const gradient =
    tone === 'primary' ? GRAD.primary : tone === 'indigo' ? GRAD.indigo :
    tone === 'money' ? GRAD.money : null;
  const fg = gradient ? C.white : tone === 'tonal' ? C.primaryDeep : C.ink;
  const h = large ? 68 : 58;

  const inner = (
    <View style={[st.btnInner, { minHeight: h }]}>
      {busy ? <ActivityIndicator color={fg} /> : (
        <>
          {icon}
          <View>
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold', color: fg,
                                    fontSize: large ? 19 : 17 }]}>{label}</Text>
            {sub ? (
              <Text style={[T.micro, { color: fg, opacity: 0.85, fontSize: 13 }]}>{sub}</Text>
            ) : null}
          </View>
        </>
      )}
    </View>
  );

  return (
    <Animated.View style={[{ transform: [{ scale }] }, style]}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        onPressIn={() => to(1)}
        onPressOut={() => to(0)}
        onPress={onPress}
        disabled={busy || disabled}
        style={{ opacity: disabled ? 0.45 : 1, borderRadius: R.lg, overflow: 'hidden' }}
      >
        {gradient ? (
          <LinearGradient colors={gradient} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                          style={[st.btn, shadow(2)]}>
            {inner}
          </LinearGradient>
        ) : (
          <View style={[st.btn, {
            backgroundColor: tone === 'tonal' ? C.primarySoft : C.surface,
            borderWidth: tone === 'ghost' ? 1.5 : 0, borderColor: C.lineStrong,
          }, tone === 'ghost' ? null : shadow(1)]}>
            {inner}
          </View>
        )}
      </Pressable>
    </Animated.View>
  );
}

/* -------------------------------------------------------------------- Card */

export function Card({
  children, style, tone = 'plain', onPress,
}: {
  children: React.ReactNode; style?: ViewStyle;
  tone?: 'plain' | 'soft' | 'indigo' | 'money' | 'warn' | 'danger';
  onPress?: () => void;
}) {
  const map = {
    plain:  { bg: C.surface,     bd: C.line },
    soft:   { bg: C.primarySoft, bd: '#F3DCCB' },
    indigo: { bg: C.indigoSoft,  bd: '#D6D2EE' },
    money:  { bg: C.moneySoft,   bd: '#C6E5D8' },
    warn:   { bg: C.goldSoft,    bd: '#EFDFB4' },
    danger: { bg: C.dangerSoft,  bd: '#F2CFCB' },
  }[tone];
  const body = (
    <View style={[st.card, { backgroundColor: map.bg, borderColor: map.bd },
                  tone === 'plain' ? shadow(1) : null, style]}>
      {children}
    </View>
  );
  return onPress ? <Pressable onPress={onPress}>{body}</Pressable> : body;
}

/* ------------------------------------------------------------------ Screen */

export function Screen({
  title, subtitle, children, onBack, step, hero,
}: {
  title: string; subtitle?: string; children: React.ReactNode; onBack?: () => void;
  step?: number;            // 1-4, drives the progress rail
  hero?: React.ReactNode;   // optional content inside the gradient header
}) {
  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <LinearGradient colors={GRAD.indigo} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                      style={st.header}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          {onBack ? (
            <Pressable onPress={onBack} accessibilityLabel="Back" style={st.back} hitSlop={10}>
              <Back color={C.white} size={22} />
            </Pressable>
          ) : null}
          <View style={{ flex: 1 }}>
            <Text style={[T.title, { color: C.white }]}>{title}</Text>
            {subtitle ? (
              <Text style={[T.label, { color: 'rgba(255,255,255,0.72)' }]}>{subtitle}</Text>
            ) : null}
          </View>
        </View>
        {hero}
      </LinearGradient>

      <ScrollView
        contentContainerStyle={{ padding: S.lg, paddingBottom: 120, gap: S.md }}
        showsVerticalScrollIndicator={false}
      >
        {children}
      </ScrollView>
    </View>
  );
}

/* --------------------------------------------------------------- Pill/Badge */

export function Pill({
  text, tone = 'soft', icon,
}: { text: string; tone?: 'soft' | 'good' | 'warn' | 'indigo' | 'danger';
     icon?: React.ReactNode }) {
  const map = {
    soft:   [C.primarySoft, C.primaryDeep],
    good:   [C.moneySoft, C.moneyDeep],
    warn:   [C.goldSoft, C.gold],
    indigo: [C.indigoSoft, C.indigo],
    danger: [C.dangerSoft, C.danger],
  }[tone];
  return (
    <View style={[st.pill, { backgroundColor: map[0] }]}>
      {icon}
      <Text style={[T.micro, { color: map[1] }]}>{text}</Text>
    </View>
  );
}

/* --------------------------------------------------------------------- Row */

export function Row({
  label, value, strong, sub,
}: { label: string; value: string; strong?: boolean; sub?: string }) {
  return (
    <View style={st.row}>
      <View style={{ flex: 1 }}>
        <Text style={T.bodySoft}>{label}</Text>
        {sub ? <Text style={[T.micro, { fontSize: 12 }]}>{sub}</Text> : null}
      </View>
      <Text style={[
        T.body,
        strong ? { fontFamily: 'Mukta_800ExtraBold', color: C.money, fontSize: 19 } : null,
      ]}>{value}</Text>
    </View>
  );
}

/* ---------------------------------------------------------------- Skeleton */

export function Skeleton({ h = 18, w = '100%', r = 8 }: { h?: number; w?: any; r?: number }) {
  const a = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(a, { toValue: 1, duration: 800, easing: Easing.inOut(Easing.quad),
                           useNativeDriver: Platform.OS !== 'web' }),
      Animated.timing(a, { toValue: 0, duration: 800, easing: Easing.inOut(Easing.quad),
                           useNativeDriver: Platform.OS !== 'web' }),
    ]));
    loop.start();
    return () => loop.stop();
  }, []);
  const opacity = a.interpolate({ inputRange: [0, 1], outputRange: [0.35, 0.75] });
  return (
    <Animated.View style={{
      height: h, width: w, borderRadius: r, backgroundColor: C.bgAlt, opacity,
    }} />
  );
}

/* --------------------------------------------------------------- StatTile */

export function StatTile({
  label, value, tone = 'plain',
}: { label: string; value: string; tone?: 'plain' | 'glass' }) {
  const glass = tone === 'glass';
  return (
    <View style={{
      flex: 1, padding: S.md, borderRadius: R.lg,
      backgroundColor: glass ? 'rgba(255,255,255,0.14)' : C.surface,
      borderWidth: glass ? 0 : 1, borderColor: C.line,
    }}>
      <Text style={[T.micro, { color: glass ? 'rgba(255,255,255,0.7)' : C.inkSoft }]}>
        {label}
      </Text>
      <Text style={[T.section, { color: glass ? C.white : C.ink, marginTop: 2 }]}>
        {value}
      </Text>
    </View>
  );
}

/* ------------------------------------------------------------------ Divider */

export const Divider = () => (
  <View style={{ height: 1, backgroundColor: C.line, marginVertical: S.sm }} />
);

export { money } from './theme';

const st = StyleSheet.create({
  btn: { borderRadius: R.lg },
  btnInner: {
    alignItems: 'center', justifyContent: 'center', flexDirection: 'row',
    gap: S.md, paddingHorizontal: S.lg, paddingVertical: S.md,
  },
  card: { borderRadius: R.xl, padding: S.lg, borderWidth: 1, gap: S.sm },
  header: {
    paddingTop: 54, paddingHorizontal: S.lg, paddingBottom: S.lg,
    borderBottomLeftRadius: R.xxl, borderBottomRightRadius: R.xxl,
  },
  back: {
    width: 42, height: 42, borderRadius: R.pill, alignItems: 'center',
    justifyContent: 'center', backgroundColor: 'rgba(255,255,255,0.14)',
  },
  pill: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: R.pill, alignSelf: 'flex-start',
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: S.md, paddingVertical: 7 },
});

/* ═══════════════════════════════════════════════ progressive-form primitives */

import { TextInput } from 'react-native';

/**
 * An editable field. Everything the AI produces lands in one of these, and stays
 * editable right up to submission - the artisan's edit always wins over the model.
 * When a value came from detection, its confidence is shown so the artisan knows
 * how much to trust it before accepting it.
 */
export function Field({
  label, value, onChange, placeholder, multiline, numeric, confidence, source, suffix,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  multiline?: boolean;
  numeric?: boolean;
  confidence?: number;
  source?: string;
  suffix?: string;
}) {
  const [focus, setFocus] = React.useState(false);
  const pct = confidence != null ? Math.round(confidence * 100) : null;
  return (
    <View style={{ gap: 6 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
        <Text style={[T.label, { flex: 1 }]}>{label}</Text>
        {pct != null ? (
          <Pill text={`AI ${pct}%`} tone={pct >= 80 ? 'good' : pct >= 55 ? 'warn' : 'danger'} />
        ) : null}
        {source ? <Pill text={source} tone="indigo" /> : null}
      </View>
      <View style={{
        flexDirection: 'row', alignItems: 'center',
        borderWidth: 1.5, borderRadius: R.md,
        borderColor: focus ? C.primary : C.line,
        backgroundColor: C.surface,
        paddingHorizontal: S.md,
      }}>
        {suffix ? (
          <Text style={[T.body, { color: C.inkSoft, marginRight: 4 }]}>{suffix}</Text>
        ) : null}
        <TextInput
          value={value}
          onChangeText={onChange}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
          placeholder={placeholder}
          placeholderTextColor={C.inkSoft}
          multiline={multiline}
          keyboardType={numeric ? 'numeric' : 'default'}
          style={{
            flex: 1, paddingVertical: 14, fontSize: 17, color: C.ink,
            fontFamily: 'Mukta_500Medium',
            minHeight: multiline ? 108 : undefined,
            textAlignVertical: multiline ? 'top' : 'center',
            outlineStyle: 'none',
          } as any}
        />
      </View>
    </View>
  );
}

/**
 * One block of the growing form. Completed sections stay mounted and visible above,
 * so scrolling back always shows exactly what was entered - nothing is unmounted or
 * reset as the artisan moves on.
 */
export function Section({
  n, title, subtitle, state, children, action,
}: {
  n: number;
  title: string;
  subtitle?: string;
  state: 'done' | 'active' | 'locked';
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  const done = state === 'done';
  const locked = state === 'locked';
  return (
    <View style={{ opacity: locked ? 0.45 : 1, gap: S.md }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.md }}>
        <View style={{
          width: 34, height: 34, borderRadius: R.pill,
          backgroundColor: done ? C.money : locked ? C.bgAlt : C.primary,
          alignItems: 'center', justifyContent: 'center',
        }}>
          <Text style={{
            fontFamily: 'Mukta_800ExtraBold', fontSize: 15,
            color: locked ? C.inkSoft : C.white,
          }}>{done ? '✓' : n}</Text>
        </View>
        <View style={{ flex: 1 }}>
          <Text style={T.section}>{title}</Text>
          {subtitle ? <Text style={[T.micro, { fontSize: 12 }]}>{subtitle}</Text> : null}
        </View>
        {action}
      </View>
      {!locked && children ? (
        <View style={{ paddingLeft: 46, gap: S.md }}>{children}</View>
      ) : null}
    </View>
  );
}

/** Vertical rail connecting sections - the visual thread of the growing form. */
export const Rail = () => (
  <View style={{ paddingLeft: 16 }}>
    <View style={{ width: 2, height: 20, backgroundColor: C.line }} />
  </View>
);

/** Status chip for a publication or order state. */
export function StatusChip({ status }: { status: string }) {
  const map: Record<string, 'good' | 'warn' | 'danger' | 'indigo' | 'soft'> = {
    published: 'good', active: 'good', paid: 'good', delivered: 'good',
    completed: 'good', approved: 'good',
    processing: 'warn', queued: 'warn', submitted: 'warn', pending: 'warn',
    payment_pending: 'warn', shipped: 'warn', packed: 'warn', confirmed: 'warn',
    failed: 'danger', cancelled: 'danger', refunded: 'danger',
    not_configured: 'soft', draft: 'soft', created: 'indigo', sold: 'indigo',
  };
  return <Pill text={status.replace(/_/g, ' ')} tone={map[status] || 'soft'} />;
}
