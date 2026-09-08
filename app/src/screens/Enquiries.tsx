/**
 * Bulk and custom order requests.
 *
 * This replaces the screen called "Samuh". That screen displayed a 14-member artisan
 * consortium filling a 500-piece order, with named weavers, per-member progress bars
 * and a Rs 2,08,000 payout. None of it existed - the members were a hardcoded array
 * and the Join button set a boolean. The underlying idea (a cluster pooling capacity
 * to accept an order no single artisan can fill) is genuinely the best answer to the
 * middleman problem, and it is documented as future work. What it is not is something
 * to draw as though it were running.
 *
 * What is real is the demand side, so that is what this screen is now. Every product
 * page carries a form asking for a larger quantity or a custom variation; those
 * requests are rows in the database and they arrive here. The name says what it is in
 * words an artisan uses: bulk and custom orders, from customers who want many pieces.
 */
import React, { useCallback, useMemo, useState } from 'react';
import { View, Text, Linking, Pressable, TextInput } from 'react-native';
import { Card, Btn, Pill, Row, Divider, Skeleton, money } from '../ui';
import { FilterBar, applyList, byDate, byNum, FilterDef, SortDef } from '../ui/Filters';
import { C, S, T, R } from '../theme';
import { Users, Rupee, Warning } from '../icons';
import { TopBar } from '../nav/Shell';
import { ScrollView } from 'react-native';
import { useI18n } from '../i18n';
import { ago } from '../lib/ago';
import * as api from '../lib/api';

const FILTERS: FilterDef<api.Enquiry>[] = [
  { key: 'new', label: 'enq.new', match: (e) => e.status === 'new' },
  { key: 'replied', label: 'enq.replied', match: (e) => e.status === 'replied' },
  { key: 'quoted', label: 'enq.quoted', match: (e) => e.status === 'quoted' },
  { key: 'won', label: 'enq.won', match: (e) => e.status === 'won' },
  { key: 'lost', label: 'enq.lost', match: (e) => e.status === 'lost' },
  { key: 'all', label: 'prod.all', match: () => true },
];

const SORTS: SortDef<api.Enquiry>[] = [
  { key: 'newest', label: 'sort.newest', cmp: byDate((e) => e.createdAt, 1) },
  { key: 'oldest', label: 'sort.oldest', cmp: byDate((e) => e.createdAt, -1) },
  { key: 'qtyHigh', label: 'sort.valueHigh', cmp: byNum((e) => e.quantity, 1) },
  { key: 'qtyLow', label: 'sort.valueLow', cmp: byNum((e) => e.quantity, -1) },
];

export default function Enquiries({
  enquiries, loading, onBack, onRefresh,
}: {
  enquiries: api.Enquiry[];
  loading: boolean;
  onBack: () => void;
  onRefresh: () => void;
}) {
  const { t } = useI18n();
  const [filter, setFilter] = useState('new');
  const [sort, setSort] = useState('newest');
  const [openId, setOpenId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);

  const shown = useMemo(
    () => applyList(enquiries, FILTERS, filter, SORTS, sort),
    [enquiries, filter, sort]);

  const act = useCallback(async (id: string, body: { reply?: string; status?: string }) => {
    setBusy(true);
    try {
      await api.replyEnquiry(id, body);
      setOpenId(null);
      setDraft('');
      onRefresh();
    } catch { /* the list refresh will show whether it landed */ }
    finally { setBusy(false); }
  }, [onRefresh]);

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <TopBar title={t('enq.title')} subtitle={t('enq.sub')} onBack={onBack} />
      <ScrollView contentContainerStyle={{ padding: S.lg, gap: S.md, paddingBottom: 60 }}
                  showsVerticalScrollIndicator={false}>

        <Card tone="soft">
          <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'flex-start' }}>
            <Users color={C.indigo} size={18} />
            <Text style={[T.bodySoft, { flex: 1 }]}>{t('enq.explain')}</Text>
          </View>
        </Card>

        <FilterBar items={enquiries} filters={FILTERS} filter={filter}
                   onFilter={setFilter} sorts={SORTS} sort={sort} onSort={setSort} />

        {loading && !enquiries.length ? (
          <Card><Skeleton h={70} /></Card>
        ) : shown.length === 0 ? (
          <Card style={{ alignItems: 'center', paddingVertical: S.xl, gap: S.sm }}>
            <View style={{ width: 54, height: 54, borderRadius: R.pill,
                           backgroundColor: C.bgAlt, alignItems: 'center',
                           justifyContent: 'center' }}>
              <Users color={C.inkSoft} size={24} />
            </View>
            <Text style={T.body}>
              {enquiries.length ? t('prod.emptyFiltered') : t('enq.empty')}
            </Text>
            {!enquiries.length ? (
              <Text style={[T.bodySoft, { textAlign: 'center' }]}>{t('enq.emptySub')}</Text>
            ) : null}
          </Card>
        ) : shown.map((e) => {
          const open = openId === e.id;
          return (
            <Card key={e.id}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
                <View style={{ flex: 1 }}>
                  <Text style={[T.body, { fontFamily: 'Mukta_700Bold' }]}>
                    {e.buyerName}
                  </Text>
                  {e.organisation ? (
                    <Text style={[T.micro, { fontSize: 12 }]}>
                      {t('enq.org', { name: e.organisation })}
                    </Text>
                  ) : null}
                </View>
                <Pill text={t(`enq.${e.status}`)}
                      tone={e.status === 'new' ? 'warn'
                            : e.status === 'won' ? 'good'
                            : e.status === 'lost' ? 'danger' : 'indigo'} />
              </View>

              <Text style={[T.body, { fontFamily: 'Mukta_700Bold', color: C.indigo }]}>
                {t('enq.wants', { n: e.quantity })}
              </Text>
              {e.productTitle ? (
                <Text style={[T.micro, { fontSize: 12.5 }]}>{e.productTitle}</Text>
              ) : null}
              {e.neededBy ? (
                <Text style={T.bodySoft}>{t('enq.by', { date: e.neededBy })}</Text>
              ) : null}
              {e.targetPrice ? (
                <Row label={t('create.price')} value={money(e.targetPrice)} />
              ) : null}
              {e.message ? <Text style={T.bodySoft}>{e.message}</Text> : null}

              <Text style={[T.micro, { fontSize: 11.5 }]}>{ago(t, e.createdAt)}</Text>

              {e.reply ? (
                <>
                  <Divider />
                  <Text style={T.label}>{t('enq.reply')}</Text>
                  <Text style={T.bodySoft}>{e.reply}</Text>
                </>
              ) : null}

              <Divider />
              {open ? (
                <View style={{ gap: S.sm }}>
                  <TextInput
                    value={draft}
                    onChangeText={setDraft}
                    multiline
                    placeholder={t('enq.replyPlaceholder')}
                    placeholderTextColor={C.inkSoft}
                    style={{
                      borderWidth: 1, borderColor: C.lineStrong, borderRadius: R.md,
                      padding: S.md, minHeight: 84, fontFamily: 'Mukta_400Regular',
                      fontSize: 16, color: C.ink, textAlignVertical: 'top',
                      backgroundColor: C.surface,
                    }}
                  />
                  <Btn label={t('enq.send')} tone="money" busy={busy}
                       onPress={() => act(e.id, { reply: draft, status: 'quoted' })} />
                  <Btn label={t('common.cancel')} tone="tonal"
                       onPress={() => { setOpenId(null); setDraft(''); }} />
                </View>
              ) : (
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                  <Pressable onPress={() => { setOpenId(e.id); setDraft(e.reply || ''); }}>
                    <Pill text={t('enq.reply')} tone="indigo" />
                  </Pressable>
                  {e.buyerPhone ? (
                    <Pressable onPress={() => Linking.openURL(`tel:${e.buyerPhone}`)}>
                      <Pill text={`${t('enq.call')} · ${e.buyerPhone}`} tone="good" />
                    </Pressable>
                  ) : null}
                  {e.status !== 'won' ? (
                    <Pressable onPress={() => act(e.id, { status: 'won' })}>
                      <Pill text={t('enq.markWon')} tone="good" />
                    </Pressable>
                  ) : null}
                  {e.status !== 'lost' ? (
                    <Pressable onPress={() => act(e.id, { status: 'lost' })}>
                      <Pill text={t('enq.markLost')} tone="soft" />
                    </Pressable>
                  ) : null}
                </View>
              )}
            </Card>
          );
        })}
      </ScrollView>
    </View>
  );
}
