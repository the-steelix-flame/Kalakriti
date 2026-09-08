/**
 * Filter chips and a sort control, shared by every list in the app.
 *
 * One component rather than three copies, because the lists differ only in what they
 * filter by. The chips carry live counts so an empty result is explained before it is
 * reached - "Failed · 0" tells the artisan there is nothing wrong, where a chip that
 * leads to an empty screen just looks broken.
 *
 * Sort is a row of chips rather than a dropdown: a picker on Android opens a modal
 * with small targets and a scroll, which is the wrong shape for somebody reading
 * slowly. Two visible rows cost more space and are far easier to hit.
 */
import React from 'react';
import { View, Text, Pressable, ScrollView } from 'react-native';
import { C, S, T, R } from '../theme';
import { useI18n } from '../i18n';

export type FilterDef<T> = { key: string; label: string; match: (x: T) => boolean };
export type SortDef<T> = { key: string; label: string; cmp: (a: T, b: T) => number };

function Chip({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} accessibilityRole="button"
               accessibilityState={{ selected: on }} accessibilityLabel={label}
               hitSlop={4}>
      <View style={{
        paddingHorizontal: 13, paddingVertical: 8, borderRadius: R.pill,
        backgroundColor: on ? C.ink : C.bgAlt,
        borderWidth: 1, borderColor: on ? C.ink : C.line,
      }}>
        <Text style={{ fontFamily: on ? 'Mukta_700Bold' : 'Mukta_500Medium',
                       fontSize: 13.5, color: on ? C.white : C.inkMid }}>
          {label}
        </Text>
      </View>
    </Pressable>
  );
}

export function FilterBar<T>({
  items, filters, filter, onFilter, sorts, sort, onSort,
}: {
  items: T[];
  filters: FilterDef<T>[];
  filter: string;
  onFilter: (k: string) => void;
  sorts?: SortDef<T>[];
  sort?: string;
  onSort?: (k: string) => void;
}) {
  const { t } = useI18n();
  return (
    <View style={{ gap: S.sm }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
                  contentContainerStyle={{ gap: 6, paddingRight: S.lg }}>
        {filters.map((f) => {
          const n = items.filter(f.match).length;
          return (
            <Chip key={f.key} on={filter === f.key} onPress={() => onFilter(f.key)}
                  label={`${t(f.label)}${n ? ` · ${n}` : ''}`} />
          );
        })}
      </ScrollView>

      {sorts?.length && onSort ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Text style={[T.micro, { fontSize: 12 }]}>{t('sort.label')}</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}
                      contentContainerStyle={{ gap: 6, paddingRight: S.lg }}>
            {sorts.map((sd) => (
              <Chip key={sd.key} on={sort === sd.key} onPress={() => onSort(sd.key)}
                    label={t(sd.label)} />
            ))}
          </ScrollView>
        </View>
      ) : null}
    </View>
  );
}

/** Applies the chosen filter and sort. Sorting is stable, so equal rows keep order. */
export function applyList<T>(
  items: T[], filters: FilterDef<T>[], filter: string,
  sorts?: SortDef<T>[], sort?: string,
): T[] {
  const f = filters.find((x) => x.key === filter);
  const out = f ? items.filter(f.match) : [...items];
  const sd = sorts?.find((x) => x.key === sort);
  return sd ? out.slice().sort(sd.cmp) : out;
}

/** Sort helpers. Missing timestamps sort last rather than throwing off the order. */
export const byDate = (get: (x: any) => string | undefined, dir: 1 | -1) =>
  (a: any, b: any) => {
    const ta = Date.parse(get(a) || '') || 0;
    const tb = Date.parse(get(b) || '') || 0;
    return (tb - ta) * dir;
  };

export const byNum = (get: (x: any) => number | null | undefined, dir: 1 | -1) =>
  (a: any, b: any) => ((get(b) ?? -1) - (get(a) ?? -1)) * dir;
