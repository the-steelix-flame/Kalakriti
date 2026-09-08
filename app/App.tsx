import React, { useCallback, useEffect, useState } from 'react';
import { View, ActivityIndicator } from 'react-native';
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
import Create from './src/screens/Create';
import Samuh from './src/screens/Samuh';
import { TabBar, TabKey } from './src/nav/Shell';
import { I18nProvider } from './src/i18n';
import * as api from './src/lib/api';
import * as session from './src/lib/session';
import { C } from './src/theme';

/**
 * App shell.
 *
 *   fonts -> onboarding (first launch only) -> tabs
 *
 * Four tabs are the whole primary navigation; Create, Samuh and the sheets are
 * pushed over the top of them. Nothing that navigates lives inside page content
 * any more.
 */
type Stack =
  | { screen: 'tabs' }
  | { screen: 'create'; resumeId?: string | null }
  | { screen: 'samuh' };

function AppInner() {
  const [fontsLoaded] = useFonts({
    Mukta_300Light, Mukta_400Regular, Mukta_500Medium,
    Mukta_600SemiBold, Mukta_700Bold, Mukta_800ExtraBold,
  });

  const [booting, setBooting] = useState(true);
  const [onboarded, setOnboarded] = useState(session.isOnboarded());
  const [tab, setTab] = useState<TabKey>('home');
  const [stack, setStack] = useState<Stack>({ screen: 'tabs' });

  const [artisan, setArtisan] = useState<api.Artisan | null>(null);
  const [drafts, setDrafts] = useState<api.Listing[]>([]);
  const [listings, setListings] = useState<api.Listing[]>([]);
  const [orders, setOrders] = useState<api.Order[]>([]);
  const [readiness, setReadiness] = useState<api.Readiness[]>([]);
  const [loadingLists, setLoadingLists] = useState(false);

  const [authOpen, setAuthOpen] = useState(false);
  const [langOpen, setLangOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const b = await api.bootstrap();
      setArtisan(b.artisan);
      setDrafts(b.drafts || []);
      setReadiness(b.marketplaceReadiness || []);
    } catch {
      // Offline: the app still opens, and the Profile tab says the server is unset.
    } finally {
      setBooting(false);
    }
  }, []);

  const loadLists = useCallback(async () => {
    setLoadingLists(true);
    try {
      const [l, o] = await Promise.allSettled([api.listListings(), api.listOrders()]);
      if (l.status === 'fulfilled') setListings(l.value.listings || []);
      if (o.status === 'fulfilled') setOrders(o.value.orders || []);
    } finally {
      setLoadingLists(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (tab === 'products' || tab === 'orders') loadLists();
  }, [tab, loadLists]);

  if (!fontsLoaded || booting) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg, alignItems: 'center',
                     justifyContent: 'center' }}>
        <ActivityIndicator color={C.primary} size="large" />
      </View>
    );
  }

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
          onAuthed={(a) => { setArtisan(a); setAuthOpen(false); refresh(); }}
        />
      </SafeAreaProvider>
    );
  }

  const openLang = () => setLangOpen(true);
  const backToTabs = () => { setStack({ screen: 'tabs' }); refresh(); loadLists(); };

  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <View style={{ flex: 1, backgroundColor: C.bg }}>
        {stack.screen === 'create' ? (
          <Create resumeId={stack.resumeId ?? null} onHome={backToTabs} />
        ) : stack.screen === 'samuh' ? (
          <Samuh onBack={backToTabs} />
        ) : (
          <>
            <View style={{ flex: 1 }}>
              {tab === 'home' && (
                <HomeTab
                  artisan={artisan}
                  drafts={drafts}
                  onNew={() => {
                    session.setDraft(null);
                    setStack({ screen: 'create', resumeId: null });
                  }}
                  onResume={(id) => setStack({ screen: 'create', resumeId: id })}
                  onSamuh={() => setStack({ screen: 'samuh' })}
                  onLogin={() => setAuthOpen(true)}
                  onLanguage={openLang}
                />
              )}
              {tab === 'products' && (
                <ProductsTab
                  listings={listings}
                  loading={loadingLists}
                  onOpen={(id) => setStack({ screen: 'create', resumeId: id })}
                  onLanguage={openLang}
                  onRefresh={loadLists}
                />
              )}
              {tab === 'orders' && (
                <OrdersTab
                  orders={orders}
                  loading={loadingLists}
                  artisan={artisan}
                  onLanguage={openLang}
                  onRefresh={loadLists}
                  onLogin={() => setAuthOpen(true)}
                  onShip={async (id) => {
                    try { await api.shipOrder(id); } catch { /* shown after refresh */ }
                    await loadLists();
                  }}
                />
              )}
              {tab === 'profile' && (
                <ProfileTab
                  artisan={artisan}
                  readiness={readiness}
                  onLanguage={openLang}
                  onLogin={() => setAuthOpen(true)}
                  onEditProfile={() => setAuthOpen(true)}
                  onRefresh={refresh}
                  onLogout={async () => {
                    try { await api.logout(); } catch { /* already gone */ }
                    session.signOutLocal();
                    setArtisan(null);
                    setListings([]);
                    setOrders([]);
                    refresh();
                  }}
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
        onAuthed={(a) => { setArtisan(a); setAuthOpen(false); refresh(); loadLists(); }}
      />
    </SafeAreaProvider>
  );
}

/**
 * The i18n provider wraps everything, so choosing a language re-renders the whole
 * tree at once instead of only being written to storage.
 */
export default function App() {
  return (
    <I18nProvider>
      <AppInner />
    </I18nProvider>
  );
}
