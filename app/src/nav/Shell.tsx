import React from 'react';
import { View, Text, Pressable, ScrollView, Platform } from 'react-native';
import type { RefreshControlProps } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { C, S, T, R, shadow } from '../theme';
import { Home as HomeIcon, Tag, Rupee, Users, Globe, Check, Trend } from '../icons';
import { useI18n } from '../i18n';

/**
 * The app shell: a light top bar with the language selector, and a real bottom tab
 * bar.
 *
 * What changed and why:
 *  - The old screens each painted a tall indigo gradient header. Stacked up, that
 *    read as a web dashboard: a large coloured band eating a third of a small screen
 *    before any content appeared. The header is now a slim, near-flat bar in the page
 *    background, so the content starts at the top of the phone where it belongs.
 *  - Navigation lived inside page content (buttons in the middle of Home). Primary
 *    navigation now sits in a fixed bottom bar, within thumb reach, which is where a
 *    phone user looks for it.
 *  - Safe-area insets are respected on both edges, so the bar never sits under the
 *    gesture pill and content is never hidden behind the bar.
 */

// Five, because the work splits five ways: see what needs doing, approve products,
// move orders, read the money, manage the account.
//
// `home` keeps its key rather than being renamed to `dashboard`. The key is persisted
// in session state, so renaming it would drop every returning user onto a tab they did
// not choose, and the label is what they actually read.
//
// `products` is gone from the bar and is NOT gone from the app: it is reached from the
// dashboard and from Approvals, both of which land on it with a filter already applied,
// which is more useful than an unfiltered list of everything.
export type TabKey = 'home' | 'approvals' | 'orders' | 'reports' | 'profile';

const TABS: { key: TabKey; icon: any; labelKey: string }[] = [
  { key: 'home', icon: HomeIcon, labelKey: 'nav.dashboard' },
  { key: 'approvals', icon: Check, labelKey: 'nav.approvals' },
  { key: 'orders', icon: Rupee, labelKey: 'nav.orders' },
  { key: 'reports', icon: Trend, labelKey: 'nav.reports' },
  { key: 'profile', icon: Users, labelKey: 'nav.profile' },
];

export const TAB_BAR_HEIGHT = 62;

/* ─────────────────────────────────────────────────────── language selector */

export function LanguagePill({ onPress }: { onPress: () => void }) {
  const { native } = useI18n();
  return (
    <Pressable onPress={onPress} accessibilityLabel="Language"
               hitSlop={8}
               style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>
      <View style={{
        flexDirection: 'row', alignItems: 'center', gap: 6,
        paddingHorizontal: 12, paddingVertical: 8,
        borderRadius: R.pill, backgroundColor: C.surface,
        borderWidth: 1, borderColor: C.line,
      }}>
        <Globe color={C.inkMid} size={16} />
        <Text style={{ fontFamily: 'Mukta_600SemiBold', fontSize: 14, color: C.ink }}>
          {native}
        </Text>
        <Text style={{ fontSize: 10, color: C.inkSoft, marginTop: 1 }}>▼</Text>
      </View>
    </Pressable>
  );
}

/* ──────────────────────────────────────────────────────────────── top bar */

export function TopBar({
  title, subtitle, onLanguage, right, onBack,
}: {
  title: string;
  subtitle?: string;
  onLanguage?: () => void;
  right?: React.ReactNode;
  onBack?: () => void;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View style={{
      paddingTop: insets.top + 8,
      paddingHorizontal: S.lg,
      paddingBottom: S.md,
      backgroundColor: C.bg,
      borderBottomWidth: 1,
      borderBottomColor: C.line,
      flexDirection: 'row',
      alignItems: 'center',
      gap: S.sm,
    }}>
      {onBack ? (
        <Pressable onPress={onBack} hitSlop={12} accessibilityLabel="Back"
                   style={{ width: 38, height: 38, borderRadius: R.pill,
                            alignItems: 'center', justifyContent: 'center',
                            backgroundColor: C.bgAlt }}>
          <Text style={{ fontSize: 20, color: C.ink, marginTop: -2 }}>‹</Text>
        </Pressable>
      ) : null}
      <View style={{ flex: 1 }}>
        <Text style={[T.title, { fontSize: 21 }]} numberOfLines={1}>{title}</Text>
        {subtitle ? (
          <Text style={[T.micro, { fontSize: 12.5 }]} numberOfLines={1}>{subtitle}</Text>
        ) : null}
      </View>
      {right}
      {onLanguage ? <LanguagePill onPress={onLanguage} /> : null}
    </View>
  );
}

/* ───────────────────────────────────────────────────────────── tab screen */

export function TabScreen({
  title, subtitle, onLanguage, children, right, scroll = true, refreshControl, banner,
}: {
  title: string;
  subtitle?: string;
  onLanguage?: () => void;
  right?: React.ReactNode;
  children: React.ReactNode;
  scroll?: boolean;
  /** Pull-to-refresh. Every list is a cached copy, so re-pulling must be possible. */
  refreshControl?: React.ReactElement<RefreshControlProps>;
  /** Sits under the title bar, above the scroll: offline and unsent-work notices. */
  banner?: React.ReactNode;
}) {
  const insets = useSafeAreaInsets();
  // Content must clear the tab bar plus the home indicator, or the last card sits
  // permanently under the navigation.
  const bottomPad = TAB_BAR_HEIGHT + insets.bottom + S.xl;

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar title={title} subtitle={subtitle} onLanguage={onLanguage} right={right} />
      {banner}
      {scroll ? (
        <ScrollView
          contentContainerStyle={{ padding: S.lg, paddingBottom: bottomPad, gap: S.md }}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
          refreshControl={refreshControl}
        >
          {children}
        </ScrollView>
      ) : (
        <View style={{ flex: 1, paddingBottom: bottomPad }}>{children}</View>
      )}
    </View>
  );
}

/* ───────────────────────────────────────────────────────────── tab bar */

export function TabBar({
  active, onChange,
}: { active: TabKey; onChange: (k: TabKey) => void }) {
  const { t } = useI18n();
  const insets = useSafeAreaInsets();

  return (
    <View style={[{
      position: 'absolute', left: 0, right: 0, bottom: 0,
      // Explicit stacking: without it the scrolling content can land above the bar
      // on web and swallow taps near the bottom of the screen.
      zIndex: 10, elevation: 8,
      backgroundColor: C.surface,
      borderTopWidth: 1, borderTopColor: C.line,
      paddingBottom: insets.bottom,
      flexDirection: 'row',
    }, Platform.OS === 'web' ? null : shadow(2)]}>
      {TABS.map(({ key, icon: Icon, labelKey }) => {
        const on = active === key;
        return (
          <Pressable
            key={key}
            onPress={() => onChange(key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: on }}
            accessibilityLabel={t(labelKey)}
            style={{ flex: 1 }}
          >
            <View style={{
              height: TAB_BAR_HEIGHT, alignItems: 'center', justifyContent: 'center',
              gap: 3,
            }}>
              {/* A short bar above the active icon: colour alone is not enough for
                  a low-vision user, and it survives any theme. */}
              <View style={{
                position: 'absolute', top: 0, width: 34, height: 3,
                borderRadius: 99,
                backgroundColor: on ? C.primary : 'transparent',
              }} />
              <Icon color={on ? C.primary : C.inkSoft} size={23}
                    strokeWidth={on ? 2.1 : 1.7} />
              <Text
                numberOfLines={1}
                style={{
                  fontFamily: on ? 'Mukta_700Bold' : 'Mukta_500Medium',
                  fontSize: 10.5,
                  color: on ? C.primary : C.inkSoft,
                  paddingHorizontal: 1,
                }}
              >
                {t(labelKey)}
              </Text>
            </View>
          </Pressable>
        );
      })}
    </View>
  );
}
