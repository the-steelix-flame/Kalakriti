/**
 * "We are making your listing" / "your listing is ready".
 *
 * This is how the artisan is told about work happening on the server. It is in-app
 * rather than a push notification, and that is a real limitation stated plainly:
 * waking a closed app needs Firebase Cloud Messaging credentials and a Google project
 * this deployment does not have. What exists instead is honest - the card is here
 * whenever she opens the app, the count is on the Home tab, and nothing is lost while
 * she is away.
 *
 * The progress line names the actual stage the server is on, not a generic spinner,
 * because "reading the label" tells her the photograph was good enough to read and
 * "looking at your product" tells her it got that far.
 */
import React from 'react';
import { View, Text, Image, Pressable } from 'react-native';
import { Card, Btn, Pill, money } from '../ui';
import { Spark } from './AiButton';
import { C, S, T, R } from '../theme';
import { Warning, Check, Camera } from '../icons';
import { useI18n } from '../i18n';
import * as api from '../lib/api';

function Bar({ pct }: { pct: number }) {
  return (
    <View style={{ height: 8, borderRadius: 99, backgroundColor: C.bgAlt,
                   overflow: 'hidden' }}>
      <View style={{ height: 8, borderRadius: 99,
                     width: `${Math.max(4, Math.min(100, pct))}%`,
                     backgroundColor: C.indigo }} />
    </View>
  );
}

export function JobCard({
  job, onOpen, onDismiss, onRetry,
}: {
  job: api.Job;
  onOpen: (listingId: string) => void;
  onDismiss: (id: string) => void;
  onRetry?: (job: api.Job) => void;
}) {
  const { t } = useI18n();
  const r = job.result;

  if (job.status === 'failed') {
    return (
      <Card tone="danger">
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <Warning color={C.danger} size={18} />
          <Text style={[T.body, { flex: 1, fontFamily: 'Mukta_700Bold' }]}>
            {t('job.failed')}
          </Text>
        </View>
        <Text style={T.bodySoft}>{t('job.failedSub')}</Text>
        {job.error ? (
          <Text style={[T.micro, { fontSize: 11.5 }]} numberOfLines={3}>{job.error}</Text>
        ) : null}
        <View style={{ flexDirection: 'row', gap: 6 }}>
          {onRetry ? (
            <Btn label={t('job.retry')} tone="tonal" onPress={() => onRetry(job)} />
          ) : null}
          <Btn label={t('common.close')} tone="ghost" onPress={() => onDismiss(job.id)} />
        </View>
      </Card>
    );
  }

  if (job.status === 'done') {
    return (
      <Card tone="money">
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <Check color={C.money} size={18} />
          <Text style={[T.body, { flex: 1, fontFamily: 'Mukta_700Bold' }]}>
            {t('job.ready')}
          </Text>
        </View>

        <View style={{ flexDirection: 'row', gap: S.md, alignItems: 'center' }}>
          {/* The thumbnail is ~15 KB. The full image is not fetched until she opens
              the product, which is the whole point of delivering text first. */}
          {r?.thumbUrl ? (
            <Image source={{ uri: r.thumbUrl }}
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
            <Text style={[T.body, { fontFamily: 'Mukta_600SemiBold' }]}
                  numberOfLines={2}>
              {r?.titleHi || r?.titleEn || t('home.noDraftName')}
            </Text>
            {r?.price ? (
              <Text style={[T.body, { color: C.money, fontFamily: 'Mukta_700Bold' }]}>
                {money(r.price)}
              </Text>
            ) : null}
          </View>
        </View>

        <Text style={T.bodySoft}>{t('job.readySub')}</Text>
        <View style={{ flexDirection: 'row', gap: 6 }}>
          <Btn label={t('job.open')} tone="money"
               onPress={() => { onDismiss(job.id); onOpen(job.listingId); }} />
          <Btn label={t('common.close')} tone="ghost"
               onPress={() => onDismiss(job.id)} />
        </View>
      </Card>
    );
  }

  // queued or running
  const stageKey = `job.stage.${job.stage}`;
  return (
    <Card tone="indigo">
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
        <Spark size={17} />
        <Text style={[T.body, { flex: 1, fontFamily: 'Mukta_700Bold' }]}>
          {t('job.working')}
        </Text>
        <Pill text={`${job.progress || 0}%`} tone="indigo" />
      </View>
      <Bar pct={job.progress || 0} />
      <Text style={T.bodySoft}>{t(stageKey)}</Text>
      <Text style={[T.micro, { fontSize: 12 }]}>{t('job.sentSub')}</Text>
    </Card>
  );
}

/** Every unfinished or unseen upload, for the top of Home. */
export function JobList({
  jobs, onOpen, onDismiss, onRetry,
}: {
  jobs: api.Job[];
  onOpen: (listingId: string) => void;
  onDismiss: (id: string) => void;
  onRetry?: (job: api.Job) => void;
}) {
  const shown = jobs.filter(
    (j) => j.status === 'queued' || j.status === 'running'
      || (j.status !== 'cancelled' && !j.seen));
  if (!shown.length) return null;
  return (
    <>
      {shown.map((j) => (
        <JobCard key={j.id} job={j} onOpen={onOpen} onDismiss={onDismiss}
                 onRetry={onRetry} />
      ))}
    </>
  );
}
