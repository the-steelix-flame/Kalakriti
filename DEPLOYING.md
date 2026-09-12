# Putting the backend on Hugging Face, so the app stops needing your laptop

Right now the backend runs on one laptop behind a tunnel whose hostname changes every
restart, and that hostname is compiled into the APK. So every restart costs a rebuild
and a reinstall, for each person on the team independently. This removes all of that.

After this, the address never changes, and the app works with every laptop shut.

Everything in the repository is already prepared for it. What is left needs your
accounts, so it is written as steps for you rather than done for you.

---

## Why Hugging Face and not Render

Render's free tier gives 512 MB of RAM. This backend needs roughly 700 MB resident,
because `onnxruntime` plus the U²-Net matting weights plus the OCR models all sit in
memory. On Render free it is OOM-killed on the first photograph, and the symptom is a
restart loop rather than an error message.

A free Hugging Face Space gets **2 vCPU and 16 GB of RAM** on CPU basic. It is the
only genuinely free tier with the memory this needs.

This guide uses the **Gradio SDK rather than Docker**, so there is no Dockerfile in
the loop. The Dockerfile stays in the repository and still works; see the note at the
end of step 3 for switching back.

The trade: a free Space sleeps after 48 hours of no traffic, which
`.github/workflows/keepalive.yml` handles, and its filesystem is wiped on every
rebuild, which steps 1 and 2 handle.

---

## Step 1 — Product photos off the filesystem  ✅ already done

> **This is finished.** `MEDIA_S3_*` is configured against Supabase Storage, all
> fourteen listings with images point at the bucket, and a sample of six returned
> HTTP 200. The files still sitting in `backend/media/` are leftover originals; they
> are not referenced by anything and do not need uploading.
>
> Keep reading only if you are setting this up on a fresh Supabase project.

**Why it had to come first.** A Space's disk is ephemeral. Photographs on local disk
vanish at the next rebuild, and every listing would show a broken image - including
ones already published with a live URL a buyer might open.

You already have somewhere to put them: your Supabase project includes S3-compatible
storage, so this needs no new account.

1. Supabase dashboard → **Storage** → **New bucket**. Name it `kalakriti-media` and
   mark it **Public**. Public matters: buyers and marketplaces fetch these images
   without credentials.
2. **Project Settings → Storage**, find the **S3 connection** panel. Note the
   endpoint, which looks like
   `https://<project-ref>.storage.supabase.co/storage/v1/s3`.
3. On the same screen, **new access key**. It gives you an access key id and a
   secret, shown once.
4. Put all five into `backend/.env`:

   ```
   MEDIA_S3_ENDPOINT=https://<project-ref>.storage.supabase.co/storage/v1/s3
   MEDIA_S3_BUCKET=kalakriti-media
   MEDIA_S3_KEY=<access key id>
   MEDIA_S3_SECRET=<secret>
   MEDIA_S3_REGION=ap-south-1
   MEDIA_PUBLIC_BASE=https://<project-ref>.storage.supabase.co/storage/v1/object/public/kalakriti-media
   ```

5. Move the existing photographs and rewrite the listing URLs that point at them:

   ```bash
   cd backend
   ./.venv/Scripts/python.exe migrate_media_to_s3.py --dry-run   # look first
   ./.venv/Scripts/python.exe migrate_media_to_s3.py
   ```

Setting `MEDIA_S3_*` only changes where the *next* photograph goes. The script is
what moves the 85 already there and repoints the rows at the bucket.

---

## Step 2 — Generate the two keys that must not change

Both of these are currently written to disk on first use, which on a Space means a
new one after every rebuild.

```bash
cd backend

# Signs session tokens. Changing it logs everybody out.
./.venv/Scripts/python.exe -c "import secrets;print(secrets.token_urlsafe(64))"

# Signs Provenance Passports. Changing it means no stable issuer identity.
./.venv/Scripts/python.exe -c "import base64;from cryptography.hazmat.primitives.asymmetric import ed25519;print(base64.b64encode(ed25519.Ed25519PrivateKey.generate().private_bytes_raw()).decode())"
```

`backend/.env` already has values for `JWT_SECRET`, `VIEW_SALT` and
`PASSPORT_PRIVATE_KEY`. Reuse those rather than generating new ones, so the Space and
your laptop share one identity and nobody is logged out when you switch.

---

## Step 3 — Create the Space (no Docker)

1. **huggingface.co → New Space.** SDK **Gradio**, hardware **CPU basic (free)**,
   visibility **Public**.

   Public is not optional on the free tier: a private Space needs a token on every
   request, and a buyer opening a storefront link has no token.

2. The repository already carries everything a non-Docker Space reads. Do not delete
   any of it:

   | File | What the Space does with it |
   |---|---|
   | `app.py` | The one file it runs. Puts `backend/` on the path and starts uvicorn on port 7860. |
   | `requirements.txt` | Installed with pip. It just includes `backend/requirements.txt`, so versions are pinned in one place. |
   | `packages.txt` | Installed with apt. Two libraries opencv needs - the non-Docker equivalent of the Dockerfile's apt line. |
   | README front matter | `sdk: gradio`, `app_file: app.py`. This is what selects the SDK. |

   **Nothing imports gradio and no Gradio interface is served.** The Gradio SDK is
   simply how a Space runs a plain Python process: install the requirements, install
   the apt packages, run one file. `app.py` starts the real FastAPI app.

3. Push this repository to the Space:

   ```bash
   git remote add space https://huggingface.co/spaces/<your-user>/<space-name>
   git push space main
   ```

   It asks for your username and an **access token** as the password. Create one at
   huggingface.co → Settings → Access Tokens, with **write** permission.

> **If you would rather use Docker after all**, the `Dockerfile` is still in the
> repository and still correct. Change the front matter back to `sdk: docker` and
> `app_port: 7860`, and delete nothing. The only real difference is that Docker
> pre-fetches the 176 MB matting weights during the build, while this route fetches
> them on a background thread at startup - so the first photograph after a cold start
> is a little slower on a Gradio Space and identical afterwards.

---

## Step 4 — Give the Space its secrets

**Space → Settings → Variables and secrets.** Add every non-empty value from
`backend/.env` as a **Secret**, not a Variable. Secrets are hidden from the build log
and from the public repository page, which matters because the Space is public.

The ones that actually matter:

| Secret | Why |
|---|---|
| `DATABASE_URL` | Supabase. Without it there is no data. |
| `NVIDIA_API_KEY` | The language and vision models. |
| `JWT_SECRET` | Sessions. Copy the one from `.env`. |
| `VIEW_SALT` | Keeps view rows non-identifying. |
| `PASSPORT_PRIVATE_KEY` | A stable passport issuer. |
| `MEDIA_S3_*` and `MEDIA_PUBLIC_BASE` | From step 1. Six values. |
| `RAZORPAY_KEY_ID` / `_SECRET` / `_WEBHOOK_SECRET` | Checkout. |
| `MSG91_AUTH_KEY` / `_TEMPLATE_ID` | OTP, once the wallet has credit. |

Leave `OTP_DEV_ECHO` **empty**. Set, it returns the login code in the API response,
which on a public host is a login bypass for anybody who can call the endpoint.

---

## Step 5 — Set `PUBLIC_BASE_URL` to the Space's own address

This is the one value that cannot be filled in before the Space exists, and the one
everything else is built from - storefront links, image URLs, ONDC callbacks, the
QR code on a passport.

The address is on the Space page and looks like:

```
https://<your-user>-<space-name>.hf.space
```

Add it as a secret named `PUBLIC_BASE_URL`, with **no trailing slash**. The Space
restarts itself.

Then check it, rather than assuming:

```bash
curl https://<your-user>-<space-name>.hf.space/health
```

The first build takes several minutes, most of it installing `onnxruntime`,
`opencv` and `gradio`. Watch the build log on the Space page; a crash there is almost
always a missing secret, and it names it.

The matting weights are fetched after the build, on a background thread as the app
starts, so the Space answers `/health` before that finishes. The log line to look for
is `u2net ready`.

---

## Step 6 — Point the app at it, and rebuild once

In `app/.env`, replace the whole value with the Space address. The private-IP
fallbacks can go entirely now - they existed only because the backend was on a
laptop:

```
EXPO_PUBLIC_API_URL=https://<your-user>-<space-name>.hf.space
```

With no plain-HTTP host left in the list, `plugins/withLocalNetworkAccess` can also
come out of `app/app.json`. The app then refuses cleartext to anything, anywhere,
which is the posture it should ship in.

Rebuild and install:

```bash
cd app/android
./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
adb install -r app/build/outputs/apk/release/app-release.apk
```

**This is the last time the URL forces a rebuild.** From here the address is
permanent, teammates can install the same APK, and nobody needs a tunnel.

---

## Step 7 — Turn on the keep-alive

`.github/workflows/keepalive.yml` is already in the repository. It needs one thing:

**GitHub → Settings → Secrets and variables → Actions → Variables → New repository
variable**, named `BACKEND_URL`, holding the Space address with no trailing slash.

Then **Actions → Keep the backend awake → Run workflow** to check it now rather than
waiting half an hour.

It pings `/health` every 30 minutes, retries six times over about three minutes
because a sleeping Space takes most of a minute to wake, and **fails the run** if the
Space genuinely does not answer - so a dead backend emails you instead of being
quietly pinged for a week.

Two things about GitHub cron worth knowing before you trust it: it is best-effort and
can drift by ten or twenty minutes under load, and GitHub disables scheduled
workflows on a repository with no pushes for 60 days.

---

## Step 8 — Razorpay webhook, now that there is a permanent URL

This was impossible before, because the tunnel hostname changed. Razorpay dashboard →
Settings → Webhooks → Add:

```
https://<your-user>-<space-name>.hf.space/webhooks/razorpay
```

Secret: the `RAZORPAY_WEBHOOK_SECRET` from `.env`. Events: `payment.captured`,
`payment.failed`, `order.paid`, `refund.processed`.

Not strictly required - a buyer who stays on the page is confirmed from the signed
response their own checkout returns - but it is the backstop for the buyer who pays
and then closes the browser.

---

## Checking it properly

```bash
cd backend
./.venv/Scripts/python.exe preflight.py --production
```

Run it with the Space's own values in the environment. `--production` treats
hosted-only problems as failures rather than warnings: SQLite instead of Postgres,
images on a container disk, a private `PUBLIC_BASE_URL`, undeliverable OTP. It exits
non-zero if any of it is wrong and names the variable.

Two failures are expected and neither blocks a demo:

- **OTP delivery.** MSG91 authenticates but its wallet is empty, and it answers a
  send from an empty wallet with `type: success` while delivering nothing. Use the
  two seeded password accounts until there is credit.
- **Demo accounts.** `preflight` fails while `9999900002` and `9999900001` still hold
  the passwords written in `seed_demo_users.py`. That is deliberate: those passwords
  are in a public repository. Before anything real, `python seed_demo_users.py
  --remove`.

---

## What changes for your teammates

Most of `RUNNING.md` stops applying, which is the point.

They no longer need cloudflared, no longer need to run the backend at all, and no
longer need to build their own APK - one APK now works for everybody, because the
address inside it is permanent.

They need the backend running locally only to change backend code. For app work they
point `app/.env` at the Space and never think about it again.
