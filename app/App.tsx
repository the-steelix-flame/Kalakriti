import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ActivityIndicator, Pressable } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import {
  useFonts,
  Mukta_300Light, Mukta_400Regular, Mukta_500Medium,
  Mukta_600SemiBold, Mukta_700Bold, Mukta_800ExtraBold,
} from '@expo-google-fonts/mukta';

import Onboarding from './src/screens/Onboarding';
import AuthSheet from './src/screens/AuthSheet';
import LanguageSheet from './src/screens/LanguageSheet';
import HomeTab from './src/screens/HomeTab';
import ProductsTab from './src/screens/ProductsTab';
import OrdersTab from './src/screens/OrdersTab';
import ProfileTab from './src/screens/ProfileTab';
import ProductDetail from './src/screens/ProductDetail';
import MarketplaceDetail from './src/screens/MarketplaceDetail';
import Enquiries from './src/screens/Enquiries';
import Create from './src/screens/Create';
import { TabBar, TabKey } from './src/nav/Shell';
import { I18nProvider, useI18n } from './src/i18n';
import { StoreProvider, useStore } from './src/lib/store';
import { Card, Btn } from './src/ui';
import * as api from './src/lib/api';
import * as session from './src/lib/session';
import { C, S, T } from './src/theme';

/**
 * App shell.
 *
 *   fonts + persisted state -> onboarding (genuinely first launch only) -> tabs
 *
 * The critical fix is the gate below. Onboarding used to reappear every launch on a
 * phone because `session` kept its state in memory: closing the app erased the fact
 * that the artisan had ever opened it. State now lives in AsyncStorage and the
 * Keystore, and `store.ready` means it has been read back. Nothing renders before
 * that, because rendering earlier means briefly believing there is no user and
 * showing the language picker to somebody who chose Gujarati weeks ago.
 */
type Stack =
  | { screen: 'tabs' }
  | { screen: 'create'; resumeId?: string | null }
  | { screen: 'product'; id: string }
  | { screen: 'marketplace'; id: string; channel: string }
  | { screen: 'enquiries' };

/** Offline and unsent-work notice, shown above every tab that reads from the server. */
function SyncBanner() {
  const { t } = useI18n();
  const { offline, pendingWrites } = useStore();
  if (!offline && !pendingWrites) return null;
  return (
    <View style={{
      paddingHorizontal: S.lg, paddingVertical: 8,
      backgroundColor: offline ? C.goldSoft : C.moneySoft,
      borderBottomWidth: 1, borderBottomColor: offline ? '#EFDFB4' : '#C6E5D8',
    }}>
      <Text style={[T.micro, { fontSize: 12.5,
                               color: offline ? '#7A5B10' : C.moneyDeep }]}>
        {offline ? t('sync.offline') : ''}
        {offline && pendingWrites ? ' · ' : ''}
        {pendingWrites
          ? (pendingWrites === 1
              ? t('sync.pending', { n: pendingWrites })
              : t('sync.pendingPlural', { n: pendingWrites }))
          : ''}
      </Text>
    </View>
  );
}

/** Conflicts raised while replaying offline edits. Both values, never a silent loss. */
function ConflictNotice() {
  const { t } = useI18n();
  const { conflicts, dismissConflict } = useStore();
  if (!conflicts.length) return null;
  const c = conflicts[0];
  return (
    <View style={{ padding: S.lg }}>
      <Card tone="warn">
        <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
          {t('sync.conflictTitle')}
        </Text>
        <Text style={T.bodySoft}>{t('sync.conflictBody', { field: c.field })}</Text>
        <Text style={T.micro}>{t('sync.conflictMine')}: {String(c.mine)}</Text>
        <Text style={T.micro}>{t('sync.conflictTheirs')}: {String(c.theirs)}</Text>
        <Btn label={t('sync.conflictOk')} tone="tonal"
             onPress={() => dismissConflict(c.op.id, c.field)} />
      </Card>
    </View>
  );
}

function AppInner() {
  const [fontsLoaded] = useFonts({
    Mukta_300Light, Mukta_400Regular, Mukta_500Medium,
    Mukta_600SemiBold, Mukta_700Bold, Mukta_800ExtraBold,
  });

  const store = useStore();
  const [onboarded, setOnboarded] = useState(false);
  const [tab, setTab] = useState<TabKey>('home');
  const [stack, setStack] = useState<Stack>({ screen: 'tabs' });
  const [authOpen, setAuthOpen] = useState(false);
  const [langOpen, setLangOpen] = useState(false);

  // Read once storage has been loaded, not before.
  useEffect(() => {
    if (store.ready) setOnboarded(session.isOnboarded());
  }, [store.ready]);

  const backToTabs = useCallback(() => {
    setStack({ screen: 'tabs' });
    store.refresh({ silent: true });
    store.refreshOrders();
  }, [store]);

  if (!fontsLoaded || !store.ready) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg, alignItems: 'center',
                     justifyContent: 'center' }}>
        <ActivityIndicator color={C.primary} size="large" />
      </View>
    );
  }

  /* First launch only. A returning artisan - signed in or a guest with drafts on this
     device - goes straight to the tabs, because the flag survived the process dying. */
  if (!onboarded) {
    return (
      <SafeAreaProvider>
        <StatusBar style="dark" />
        <Onboarding
          onDone={({ wantsLogin }) => {
            session.setOnboarded();
            setOnboarded(true);
            if (wantsLogin) setAuthOpen(true);
          }}
        />
        <AuthSheet
          visible={authOpen}
          onClose={() => setAuthOpen(false)}
          onAuthed={(a) => {
            store.setArtisan(a);
            setAuthOpen(false);
            store.refresh();
            store.refreshOrders();
          }}
        />
      </SafeAreaProvider>
    );
  }

  const openLang = () => setLangOpen(true);

  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <View style={{ flex: 1, backgroundColor: C.bg }}>
        {stack.screen === 'create' ? (
          <Create resumeId={stack.resumeId ?? null} onHome={backToTabs} />
        ) : stack.screen === 'product' ? (
          <ProductDetail
            listingId={stack.id}
            onBack={backToTabs}
            onEdit={(id) => setStack({ screen: 'create', resumeId: id })}
            onPublish={(id) => setStack({ screen: 'create', resumeId: id })}
            onOpenMarketplace={(id, channel) =>
              setStack({ screen: 'marketplace', id, channel })}
          />
        ) : stack.screen === 'marketplace' ? (
          <MarketplaceDetail
            listingId={stack.id}
            channel={stack.channel}
            onBack={() => setStack({ screen: 'product', id: stack.id })}
          />
        ) : stack.screen === 'enquiries' ? (
          <Enquiries
            enquiries={store.enquiries}
            loading={store.loading}
            onBack={backToTabs}
            onRefresh={store.refreshOrders}
          />
        ) : (
          <>
            <View style={{ flex: 1 }}>
              <SyncBanner />
              <ConflictNotice />
              {tab === 'home' && (
                <HomeTab
                  artisan={store.artisan}
                  summary={store.summary}
                  insights={store.insights}
                  loading={store.loading}
                  jobs={store.jobs}
                  onDismissJob={store.dismissJob}
                  onNew={() => {
                    session.setDraft(null);
                    setStack({ screen: 'create', resumeId: null });
                  }}
                  onOpenProduct={(id) => setStack({ screen: 'product', id })}
                  onResumeDraft={(id) => setStack({ screen: 'create', resumeId: id })}
                  onEnquiries={() => setStack({ screen: 'enquiries' })}
                  onLogin={() => setAuthOpen(true)}
                  onLanguage={openLang}
                  onTab={setTab}
                  onRefresh={() => { store.refresh(); store.refreshOrders(); }}
                />
              )}
              {tab === 'products' && (
                <ProductsTab
                  cards={store.cards}
                  loading={store.loading}
                  offline={store.offline}
                  lastSync={store.lastSync}
                  onOpen={(id) => setStack({ screen: 'product', id })}
                  onLanguage={openLang}
                  onRefresh={store.refresh}
                />
              )}
              {tab === 'orders' && (
                <OrdersTab
                  orders={store.orders}
                  loading={store.loading}
                  artisan={store.artisan}
                  offline={store.offline}
                  onLanguage={openLang}
                  onRefresh={store.refreshOrders}
                  onLogin={() => setAuthOpen(true)}
                  onOpenProduct={(id) => setStack({ screen: 'product', id })}
                  onShip={async (id) => {
                    try { await api.shipOrder(id); } catch { /* surfaced on refresh */ }
                    await store.refreshOrders();
                  }}
                />
              )}
              {tab === 'profile' && (
                <ProfileTab
                  artisan={store.artisan}
                  readiness={store.readiness}
                  onLanguage={openLang}
                  onLogin={() => setAuthOpen(true)}
                  onEditProfile={() => setAuthOpen(true)}
                  onRefresh={store.refresh}
                  onLogout={async () => { await store.signOut(); }}
                />
              )}
            </View>
            <TabBar active={tab} onChange={setTab} />
          </>
        )}
      </View>

      <LanguageSheet visible={langOpen} onClose={() => setLangOpen(false)} />
      <AuthSheet
        visible={authOpen}
        onClose={() => setAuthOpen(false)}
        onAuthed={(a) => {
          store.setArtisan(a);
          setAuthOpen(false);
          store.refresh();
          store.refreshOrders();
        }}
      />
    </SafeAreaProvider>
  );
}

/**
 * Providers, outermost first.
 *
 * I18n wraps everything so a language change re-renders the whole tree rather than
 * only being written to storage. Store sits inside it and outside every screen, so
 * cached data, the offline flag and the write queue are shared rather than refetched
 * per tab - which is also what stops four tabs each firing their own request on boot.
 */
export default function App() {
  return (
    <I18nProvider>
      <StoreProvider>
        <AppInner />
      </StoreProvider>
    </I18nProvider>
  );
}
