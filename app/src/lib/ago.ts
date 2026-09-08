/**
 * "3 min ago", in the artisan's language.
 *
 * Translated rather than formatted with Intl.RelativeTimeFormat, because Hermes ships
 * without full ICU data - RelativeTimeFormat silently falls back to English for every
 * Indian locale, which is precisely the bug this app already had once.
 */
export function ago(t: (k: string, v?: any) => string, iso?: string | number | null): string {
  if (!iso) return '';
  const ms = typeof iso === 'number' ? iso : Date.parse(iso);
  if (!ms) return '';
  const mins = Math.floor((Date.now() - ms) / 60000);
  if (mins < 1) return t('sync.justNow');
  if (mins < 60) return t('sync.minutesAgo', { n: mins });
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return t('sync.hoursAgo', { n: hrs });
  return t('sync.daysAgo', { n: Math.floor(hrs / 24) });
}
