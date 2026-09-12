# Putting Kalakriti online

Right now the app talks to a laptop over Wi-Fi. That is fine for building it and
useless for anything else: the phone must be on the same network, the backend dies
when the laptop sleeps, and no buyer outside the room can open a listing URL.

This is what actually has to change, and why.

---

## Ten minutes to online

Three free accounts and one paid instance. Do them in this order; each step produces
values the next one needs. Times are how long the clicking takes, not how long the
build takes.

Everything is set in the Render dashboard at the end, so keep a scratch file open and
paste values into it as you go.

### 1. Database - Neon (about 3 minutes)

Neon's free tier is 0.5 GB, scales to zero when idle, and does not expire. That last
part is why it is here rather than Render's own free Postgres, which is deleted after
30 days and takes the data with it.

1. Sign up at **neon.tech**. Signing in with GitHub skips the email round trip.
2. Create a project. Choose the region closest to where the backend will run - a
   database on another continent adds a round trip to every single query, and it is
   not something you can fix later without moving the data.
3. The project dashboard has a **connection details** panel showing a connection
   string. Copy it. Neon offers a pooled and a direct connection string; either works
   here, because `db.py` runs a small pool of its own sized for one container.
4. Keep the `?sslmode=require` on the end. Neon refuses unencrypted connections.
5. That value is `DATABASE_URL`.

The string Neon gives you begins `postgres://` or `postgresql://`. Paste it as-is:
`db.py` rewrites either form to name the driver explicitly at startup, because
SQLAlchemy 2 dropped the `postgres://` alias and otherwise reaches for psycopg2,
which this project does not install. The canonical explicit form, if you would rather
write it out, is:

```
postgresql+psycopg://USER:PASSWORD@HOST/DBNAME?sslmode=require
```

If the password contains `@`, `:`, `/` or `?`, percent-encode it - `@` becomes `%40`.
That is the single most common reason a correct-looking URL fails to connect.

> Scale-to-zero means the first query after an idle period takes about a second while
> the database resumes. `db.init()` retries with backoff for exactly this reason, so a
> cold database does not turn into a failed deploy.

If the laptop's `kalakriti.db` already holds accounts, listings and orders you want to
keep, move them across before switching over - pointing `DATABASE_URL` at Neon
otherwise creates an empty schema and leaves all of it behind:

```bash
cd backend
python migrate_to_postgres.py --dry-run
python migrate_to_postgres.py --target "postgresql://...your Neon string..."
```

### 2. Images - Cloudflare R2 (about 4 minutes)

R2 is 10 GB free with no egress charge. Egress is what makes image hosting expensive
everywhere else, and product photographs are almost entirely egress.

1. **dash.cloudflare.com**, then **R2** in the sidebar. Activating R2 asks for a
   payment card even though the free allowance costs nothing. Budget a minute for
   that if the account is new.
2. Create a bucket. `kalakriti-media` is a reasonable name.
3. Create an **R2 API token** with object read and write permission. Cloudflare's R2
   API-token screen is where this lives. It issues three things you need: an **Access
   Key ID**, a **Secret Access Key**, and the **S3 API endpoint** for your account,
   which looks like `https://<account-id>.r2.cloudflarestorage.com`. The secret is
   shown once - copy it before leaving the page.
4. **Enable public access on the bucket.** The bucket's settings page has a public
   access section; turning on the r2.dev development subdomain gives you a public URL
   immediately, and connecting a custom domain is the tidier long-term option. Skip
   this and uploads will succeed while every buyer sees a broken image, because
   nothing in the app signs image URLs.
5. The mapping:

| Variable | Value |
|---|---|
| `MEDIA_S3_ENDPOINT` | the S3 API endpoint, `https://<account-id>.r2.cloudflarestorage.com` |
| `MEDIA_S3_BUCKET` | the bucket name |
| `MEDIA_S3_KEY` | Access Key ID |
| `MEDIA_S3_SECRET` | Secret Access Key |
| `MEDIA_PUBLIC_BASE` | the r2.dev URL, or your custom domain |
| `MEDIA_S3_REGION` | the literal string `auto` |

The endpoint and the public base are two different hosts and they are not
interchangeable. The endpoint is the authenticated API this app writes through; the
public base is what a buyer's browser reads from. Swapping them is the usual mistake
here, and `preflight.py` tests both separately for that reason.

### 3. The backend - Render (about 3 minutes)

1. **render.com**, then **New** and **Blueprint**, and connect this repository. Render
   reads `render.yaml` and proposes one web service, `kalakriti-api`, on the free plan
   in the Singapore region.
2. Render prompts for every variable marked `sync: false`, which is all of them.
   Nothing is generated for you, deliberately: `JWT_SECRET`, `VIEW_SALT` and
   `PASSPORT_PRIVATE_KEY` must match the values your laptop already uses, or the two
   disagree and every artisan is logged out on the first deploy.
3. There is no `databases:` block to remove. `DATABASE_URL` is `sync: false` and points
   at Supabase, or Neon, so Render never creates a Postgres instance to bill for. Use
   the **session pooler** connection string, port 5432, username
   `postgres.<project-ref>`, with `?sslmode=require` on the end. The direct string is
   IPv6-only and Render cannot route to it, which fails as a bare timeout.
4. Deploy. The first build takes several minutes, mostly installing `onnxruntime` and
   `opencv`. It does not download the 176 MB U^2-Net weights, because the blueprint
   builds with `LOCAL_VISION=off`; see the memory section below for why.
5. When the service is live, copy its `https://...onrender.com` address from the top of
   the service page and set `PUBLIC_BASE_URL` to it. The service redeploys itself so
   the value is present at startup.

`PUBLIC_BASE_URL` is a manual step on purpose. Render can inject a service's own `host`
property, but that is a bare hostname with no `https://`, and this value is concatenated
straight into listing URLs, storefront links and webhook targets. A URL with no scheme
is not a URL.

`plan: free` is what the blueprint ships, and it works because `LOCAL_VISION=off` makes
the backend fit 512 MB. Read the memory section below before changing either.

### 4. Check it, rather than assume it

```bash
cd backend
python preflight.py --production
```

Run it with the same environment the service has - either from Render's shell on the
running service, or locally with those values exported. It does not read
configuration and tell you what it found; it connects to the database and times a
query, writes and reads back and deletes a test object in the bucket, fetches that
object again with no credentials the way a buyer's browser would, calls the model
endpoint, and fetches `/health` over the public URL.

`--production` makes the hosted-only problems - SQLite, images on the container disk,
an OTP nobody can receive, a private `PUBLIC_BASE_URL` - failures rather than
warnings. Exit code is 0 when nothing failed, so it can gate a deploy in CI.

Every failure line names the exact variable to set.

### 5. Rebuild the APK against the public URL

```bash
cd app/android
EXPO_PUBLIC_API_URL="https://kalakriti-api.onrender.com" \
  ./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
```

`EXPO_PUBLIC_API_URL` accepts a comma-separated list, probed in order at startup, and
the first backend that answers wins:

```bash
EXPO_PUBLIC_API_URL="https://kalakriti-api.onrender.com,http://192.168.1.5:8000"
```

That is worth doing. One APK then works both on a network where the hosted backend is
reachable and in a room where only the laptop is, without a fourteen-minute Gradle
rebuild between the two.

---

## What is already real, and what is not

It is worth being precise, because "we have no database" is not true.

| | State |
|---|---|
| Database | **Real.** SQLite or Postgres through SQLAlchemy - accounts, listings, publications, orders, shipments, enquiries, an append-only event log. A real relational database with real constraints. Until you set `DATABASE_URL` it is just a **file on your laptop**. |
| Images | **Real files**, written to `backend/media/`. Also on your laptop until `MEDIA_S3_*` is set. |
| Auth | **Real.** Random OTP, salted-SHA-256 hashed, expiring, rate-limited, server-side sessions that logout genuinely revokes. |
| Storefront | **Real.** A public product page with a working Buy button that creates a real order row. It is only reachable on your LAN. |

So nothing here is fake. It is **single-machine**. The three sections below are the
same three moves as the click path above, with the reasoning behind them.

---

## 1. Postgres instead of a SQLite file

SQLite on a hosted container is the failure that hurts most, because it is silent.
The container filesystem is wiped on every deploy, so every artisan account, listing
and order disappears - and nobody notices until somebody tries to log in.

Nothing in the code needs changing: `DATABASE_URL` is already read, the driver is in
`requirements.txt`, and `db.py` normalises whatever shape the provider hands out.

```bash
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/kalakriti?sslmode=require
```

Free options that are genuinely free, not trials:

| | Free tier | Note |
|---|---|---|
| **Neon** | 0.5 GB, scale-to-zero | Best fit, and what the click path uses. An idle demo costs nothing. First query after idle takes about 1s. |
| **Supabase** | 500 MB | Also gives you object storage in the same project, which you need anyway. |
| **Render Postgres** | 1 GB, **expires after 30 days** | Fine for a hackathon, not past it. |

`/health` reports `database.engine`, the password-stripped URL, the query latency and
the pool state. `preflight.py` checks the same things and additionally confirms every
table exists. Check one of them after the first deploy.

> **Schema:** tables are created with `Base.metadata.create_all` at startup. That is
> correct for a project with no production data yet, and it does **not** perform
> migrations - it creates what is missing and leaves existing tables alone. The first
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
MEDIA_S3_REGION=auto
MEDIA_PUBLIC_BASE=https://media.yourdomain.com
```

| | Free tier | Note |
|---|---|---|
| **Cloudflare R2** | 10 GB, **no egress charge** | The right answer. Egress is what makes image hosting expensive elsewhere. |
| **Supabase Storage** | 1 GB | Same API, and you may already be there for Postgres. |
| **Backblaze B2** | 10 GB | Fine; egress is free up to 3x stored. |

All four of endpoint, bucket, key and secret must be set together. Set three of them
and `media.py` logs which are missing once at startup and writes to disk instead,
which looks like it is working.

`/health` reports which backend is live. If S3 is configured but a put fails, the
image is written to disk instead and the error is logged - the artisan keeps her
photograph rather than losing it to a bucket policy.

### Move the photographs that already exist

Setting those five variables only changes where the *next* photograph goes. Every
image taken before that is a file under `backend/media/`, and every listing holds a
URL pointing at whichever hostname was serving it at the time. Move them:

```bash
cd backend
python migrate_media_to_s3.py --dry-run     # what would move, and where to
python migrate_media_to_s3.py               # upload, then rewrite the stored URLs
```

It uploads each file under its own name, then rewrites `image_url`, `raw_url` and the
thumbnail inside `vision` on every listing to point at the bucket. Safe to run twice.

Skip it and the storefront shows a dead photograph. The Provenance Passport page is
worse than dead: its whole claim is "here is the photograph as taken, and here is the
one buyers see", and two broken images leave that claim standing while making it
impossible to check.

## 3. A public HTTPS host

```bash
# Render, from the blueprint in the repo
render blueprint launch          # reads render.yaml

# or anything that runs a Dockerfile
docker build -t kalakriti .
docker run -p 8000:8000 --env-file backend/.env kalakriti
```

Two things get better the moment the backend is on HTTPS:

- **The same-Wi-Fi requirement disappears.** The phone can be anywhere, on mobile data.
- **The cleartext exemption stops mattering.** `withLocalNetworkAccess.js` only ever
  permitted plain HTTP for private LAN ranges; a public HTTPS host does not use it.
  Leave the plugin in - it costs nothing and keeps local development working.

### Memory: the one thing that will bite you

This was estimated for a long time and the estimate was wrong in the direction that
mattered, so here is the measurement. Whole app, plus one real matte of a 3072x4080
phone photo, resident set size at peak:

| configuration | peak RSS |
|---|---|
| `u2net`, the default matting model | 729 MB |
| `u2netp`, the small one | 544 MB |
| `u2netp`, working resolution capped at 1600px | 519 MB |
| `LOCAL_VISION=off` | 227 MB |

Render's free and starter plans are both **512 MB**. Nothing that loads a vision model
fits in that, not even the small model with the resolution capped. Capping the
resolution was the obvious lever and it bought only 10 to 25 MB, because the cost is
the ONNX weights and their allocation arenas rather than the image buffers.

**There is now a setting that turns the local models off.** Earlier versions of this
document said there was not, and that making one would be a code change rather than a
setting. That change has since been made. `LOCAL_VISION=off` makes `imaging.matte` and
`vision.run_ocr` report themselves unavailable instead of loading anything, and the
process settles at about 227 MB.

What that gives up is local background removal and offline OCR, and nothing else.
Everything on NVIDIA's endpoint is untouched: the listing copy, the price band, the
HSN code, the translations, and the vision model that reads the photograph are all
remote calls. A photograph is kept as taken rather than cut out, and the operations
log that the Provenance Passport signs records that, so a listing made on such a host
does not claim an edit that never happened.

Two related settings, both in `backend/imaging.py`:

- `REMBG_MODEL` picks the matting model, `u2net` by default. `u2netp` is looser around
  fringes and frayed selvedge, which is exactly where handloom lives, so it is a
  hosting compromise and not an improvement.
- `MATTE_MAX_PX` caps the longest edge during matting, 1600 by default. Kept because
  it is free and bounds the worst case, not because it saves much.

So the honest position is one of two, and both are defensible:

- **free plan, `LOCAL_VISION=off`** - which is what `render.yaml` is set up for, and
  what a demo should use.
- **`plan: standard`, 2 GB, $25/month** - runs the full pipeline with room to spare.

`plan: starter` at $7/month is the one option not worth taking: it has the same 512 MB
as free, so it buys no capability, only the absence of the idle spin-down.

### Render's free tier: the two limits that catch people

**It spins down after 15 minutes** with no inbound traffic, and the next request waits
30 to 60 seconds while the container starts. `.github/workflows/keepalive.yml` pings
`/health` every ten minutes to prevent that.

**It allows 750 instance-hours a month** across every free web service in the account.
One service held permanently awake uses about 730, a calendar month, so it fits with
almost nothing spare. A second always-on free service takes the account over the limit
and Render suspends both.

Full step-by-step instructions for this path, including the values to paste and what
each failure looks like, are in `DEPLOYING.md` at the repository root.

### A note on Hugging Face Spaces

Earlier versions of this document recommended a free Hugging Face Space, on the
strength of its 16 GB of RAM on the free CPU tier, which is far more than any figure in
the table above. That is still true of the hardware, and the path no longer works for
us in practice, so `DEPLOYING.md` targets Render instead.

The repository still carries everything a Space needs, so the path is recoverable
rather than deleted: the `Dockerfile`, plus `app.py`, the root `requirements.txt` and
`packages.txt` for the non-Docker Gradio SDK route, and the YAML front matter at the
top of `README.md` that selects between them. Render ignores all of it and builds the
`Dockerfile`.

**One thing to know about the container filesystem, on any of these hosts.** The
container writes `passport_key.pem`, the Ed25519 key that signs Provenance Passports,
on first use, and that file does not survive a rebuild. A new key is then generated and
passports minted afterwards are signed with it. Older passports still verify, because
each one stores the public key that actually signed it and `/passport/{id}` checks
against that rather than against whatever key the server holds today. Nothing breaks.
It is simply worth knowing that "the Kalakriti key" is not one key over time. The fix,
and the reason `PASSPORT_PRIVATE_KEY` exists as an environment variable, is to set the
key explicitly so every deploy signs with the same one.

## 4. Real SMS, so login works for somebody who is not you

`OTP_DEV_ECHO=true` prints the code on screen. It must never be set in production -
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

DLT template approval is not instant. If you need SMS working to a deadline, start
that application before anything else on this page.

Without either, `sms.py` returns `not_configured` and **does not deliver the code**.
Verification does not weaken - the code still has to match. It simply cannot be
received, which is the truth.

---

## The whole checklist

```
[ ] DATABASE_URL          Postgres, not SQLite
[ ] MEDIA_S3_*            object storage, not the container disk
[ ] MEDIA_PUBLIC_BASE     the bucket's public URL, not the S3 API endpoint
[ ] PUBLIC_BASE_URL       your https:// host - this is what listing URLs point at
[ ] NVIDIA_API_KEY        as before
[ ] JWT_SECRET            a long random value; sessions are signed with it
[ ] VIEW_SALT             a long random value; keeps view rows non-identifying
[ ] MSG91_*               real OTP delivery
[ ] OTP_DEV_ECHO          NOT SET
[ ] instance memory       2 GB; there is no low-memory mode
[ ] EXPO_PUBLIC_API_URL   the https:// host, baked into the APK
[ ] old images moved      python migrate_media_to_s3.py
[ ] passport QR scanned   from a real phone's camera app, after the host is final
```

The last line is the one that is easy to skip. The Provenance Passport QR code encodes
`PUBLIC_BASE_URL` at the moment the photograph is analysed, so every passport minted
against a temporary hostname points at a hostname that will stop existing. Settle the
host first, then mint the passports you intend to print.

Then, rather than reading that list back to yourself:

```bash
cd backend && python preflight.py --production
```

It tests each line above and exits non-zero if any of them is wrong. `GET /health`
covers a narrower set of the same ground from inside the running process.

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

Until then each reports `not_configured` and names the exact variables it wants -
`preflight.py` prints that list. That is the intended behaviour and the demo works
without any of them: the storefront channel is fully real, publishes immediately, and
takes real orders.

---

## Push notifications, honestly

When the server finishes a listing in auto mode, the app shows a card the next time it
is opened, and the count sits on the Home tab. That is real and it works.

Waking a **closed** app requires Firebase Cloud Messaging: a Google project, a
`google-services.json`, and `expo-notifications` configured with an FCM server key.
None of that is in this repository, so the app does not claim to do it. If you want
it, that is the exact configuration required - and the job status endpoint it would
be driven from is already there.
