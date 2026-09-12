# Putting the backend on Render, so the app stops needing your laptop

Right now the backend runs on one laptop behind a tunnel whose hostname changes every
restart, and that hostname is compiled into the APK. So every restart costs a rebuild
and a reinstall, for each person on the team independently. This removes all of that.

After this, the address never changes and the app works with every laptop shut.

Everything in the repository is already prepared for it. What is left needs your
accounts, so it is written as steps for you rather than done for you.

---

## Why Render, and what the free tier costs us

We tried Hugging Face first. A free Space gets 16 GB of RAM, which is far more than
this needs, and that was the whole appeal. It did not work out, so this is the move to
Render. Render is a plainer host: it builds the `Dockerfile` in this repository and
runs it, and Docker on Render is not a paid feature.

The one thing you have to accept is memory. I measured it rather than estimating,
because the number decides the plan. Whole app, plus one real matte of a 3072x4080
phone photo:

| configuration | peak resident memory |
|---|---|
| `u2net` (the default matting model) | 729 MB |
| `u2netp` (the small one) | 544 MB |
| `u2netp`, capped at 1600px | 519 MB |
| `LOCAL_VISION=off` | **227 MB** |

Render free and Render Starter are both **512 MB**. So there is no configuration that
loads a vision model and fits. Capping the image resolution barely helped, about 10 to
25 MB, because the cost is the ONNX model weights and not the picture.

Hence `LOCAL_VISION=off`, which `render.yaml` already sets.

**What that gives up:** local background removal, and offline OCR. That is all.

**What still works, in full:** the listing copy, the price band, the HSN code, the
translations, and the vision model that actually reads the photograph. Every one of
those runs on NVIDIA's endpoint, so none of them is affected. A photograph is kept as
taken instead of cut out onto a white background, and the operations log on the
Provenance Passport says so rather than implying an edit that never happened.

If you want the cut-out back, it is `plan: standard` in `render.yaml`, which is 2 GB
at $25 a month, and both `LOCAL_VISION` lines changed to `on`. Nothing else changes.

### Should you create a new Hugging Face Space?

No. Nothing below touches Hugging Face. Leave the existing Space alone, or delete it,
which costs nothing either way. The `app.py`, root `requirements.txt` and
`packages.txt` files stay in the repository as a working fallback, and Render ignores
all three because it builds the `Dockerfile` instead.

---

## Step 1 - Product photos off the filesystem  [already done]

> **This is finished.** `MEDIA_S3_*` is configured against Supabase Storage, all
> listings with images point at the bucket, and a sample of six returned HTTP 200. The
> files still sitting in `backend/media/` are leftover originals; they are not
> referenced by anything and do not need uploading.
>
> Keep reading only if you are setting this up on a fresh Supabase project.

**Why it has to come first.** A Render container's disk is ephemeral and is replaced
on every deploy. Photographs on local disk vanish then, and every listing would show a
broken image, including ones already published with a live URL a buyer might open.

You already have somewhere to put them: your Supabase project includes S3-compatible
storage, so this needs no new account.

1. Supabase dashboard, **Storage**, **New bucket**. Name it `kalakriti-media` and mark
   it **Public**. Public matters: buyers and marketplaces fetch these images without
   credentials.
2. **Project Settings, Storage**, find the **S3 connection** panel. Note the endpoint,
   which looks like `https://<project-ref>.storage.supabase.co/storage/v1/s3`.
3. On the same screen, **new access key**. It gives you an access key id and a secret,
   shown once.
4. Put all six into `backend/.env`:

   ```
   MEDIA_S3_ENDPOINT=https://<project-ref>.storage.supabase.co/storage/v1/s3
   MEDIA_S3_BUCKET=kalakriti-media
   MEDIA_S3_KEY=<access key id>
   MEDIA_S3_SECRET=<secret>
   MEDIA_S3_REGION=ap-south-1
   MEDIA_PUBLIC_BASE=https://<project-ref>.storage.supabase.co/storage/v1/object/public/kalakriti-media
   ```

5. Move the photographs that already exist and rewrite the rows pointing at them:

   ```bash
   cd backend
   ./.venv/Scripts/python.exe migrate_media_to_s3.py --dry-run
   ./.venv/Scripts/python.exe migrate_media_to_s3.py
   ```

Setting `MEDIA_S3_*` only changes where the *next* photograph goes. The script is what
moves the ones already there.

---

## Step 2 - Check the database URL is the pooler, not the direct one

This one is worth thirty seconds, because getting it wrong fails as a timeout, and a
timeout tells you nothing about the cause.

Supabase offers two connection strings. The **direct** one is IPv6-only, and Render
does not route to it. The **session pooler** one is IPv4, on port 5432, with a
username shaped `postgres.<project-ref>`. That is the one that works.

Look at `DATABASE_URL` in `backend/.env` and confirm all three things:

- the username contains a dot and your project ref,
- the port is `5432`,
- the value ends with `?sslmode=require`.

The last is not cosmetic. Without it, `psycopg` negotiates TLS on a `prefer` basis,
which means it accepts an unencrypted connection if the server offers one, carrying
every artisan's phone number and every order in plain text.

---

## Step 3 - Have the three permanent keys ready

Three values must be identical on your laptop and on Render, and must not change
between deploys. They already exist in `backend/.env`. Reuse them. Do not generate new
ones, and do not let Render generate them for you.

| Key | What changing it breaks |
|---|---|
| `JWT_SECRET` | Every artisan is logged out. |
| `VIEW_SALT` | Storefront view rows stop being comparable across the change. |
| `PASSPORT_PRIVATE_KEY` | Passports signed before the deploy no longer verify against the issuer key served after it, which is precisely the claim a passport exists to make. |

If you are ever starting fresh, these are the two commands:

```bash
cd backend
./.venv/Scripts/python.exe -c "import secrets;print(secrets.token_urlsafe(64))"
./.venv/Scripts/python.exe -c "import base64;from cryptography.hazmat.primitives.asymmetric import ed25519;print(base64.b64encode(ed25519.Ed25519PrivateKey.generate().private_bytes_raw()).decode())"
```

The code has a fallback that writes a generated key to `jwt_secret.txt` if
`JWT_SECRET` is missing. On Render that fallback is worse than no key at all, because
the file lives on the container disk and is replaced on every deploy. So it looks like
it worked, and logs everybody out a day later.

---

## Step 4 - Create the service

1. Push this branch first. Render reads `render.yaml` from the repository, so the
   blueprint has to be on GitHub before Render can see it.

2. **render.com, New, Blueprint**, and pick the `Kalakriti` repository.

   Render reads `render.yaml`, shows you one web service named `kalakriti-api` on the
   **free** plan in the **Singapore** region, and then asks you for every value marked
   `sync: false`, which is all of them, because none of our secrets are in the
   repository.

   > If the blueprint refuses the free plan, create the service by hand instead:
   > **New, Web Service**, pick the repository, choose **Docker**, leave the Dockerfile
   > path as `./Dockerfile`, and set the environment variables in the dashboard. You
   > then also have to set `LOCAL_VISION=off` under **Settings, Docker Build
   > Arguments**, as well as `LOCAL_VISION=off` as an environment variable. The
   > blueprint does both for you, which is why it is worth trying first.

3. Paste in the values. Open `backend/.env` beside the form. Every name in it matches a
   name Render is asking for. The ones that matter most, in the order things break
   without them:

   | Variable | Consequence if missing |
   |---|---|
   | `DATABASE_URL` | No data at all. |
   | `NVIDIA_API_KEY` | No listings. The copy, price, HSN and translations are all this. |
   | `JWT_SECRET`, `VIEW_SALT`, `PASSPORT_PRIVATE_KEY` | See step 3. |
   | the six `MEDIA_*` values | Photographs land on a disk that is wiped on deploy. |
   | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | No checkout. |
   | `MSG91_AUTH_KEY`, `MSG91_TEMPLATE_ID` | No OTP. The two seeded password accounts still work. |

   Leave `PUBLIC_BASE_URL` blank for now. It is step 5, because it cannot be known
   until the service exists.

   Leave `OTP_DEV_ECHO` **empty and unset**. It returns the login code in the API
   response, so on a public host it is a login bypass for any phone number, for anybody
   who can call the endpoint.

4. Deploy, and watch the build log.

   The first build takes several minutes, most of it downloading `onnxruntime` and
   `opencv`. It does **not** download the 176 MB matting model, because
   `LOCAL_VISION=off` skips that step. You should see
   `LOCAL_VISION=off - skipping the matting model download` go past.

   A crash in the build log is almost always a missing variable, and it names it.

---

## Step 5 - Set PUBLIC_BASE_URL to the service's own address

This is the one value that cannot be filled in before the service exists, and the one
everything else is built from: storefront links, image URLs, ONDC callbacks, the QR
code on a passport.

The address is at the top of the service page and looks like:

```
https://kalakriti-api.onrender.com
```

Add it under **Environment**, named `PUBLIC_BASE_URL`, with **no trailing slash**. The
service redeploys itself.

Then check it, rather than assuming:

```bash
curl https://kalakriti-api.onrender.com/health
```

Read the `warning` field in the response, not just the status code. It is there to
catch a service that is up but wrong, such as SQLite instead of Postgres or images on
the container disk, which otherwise only shows up later, in the app, as lost data.

---

## Step 6 - Point the app at it, and rebuild once

In `app/.env`, replace the whole value. The private-IP fallbacks can go entirely now.
They existed only because the backend was on a laptop:

```
EXPO_PUBLIC_API_URL=https://kalakriti-api.onrender.com
```

With no plain-HTTP host left in the list, `plugins/withLocalNetworkAccess` can also
come out of `app/app.json`. The app then refuses cleartext to anything, anywhere, which
is the posture it should ship in.

Rebuild and install:

```bash
cd app/android
./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
adb install -r app/build/outputs/apk/release/app-release.apk
```

**This is the last time the URL forces a rebuild.** From here the address is permanent,
teammates can install this same APK, and nobody needs a tunnel again.

One thing to warn a demo audience about, or better, to avoid: if the service has spun
down, the first screen in the app waits 30 to 60 seconds. Step 7 is what prevents that,
but open the app once yourself a minute before a demo regardless.

---

## Step 7 - Turn on the keep-alive

A free Render service spins down after **15 minutes** with no traffic. This is the
biggest practical difference from Hugging Face, where the idle window was 48 hours.
Fifteen minutes is shorter than the gap between rehearsing a demo and giving it.

`.github/workflows/keepalive.yml` handles it and is already in the repository. It needs
one thing:

**GitHub, Settings, Secrets and variables, Actions, Variables, New repository
variable**, named `BACKEND_URL`, holding the service address with no trailing slash.

Then **Actions, Keep the backend awake, Run workflow**, to prove it works now rather
than waiting ten minutes.

It pings `/health` every 10 minutes, retries six times over about three minutes because
a spun-down service answers 502 and then takes most of a minute to come up, and **fails
the run** if the service genuinely does not answer. So a dead backend emails you
instead of being quietly pinged for a week.

Three facts about this arrangement are worth knowing before you rely on it:

- **Render's free tier is 750 instance-hours a month, across every free web service in
  the account.** Kept permanently awake, one service uses about 730, a calendar month,
  which fits with almost nothing spare. A second always-on free service would take the
  account over and Render suspends both. One is the budget.
- **GitHub cron is best-effort** and can drift ten or twenty minutes under load. A
  drift past the 15-minute window costs one cold start, not an outage.
- **GitHub disables scheduled workflows on a repository with no pushes for 60 days**,
  and emails the owner. If the pings ever stop, check that first.

---

## Step 8 - Razorpay webhook, now that there is a permanent URL

This was impossible before, because the tunnel hostname changed. Razorpay dashboard,
Settings, Webhooks, Add:

```
https://kalakriti-api.onrender.com/webhooks/razorpay
```

Secret: the `RAZORPAY_WEBHOOK_SECRET` from `.env`. Events: `payment.captured`,
`payment.failed`, `order.paid`, `refund.processed`.

Not strictly required. A buyer who stays on the page is confirmed from the signed
response their own checkout returns, which the server then verifies against Razorpay
directly. The webhook is the backstop for the buyer who pays and then closes the
browser.

---

## Checking it properly

```bash
cd backend
./.venv/Scripts/python.exe preflight.py --production
```

Run it with the Render service's own values in the environment. `--production` treats
hosted-only problems as failures rather than warnings: SQLite instead of Postgres,
images on a container disk, a private `PUBLIC_BASE_URL`, undeliverable OTP. It exits
non-zero if any of it is wrong, and names the variable.

Two failures are expected and neither blocks a demo:

- **OTP delivery.** MSG91 authenticates but its wallet is empty, and it answers a send
  from an empty wallet with `type: success` while delivering nothing. Use the two
  seeded password accounts until there is credit.
- **Demo accounts.** `preflight` fails while `9999900002` and `9999900001` still hold
  the passwords written in `seed_demo_users.py`. That is deliberate: those passwords
  are in a public repository. Before anything real, run
  `python seed_demo_users.py --remove`.

---

## When it breaks, in order of likelihood

| Symptom | Cause | Fix |
|---|---|---|
| Events tab says **Out of memory**, service restart-loops | a vision model is loading on a 512 MB box | `LOCAL_VISION=off` in Environment *and* in Docker Build Arguments |
| First request hangs 30 to 60 s, then works | the service had spun down | expected on free; step 7 reduces it |
| Everything returns 502 forever | build failed, or the month's 750 hours are spent | Logs tab, then the Billing page |
| `/health` says `engine: sqlite` | `DATABASE_URL` did not reach the process | check spelling in Environment. A typo is silent, because SQLite is the fallback |
| Database connection times out | the direct Supabase URL, which is IPv6-only | use the session pooler string, step 2 |
| Product images broken for buyers | `MEDIA_PUBLIC_BASE` missing, so URLs point at the signed S3 endpoint | set it, step 1 |
| Everyone logged out after a deploy | `JWT_SECRET` not set, so the disk fallback generated a new one | set it, step 3 |

---

## What changes for your teammates

Most of `RUNNING.md` stops applying, which is the point.

They no longer need `cloudflared`, no longer need to run the backend at all, and no
longer need to build their own APK. One APK works for everybody, because the address
inside it is permanent.

They need the backend running locally only to change backend code. For app work they
point `app/.env` at the Render URL and never think about it again.

One difference they should know about: on the deployed backend a product photograph is
not cut out onto a white background, because `LOCAL_VISION` is off there. Running the
backend locally, it is. So the same photograph produces a slightly different listing
image depending on which backend they are pointed at, and that is a setting rather than
a bug.
