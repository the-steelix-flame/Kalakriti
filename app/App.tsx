import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ActivityIndicator, Pressable, Alert } from 'react-native';
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
import * as drafts from './src/lib/drafts';
import MarketplaceDetail from './src/screens/MarketplaceDetail';
import Marketplace from './src/screens/Marketplace';
import MarketProduct from './src/screens/MarketProduct';
import OrderTracker from './src/screens/OrderTracker';
import OpsHeader from './src/screens/OpsHeader';
import Approvals from './src/screens/Approvals';
import Reports from './src/screens/Reports';
import ClusterOps from './src/screens/ClusterOps';
import TransitBoard from './src/screens/TransitBoard';
import Enquiries from './src/screens/Enquiries';
import Clusters from './src/screens/Clusters';
import ClusterDashboard from './src/screens/ClusterDashboard';
import GoodsReceipts from './src/screens/GoodsReceipts';
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
  // The buyer's shop, its product page, and the tracker for one order. `shop` and
  // `shopItem` are the customer-facing half of the platform; `track` is shared -
  // a buyer follows a parcel on it and the artisan moves it along on the same screen,
  // with the server deciding which of the two they are.
  | { screen: 'shop' }
  | { screen: 'shopItem'; id: string }
  | { screen: 'track'; orderId: string }
  // One cluster from its operator's side: its listings, its orders and its roster.
  | { screen: 'clusterOps'; clusterId: string }
  // The product list. Was a bottom tab; the bar now carries the five operational
  // sections, so this is pushed from the dashboard instead of being dropped.
  | { screen: 'products' }
  // Stage counts across every order this person is answerable for: where the
  // pile-up is, and which parcels are in it.
  | { screen: 'board' }
  | { screen: 'enquiries' }
  | { screen: 'clusters' }
  | { screen: 'clusterAdmin' }
  | { screen: 'grn'; clusterId: string; enquiryId?: string };

/** Offline and unsent-work notice, shown above every tab that reads from the server. */
function SyncBanner() {
  const { t } = useI18n();
  const { offline, pendingWrites, localDrafts } = useStore();
  const waiting = pendingWrites + localDrafts.length;
  if (!offline && !waiting) return null;
  return (
    <View style={{
      paddingHorizontal: S.lg, paddingVertical: 8,
      backgroundColor: offline ? C.goldSoft : C.moneySoft,
      borderBottomWidth: 1, borderBottomColor: offline ? '#EFDFB4' : '#C6E5D8',
    }}>
      <Text style={[T.micro, { fontSize: 12.5,
                               color: offline ? '#7A5B10' : C.moneyDeep }]}>
        {/* Offline is a state, not a failure - the app works either way. Saying so
            in the same line is what stops it reading as "nothing works now". */}
        {offline ? t('sync.offlineOk') : ''}
        {offline && waiting ? ' · ' : ''}
        {waiting
          ? (waiting === 1
              ? t('sync.pending', { n: waiting })
              : t('sync.pendingPlural', { n: waiting }))
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
  // Needed for the delete confirmation below, which has to be readable in whichever
  // language she chose - a destructive prompt in English is worse than no prompt.
  const { t } = useI18n();
  const [onboarded, setOnboarded] = useState(false);
  const [tab, setTab] = useState<TabKey>('home');

  /* ── operations dashboard ──────────────────────────────────────────────────
   *
   * Fetched here rather than inside HomeTab so every metric tile can navigate: the
   * navigator owns the stack, and a tile that cannot move you somewhere is just a
   * number on a card. Refetched whenever the store refreshes, so publishing a product
   * on the Approvals tab is reflected in "Pending reviews" without a restart.
   */
  const [ops, setOps] = useState<api.DashboardMetrics | null>(null);
  const [opsLoading, setOpsLoading] = useState(false);

  const loadOps = React.useCallback(async () => {
    if (!store.artisan) { setOps(null); return; }
    setOpsLoading(true);
    try {
      setOps(await api.dashboard());
    } catch {
      // Signed out, offline, or the endpoint is unreachable. The tiles then show a
      // dash rather than a zero, which is the honest thing for a number we do not
      // have - and the rest of Home still works.
      setOps(null);
    } finally {
      setOpsLoading(false);
    }
  }, [store.artisan]);

  useEffect(() => { void loadOps(); }, [loadOps]);

  const [stack, setStack] = useState<Stack>({ screen: 'tabs' });
  const [authOpen, setAuthOpen] = useState(false);
  const [langOpen, setLangOpen] = useState(false);

  // Read once storage has been loaded, not before.
  useEffect(() => {
    if (store.ready) setOnboarded(session.isOnboarded());
  }, [store.ready]);

  /**
   * Open a product.
   *
   * A listing that exists only on this phone has no server row behind it, so the
   * detail page would have nothing to fetch. It opens in the create screen instead,
   * resumed exactly as she left it, which is also the only place it can be finished.
   */
  const openProduct = useCallback((id: string) => {
    if (drafts.isLocalId(id)) setStack({ screen: 'create', resumeId: id });
    else setStack({ screen: 'product', id });
  }, []);

  /**
   * Confirm before discarding a draft.
   *
   * Destructive and irreversible, so it asks - and it says the photograph goes too,
   * because that is the part somebody would not think of and would miss.
   *
   * A refusal from the server (it turned out to be published, or somebody ordered
   * it) is shown as the reason it gave rather than swallowed. The draft is still
   * there either way.
   */
  const askDeleteDraft = useCallback((id: string, title: string) => {
    Alert.alert(
      t('prod.deleteDraft'),
      title ? t('prod.deleteAskNamed', { what: title }) : t('prod.deleteAsk'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('common.delete'),
          style: 'destructive',
          onPress: async () => {
            try {
              await store.deleteDraft(id);
            } catch (e: any) {
              Alert.alert('', e?.message || 'Could not delete that.');
            }
          },
        },
      ]);
  }, [store, t]);

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
          <Create resumeId={stack.resumeId ?? null} onHome={backToTabs}
                  onClusters={() => setStack({ screen: 'clusters' })} />
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
        ) : stack.screen === 'shop' ? (
          <Marketplace
            onBack={backToTabs}
            onOpen={(id) => setStack({ screen: 'shopItem', id })}
          />
        ) : stack.screen === 'shopItem' ? (
          <MarketProduct
            id={stack.id}
            onBack={() => setStack({ screen: 'shop' })}
            onOrdered={(orderId) => setStack({ screen: 'track', orderId })}
          />
        ) : stack.screen === 'track' ? (
          <OrderTracker orderId={stack.orderId} onBack={backToTabs} />
        ) : stack.screen === 'board' ? (
          <TransitBoard
            onBack={backToTabs}
            onTrack={(orderId) => setStack({ screen: 'track', orderId })}
          />
        ) : stack.screen === 'products' ? (
          <ProductsTab
            cards={store.cards}
            loading={store.loading}
            offline={store.offline}
            lastSync={store.lastSync}
            onOpen={openProduct}
            onLanguage={openLang}
            onRefresh={store.refresh}
            onDelete={askDeleteDraft}
          />
        ) : stack.screen === 'clusterOps' ? (
          <ClusterOps
            clusterId={stack.clusterId}
            onBack={() => setStack({ screen: 'clusters' })}
            onOpenProduct={(id) => setStack({ screen: 'product', id })}
            onTrack={(orderId) => setStack({ screen: 'track', orderId })}
          />
        ) : stack.screen === 'enquiries' ? (
          <Enquiries
            enquiries={store.enquiries}
            loading={store.loading}
            onBack={backToTabs}
            onRefresh={store.refreshOrders}
          />
        ) : stack.screen === 'clusters' ? (
          <Clusters
            onBack={backToTabs}
            onLanguage={openLang}
            onChanged={() => store.refresh()}
            onOpenOps={(clusterId) => setStack({ screen: 'clusterOps', clusterId })}
          />
        ) : stack.screen === 'clusterAdmin' ? (
          <ClusterDashboard
            onBack={backToTabs}
            onLanguage={openLang}
            onChanged={() => store.refresh()}
            onGoodsReceipts={(clusterId) => setStack({ screen: 'grn', clusterId })}
          />
        ) : stack.screen === 'grn' ? (
          <GoodsReceipts
            clusterId={stack.clusterId}
            enquiryId={stack.enquiryId}
            onBack={() => setStack({ screen: 'clusterAdmin' })}
            onLanguage={openLang}
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
                  onOpenProduct={openProduct}
                  onResumeDraft={(id) => setStack({ screen: 'create', resumeId: id })}
                  onEnquiries={() => setStack({ screen: 'enquiries' })}
                  onClusters={() => setStack({ screen: 'clusters' })}
                  onClusterAdmin={() => setStack({ screen: 'clusterAdmin' })}
                  onShop={() => setStack({ screen: 'shop' })}
                  onProducts={() => setStack({ screen: 'products' })}
                  ops={store.artisan ? (
                    <OpsHeader
                      metrics={ops}
                      loading={opsLoading}
                      // Pending reviews and Listing Reviews are the same queue, so
                      // they lead to the same place rather than to two screens that
                      // would have to be kept in agreement.
                      onPendingReviews={() => setTab('approvals')}
                      onLiveProducts={() => setStack({ screen: 'shop' })}
                      onActiveOrders={() => setStack({ screen: 'board' })}
                      onPayouts={() => setTab('reports')}
                      onRevenue={() => setTab('reports')}
                      onBulkSplitting={() => setStack({ screen: 'enquiries' })}
                      onClusters={() => setStack({ screen: 'clusters' })}
                    />
                  ) : null}
                  onLogin={() => setAuthOpen(true)}
                  onLanguage={openLang}
                  onTab={setTab}
                  onRefresh={() => { store.refresh(); store.refreshOrders(); }}
                />
              )}
              {tab === 'approvals' && (
                <Approvals
                  onLanguage={openLang}
                  onOpen={(id) => setStack({ screen: 'create', resumeId: id })}
                />
              )}
              {tab === 'reports' && (
                <Reports onLanguage={openLang} onOrders={() => setTab('orders')} />
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
                  onOpenProduct={openProduct}
                  onTrack={(orderId) => setStack({ screen: 'track', orderId })}
                  onBoard={() => setStack({ screen: 'board' })}
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
