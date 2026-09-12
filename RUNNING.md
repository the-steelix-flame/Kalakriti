# Running the whole flow on your own machine

`SETUP.md` is the reference for installing everything. This is the short version for
somebody who has just pulled and wants the **full demo flow** working today: create a
cluster, join it from a second account, list a product, sell it in our own shop, and
watch the order split across the cluster.

macOS and Windows both, side by side. Where a command differs, both are given.

---

## The shape of it, before any commands

**Read this first: the backend is deployed now.** It lives at
`https://kalakriti-api-2rho.onrender.com` and it is permanent. Nobody needs to run the
backend, and nobody needs a tunnel, to use the app or to work on the app. That is a
change from how this document read before, and it removes most of it.

| Piece | What it is | Where it runs |
|---|---|---|
| **backend** | Python/FastAPI. All the logic, the shop, the AI calls. | Render, permanently. On your laptop only if you are changing backend code. |
| **database** | Supabase Postgres | the internet, shared by all of us |
| **storage** | Supabase Storage, the product photographs | the internet, shared by all of us |
| **app** | the Android APK, or a browser | your phone, or your laptop |

There is no **tunnel** row any more. `cloudflared` gave the laptop backend a temporary
public address whose hostname changed on every restart, and since that hostname is
compiled into the APK, every restart cost everyone a rebuild. The Render address never
changes, so one APK works for the whole team.

**The database is shared.** We all point at the same Supabase project, so the demo
accounts, clusters and listings already exist and you do not have to create them. That
also means a listing you delete is deleted for everybody, so be a little careful.

**The phone does not need to be plugged in**, and now it does not need your laptop
switched on either. The cable is only for installing an APK.

### Two things about the deployed backend that are settings, not bugs

**A product photograph is not cut out onto a white background.** The Render free plan
has 512 MB of memory and the background-removal model needs 729 MB, so `LOCAL_VISION`
is off there. Everything else about a listing is unaffected, because the copy, the
price band, the HSN code, the translations and the vision model that reads the photo all
run on NVIDIA's endpoint. Run the backend locally and you get the cut-out; point at
Render and you do not. `/health` says which.

**The first request after a quiet spell takes 30 to 60 seconds.** A free Render service
spins down after 15 minutes of no traffic. A GitHub Action pings it every 10 minutes to
prevent that, but if you open the app and the first screen hangs, that is what is
happening. It answers normally after that. Open the app once a minute before you demo
anything.

---

## 1. Prerequisites

| Tool | Version | macOS | Windows |
|---|---|---|---|
| Node | 20 or 22 | `brew install node@22` | `winget install OpenJS.NodeJS.LTS` |
| Python | 3.11 or 3.12 | `brew install python@3.12` | `winget install Python.Python.3.12` |
| JDK | 17 or 22 | `brew install --cask temurin@17` | `winget install EclipseAdoptium.Temurin.17.JDK` |
| Android SDK | platform 34+ | only if you build the APK yourself | same |

`cloudflared` used to be on this list and is not needed by anyone any more. Python and
the JDK are only needed if you are changing backend code or building an APK
respectively; for app work against the deployed backend, Node alone is enough.

```bash
node -v && python3 --version && java -version
```

On Windows use **Git Bash** for these commands, not cmd.exe. Where `python3` is
written, Windows usually wants `python`.

---

## 2. Get the secrets

`backend/.env` is **not in the repo** and never will be - it holds the database
password, the NVIDIA key and the Razorpay keys. Ask in the group and copy the file
into `backend/.env`. Nothing works without it.

There is a documented template at `backend/.env.example` if you want to see what each
value is for; it contains only placeholders.

---

## 3. Backend (only if you are changing backend code)

**Skip this whole section for app work.** The backend is deployed and the app points at
it. You need a local one only to change Python code, or to run the test suites.

```bash
cd backend

# macOS / Linux
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# Windows (Git Bash)
python -m venv .venv
./.venv/Scripts/pip.exe install -r requirements.txt
```

The first install pulls `onnxruntime` and `opencv`, roughly 250 MB. It is slow once.

Then check it before trying to run anything:

```bash
# macOS / Linux
./.venv/bin/python preflight.py

# Windows
./.venv/Scripts/python.exe preflight.py
```

`preflight.py` does not trust the config, it tests it: connects to Supabase, calls
the language model, checks every credential and names the variable behind anything
that is wrong. Read its output rather than guessing. One `FAIL` on OTP delivery is
expected and does not block anything - see step 7.

Start it:

```bash
# macOS / Linux
./.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000

# Windows
./.venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Leave that terminal open. Confirm in another one:

```bash
curl http://127.0.0.1:8000/health
```

> The first request can take a second or two. Supabase scales to zero when idle, so
> the first query after a quiet period is a cold start, not a hang.

---

## 4. Point the app at a backend

This section used to be about running `cloudflared` to give your laptop backend a
temporary public address. That is gone. The backend has a permanent one.

For app work, which is almost all of the time, `app/.env` already says the right thing
and you do not need to touch it:

```
EXPO_PUBLIC_API_URL=https://kalakriti-api-2rho.onrender.com
```

`PUBLIC_BASE_URL` in `backend/.env` also no longer needs editing. It matters only for a
backend you are running yourself, and only for the links that backend writes into
storefront pages and passport QR codes.

### If you are changing backend code

Run the backend as in section 3, then put your laptop's LAN address in front of the
Render one so the app prefers yours and still has a fallback:

```
EXPO_PUBLIC_API_URL=http://192.168.1.5:8000,https://kalakriti-api-2rho.onrender.com
```

Use your own address, which `ipconfig` on Windows or `ipconfig getifaddr en0` on macOS
will tell you. The phone and the laptop must be on the same network for this.

Plain HTTP works here only because `plugins/withLocalNetworkAccess.js` permits cleartext
to loopback and the three private IPv4 ranges. A public host still requires HTTPS, so a
mistyped production URL fails loudly instead of sending an artisan's details in the
clear. **Leave that plugin in `app.json`**, even though the deployed backend is HTTPS.
Removing it breaks exactly this workflow.

Changing `app/.env` means rebuilding the APK. See section 6, and read the warning about
the cached bundle, because that one silently ships the old address.

---

## 5. The app in a browser (fastest way to see the flow)

```bash
cd app
npm install
npx expo start --web
```

Good for the cluster screens, the listing form and the shop. **Not** good for the
camera, the microphone, offline behaviour or anything about install size - those only
exist on a real phone. A screen that works here is not finished; see the rule at the
bottom of `phases.md`.

---

## 6. The app on your Android phone

**Most of the time you do not need to build anything.** The backend address compiled
into the APK is permanent now, so one APK works for everybody. Ask for the current
`app-release.apk`, put it on your phone, and install it. That is the whole step.

You need to build your own only in one of these cases:

- you changed anything under `app/`,
- you changed `app/.env` to point at your own backend,
- you are the one producing the APK the rest of the team installs.

### Building it

```bash
cd app
npx expo prebuild --platform android      # once, after a fresh clone
cd android
```

Then, and this is the step that is easy to skip and expensive to skip:

```bash
# Delete the cached JavaScript bundle. Gradle does NOT notice that app/.env changed,
# so without this it reuses a bundle with the old backend address compiled in, and
# produces an APK that installs, runs, and cannot reach anything. Nothing warns you.
rm -f app/build/generated/assets/react/release/index.android.bundle \
      app/build/intermediates/assets/release/mergeReleaseAssets/index.android.bundle
```

Now build. Both variables have to be set on the command line, or Gradle stops with
"Please set the JAVA_HOME variable in your environment". That is the most common way
this step fails, and it fails before compiling anything, so it is quick to spot.

```bash
# macOS
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
ANDROID_HOME=$HOME/Library/Android/sdk \
./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a

# Windows (Git Bash)
JAVA_HOME="/c/Program Files/Java/jdk-22" \
ANDROID_HOME="$LOCALAPPDATA/Android/Sdk" \
./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
```

The JDK path differs between machines, so check yours rather than pasting blindly:

```bash
ls -d "/c/Program Files/Java"/* "/c/Program Files/Eclipse Adoptium"/* 2>/dev/null   # Windows
/usr/libexec/java_home -V                                                          # macOS
```

Any JDK from 17 to 22 works.

Four minutes or so. The APK lands at:

```
app/android/app/build/outputs/apk/release/app-release.apk
```

### Check the address actually made it in

Thirty seconds, and it catches the cached-bundle mistake before you spend ten minutes
wondering why the app is offline:

```bash
grep -c onrender.com app/build/generated/assets/react/release/index.android.bundle
```

Anything above zero is right. Zero means the bundle is stale: delete both files above
and build again.

### Installing it

Phone plugged in, USB debugging on:

```bash
adb devices     # confirm it says "device" and not "unauthorized"
adb install -r app/build/outputs/apk/release/app-release.apk
```

`-r` replaces the existing install and keeps its data, so drafts and the signed-in
account survive. If it refuses with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, the previous
APK was signed with a different key: `adb uninstall in.kalakriti.app` first, and
accept that local drafts on that phone are lost.

The cable is only for installing. Unplug it afterwards. The app reaches Render over
mobile data or any Wi-Fi.

> **`arm64-v8a` matters.** Without that flag Gradle builds all four CPU architectures
> and the APK comes out at 113 MB instead of 43. Our user is on a cheap phone with
> metered data.

### When a teammate pulls your changes

```bash
git pull
cd app && npm install        # only if package.json changed
```

Then rebuild only if the pull touched `app/`. A pull that only changed `backend/` needs
no rebuild at all, because the deployed backend is what the APK talks to.

Render redeploys on a push to `main` if auto-deploy is on, which is the default for a
blueprint service. If it is off, or you want it now, use **Manual Deploy** on the
service page. Either way, give it two or three minutes and check what is actually
running:

```bash
curl https://kalakriti-api-2rho.onrender.com/health
```

---

## 7. Signing in

OTP over SMS **does not work yet**. MSG91 is configured and authenticates, but the
account has no credits, and it answers a send from an empty wallet with
`type: success` while delivering nothing. `preflight.py` reports this as a failure on
purpose.

So use the two seeded password accounts. On the sign-in sheet tap **"Sign in with a
password instead"**:

| Who | Phone | Password | Role |
|---|---|---|---|
| Ramesh Prasad | `9999900002` | `kalakriti-cluster` | Cluster Creator, holds the GSTIN |
| Sunita Devi | `9999900001` | `kalakriti-weaver` | Artisan, no GST |

Both are real accounts with scrypt-hashed passwords; nothing about them is a bypass.
They exist in the shared Supabase database, so they work from any machine.

If you need them again: `python seed_demo_users.py --show`

---

## 8. Walking the whole flow

Two accounts are needed, so either use two phones, or one phone and the browser.

1. **Sign in as Ramesh** (`9999900002`). Home shows **My cluster** rather than
   Clusters, because his role is Cluster Creator. Open it. A cluster exists already
   with an invite code and a QR.
2. **Sign in as Sunita** on the other device (`9999900001`). Home shows **Clusters**.
   Find the cluster, read the commission and the connected platforms *before*
   joining, then join by code or QR.
3. **Set Sunita's capacity** in her profile - how many pieces she can make. Try
   committing more than that to two clusters; the second is refused, which is the
   point.
4. **List a product as Sunita.** Tap the big photo box, photograph something, let the
   AI write the copy or write it yourself, set a price, and publish to **My
   Storefront**. The other four channels report `not_configured` and name the
   credentials they want, which is correct and not a bug.
5. **Open the shop** at `https://kalakriti-api-2rho.onrender.com/market` in any
   browser, on any device. The product is there. That link is permanent, so it can go
   in a slide.
6. **Buy it.** Name, phone, address, Place order. Razorpay Checkout opens - use card
   `4111 1111 1111 1111`, any future expiry, any CVV. It is genuine Razorpay in test
   mode, so the API calls are real and the money is not.
7. **The order appears in the seller's Orders tab** within a refresh.
8. **Record what was delivered.** As Ramesh, My cluster → *What came in*. Enter how
   many arrived and how many were not good enough. The accepted count updates as you
   type.
9. **Settle it.** The split comes off the *net* amount - marketplace fee, GST by HSN,
   shipping - and divides by units **actually accepted**, not units promised. Any
   figure the system cannot establish stops the settlement and names it rather than
   assuming zero.

---

## 9. When something does not work

| Symptom | Cause, almost always |
|---|---|
| First screen hangs 30 to 60 seconds, then works | Render had spun the service down. Normal on the free plan. |
| App says it is offline, `/health` in a browser is fine | Your APK has an old address baked in. Rebuild, and delete the two cached bundles first. |
| You rebuilt and it *still* has the old address | You did not delete the cached bundles. `grep -c onrender.com` the bundle to confirm. |
| Everything 502s and never recovers | Check the Render dashboard: Logs, then Events. Out of memory and a spent monthly hour budget both look like this. |
| `getaddrinfo failed` at startup | DNS blip reaching Supabase. Start it again. |
| Backend will not start, `DATABASE_URL` error | `backend/.env` is missing or was not copied whole. |
| Camera or mic does nothing in the browser | Correct. Those are phone-only. |
| `CLEARTEXT ... not permitted` | You put a plain `http://` *public* URL in `app/.env`. Only private addresses may be HTTP. |
| Photos are not cut out onto white | Expected against Render. `LOCAL_VISION` is off there, see the top of this file. |

Run `python preflight.py` first whenever anything is confusing. It is faster than
reading code and it names the variable.

---

## 10. Before you commit

```bash
cd app     && ./node_modules/.bin/tsc --noEmit        # must be silent
cd backend && python -c "import main"                 # must import cleanly
cd backend && python test_clusters.py                 # and the other test_*.py
```

Never commit `backend/.env` or `app/.env`. They are gitignored; keep it that way.
