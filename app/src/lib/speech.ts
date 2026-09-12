/**
 * Voice input, on the phone and in a browser.
 *
 * Two bugs lived here, and both are worth recording because they failed in opposite
 * directions.
 *
 * The first invented data. On native there was no recogniser at all, so `listen()`
 * waited 1.2 seconds and handed back a hardcoded Hindi sentence about a Banarasi
 * dupatta - whatever the artisan had said, and whatever she was photographing. A
 * potter holding a water pot would have watched the app confidently describe a silk
 * scarf. Nothing fabricates a transcript here any more.
 *
 * The second was mine, replacing the first. `expo-speech-recognition` does not
 * export `addSpeechRecognitionListener`; I wrote against an API I had not read. The
 * call threw, the throw was caught, and the artisan saw "could not start" after
 * granting permission - which looks exactly like a permissions problem and is not
 * one.
 *
 * What runs now
 * -------------
 * One implementation for both platforms, because the module ships
 * `ExpoWebSpeechRecognition`, a real implementation of the browser's
 * `SpeechRecognition` interface backed by Android's own recogniser. So the native
 * path is the web path with a different constructor, rather than a second body of
 * code guessing at a native API.
 *
 * That matters for this user base: Android's recogniser handles hi-IN, bn-IN, ta-IN,
 * mr-IN and gu-IN, it is free, it needs no key, and with the language pack installed
 * it works with no network.
 *
 * The honest limitation: the language pack for the chosen language has to be present
 * on the phone. When it is not, that surfaces as a named error rather than silence.
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

export type Listener = { stop: () => void };

export type ListenOpts = {
  lang: string;
  onPartial?: (text: string) => void;
  onFinal: (text: string) => void;
  /** Always something showable. Never a bare code. */
  onError?: (msg: string) => void;
};

/** Lazily required so a web bundle never reaches for a native module. */
function nativeLib(): any | null {
  if (Platform.OS === 'web') return null;
  try {
    return require('expo-speech-recognition');
  } catch {
    return null;
  }
}

/**
 * A recogniser object, whichever platform we are on.
 *
 * Both branches return something implementing the same interface - `lang`,
 * `continuous`, `interimResults`, `start()`, `stop()`, `onresult`, `onerror`,
 * `onend` - which is the whole reason there is one `listen()` below.
 */
function makeRecognition(): any | null {
  if (Platform.OS === 'web') {
    if (typeof window === 'undefined') return null;
    const Ctor = (window as any).SpeechRecognition
      || (window as any).webkitSpeechRecognition;
    return Ctor ? new Ctor() : null;
  }
  const lib = nativeLib();
  if (!lib?.ExpoWebSpeechRecognition) return null;
  try {
    return new lib.ExpoWebSpeechRecognition();
  } catch {
    return null;
  }
}

/** Is there a real recogniser here? Never true just because a fallback exists. */
export function isSupported(): boolean {
  return makeRecognition() !== null;
}

/** Ask for the microphone. Returns whether we may now listen. */
export async function requestPermission(): Promise<boolean> {
  if (Platform.OS === 'web') return isSupported();
  const mod = nativeLib()?.ExpoSpeechRecognitionModule;
  if (!mod?.requestPermissionsAsync) return false;
  try {
    const res = await mod.requestPermissionsAsync();
    return !!(res?.granted ?? res?.status === 'granted');
  } catch {
    return false;
  }
}

/** Turn a recogniser code into something worth showing an artisan. */
function describe(code: string, lang: string): string {
  const name = LANGS.find((l) => l.code === lang)?.label || lang;
  if (/language|not-supported|unsupported|locale/i.test(code)) {
    return `${name} voice typing is not installed on this phone. `
      + 'Add it in Settings, or type instead.';
  }
  if (/permission|denied|not-allowed/i.test(code)) {
    return 'The microphone is blocked. Allow it in Settings to speak.';
  }
  if (/network/i.test(code)) {
    return 'Voice typing needs a connection right now, and there is none.';
  }
  if (/no-speech|no_match|nomatch/i.test(code)) {
    return 'Nothing was heard. Hold the button and speak again.';
  }
  if (/busy|recognizer/i.test(code)) {
    return 'The microphone is busy. Try once more.';
  }
  if (/client/i.test(code)) {
    return 'Voice typing stopped unexpectedly. Try once more, or type instead.';
  }
  return `Voice typing could not start (${code || 'unknown'}). Type instead.`;
}

/**
 * Start listening.
 *
 * `onPartial` fires while she speaks. Exactly one of `onFinal` or `onError` fires
 * afterwards, and `onFinal` only ever carries words that were actually heard.
 */
export function listen(opts: ListenOpts): Listener {
  const rec = makeRecognition();
  if (!rec) {
    opts.onError?.(Platform.OS === 'web'
      ? 'This browser has no voice typing. Type instead.'
      : 'Voice typing is not available on this phone. Type instead.');
    return { stop: () => {} };
  }

  rec.lang = opts.lang;
  // One utterance at a time. `continuous` on Android keeps the mic open after she
  // lets go of the button, which is both confusing and a battery cost.
  rec.continuous = false;
  rec.interimResults = true;
  rec.maxAlternatives = 1;

  let best = '';
  let settled = false;

  const finish = () => {
    if (settled) return;
    settled = true;
    const text = best.trim();
    // Nothing heard is reported as nothing heard. It is never filled in.
    if (text) opts.onFinal(text);
    else opts.onError?.('Nothing was heard. Hold the button and speak again.');
  };

  rec.onresult = (e: any) => {
    let finalText = '';
    let interim = '';
    const results = e?.results ?? [];
    for (let i = 0; i < results.length; i++) {
      const r = results[i];
      const alt = r?.[0]?.transcript ?? '';
      if (r?.isFinal) finalText += alt;
      else interim += alt;
    }
    const heard = (finalText + interim).trim();
    if (heard) {
      best = heard;
      opts.onPartial?.(heard);
    }
  };

  rec.onerror = (e: any) => {
    const code = String(e?.error || e?.message || '');
    // Ending on silence after real speech is not worth showing as a failure.
    if (/no-speech|aborted|no_match|nomatch/i.test(code) && best.trim()) {
      finish();
      return;
    }
    if (/aborted/i.test(code)) { finish(); return; }
    settled = true;
    opts.onError?.(describe(code, opts.lang));
  };

  rec.onend = finish;

  try {
    rec.start();
  } catch (e: any) {
    settled = true;
    opts.onError?.(describe(String(e?.message || e), opts.lang));
  }

  return {
    stop: () => {
      try { rec.stop(); } catch { finish(); }
    },
  };
}
