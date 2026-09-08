/**
 * Localisation runtime.
 *
 * Three things were wrong before and are fixed here:
 *
 *  1. The chosen language was written to storage and then ignored, because every
 *     screen carried literal Devanagari. All strings now come from the catalogue.
 *  2. Hindi was the hardcoded fallback. Now the fallback chain is
 *     chosen -> English -> the key itself, so picking Gujarati never silently
 *     renders Hindi. A missing Gujarati string shows English, which is a visible,
 *     fixable gap rather than a confusing one.
 *  3. Changing the language did not re-render. The provider sits above the whole
 *     tree, so a change is immediate everywhere.
 */
import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import { EN, HI, StringKey } from './catalog';
import * as session from '../lib/session';

export type Lang = 'hi' | 'bn' | 'ta' | 'te' | 'mr' | 'gu' | 'kn' | 'or' | 'en';

export const LANGUAGES: { code: Lang; native: string; english: string; speech: string }[] = [
  { code: 'hi', native: 'हिन्दी',   english: 'Hindi',     speech: 'hi-IN' },
  { code: 'bn', native: 'বাংলা',    english: 'Bengali',   speech: 'bn-IN' },
  { code: 'mr', native: 'मराठी',    english: 'Marathi',   speech: 'mr-IN' },
  { code: 'gu', native: 'ગુજરાતી',  english: 'Gujarati',  speech: 'gu-IN' },
  { code: 'ta', native: 'தமிழ்',    english: 'Tamil',     speech: 'ta-IN' },
  { code: 'te', native: 'తెలుగు',   english: 'Telugu',    speech: 'te-IN' },
  { code: 'kn', native: 'ಕನ್ನಡ',    english: 'Kannada',   speech: 'kn-IN' },
  { code: 'or', native: 'ଓଡ଼ିଆ',    english: 'Odia',      speech: 'or-IN' },
  { code: 'en', native: 'English',  english: 'English',   speech: 'en-IN' },
];

// Generated locales. Bundled statically because Metro needs literal require paths,
// and because an artisan with no signal must still get their own language.
const GENERATED: Partial<Record<Lang, Record<string, string>>> = {
  bn: require('./locales/bn.json'),
  mr: require('./locales/mr.json'),
  gu: require('./locales/gu.json'),
  ta: require('./locales/ta.json'),
  te: require('./locales/te.json'),
  kn: require('./locales/kn.json'),
  or: require('./locales/or.json'),
};

function dictFor(lang: Lang): Record<string, string> {
  if (lang === 'en') return EN as any;
  if (lang === 'hi') return HI as any;
  return GENERATED[lang] ?? (EN as any);
}

export function interpolate(s: string, vars?: Record<string, string | number>): string {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k) =>
    (k in vars ? String(vars[k]) : m));
}

type Ctx = {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: StringKey | string, vars?: Record<string, string | number>) => string;
  locale: string;
  native: string;
};

const I18nCtx = createContext<Ctx>({
  lang: 'hi',
  setLang: () => {},
  t: (k) => (HI as any)[k] ?? String(k),
  locale: 'hi-IN',
  native: 'हिन्दी',
});

function storedLang(): Lang | null {
  const stored = session.getLang() as Lang | null;
  return stored && LANGUAGES.some((l) => l.code === stored) ? stored : null;
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  // Storage is asynchronous on a phone, so the chosen language is not known at the
  // first render. It is read here as soon as it is available; the brief moment before
  // that shows Hindi, which is the onboarding default anyway.
  const [lang, setLangState] = useState<Lang>(() => storedLang() ?? 'hi');

  useEffect(() => {
    let alive = true;
    session.hydrate().then(() => {
      const l = storedLang();
      if (alive && l) setLangState(l);
    });
    return () => { alive = false; };
  }, []);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    session.setLang(l);        // persisted, so a relaunch keeps it
  }, []);

  const value = useMemo<Ctx>(() => {
    const dict = dictFor(lang);
    const meta = LANGUAGES.find((l) => l.code === lang) ?? LANGUAGES[0];
    return {
      lang,
      setLang,
      locale: meta.speech,
      native: meta.native,
      t: (key, vars) => {
        const raw = dict[key as string] ?? (EN as any)[key] ?? String(key);
        return interpolate(raw, vars);
      },
    };
  }, [lang, setLang]);

  return <I18nCtx.Provider value={value}>{children}</I18nCtx.Provider>;
}

export const useI18n = () => useContext(I18nCtx);

/** Convenience for components that only need the translate function. */
export const useT = () => useI18n().t;
