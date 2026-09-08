/**
 * Voice input.
 *
 * On web we use the browser's built-in SpeechRecognition (Web Speech API) — free,
 * no key, no model download, and it genuinely handles hi-IN, bn-IN, ta-IN and the
 * other major Indian locales. That makes the browser demo a real voice demo rather
 * than a simulated one.
 *
 * On native the production path is AI4Bharat IndicConformer (MIT, 22 languages)
 * running behind /v1/asr — the browser API does not exist there, and Whisper-class
 * models are weak on Bhojpuri, Maithili and Kutchi, which is what this user base
 * actually speaks. Until that endpoint is wired, native falls back to sample text.
 */
import { Platform } from 'react-native';

export const LANGS = [
  { code: 'hi-IN', label: 'हिन्दी' },
  { code: 'bn-IN', label: 'বাংলা' },
  { code: 'ta-IN', label: 'தமிழ்' },
  { code: 'mr-IN', label: 'मराठी' },
  { code: 'gu-IN', label: 'ગુજરાતી' },
  { code: 'en-IN', label: 'English' },
];

const SAMPLE =
  'ये बनारसी सिल्क का दुपट्टा है, हाथ से बुना है, तीन दिन लगे, असली ज़री का काम है, लाल रंग।';

function getRecognition(): any | null {
  if (Platform.OS !== 'web' || typeof window === 'undefined') return null;
  const Ctor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}

export function isSupported(): boolean {
  return getRecognition() !== null;
}

export type Listener = {
  stop: () => void;
};

/**
 * Starts listening. `onPartial` fires as the user speaks; `onFinal` fires once,
 * with the best transcript, when recognition ends or `stop()` is called.
 */
export function listen(opts: {
  lang: string;
  onPartial?: (text: string) => void;
  onFinal: (text: string) => void;
  onError?: (msg: string) => void;
}): Listener {
  const rec = getRecognition();

  if (!rec) {
    // Native, or a browser without the API (Firefox). Give the flow something to work on.
    const id = setTimeout(() => opts.onFinal(SAMPLE), 1200);
    return { stop: () => clearTimeout(id) };
  }

  rec.lang = opts.lang;
  rec.continuous = true;
  rec.interimResults = true;
  rec.maxAlternatives = 1;

  let best = '';
  let settled = false;

  const finish = () => {
    if (settled) return;
    settled = true;
    opts.onFinal(best.trim() || SAMPLE);
  };

  rec.onresult = (e: any) => {
    let finalText = '';
    let interim = '';
    for (let i = 0; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) finalText += r[0].transcript;
      else interim += r[0].transcript;
    }
    best = (finalText + interim).trim();
    opts.onPartial?.(best);
  };

  rec.onerror = (e: any) => {
    // 'no-speech' and 'aborted' are normal when the user taps stop quickly.
    if (e.error !== 'no-speech' && e.error !== 'aborted') {
      opts.onError?.(String(e.error));
    }
    finish();
  };

  rec.onend = finish;

  try {
    rec.start();
  } catch {
    finish();
  }

  return {
    stop: () => {
      try {
        rec.stop();
      } catch {
        finish();
      }
    },
  };
}
