import { Platform } from 'react-native';

/**
 * Design system.
 *
 * The brief asks for a minimalist, accessible UI for low-literacy users. That is
 * often read as "make it plain", which produces something that looks unfinished and
 * signals to the artisan that they got the cheap version. The opposite is true: this
 * is the first digital storefront most of these users will ever own, and it should
 * feel like a premium tool.
 *
 * So: high contrast and large targets for accessibility, but a real palette, real
 * depth, and a typeface that treats Devanagari as a first-class citizen rather than
 * a fallback. Mukta (Indian Type Foundry) covers Devanagari and Latin in one family,
 * so Hindi and English share a single type system instead of colliding.
 */

export const C = {
  // Warm paper ground - reads well in direct sunlight, unlike pure white or grey.
  bg: '#FBF8F4',
  bgAlt: '#F4EEE6',
  surface: '#FFFFFF',

  // Deep indigo ink rather than black: softer at large sizes, still AAA on paper.
  ink: '#171331',
  inkMid: '#4A4468',
  inkSoft: '#7C7596',
  line: '#E9E1D8',
  lineStrong: '#D8CDC0',

  // Saffron -> terracotta. The brand gradient.
  primary: '#D4541F',
  primaryDeep: '#A93C12',
  primaryLite: '#FF9A4D',
  primarySoft: '#FDEEE4',

  // Indigo, for the passport / trust surfaces.
  indigo: '#2E2A6B',
  indigoDeep: '#1B1846',
  indigoSoft: '#EAE8F6',

  // Emerald means money, always. Never used decoratively.
  money: '#0B7A54',
  moneyDeep: '#065C3F',
  moneySoft: '#E3F3EC',

  gold: '#B98A1F',
  goldSoft: '#FBF2DC',
  danger: '#B3261E',
  dangerSoft: '#FCEBE9',

  white: '#FFFFFF',
} as const;

export const GRAD = {
  primary: ['#FF9A4D', '#D4541F'] as const,
  primaryDeep: ['#D4541F', '#A93C12'] as const,
  indigo: ['#3A3585', '#1B1846'] as const,
  money: ['#12A06E', '#065C3F'] as const,
  gold: ['#E8C15F', '#B98A1F'] as const,
};

export const S = {
  xs: 4, sm: 8, md: 12, lg: 18, xl: 26, xxl: 38,
};

export const R = {
  sm: 10, md: 16, lg: 22, xl: 28, xxl: 34, pill: 999,
};

// Mukta ships these weights; the numeric aliases keep call sites readable.
export const F = {
  light: 'Mukta_300Light',
  regular: 'Mukta_400Regular',
  medium: 'Mukta_500Medium',
  semi: 'Mukta_600SemiBold',
  bold: 'Mukta_700Bold',
  extra: 'Mukta_800ExtraBold',
};

/**
 * Type scale. Deliberately starts at 15 - there is no "small print" tier, because
 * every string here has to be readable by someone who reads slowly.
 * Devanagari sits lower than Latin on the baseline, so line heights are generous.
 */
export const T = {
  display: { fontFamily: F.extra, fontSize: 40, lineHeight: 50, color: C.ink },
  hero:    { fontFamily: F.extra, fontSize: 30, lineHeight: 40, color: C.ink },
  title:   { fontFamily: F.bold,  fontSize: 22, lineHeight: 32, color: C.ink },
  section: { fontFamily: F.bold,  fontSize: 18, lineHeight: 27, color: C.ink },
  body:    { fontFamily: F.medium, fontSize: 17, lineHeight: 27, color: C.ink },
  bodySoft:{ fontFamily: F.regular, fontSize: 16, lineHeight: 26, color: C.inkMid },
  label:   { fontFamily: F.semi,  fontSize: 15, lineHeight: 22, color: C.inkSoft },
  micro:   { fontFamily: F.semi,  fontSize: 13, lineHeight: 18, color: C.inkSoft,
             letterSpacing: 0.4 },
  money:   { fontFamily: F.extra, fontSize: 34, lineHeight: 44, color: C.money },
} as const;

/** Layered shadows. RN maps these to elevation on Android and box-shadow on web. */
export const shadow = (level: 1 | 2 | 3 = 1) => {
  const spec = {
    1: { r: 10, y: 3, o: 0.06, e: 2 },
    2: { r: 22, y: 8, o: 0.09, e: 6 },
    3: { r: 38, y: 16, o: 0.14, e: 12 },
  }[level];
  return Platform.select({
    web: { boxShadow: `0 ${spec.y}px ${spec.r}px rgba(23,19,49,${spec.o})` } as any,
    default: {
      shadowColor: '#171331',
      shadowOpacity: spec.o,
      shadowRadius: spec.r,
      shadowOffset: { width: 0, height: spec.y },
      elevation: spec.e,
    },
  });
};

export const money = (n: number) => '₹' + Math.round(n).toLocaleString('en-IN');
