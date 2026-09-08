# Kalakriti · कलाकृति

**Craft, and the market it could never reach.**

*AI-Driven Market Linkage and Smart Cataloging for Marginalized Artisans*

> **शिल्प** (*shilp*) — Sanskrit and Hindi for craft: the skill of the hand.
> **市場** (*ichiba*) — Japanese for the marketplace, the word Rakuten built Japan's
> largest e-commerce business on.
>
> The name is the entire thesis in two words. The craft has always existed. The market
> has always existed. What has never existed is the bridge between them — which is the
> only thing this application is for.

A cross-platform mobile app that acts as a virtual business manager for weavers, artisans and
micro-entrepreneurs: photograph a product, describe it by voice in your own language, get a
fair price, and publish to ONDC, GeM and the major marketplaces — without typing a word.

The full strategy, differentiation and stack rationale is in
[Kalakriti_Strategy_and_Build.pdf](Kalakriti_Strategy_and_Build.pdf).

---

## What runs today

A complete, type-checked Expo / React Native app implementing the whole artisan journey.
It runs **standalone with no backend and no API keys** — every AI call resolves against a
deterministic mock layer, so a demo cannot fail because of the network.

| Screen | Feature |
|---|---|
| Home | Earnings, product grid, **Trend-to-Loom** production advice |
| Studio | Camera capture, AI enhancement, hold-to-compare, disclosed edit log, **Provenance Passport** |
| Describe | Press-and-hold voice capture → bilingual SEO listing + HSN autofill + spoken read-back |
| Price | Market comps, **fair floor price**, underpricing warning, full cost breakdown |
| Market | Multi-channel publish (ONDC / GeM / Karigar / Samarth / WhatsApp), **Samuh group orders** |

Point `EXPO_PUBLIC_API_URL` at the real backend and every feature switches to live models
with no UI change. The contract is five endpoints — see [`app/src/lib/ai.ts`](app/src/lib/ai.ts).

## Layout

```
app/
  App.tsx              five-step linear flow (not a tab bar - see the PDF for why)
  app.json             package id, permissions, config plugins
  eas.json             cloud build profiles; `preview` emits an APK
  src/theme.ts         design tokens: sunlight-legible palette, 56dp touch targets
  src/ui.tsx           primitives - every control pairs an icon with its label
  src/i18n.ts          Hindi/English strings
  src/lib/ai.ts        the AI contract: mock + remote behind one signature
  src/screens/         Home, Studio, Catalog, Pricing, Market
  android/             native project from `expo prebuild` - this is what Gradle compiles
```

## Run it

```bash
cd app
npm install
npx expo start          # scan with Expo Go, or press 'a' for a device/emulator
```

## Running it on a real phone

The phone and the laptop must be on the same Wi-Fi, and the backend must listen on
all interfaces, not just localhost:

```bash
cd backend
./.venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Then bake your LAN address into the build (`ipconfig` / `Get-NetIPAddress` to find it):

```bash
cd app
EXPO_PUBLIC_API_URL=http://<LAN-IP>:8000 npx expo start
```

If the APK cannot reach the server, open **Settings → सर्वर का पता** in the app and
type the address there — no rebuild needed. Without a reachable server the camera,
capture guidance, image checks and drafts all still work; only publishing, login and
payment need the network.

> On Windows, allow the port once:
> `New-NetFirewallRule -DisplayName "Kalakriti API" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow`

## Language and navigation

Every user-facing string lives in `app/src/i18n/catalog.ts` (330 keys, English and
Hindi authored). The other seven languages are generated from those two by
`backend/translate_ui.py` using Nemotron and committed as JSON, so the app renders
its own interface with no network.

The fallback chain is **chosen → English → the key**. Hindi is deliberately *not* the
fallback: if a Gujarati string is missing you see English, which is a visible gap
rather than a confusing one.

Language is chosen during onboarding, changeable at any time from the pill in the
top-right of every main screen, persisted on the device, and synced to the artisan's
profile once they sign in.

Primary navigation is a bottom tab bar — **Home · My Products · Orders · Profile** —
with the create flow, group orders and the auth/language sheets pushed over the top
of it. Nothing that navigates lives inside page content.

Regenerate translations after adding strings:

```bash
cd app && node scripts/dump-en.mjs        # catalogue -> locales/en.json + hi.json
cd ../backend && python translate_ui.py   # -> gu, bn, mr, ta, te, kn, or
```

## What works offline

The capture pipeline is deliberately local — see [docs/ON-DEVICE.md](docs/ON-DEVICE.md)
for the full evaluation of ML Kit, MediaPipe, VisionCamera, PaddleOCR and the rest,
and the reasoning behind what was chosen.

| On the phone, no network | Needs the network |
|---|---|
| In-app camera viewfinder | Login and OTP |
| Live framing, focus, lighting and glare checks | Publishing to marketplaces |
| Subject detection and framing guidance | Payment and order sync |
| Category-specific capture rules, multi-shot plan | Courier booking and tracking |
| Spoken guidance | AI copywriting and pricing |
| Draft saving | |

## Build an APK

### Route A — EAS cloud build (no local Android toolchain needed)

The most reliable path on a machine without a configured SDK, and the one to use in an
agentic IDE. Build runs on Expo's servers and returns a download link.

```bash
npm install -g eas-cli
eas login
eas build --platform android --profile preview   # emits .apk, not .aab
```

### Route B — local Gradle

Requires **JDK 17** and the Android SDK. The release variant is signed with the debug
keystore — correct for a demo, replace with a real upload key before any Play submission.

```powershell
winget install EclipseAdoptium.Temurin.17.JDK      # if JDK 17 is missing
$env:JAVA_HOME    = 'C:\Program Files\Eclipse Adoptium\jdk-17'
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"

cd app
npx expo prebuild --platform android --clean       # already run; regenerates android/
cd android
./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
# -> app/build/outputs/apk/release/app-release.apk   (~32 MB)
```

Build size matters here — the target user is on a low-end handset and metered data. Expo's
template ships release builds unminified and bundles all four ABIs into one universal APK,
which measured **100 MB**. Enabling `android.enableMinifyInReleaseBuilds` and
`android.enableShrinkResourcesInReleaseBuilds` in `android/gradle.properties` (already done)
and restricting to `arm64-v8a` — what any modern phone actually runs — brings it to **32 MB**.
Drop the `-PreactNativeArchitectures` flag if you need a universal APK for older 32-bit devices.

### Install it

```bash
adb devices
adb install -r app-release.apk
```

No phone attached? Create an emulator:

```bash
sdkmanager "system-images;android-34;google_apis;x86_64"
avdmanager create avd -n pixel34 -k "system-images;android-34;google_apis;x86_64"
emulator -avd pixel34
```

For judging, sideload onto a real mid-range Android phone. The entire UI argument of this
project is legibility and performance on low-end hardware, and an emulator hides exactly that.

## Scope honesty

The front end is real and complete. The backend services, trained models and marketplace
integrations described in the PDF are the design, not yet the build — the boundary is drawn
explicitly in `src/lib/ai.ts`, where each mock sits beside the remote call it stands in for.
