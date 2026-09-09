# Putting Kalakriti online

Right now the app talks to a laptop over Wi-Fi. That is fine for building it and
useless for anything else: the phone must be on the same network, the backend dies
when the laptop sleeps, and no buyer outside the room can open a listing URL.

This is what actually has to change, and why.

---

## What is already real, and what is not

It is worth being precise, because "we have no database" is not true.

| | State |
|---|---|
| Database | **Real.** SQLite through SQLAlchemy — accounts, listings, publications, orders, shipments, enquiries, an append-only event log. It is a real relational database with real constraints. It is just a **file on your laptop**. |
| Images | **Real files**, written to `backend/media/`. Also on your laptop. |
| Auth | **Real.** Random OTP, salted-SHA-256 hashed, expiring, rate-limited, server-side sessions that logout genuinely revokes. |
| Storefront | **Real.** A public product page with a working Buy button that creates a real order row. It is only reachable on your LAN. |

So nothing here is fake. It is **single-machine**. Three things move it to a real
deployment, in this order of importance.

---

## 1. Postgres instead of a SQLite file

SQLite on a hosted container is the failure that hurts most, because it is silent.
The container filesystem is wiped on every deploy, so every artisan account, listing
and order disappears — and nobody notices until somebody tries to log in.

Nothing in the code needs changing: `DATABASE_URL` is already read, and the driver is
in `requirements.txt`.

```bash
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/kalakriti
```

Free options that are genuinely free, not trials:

| | Free tier | Note |
|---|---|---|
| **Neon** | 0.5 GB, scale-to-zero | Best fit. Serverless, so an idle demo costs nothing. First query after idle takes ~1s. |
| **Supabase** | 500 MB | Also gives you object storage in the same project, which you need anyway. |
| **Render Postgres** | 1 GB, **expires after 30 days** | Fine for a hackathon, not past it. |

`/health` reports `"database": {"engine": ...}` and warns loudly if it finds SQLite on
a hosted container. Check it after the first deploy.

> **Schema:** tables are created with `Base.metadata.create_all` at startup. That is
> correct for a project with no production data yet, and it does **not** perform
> migrations — it creates what is missing and leaves existing tables alone. The first
> time a column changes on a live database you will need Alembic. Adding it before
> there is anything to migrate would be ceremony.

## 2. Object storage instead of the container disk

Same failure, different data. `backend/media.py` uses S3-compatible storage when it is
configured and falls back to disk when it is not.

```bash
MEDIA_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
MEDIA_S3_BUCKET=kalakriti-media
MEDIA_S3_KEY=...
MEDIA_S3_SECRET=...
MEDIA_PUBLIC_BASE=https://media.yourdomain.com
```

| | Free tier | Note |
|---|---|---|
| **Cloudflare R2** | 10 GB, **no egress charge** | The right answer. Egress is what makes image hosting expensive elsewhere. |
| **Supabase Storage** | 1 GB | Same API, and you may already be there for Postgres. |
| **Backblaze B2** | 10 GB | Fine; egress is free up to 3× stored. |

`/health` reports which backend is live. If S3 is configured but a put fails, the image
is written to disk instead and the error is logged — the artisan keeps her photograph
rather than losing it to a bucket policy.

## 3. A public HTTPS host

```bash
# Render, from the blueprint in the repo
render blueprint launch          # reads render.yaml

# or anything that runs a Dockerfile
docker build -t kalakriti .
docker run -p 8000:8000 --env-file backend/.env kalakriti
```

Then rebuild the APK against the public URL:

```bash
EXPO_PUBLIC_API_URL="https://kalakriti-api.onrender.com" ./gradlew assembleRelease
```

Two things get better the moment the backend is on HTTPS:

- **The same-Wi-Fi requirement disappears.** The phone can be anywhere, on mobile data.
- **The cleartext exemption stops mattering.** `withLocalNetworkAccess.js` only ever
  permitted plain HTTP for private LAN ranges; a public HTTPS host does not use it.
  Leave the plugin in — it costs nothing and keeps local development working.

### Memory: the one thing that will bite you

`onnxruntime` + the U²-Net weights + RapidOCR need roughly **700 MB resident**. A
512 MB free instance is OOM-killed on the first photograph, and the symptom is a
restart loop rather than an error message.

Two honest options:

1. **Pay ~$7/month** for a 2 GB instance (`plan: starter` in `render.yaml`). This is
   the only way to keep local OCR and local background removal.
2. **Turn the heavy local models off** and rely on the hosted vision model, which
   already does the detection. You lose RapidOCR and rembg; you keep everything else.
   The trade is real and should be a deliberate choice, not a surprise.

## 4. Real SMS, so login works for somebody who is not you

`OTP_DEV_ECHO=true` prints the code on screen. It must never be set in production —
it is not a security shortcut, it is a *development* shortcut, and the OTP itself is
real either way.

```bash
MSG91_AUTH_KEY=...
MSG91_TEMPLATE_ID=...
```

MSG91 is the usual choice for Indian OTP: DLT-registered templates, which are legally
required for transactional SMS in India, and most government-facing projects already
have an account. Twilio also works (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_FROM_NUMBER`) but needs a DLT-registered Indian sender for reliable delivery.

Without either, `sms.py` returns `not_configured` and **does not deliver the code**.
Verification does not weaken — the code still has to match. It simply cannot be
received, which is the truth.

---

## The whole checklist

```
[ ] DATABASE_URL          Postgres, not SQLite
[ ] MEDIA_S3_*            object storage, not the container disk
[ ] PUBLIC_BASE_URL       your https:// host — this is what listing URLs point at
[ ] NVIDIA_API_KEY        as before
[ ] JWT_SECRET            a long random value; sessions are signed with it
[ ] VIEW_SALT             a long random value; keeps view rows non-identifying
[ ] MSG91_*               real OTP delivery
[ ] OTP_DEV_ECHO          NOT SET
[ ] instance memory       2 GB, or HEAVY_VISION off
[ ] EXPO_PUBLIC_API_URL   the https:// host, baked into the APK
```

Then: `GET /health` and read `database.engine`, `media.backend`, and any `warning`.
It is designed to tell you what is still wrong.

---

## What still needs an account somebody has to open

These are not code problems. They are paperwork, and the integration is already
written and waiting for the credentials.

| | What it needs | Realistic time |
|---|---|---|
| **ONDC** | Registration as a network participant, an Ed25519 keypair published in the registry, and a subscriber ID | Weeks. Start early. |
| **GeM** | A registered seller account with GSTIN and PAN | Days to weeks |
| **Amazon** | Professional selling account, LWA app, refresh token; per-listing traffic also needs Brand Registry | Days |
| **Razorpay** | KYC on a business entity | Days |
| **Shiprocket** | A seller account and a pickup address | Hours |

Until then each reports `not_configured` and names the exact variables it wants. That
is the intended behaviour and the demo works without any of them: the storefront
channel is fully real, publishes immediately, and takes real orders.

---

## Push notifications, honestly

When the server finishes a listing in auto mode, the app shows a card the next time it
is opened, and the count sits on the Home tab. That is real and it works.

Waking a **closed** app requires Firebase Cloud Messaging: a Google project, a
`google-services.json`, and `expo-notifications` configured with an FCM server key.
None of that is in this repository, so the app does not claim to do it. If you want
it, that is the exact configuration required — and the job status endpoint it would
be driven from is already there.
