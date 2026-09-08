/**
 * Getting raw pixels out of a camera frame, on device, offline.
 *
 * Web has a canvas and this is trivial. Native has neither a canvas nor any pixel
 * access from expo-camera, so the frame is resized down with expo-image-manipulator
 * (native, fast) and then decoded by jpeg-js (pure JS, MIT).
 *
 * The resize matters more than it looks: decoding a full-resolution JPEG in
 * JavaScript costs a few hundred milliseconds, while decoding a 240px one costs a
 * few. Since the estimators in quality.ts work on a 160px buffer anyway, there is
 * nothing to gain from decoding anything larger.
 */
import { Platform } from 'react-native';

export type Rgba = { data: Uint8ClampedArray; width: number; height: number };

function b64ToBytes(b64: string): Uint8Array {
  const clean = b64.includes(',') ? b64.split(',')[1] : b64;
  if (typeof atob === 'function') {
    const bin = atob(clean);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  // React Native ships a Buffer polyfill via metro for some setups; fall back to it.
  const B = (globalThis as any).Buffer;
  if (B) return new Uint8Array(B.from(clean, 'base64'));
  throw new Error('no base64 decoder available');
}

/** Decode a base64 JPEG to RGBA. Used on native only. */
export async function decodeJpegToRgba(base64: string,
                                       _targetW = 240): Promise<Rgba | null> {
  try {
    const jpeg = require('jpeg-js');
    const bytes = b64ToBytes(base64);
    // useTArray keeps it in a typed array; tolerate slightly malformed streams
    // rather than dropping the frame entirely.
    const raw = jpeg.decode(bytes, { useTArray: true, tolerantDecoding: true });
    if (!raw?.data) return null;
    return {
      data: new Uint8ClampedArray(raw.data.buffer ?? raw.data),
      width: raw.width,
      height: raw.height,
    };
  } catch {
    return null;
  }
}

/**
 * Shrink a captured frame before decoding. Returns base64 JPEG.
 * On web this is a no-op because the caller already has a canvas.
 */
export async function shrinkToBase64(uri: string, width = 240): Promise<string | null> {
  if (Platform.OS === 'web') return null;
  try {
    const IM = require('expo-image-manipulator');
    // SDK 54+ exposes a context API; older builds keep manipulateAsync. Support both
    // so this does not break on the next Expo bump.
    if (IM.ImageManipulator?.manipulate) {
      const ctx = IM.ImageManipulator.manipulate(uri).resize({ width });
      const img = await ctx.renderAsync();
      const out = await img.saveAsync({
        format: IM.SaveFormat?.JPEG ?? 'jpeg', base64: true, compress: 0.6,
      });
      return out?.base64 ?? null;
    }
    const out = await IM.manipulateAsync(
      uri, [{ resize: { width } }],
      { base64: true, compress: 0.6, format: IM.SaveFormat?.JPEG ?? 'jpeg' });
    return out?.base64 ?? null;
  } catch {
    return null;
  }
}
