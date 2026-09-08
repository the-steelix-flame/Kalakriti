import React from 'react';
import Svg, { Path, Circle, Rect, G } from 'react-native-svg';
import { C } from './theme';

/**
 * Line icons, 24px grid, 1.8 stroke. Emoji were doing this job before, which made
 * the whole app read as a prototype - they render differently on every platform and
 * carry no weight relationship to the type.
 */
type P = { size?: number; color?: string; strokeWidth?: number };

const Base = ({ size = 24, children }: P & { children: React.ReactNode }) => (
  <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">{children}</Svg>
);
const s = (color: string = C.ink, strokeWidth: number = 1.8) => ({
  stroke: color, strokeWidth, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
});

export const Camera = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h1.2a2 2 0 0 0 1.7-1l.5-.9A1.6 1.6 0 0 1 10.3 3h3.4a1.6 1.6 0 0 1 1.4.8l.5 1a2 2 0 0 0 1.7 1h1.2A2.5 2.5 0 0 1 21 8.5v8A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z" {...s(color, strokeWidth)} />
    <Circle cx="12" cy="12.5" r="3.5" {...s(color, strokeWidth)} />
  </Base>
);

export const Gallery = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Rect x="3" y="4" width="18" height="16" rx="3" {...s(color, strokeWidth)} />
    <Circle cx="8.5" cy="9.5" r="1.6" {...s(color, strokeWidth)} />
    <Path d="M4 17l4.5-4.5a2 2 0 0 1 2.8 0L16 17M14 14.5l1.8-1.8a2 2 0 0 1 2.8 0L20 14" {...s(color, strokeWidth)} />
  </Base>
);

export const Mic = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Rect x="9" y="2.5" width="6" height="11" rx="3" {...s(color, strokeWidth)} />
    <Path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" {...s(color, strokeWidth)} />
  </Base>
);

export const Sparkle = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" {...s(color, strokeWidth)} />
    <Path d="M18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" {...s(color, strokeWidth)} />
  </Base>
);

export const Shield = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Path d="M12 2.8l7 2.6v5.4c0 4.3-2.9 8.2-7 9.4-4.1-1.2-7-5.1-7-9.4V5.4z" {...s(color, strokeWidth)} />
    <Path d="M8.8 11.8l2.2 2.2 4.2-4.4" {...s(color, strokeWidth)} />
  </Base>
);

export const Rupee = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Path d="M7 4h10M7 8.5h10M16 4c0 3.6-2.6 5.6-6 5.6h-.7l7.2 10.4M7 9.6h2.3" {...s(color, strokeWidth)} />
  </Base>
);

export const Users = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Circle cx="9" cy="8" r="3.2" {...s(color, strokeWidth)} />
    <Path d="M3 19.5c0-3.1 2.7-5.3 6-5.3s6 2.2 6 5.3M16 5.2a3.2 3.2 0 0 1 0 6.1M18 19.5c0-2-.7-3.6-2-4.7" {...s(color, strokeWidth)} />
  </Base>
);

export const Trend = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Path d="M3 17l5.2-5.6 3.4 3.2L21 6" {...s(color, strokeWidth)} />
    <Path d="M15.5 6H21v5.4" {...s(color, strokeWidth)} />
  </Base>
);

export const Globe = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Circle cx="12" cy="12" r="9" {...s(color, strokeWidth)} />
    <Path d="M3.5 9.5h17M3.5 14.5h17M12 3c2.4 2.4 3.6 5.5 3.6 9S14.4 18.6 12 21c-2.4-2.4-3.6-5.5-3.6-9S9.6 5.4 12 3z" {...s(color, strokeWidth)} />
  </Base>
);

export const Check = ({ size, color, strokeWidth }: P) => (
  <Base size={size}><Path d="M4.5 12.5l5 5 10-11" {...s(color, strokeWidth)} /></Base>
);

export const Arrow = ({ size, color, strokeWidth }: P) => (
  <Base size={size}><Path d="M4 12h15M13 6l6 6-6 6" {...s(color, strokeWidth)} /></Base>
);

export const Back = ({ size, color, strokeWidth }: P) => (
  <Base size={size}><Path d="M20 12H5M11 6l-6 6 6 6" {...s(color, strokeWidth)} /></Base>
);

export const Plus = ({ size, color, strokeWidth }: P) => (
  <Base size={size}><Path d="M12 5v14M5 12h14" {...s(color, strokeWidth)} /></Base>
);

export const Speaker = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Path d="M4 9.5h3.2L12 5.4v13.2L7.2 14.5H4z" {...s(color, strokeWidth)} />
    <Path d="M15.5 9.2a4 4 0 0 1 0 5.6M18 6.8a7.4 7.4 0 0 1 0 10.4" {...s(color, strokeWidth)} />
  </Base>
);

export const Warning = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Path d="M12 3.6l9 15.6H3z" {...s(color, strokeWidth)} />
    <Path d="M12 9.5v4.2M12 16.6v.1" {...s(color, strokeWidth)} />
  </Base>
);

export const Home = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Path d="M4 10.3L12 3.6l8 6.7V19a1.6 1.6 0 0 1-1.6 1.6H5.6A1.6 1.6 0 0 1 4 19z" {...s(color, strokeWidth)} />
    <Path d="M9.6 20.6v-6.2h4.8v6.2" {...s(color, strokeWidth)} />
  </Base>
);

export const Tag = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Path d="M3.5 11.2V4.8A1.3 1.3 0 0 1 4.8 3.5h6.4a1.3 1.3 0 0 1 .9.4l8 8a1.3 1.3 0 0 1 0 1.8l-6.4 6.4a1.3 1.3 0 0 1-1.8 0l-8-8a1.3 1.3 0 0 1-.4-.9z" {...s(color, strokeWidth)} />
    <Circle cx="7.8" cy="7.8" r="1.3" {...s(color, strokeWidth)} />
  </Base>
);

export const Loom = ({ size, color, strokeWidth }: P) => (
  <Base size={size}>
    <Rect x="3.5" y="3.5" width="17" height="17" rx="2.4" {...s(color, strokeWidth)} />
    <Path d="M8.2 3.5v17M13 3.5v17M17.8 3.5v17M3.5 8.2h17M3.5 13h17M3.5 17.8h17" {...s(color, strokeWidth)} />
  </Base>
);
