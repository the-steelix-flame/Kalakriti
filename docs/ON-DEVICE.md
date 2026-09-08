# On-device architecture

The target user has an entry-level Android phone and unreliable data. So the question
for every capability is not "can we do this in the cloud" but "what is the cheapest
place this can correctly happen", and the default answer is the phone.

This document records what was evaluated, what was chosen, what is built today, and
what the upgrade path is. It is deliberately explicit about the last two being
different things.

---

## 1. What was evaluated

### Camera and live frame access

| Option | Real-time frames | Android impl | Expo | Verdict |
|---|---|---|---|---|
| `expo-camera` `CameraView` | **No frame API** — only `onBarcodeScanned` | Camera2/CameraX | Native, zero config | **Chosen for the viewfinder.** In-app preview + overlay with no new native deps |
| `expo-image-picker` `launchCameraAsync` | n/a | Hands off to the system camera app | Native | **Rejected — this was the bug.** Control leaves our app, so no guidance is possible |
| `react-native-vision-camera` v4 | **Yes**, frame processors on a worklet thread | Wraps **CameraX** | Needs a dev build + `react-native-worklets-core` | **Chosen as the upgrade path.** The only way to get true 30fps analysis in RN |

The decisive fact: `expo-camera` exposes no frame-processor callback. Verified
directly against the installed typings — the only per-frame hook is barcode scanning.
So real-time analysis is either VisionCamera, or sampling frames ourselves.

### OCR

| Option | Devanagari | Size | Offline | Licence | Notes |
|---|---|---|---|---|---|
| **ML Kit Text Recognition v2** | **Yes**, dedicated model | ~260 KB unbundled / ~4 MB bundled | Yes | Free, closed model, Play Services | Best accuracy-per-byte for Indic on Android |
| PaddleOCR mobile (ONNX) | Yes, via extra models | ~8–15 MB (det+rec+cls) | Yes | Apache-2.0 | Fully open, heavier, needs ONNX Runtime + OpenCV plumbing |
| RapidOCR ONNX | Latin/CJK strong, Devanagari weak | ~15 MB | Yes | Apache-2.0 | **Currently used server-side.** Good on the price labels we tested, poor on Hindi |
| Tesseract (`tess-two`) | Yes | ~10–30 MB per lang | Yes | Apache-2.0 | Old engine, markedly worse on photographs than on scans |

### Object detection / classification / segmentation

| Option | Task | Size | Speed (mid Android) | Licence |
|---|---|---|---|---|
| **ML Kit Object Detection & Tracking** | Detect + track, coarse labels | Bundled in Play Services | ~30fps | Free, closed |
| MediaPipe Tasks (`ObjectDetector`, `ImageSegmenter`) | Detect, segment | 3–10 MB | 15–30fps | Apache-2.0 |
| TFLite / LiteRT + EfficientDet-Lite0 | Detect | ~4.5 MB | ~20fps | Apache-2.0 |
| MediaPipe `SelfieSegmenter` | Segmentation | ~250 KB | 30fps+ | Apache-2.0 | Tuned for people, **not** products |
| U²-Net / MODNet (ONNX) | Matting | 4–176 MB | 1–4s CPU | MIT | **Currently used server-side (rembg)** |

### Speech

| Option | Indic | Offline | Notes |
|---|---|---|---|
| **Web Speech API** | hi, bn, ta, mr, gu, … | **No** — Chrome streams to Google | Currently used on web |
| Android `SpeechRecognizer` (offline) | Depends on downloaded packs | Yes, if the user installed the pack | Free, needs a native module in RN |
| Whisper tiny/base (`whisper.cpp`, ONNX) | Weak on Bhojpuri/Maithili | Yes | 40–150 MB, seconds per utterance |
| AI4Bharat IndicConformer | **22 Indian languages** | Yes, but ~600 MB | MIT — server-side realistic, on-device not yet |

---

## 2. What was chosen, and why

**For the guidance loop that had to work today: classical CV in pure TypeScript.**

`src/vision/quality.ts` implements Laplacian-variance sharpness, luminance and
clipping statistics, a Sobel edge field, a subject box derived from row/column energy
profiles, a clutter ratio, and a gradient-orientation tilt estimate.

This was chosen over shipping a model because:

- it needs **no model download, no native module and no Play Services**, so it works
  on the first launch of a fresh APK with the aeroplane mode on;
- it runs in a few milliseconds on a 160×120 buffer, so it is affordable at 6fps on
  an entry-level phone and barely touches the battery;
- its failures are **explainable**. "Your Laplacian variance is low" maps cleanly to
  "hold the phone still". A detector that silently misses a pot gives the artisan
  nothing to act on;
- framing guidance genuinely does not need semantics. Knowing *where the subject is*
  and *whether the frame is usable* is enough for every instruction we give. Knowing
  that it is a *pot* adds nothing to "move back".

**The honest limitation:** without a detector this finds *the dominant edge mass*,
not *the product*. On a plain background these coincide. On a cluttered one the box
can include background structure — which is exactly when the clutter warning fires,
so the artisan is told to fix the thing that is also degrading the estimate.

---

## 3. The on-device / internet split

### Works with no connectivity

| Capability | Where | Status |
|---|---|---|
| Camera viewfinder | `expo-camera` `CameraView` | **Built** |
| Capture quality (blur, exposure, glare) | `vision/quality.ts` | **Built** |
| Subject localisation + framing guidance | `vision/quality.ts` + `vision/guidance.ts` | **Built** |
| Category-specific capture rules | `vision/guidance.ts` | **Built** |
| Multi-shot plan | `screens/shotPlan.ts` | **Built** |
| Spoken guidance | `expo-speech` (device TTS) | **Built** |
| Draft persistence | device storage + SQLite draft row | **Built** |
| OCR | ML Kit Devanagari | **Server-side today** (RapidOCR) |
| Object labels | ML Kit / MediaPipe | **Not built** — see upgrade path |
| Background removal | U²-Net | **Server-side today** (rembg) |
| Speech-to-text | Web Speech / Android offline packs | **Online today** on web |

### Genuinely needs the network

Authentication and OTP · marketplace publishing · payment · order and shipping
sync · courier booking and tracking · Nemotron copywriting and pricing · cloud
backup. These involve a third party by definition; no amount of on-device work
removes them.

---

## 4. Upgrade path, in priority order

1. **ML Kit Text Recognition v2 (Devanagari)** — the single highest-value move.
   Turns OCR from a round trip into an instant local read, and handles Hindi, which
   the current RapidOCR server path does poorly. ~4 MB bundled.
2. **VisionCamera v4 frame processors** — replaces the sampling loop with true
   per-frame analysis at 30fps, removing the ~550 ms cadence on native.
3. **ML Kit Object Detection** — real labels and a real subject box, replacing the
   edge-energy heuristic and making the clutter case robust.
4. **MediaPipe ImageSegmenter or a TFLite U²-Net** — on-device matting, so background
   removal stops needing the server.
5. **Android offline `SpeechRecognizer`** — removes the last online dependency in the
   capture flow.

Each is independent. The interfaces in `vision/` were written so a real detector can
replace `subjectBox()` without touching the guidance rules or the camera screens.

---

## 5. Why not just add the libraries now

Steps 1–4 all require a native rebuild and, for VisionCamera, a worklets runtime that
has to match React Native 0.86 exactly. Adding four native dependencies at once to a
build that currently produces a working APK is how you end up with no working APK.
The classical pipeline delivers the requested behaviour — live guidance, offline,
category-aware — with zero build risk, and each upgrade above can then be landed and
verified one at a time.
