/**
 * Record a packaging video with the phone's own camera, and stream it up.
 *
 * Factored out of OrderTracker so a second screen - the Orders list - can offer the
 * same action without a second copy of the camera-permission-capture-upload sequence.
 * Two copies of this is how they drift: one screen gets a duration cap changed and the
 * other does not, and a year later nobody remembers why the two disagree.
 *
 * The system camera (via expo-image-picker) rather than a custom recorder, on
 * purpose: it gives record, preview and retake for free, and is far more reliable on
 * a cheap phone than anything built here from scratch.
 */
import * as ImagePicker from 'expo-image-picker';
import * as api from './api';

export type PackagingResult =
  | { ok: true; timeline: api.Timeline }
  | { ok: false; reason: 'permission' }
  | { ok: false; reason: 'cancelled' }
  | { ok: false; reason: 'error'; message: string };

/**
 * Opens the camera, waits for the artisan to record and confirm, then uploads.
 *
 * Returns a tagged result rather than throwing, because "she cancelled the camera" and
 * "the permission was refused" both need a specific, different message shown to her -
 * a bare exception loses that distinction.
 */
export async function recordAndUploadPackagingVideo(
  orderId: string,
): Promise<PackagingResult> {
  const perm = await ImagePicker.requestCameraPermissionsAsync();
  if (!perm.granted) {
    return { ok: false, reason: 'permission' };
  }

  const shot = await ImagePicker.launchCameraAsync({
    mediaTypes: ['videos'],
    // Short on purpose. This is proof the parcel was sealed, not a film, and the
    // artisan is often paying for the megabytes.
    videoMaxDuration: 30,
    quality: 0.7,
  });
  if (shot.canceled || !shot.assets?.length) {
    return { ok: false, reason: 'cancelled' };
  }
  const asset = shot.assets[0];

  try {
    const out = await api.uploadPackagingVideo(orderId, asset.uri, {
      seconds: (asset.duration || 0) / 1000,
    });
    return { ok: true, timeline: out.timeline };
  } catch (e: any) {
    return { ok: false, reason: 'error', message: e?.message || 'The upload did not finish.' };
  }
}
