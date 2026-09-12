# Running the whole flow on your own machine

`SETUP.md` is the reference for installing everything. This is the short version for
somebody who has just pulled and wants the **full demo flow** working today: create a
cluster, join it from a second account, list a product, sell it in our own shop, and
watch the order split across the cluster.

macOS and Windows both, side by side. Where a command differs, both are given.

---

## The shape of it, before any commands

Three pieces, and knowing which is which saves an hour of confusion:

| Piece | What it is | Where it runs |
|---|---|---|
| **backend** | Python/FastAPI. All the logic, the shop, the AI calls. | your laptop, port 8000 |
| **tunnel** | Gives the backend a public `https://` address | your laptop |
| **app** | The Android APK, or a browser | your phone, or your laptop |
| **database** | Supabase Postgres, already hosted | the internet, shared by all of us |

**The database is shared.** We all point at the same Supabase project, so the demo
accounts, clusters and listings already exist and you do not have to create them.
That also means a listing you delete is deleted for everybody, so be a little careful.

**The phone does not need to be plugged in.** It reaches the backend over the
internet. What it does need is for *your* backend and *your* tunnel to be running,
because the backend is on your laptop and not anywhere permanent yet.

---

## 1. Prerequisites

| Tool | Version | macOS | Windows |
|---|---|---|---|
| Node | 20 or 22 | `brew install node@22` | `winget install OpenJS.NodeJS.LTS` |
| Python | 3.11 or 3.12 | `brew install python@3.12` | `winget install Python.Python.3.12` |
| JDK | 17 or 22 | `brew install --cask temurin@17` | `winget install EclipseAdoptium.Temurin.17.JDK` |
| cloudflared | any | `brew install cloudflared` | `winget install Cloudflare.cloudflared` |
| Android SDK | platform 34+ | only if you build the APK yourself | same |

Check them:

```bash
node -v && python3 --version && java -version && cloudflared --version
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

## 3. Backend

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

## 4. Tunnel: give it a public address

The phone cannot reach `127.0.0.1` on your laptop, and Android refuses plain HTTP to
anything that is not a private address. So the backend needs a real `https://` host.

In a **second terminal**:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

It prints a line like:

```
https://something-random-four-words.trycloudflare.com
```

Copy that. It takes about 30 seconds before it actually answers, so if the first
request 502s, wait and try again.

**This hostname is different every time you start the tunnel.** That is the single
most annoying fact about this setup and the reason for step 6.

Put it in two places:

```bash
# backend/.env
PUBLIC_BASE_URL=https://your-tunnel-host.trycloudflare.com

# app/.env   (first entry in the comma-separated list)
EXPO_PUBLIC_API_URL=https://your-tunnel-host.trycloudflare.com
```

Then **restart the backend**, so storefront links and image URLs are built from the
new address.

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

The APK has the tunnel URL **compiled into it**, so you cannot use somebody else's
APK with your tunnel. You have to build your own, and rebuild whenever your tunnel
hostname changes.

```bash
cd app
npx expo prebuild --platform android      # once, after a fresh clone
cd android

# macOS
JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home \
ANDROID_HOME=$HOME/Library/Android/sdk \
./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a

# Windows (Git Bash)
JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-17" \
ANDROID_HOME="$LOCALAPPDATA/Android/Sdk" \
./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
```

Four minutes or so. The APK lands at:

```
app/android/app/build/outputs/apk/release/app-release.apk
```

Install it with the phone plugged in and USB debugging on:

```bash
adb install -r app/build/outputs/apk/release/app-release.apk
```

The cable is only for installing. Once installed, unplug it - the app talks to your
tunnel over the internet.

> **`arm64-v8a` matters.** Without that flag Gradle builds all four CPU
> architectures and the APK comes out at 113 MB instead of 43. Our user is on a
> cheap phone with metered data.

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
5. **Open the shop** at `<your-tunnel>/market` in any browser, on any device. The
   product is there.
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
| Login fails, or the app shows nothing | Your backend or tunnel is not running. Check both terminals. |
| Login fails and both *are* running | Your APK has an old tunnel hostname baked in. Rebuild. |
| `502` from the tunnel | It is still registering. Wait 30 seconds. |
| `getaddrinfo failed` at startup | DNS blip reaching Supabase. Start it again. |
| Backend will not start, `DATABASE_URL` error | `backend/.env` is missing or was not copied whole. |
| Camera or mic does nothing in the browser | Correct. Those are phone-only. |
| `CLEARTEXT ... not permitted` | You put a plain `http://` public URL in `app/.env`. Use the `https://` tunnel. |

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
