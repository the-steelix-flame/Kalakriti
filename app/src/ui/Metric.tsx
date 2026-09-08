/**
 * How a marketplace figure is displayed, including when there isn't one.
 *
 * This component exists because the interesting case is the absent one. Amazon does
 * not give a seller application a per-listing view count; ONDC has no view concept at
 * all. The tempting fix is a zero, and a zero is a lie - it tells the artisan nobody
 * looked at her work, when in truth nobody counted.
 *
 * So an unavailable metric renders as a dash with the platform's reason underneath,
 * tappable for the full explanation. It reads as a property of that marketplace
 * rather than a fault in the app, which is exactly what it is.
 */
import React, { useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { C, S, T, R } from '../theme';
import { money } from '../theme';
import { useI18n } from '../i18n';

export type Metric = {
  value: number | null;
  available: boolean;
  why: string;
  source: string;
};

export function MetricTile({
  label, metric, currency = false,
}: { label: string; metric?: Metric; currency?: boolean }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const has = metric?.available && metric.value != null;

  return (
    <Pressable
      onPress={() => { if (!has && metric?.why) setOpen((o) => !o); }}
      accessibilityRole={!has && metric?.why ? 'button' : undefined}
      accessibilityLabel={has ? `${label}: ${metric!.value}` : `${label}: ${metric?.why || ''}`}
      style={{
        flexGrow: 1, flexBasis: '30%', minWidth: 104,
        backgroundColor: C.surface, borderRadius: R.lg, borderWidth: 1,
        borderColor: C.line, padding: S.md, gap: 2,
      }}
    >
      <Text style={[T.micro, { fontSize: 11.5 }]} numberOfLines={1}>{label}</Text>
      {has ? (
        <Text style={{ fontFamily: 'Mukta_800ExtraBold', fontSize: 22,
                       color: currency ? C.money : C.ink }}>
          {currency ? money(metric!.value as number)
                    : (metric!.value as number).toLocaleString('en-IN')}
        </Text>
      ) : (
        <>
          <Text style={{ fontFamily: 'Mukta_700Bold', fontSize: 22, color: C.inkSoft }}>
            —
          </Text>
          <Text style={[T.micro, { fontSize: 11, color: C.inkSoft }]}
                numberOfLines={open ? undefined : 2}>
            {open ? metric?.why : t('mp.whyBlank')}
          </Text>
        </>
      )}
      {has && metric?.source ? (
        <Text style={[T.micro, { fontSize: 10.5, color: C.inkSoft }]} numberOfLines={1}>
          {metric.source}
        </Text>
      ) : null}
    </Pressable>
  );
}

export function MetricGrid({ children }: { children: React.ReactNode }) {
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: S.sm }}>
      {children}
    </View>
  );
}
