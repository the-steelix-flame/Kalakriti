# Kalakriti — setup guide

Getting the project running on a fresh laptop, start to finish. Budget ~20 minutes,
most of it downloads.

---

## 0. What you are installing

Three pieces, and it helps to know which is which:

| Piece | What it is | Port |
|---|---|---|
| **backend** | Python/FastAPI. OCR, vision, pricing, auth, orders, the public storefront | `8000` |
| **app** | Expo / React Native. Runs in a browser *and* as an Android APK from the same code | `8081` |
| **phone** *(optional)* | The APK, talking to your laptop over Wi-Fi | — |

You can do everything except real camera testing with just the first two.

---

## 1. Prerequisites

| Tool | Version | Check with |
|---|---|---|
| Node.js | 20 or 22 | `node -v` |
| Python | 3.11 or 3.12 | `python --version` |
| Git | any | `git --version` |
| JDK | 17 or 22 | `java -version` |
| Android SDK | platform 34+ | only needed to build the APK |

On Windows, use **Git Bash** or **PowerShell**. The commands below are written for
Git Bash; PowerShell equivalents are noted where they differ.

---

## 2. Clone

```bash
git clone https://github.com/the-steelix-flame/Kalakriti.git
cd Kalakriti
```

---

## 3. Backend

```bash
cd backend
python -m venv .venv

# Git Bash / macOS / Linux
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt   # mac/Linux
```

> The first install pulls `onnxruntime` and `opencv`, roughly 250 MB. It is slow once.

### Create `backend/.env`

Copy the example and fill in **one** required value:

```bash
cp .env.example .env
```

Then edit `.env`:

```bash
# ── REQUIRED ───────────────────────────────────────────────────────────────
# Free key from https://build.nvidia.com  (sign up, then "Get API Key")
NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ── REQUIRED for logging in during development ────────────────────────────
# No SMS provider? This shows the OTP on screen instead of texting it.
# The code is still real: random, hashed, expiring, rate-limited.
# NEVER enable this in production.
OTP_DEV_ECHO=true

# ── REQUIRED if you want your phone to open storefront links ──────────────
# Your laptop's LAN IP. Find it with `ipconfig` (Windows) or `ifconfig` (mac).
PUBLIC_BASE_URL=http://192.168.1.5:8000
```

**Everything else in `.env.example` is optional.** Each optional block enables one
more real integration (Razorpay, ONDC, Amazon, GeM, Shopify, Shiprocket, MSG91).
Leave them blank and the app still runs end to end — those channels simply report
`not_configured` and name exactly what they need, instead of pretending to work.

### Run it

```bash
# Listen on all interfaces so your phone can reach it, not just localhost
./.venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Check it: <http://localhost:8000/health> should return JSON with
`"llm": "nemotron-3-ultra"`.

> **Windows firewall**, once, so the phone can connect:
> ```powershell
> New-NetFirewallRule -DisplayName "Kalakriti API" -Direction Inbound `
>   -LocalPort 8000 -Protocol TCP -Action Allow
> ```

---

## 4. App (browser)

In a second terminal:

```bash
cd app
npm install
npx expo start --web
```

Open <http://localhost:8081>. That is the whole app — onboarding, camera (via your
webcam), listing creation, publishing.

For a phone-shaped view with a status bar showing whether the backend is up, open
<http://localhost:8000> instead: it frames the app at 390×844.

---

## 5. App (Android phone)

### Option A — Expo Go, fastest

```bash
cd app
npx expo start
```

Scan the QR with the **Expo Go** app. Live reload, no build.
*Limitation:* the in-app guided camera needs a real build; Expo Go will fall back.

### Option B — build the APK

```bash
cd app
npx expo prebuild --platform android --clean
cd android

# Windows (Git Bash)
JAVA_HOME="/c/Program Files/Java/jdk-22" \
ANDROID_HOME="$HOME/AppData/Local/Android/Sdk" \
EXPO_PUBLIC_API_URL="http://192.168.1.5:8000" \
  ./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
```

Output: `app/android/app/build/outputs/apk/release/app-release.apk` (~35 MB).

Install on a connected phone (USB debugging on):

```bash
adb install -r app/build/outputs/apk/release/app-release.apk
```

**Set `EXPO_PUBLIC_API_URL` to your own LAN IP** — it is baked in at build time.

There is no longer a "Server address" field in the app. It was developer configuration
sitting in a screen used by people who have never seen a URL, so it was removed. If
your IP changes, rebuild with the new value, or run a dev build (`npx expo start`),
which infers the LAN address from Metro automatically.

---

## 6. Verifying it works

1. Open the app → pick a language → **"Not now — start without an account"**
2. **Add a new product** → Gallery → choose any product photo
3. Wait 1–3 minutes. OCR, object detection and background generation run in sequence.
4. You should see extracted fields with confidence badges, an editable form, and a
   generated product image.
5. Press **Send to 1 place** → it will ask you to sign in → enter any 10-digit
   number → the OTP appears on screen (because `OTP_DEV_ECHO=true`)
6. Fill in name + address → it publishes to your own storefront and gives you a
   real, openable URL.

---

## 7. Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `MalformedURLException: no protocol` | No backend URL in the build | Set `EXPO_PUBLIC_API_URL` before `gradlew assembleRelease` |
| Onboarding repeats after closing the app | An old build. Fixed by real storage — see `docs/ARCHITECTURE.md` §1 | Reinstall the current APK |
| A marketplace figure shows `—` | That platform does not expose the metric | Tap it; the app states the reason. See `docs/ARCHITECTURE.md` §5 |
| `CLEARTEXT communication not permitted` | Android blocks plain HTTP in release builds | Already fixed by `app/plugins/withLocalNetworkAccess.js`. If it returns, re-run `npx expo prebuild` |
| Phone cannot reach the laptop | Different networks, or firewall | Same Wi-Fi; add the firewall rule above. Mobile data will not work |
| `"llm": "not configured"` | `NVIDIA_API_KEY` missing or `.env` not loaded | Check `backend/.env`; restart uvicorn (it reads `.env` at startup) |
| OTP never arrives | No SMS provider | Set `OTP_DEV_ECHO=true` for development |
| Analysis takes 2–5 minutes | Nemotron is a reasoning model | Expected. Pre-warm one product before a demo |
| `Port 8081 is being used` | An orphaned Metro | Kill the process on 8081, or `npx expo start --port 8082` |
| Gradle: `JAVA_HOME is invalid` | Stale path | Point it at a real JDK 17 or 22 |
| Metro serves a stale bundle | Cache | `npx expo start --clear` |

---

## 8. Adding or changing UI text

Never write a user-facing string inline. All 330 live in `app/src/i18n/catalog.ts`
(English and Hindi, authored). After editing:

```bash
cd app && node scripts/dump-en.mjs        # catalogue -> locales/en.json + hi.json
cd ../backend && python translate_ui.py   # fills only the gaps (fast)
python translate_ui.py --all gu           # or retranslate one language completely
python check_scripts.py                   # report script mixing; --fix repairs it
```

`translate_ui.py` is incremental: it translates keys that are missing, or whose value
is still identical to the English source (which is what a silently failed batch looks
like). `check_scripts.py` catches the model mixing scripts inside a word — `અकेલા` for
`અકેલા` — which is easy to miss because the rest of the string is right.

The generated JSON is committed so the app needs no network to render its own UI.
Missing strings fall back to **English, never Hindi**, so a gap is visible.

---

## 9. Project layout

```
backend/
  main.py            API: analyze, catalog, price, publish, orders, storefront
  vision.py          OCR (RapidOCR) + two-stage detection
  imaging.py         matting (rembg / U^2-Net)
  bg.py              background generation
  llm.py             Nemotron client with JSON repair
  auth.py  sms.py    phone + OTP, sessions
  seller.py          marketplace requirements and field mapping
  channels.py        storefront, ONDC, GeM, Amazon, Shopify adapters
  logistics.py       Shiprocket / Delhivery, tracking webhooks
  db.py              SQLAlchemy models
  analytics.py       view counting, product cards, home summary, insights
  pipeline.py        the image pipeline, shared by /v1/analyze and the job worker
  jobs.py            server-side processing, so a 2G upload survives
  media.py           object storage (S3-compatible) with a local-disk fallback
  translate_ui.py    regenerates the UI translations

app/
  App.tsx            shell: onboarding, bottom tabs, sheets
  src/i18n/          catalogue + generated locales
  src/nav/Shell.tsx  top bar, tab bar
  src/screens/       Home, Products, Orders, Profile, Create, GuidedCamera, Auth
  src/vision/        on-device capture analysis (no models, no network)
  src/lib/storage.ts AsyncStorage + Keystore
  src/lib/session.ts persisted session, hydrated before first paint
  src/lib/cache.ts   last known good copy of every list
  src/lib/sync.ts    offline write queue, field-level conflicts
  src/lib/store.tsx  the data layer every screen reads from
  src/lib/connection.ts   measures the link, to propose auto or manual
  src/ui/AiButton.tsx     per-field AI: suggest, never overwrite
  plugins/           Expo config plugin for LAN cleartext

docs/ON-DEVICE.md       what runs on the phone and why
docs/ARCHITECTURE.md    storage, offline sync, and which figures are real
docs/DEPLOY.md          Postgres, object storage, HTTPS - taking it off the laptop

Kalakriti_Strategy_and_Build.pdf   the full document, 24 pages
Kalakriti_Team_Modules.pdf         the five-module split, one owner each
```

---

## 10. Who needs what

| You are… | You need |
|---|---|
| Working on UI only | Node + `npm install` + a teammate's backend URL |
| Working on backend | Python + `NVIDIA_API_KEY` |
| Testing the camera | A real Android build; the webcam works for everything else |
| Demoing | Backend on `0.0.0.0`, phone on the same Wi-Fi, one product pre-warmed |
