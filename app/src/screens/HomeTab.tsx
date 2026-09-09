/**
 * Home.
 *
 * The old Home was a create button, a speak-aloud card, and three "trends" a language
 * model had invented, complete with a confidence percentage. It occupied the first tab
 * without telling the artisan anything about her own work.
 *
 * This one answers, in order, the questions she actually opens the app with:
 *
 *   1. Is there anything I need to do?      -> Needs you
 *   2. How is my work doing?                -> counts, from her own rows
 *   3. What do I want to do now?            -> quick actions
 *   4. What is selling / being looked at?   -> only where the figure is genuinely counted
 *   5. What are others doing?               -> aggregated, anonymised, dismissible
 *
 * Every number here is a count of rows in the database. If it says four products sold,
 * four orders were paid. Nothing on this screen is estimated or filled in.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Image, Pressable, RefreshControl } from 'react-native';
import { Card, Btn, Pill, Skeleton, money } from '../ui';
import { C, S, T, R } from '../theme';
import {
  Plus, Camera, Rupee, Globe, Users, Trend, Warning, Tag, Check,
} from '../icons';
import { TabScreen } from '../nav/Shell';
import { JobList } from '../ui/JobCard';
import { useI18n } from '../i18n';
import { ago } from '../lib/ago';
import * as api from '../lib/api';
import * as session from '../lib/session';

/* ------------------------------------------------------------------ pieces */

function Stat({ label, value, tone = 'ink' }: {
  label: string; value: string; tone?: 'ink' | 'money' | 'indigo';
}) {
  const color = tone === 'money' ? C.money : tone === 'indigo' ? C.indigo : C.ink;
  return (
    <View style={{
      flexGrow: 1, flexBasis: '28%', minWidth: 96, backgroundColor: C.surface,
      borderRadius: R.lg, borderWidth: 1, borderColor: C.line, padding: S.md, gap: 1,
    }}>
      <Text style={{ fontFamily: 'Mukta_800ExtraBold', fontSize: 24, color }}>
        {value}
      </Text>
      <Text style={[T.micro, { fontSize: 11.5 }]} numberOfLines={1}>{label}</Text>
    </View>
  );
}

function QuickAction({ icon, label, onPress }: {
  icon: React.ReactNode; label: string; onPress: () => void;
}) {
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label}
               style={({ pressed }) => ({
                 flexGrow: 1, flexBasis: '40%', minWidth: 140, opacity: pressed ? 0.75 : 1,
                 backgroundColor: C.surface, borderRadius: R.lg, borderWidth: 1,
                 borderColor: C.line, padding: S.md,
                 flexDirection: 'row', alignItems: 'center', gap: S.sm,
               })}>
      <View style={{ width: 34, height: 34, borderRadius: R.pill,
                     backgroundColor: C.bgAlt, alignItems: 'center',
                     justifyContent: 'center' }}>
        {icon}
      </View>
      <Text style={[T.body, { flex: 1, fontSize: 15, fontFamily: 'Mukta_600SemiBold' }]}
            numberOfLines={2}>{label}</Text>
    </Pressable>
  );
}

/* -------------------------------------------------------------------- home */

export default function HomeTab({
  artisan, summary, insights, loading, jobs, onDismissJob,
  onNew, onOpenProduct, onResumeDraft,
  onEnquiries, onLogin, onLanguage, onTab, onRefresh,
}: {
  jobs: api.Job[];
  onDismissJob: (id: string) => void;
  artisan: api.Artisan | null;
  summary: api.Summary | null;
  insights: api.Insights | null;
  loading: boolean;
  onNew: () => void;
  onOpenProduct: (id: string) => void;
  onResumeDraft: (id: string) => void;
  onEnquiries: () => void;
  onLogin: () => void;
  onLanguage: () => void;
  onTab: (t: 'products' | 'orders') => void;
  onRefresh: () => void;
}) {
  const { t } = useI18n();
  const [hideInsights, setHide] = useState(session.insightsHidden());

  const name = artisan?.fullName?.trim();
  const p = summary?.products;
  const o = summary?.orders;
  const actions = summary?.actions || [];
  const drafts = summary?.drafts || [];

  const hide = useCallback(() => {
    session.setInsightsHidden(true);      // persisted: it stays hidden across launches
    setHide(true);
  }, []);

  return (
    <TabScreen
      title={name ? `${t('home.greeting')}, ${name}` : t('home.greeting')}
      subtitle={artisan ? `+91 ${artisan.phone} · ${t('home.verified')}` : t('home.guest')}
      onLanguage={onLanguage}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={onRefresh}
                                      tintColor={C.primary} />}
    >
      <Btn label={t('home.addProduct')} sub={t('home.addProductSub')}
           icon={<Plus color={C.white} size={26} />} onPress={onNew} large />

      {/*
        Photographs the server is still working through, and finished ones she has not
        seen. This sits above everything because it is the answer to "what happened to
        the photo I sent?" - the question she opens the app with after handing one off.
      */}
      <JobList jobs={jobs} onOpen={onOpenProduct} onDismiss={onDismissJob} />

      {/* ── needs you ──────────────────────────────────────────────────── */}
      {actions.length ? (
        <>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
            <Warning color={C.primaryDeep} size={19} />
            <Text style={[T.section, { flex: 1 }]}>{t('home.needsYou')}</Text>
            {summary?.actionCount && summary.actionCount > actions.length ? (
              <Pill text={String(summary.actionCount)} tone="warn" />
            ) : null}
          </View>
          {actions.map((a, i) => {
            const label =
              a.kind === 'finish_draft' ? t('home.actFinishDraft')
              : a.kind === 'publish_failed' ? t('home.actPublishFailed', { what: a.detail ?? '' })
              : a.kind === 'out_of_stock' ? t('home.actOutOfStock')
              : t('home.actShipOrder');
            const go = a.listingId
              ? () => (a.kind === 'finish_draft'
                  ? onResumeDraft(a.listingId!) : onOpenProduct(a.listingId!))
              : () => onTab('orders');
            return (
              <Card key={`${a.kind}-${a.listingId || a.orderId || i}`} onPress={go}
                    tone={a.kind === 'publish_failed' ? 'danger' : 'plain'}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.md }}>
                  {a.image ? (
                    <Image source={{ uri: a.image }}
                           style={{ width: 46, height: 46, borderRadius: R.md,
                                    backgroundColor: C.bgAlt }} />
                  ) : (
                    <View style={{ width: 46, height: 46, borderRadius: R.md,
                                   backgroundColor: C.bgAlt, alignItems: 'center',
                                   justifyContent: 'center' }}>
                      {a.kind === 'ship_order'
                        ? <Rupee color={C.money} size={19} />
                        : <Camera color={C.inkSoft} size={19} />}
                    </View>
                  )}
                  <View style={{ flex: 1 }}>
                    <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}
                          numberOfLines={1}>{label}</Text>
                    <Text style={[T.micro, { fontSize: 12.5 }]} numberOfLines={1}>
                      {a.title}{a.amount ? ` · ${money(a.amount)}` : ''}
                    </Text>
                  </View>
                  <Text style={{ fontSize: 22, color: C.inkSoft, marginTop: -3 }}>›</Text>
                </View>
              </Card>
            );
          })}
        </>
      ) : summary?.authenticated && p?.total ? (
        <Card tone="money">
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
            <Check color={C.money} size={19} />
            <Text style={[T.bodySoft, { flex: 1 }]}>{t('home.needsYouNone')}</Text>
          </View>
        </Card>
      ) : null}

      {/* ── guests: their drafts live on this phone, and that is worth saying ── */}
      {!artisan ? (
        <>
          <Card tone="soft">
            <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
              {t('home.guestBanner')}
            </Text>
            <Text style={T.bodySoft}>{t('home.guestBannerSub')}</Text>
            <Btn label={t('home.login')} tone="tonal" onPress={onLogin} />
          </Card>
          {drafts.map((d) => (
            <Card key={d.id} onPress={() => onResumeDraft(d.id)}>
              <View style={{ flexDirection: 'row', gap: S.md, alignItems: 'center' }}>
                {d.imageUrl ? (
                  <Image source={{ uri: d.imageUrl }}
                         style={{ width: 54, height: 54, borderRadius: R.md,
                                  backgroundColor: C.bgAlt }} />
                ) : (
                  <View style={{ width: 54, height: 54, borderRadius: R.md,
                                 backgroundColor: C.bgAlt, alignItems: 'center',
                                 justifyContent: 'center' }}>
                    <Camera color={C.inkSoft} size={20} />
                  </View>
                )}
                <View style={{ flex: 1 }}>
                  <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}
                        numberOfLines={1}>
                    {d.title || t('home.noDraftName')}
                  </Text>
                  <Text style={[T.micro, { fontSize: 12 }]}>
                    {d.price ? money(d.price) : t('home.priceMissing')}
                  </Text>
                </View>
                <Pill text={t('home.resume')} tone="soft" />
              </View>
            </Card>
          ))}
        </>
      ) : null}

      {/* ── your work, in counts ───────────────────────────────────────── */}
      {summary?.authenticated ? (
        <>
          <Text style={T.section}>{t('home.yourWork')}</Text>
          {loading && !p ? (
            <Card><Skeleton h={60} /></Card>
          ) : (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: S.sm }}>
              <Stat label={t('home.statProducts')} value={String(p?.total ?? 0)} />
              <Stat label={t('home.statLive')} value={String(p?.live ?? 0)}
                    tone="indigo" />
              <Stat label={t('home.statSold')} value={String(o?.sold ?? 0)} />
              <Stat label={t('home.statOrders')} value={String(o?.orders ?? 0)} />
              <Stat label={t('home.statEarned')} value={money(o?.revenue ?? 0)}
                    tone="money" />
              <Stat label={t('home.statViews')}
                    value={String(summary?.storefrontViews ?? 0)} />
            </View>
          )}
        </>
      ) : null}

      {/* ── quick actions ─────────────────────────────────────────────── */}
      <Text style={T.section}>{t('home.quickActions')}</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: S.sm }}>
        <QuickAction icon={<Tag color={C.ink} size={18} />}
                     label={t('home.viewProducts')} onPress={() => onTab('products')} />
        <QuickAction icon={<Rupee color={C.money} size={18} />}
                     label={t('home.viewOrders')} onPress={() => onTab('orders')} />
        <QuickAction icon={<Users color={C.indigo} size={18} />}
                     label={`${t('enq.title')}${summary?.newEnquiries
                       ? ` · ${summary.newEnquiries}` : ''}`}
                     onPress={onEnquiries} />
        {p?.drafts ? (
          <QuickAction icon={<Camera color={C.inkSoft} size={18} />}
                       label={t('home.continueDraft')}
                       onPress={() => onTab('products')} />
        ) : null}
      </View>

      {/* ── attention and sales, only where counted ───────────────────── */}
      {summary?.watched?.length ? (
        <>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
            <Trend color={C.indigo} size={19} />
            <Text style={[T.section, { flex: 1 }]}>{t('home.gettingAttention')}</Text>
          </View>
          {summary.watched.map((c) => (
            <Card key={c.id} onPress={() => onOpenProduct(c.id)}>
              <View style={{ flexDirection: 'row', gap: S.md, alignItems: 'center' }}>
                {c.imageUrl ? (
                  <Image source={{ uri: c.imageUrl }}
                         style={{ width: 46, height: 46, borderRadius: R.md,
                                  backgroundColor: C.bgAlt }} />
                ) : null}
                <Text style={[T.body, { flex: 1, fontFamily: 'Mukta_600SemiBold' }]}
                      numberOfLines={1}>{c.title}</Text>
                <Pill text={t('prod.viewsCount', { n: c.views ?? 0 })} tone="indigo" />
              </View>
            </Card>
          ))}
          <Text style={[T.micro, { fontSize: 11.5 }]}>{t('home.viewsNote')}</Text>
        </>
      ) : null}

      {summary?.best?.length ? (
        <>
          <Text style={T.section}>{t('home.bestSelling')}</Text>
          {summary.best.map((c) => (
            <Card key={c.id} onPress={() => onOpenProduct(c.id)}>
              <View style={{ flexDirection: 'row', gap: S.md, alignItems: 'center' }}>
                <Text style={[T.body, { flex: 1, fontFamily: 'Mukta_600SemiBold' }]}
                      numberOfLines={1}>{c.title}</Text>
                <Text style={[T.body, { color: C.money, fontFamily: 'Mukta_700Bold' }]}>
                  {money(c.revenue)}
                </Text>
              </View>
              <Text style={[T.micro, { fontSize: 12 }]}>
                {t('prod.soldCount', { n: c.sold })}
              </Text>
            </Card>
          ))}
        </>
      ) : null}

      {/* ── what other artisans are doing ─────────────────────────────── */}
      {hideInsights ? (
        <Pressable onPress={() => { session.setInsightsHidden(false); setHide(false); }}>
          <Text style={[T.micro, { textAlign: 'center', paddingVertical: S.sm,
                                   textDecorationLine: 'underline' }]}>
            {t('ins.show')}
          </Text>
        </Pressable>
      ) : (
        <>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
            <Users color={C.ink} size={19} />
            <Text style={[T.section, { flex: 1 }]}>{t('ins.title')}</Text>
            <Pressable onPress={hide} hitSlop={10} accessibilityLabel={t('ins.hide')}>
              <Pill text={t('ins.hide')} tone="soft" />
            </Pressable>
          </View>
          <Text style={[T.micro, { fontSize: 12 }]}>{t('ins.sub')}</Text>

          {!insights ? (
            <Card><Skeleton h={44} /></Card>
          ) : !insights.enough ? (
            <Card><Text style={T.bodySoft}>{t('ins.notEnough')}</Text></Card>
          ) : (
            <>
              {insights.categories.map((c) => (
                <Card key={c.category}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                    <Text style={[T.body, { flex: 1, fontFamily: 'Mukta_700Bold',
                                            textTransform: 'capitalize' }]}>
                      {c.category}
                    </Text>
                    {c.yours ? <Pill text={t('ins.yours')} tone="good" /> : null}
                  </View>
                  <Text style={[T.body, { color: C.money, fontFamily: 'Mukta_700Bold' }]}>
                    {t('ins.priceRange', { low: money(c.priceLow),
                                           high: money(c.priceHigh) })}
                  </Text>
                  <Text style={[T.micro, { fontSize: 12 }]}>
                    {t('ins.median', { n: money(c.median) })} · {' '}
                    {t('ins.sellers', { n: c.artisans })} · {' '}
                    {t('ins.listingsCount', { n: c.listings })}
                  </Text>
                </Card>
              ))}
              <Text style={[T.micro, { fontSize: 11.5 }]}>
                {t('ins.privacy', { n: insights.minCohort })}
              </Text>
            </>
          )}
        </>
      )}
    </TabScreen>
  );
}
