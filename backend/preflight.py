#!/usr/bin/env python
"""
Is this deployment actually usable by somebody who is not the developer?

Every check here runs a real test. Nothing is inferred from the presence of a
variable, because the failures that hurt on this project are the silent ones: a
DATABASE_URL that points at a SQLite file inside a container that is wiped on the
next deploy, a bucket whose credentials are set but rejected, an OTP that is
generated correctly and never delivered. All three look fine in a config dump, and
all three lose real data or lock real people out.

    python preflight.py                 local development; hosted-only problems warn
    python preflight.py --production    hosted-only problems are failures

Exit code is 0 when nothing FAILed and 1 otherwise, so it can gate a deploy in CI.

The four states:

    PASS   tested, and it worked
    WARN   tested, and it is wrong for a hosted deployment but right on a laptop
    FAIL   tested, and it is broken; the fix line names the exact variable
    SKIP   could not be tested from here, and why
"""
from __future__ import annotations

import argparse
import os
import sys
import textwrap
import time
import uuid
from urllib.parse import urlparse

from dotenv import load_dotenv

# Every module in this backend reads its configuration at import time: llm.API_KEY,
# media.PUBLIC_BASE, db.engine. Loading .env before importing any of them means
# preflight sees exactly the configuration uvicorn would see, not a partial one.
load_dotenv()

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

# Set by the platform, not by us. Their presence means the code is running inside a
# container with an ephemeral filesystem, which is what makes checks 2, 3 and 6 hard
# requirements rather than preferences.
HOST_MARKERS = ("RENDER", "FLY_APP_NAME", "RAILWAY_ENVIRONMENT", "K_SERVICE",
                "DYNO", "WEBSITE_INSTANCE_ID", "KUBERNETES_SERVICE_HOST")

# Values people paste in to make an error message go away. A signing key that is
# guessable is not a signing key.
WEAK_SECRETS = {"changeme", "change-me", "secret", "password", "test", "dev",
                "development", "kalakriti", "your-secret-key", "supersecret",
                "please-change", "none", "null", "0", "1", "todo"}

# Long enough to tell the two interesting answers apart. When NVIDIA's shared
# endpoint is busy it holds the request for around 100 seconds before returning 503,
# and a timeout shorter than that reports "no reply" - which cannot distinguish a
# valid key behind a busy service from a key that does not work at all.
LLM_TIMEOUT = 150
HTTP_TIMEOUT = 15

_counts = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
STRICT = False


# ---------------------------------------------------------------- reporting

def report(status: str, title: str, detail: str = "", fix: str = "") -> None:
    _counts[status] += 1
    print(f"{status}  {title}")
    for para in detail.split("\n"):
        for line in textwrap.wrap(para, width=74) or [""]:
            print(f"      {line}")
    for line in [f for f in fix.split("\n") if f]:
        print(f"      -> {line}")
    print()


def harden(status: str) -> str:
    """
    A hosted-only problem.

    On a laptop, SQLite and local media and OTP_DEV_ECHO are the correct settings, so
    they warn. In a container they lose accounts, lose photographs and lock out every
    user who is not the developer, so --production turns them into failures.
    """
    return FAIL if (status == WARN and STRICT) else status


def hosted() -> bool:
    return any(os.getenv(k) for k in HOST_MARKERS)


def env(name: str) -> str:
    return (os.getenv(name) or "").strip()


# ------------------------------------------------- 1. language model (NVIDIA)

def check_llm() -> None:
    title = "1. Language model - NVIDIA Nemotron"
    key = env("NVIDIA_API_KEY")
    if not key:
        report(FAIL, title,
               "NVIDIA_API_KEY is not set. Every listing this app writes - title, "
               "description, tags, the translated copy - comes from this endpoint. "
               "Without it the app accepts a photograph and produces nothing.",
               "NVIDIA_API_KEY=nvapi-...  (from build.nvidia.com)")
        return

    import openai                                        # noqa: PLC0415
    import llm                                           # noqa: PLC0415

    # One token is enough. We are testing that the key is accepted and that the model
    # name resolves, not that the model is any good; a reasoning model may spend that
    # token on an empty completion, and the HTTP status is what we are reading.
    #
    # max_retries=0 matters. The default client retries twice, so a slow endpoint
    # turns one timeout into three and the operator kills the script before it reports
    # anything. Retrying is the right behaviour for a real request and the wrong
    # behaviour for a diagnostic, which wants one honest answer inside a bounded time.
    # llm.py keeps its own 600s timeout for actual listing work.
    client = llm.client()
    try:
        client = client.with_options(timeout=LLM_TIMEOUT, max_retries=0)
    except AttributeError:
        pass

    print(f"...   contacting {llm.BASE_URL}, up to {LLM_TIMEOUT}s", flush=True)
    t0 = time.time()
    try:
        client.chat.completions.create(
            model=llm.MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1, temperature=0)
    except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
        report(FAIL, title,
               f"NVIDIA_API_KEY is set ({_masked(key)}) and the endpoint rejected it: "
               f"HTTP {getattr(e, 'status_code', 401)}. The key is wrong, revoked, or "
               f"belongs to an account without access to this model.",
               "NVIDIA_API_KEY=  a current key from build.nvidia.com")
    except openai.NotFoundError:
        report(FAIL, title,
               f"The key was accepted but the model '{llm.MODEL}' does not exist on "
               f"{llm.BASE_URL}. This is a model-name problem, not a credential one.",
               "NEMOTRON_MODEL=nvidia/nemotron-3-ultra-550b-a55b")
    except (openai.RateLimitError, openai.InternalServerError,
            openai.APIStatusError) as e:
        code = getattr(e, "status_code", 0)
        if code in (429, 500, 502, 503, 504):
            # The credential is fine; NVIDIA's shared endpoint is busy. Failing a
            # deploy over somebody else's load would be wrong, so this warns.
            report(WARN, title,
                   f"The key was accepted but the service returned HTTP {code} - it is "
                   f"overloaded or rate limiting, not misconfigured. Retry in a "
                   f"minute. At runtime the app surfaces this error rather than "
                   f"writing an empty listing.")
        else:
            report(FAIL, title, f"The endpoint returned HTTP {code}: {str(e)[:160]}")
    except openai.APITimeoutError:
        report(WARN, title,
               f"No reply within {LLM_TIMEOUT}s. This does not say the key is wrong - a "
               f"rejected key comes back as 401 immediately, so silence means the "
               f"endpoint is saturated or the model is cold. Re-run before treating it "
               f"as broken; the app itself allows 600s for a real request.")
    except openai.APIConnectionError as e:
        report(FAIL, title,
               f"Could not reach {llm.BASE_URL}: {type(e).__name__}. That is a network "
               f"or DNS problem on the machine running the server, not a key problem.")
    except Exception as e:                              # noqa: BLE001
        report(FAIL, title, f"{type(e).__name__}: {str(e)[:200]}")
    else:
        report(PASS, title,
               f"Key accepted. A 1-token round trip to {llm.MODEL} took "
               f"{time.time() - t0:.1f}s.")


def _masked(secret: str) -> str:
    return f"{secret[:6]}...{secret[-4:]}" if len(secret) > 14 else "a short value"


# ----------------------------------------------------------------- 2. database

def check_database() -> None:
    title = "2. Database"

    # db.py owns URL normalisation (it rewrites the postgres:// that Neon, Supabase
    # and Render all print), driver selection, the connect timeout and the pool
    # settings, and db.health() already runs SELECT 1 and times it. Re-deriving any of
    # that here would mean preflight testing a connection the server does not use.
    # create_engine loads the dialect, so a malformed URL or a missing driver raises
    # on import - with a message db.py has already written to name DATABASE_URL.
    try:
        import db                                       # noqa: PLC0415
        from sqlalchemy import inspect                  # noqa: PLC0415
    except Exception as e:                              # noqa: BLE001
        report(FAIL, title, str(e)[:400],
               "DATABASE_URL=postgresql+psycopg://user:pass@host/dbname?sslmode=require")
        return

    if db.IS_SQLITE:
        path = db.DB_URL.split("///", 1)[-1] if "///" in db.DB_URL else "(memory)"
        try:
            size = f", currently {os.path.getsize(path) / 1024:.0f} KB"
        except OSError:
            size = ", not yet created"
        report(harden(WARN), title,
               f"SQLite file at {path}{size}. On a laptop this is a real relational "
               f"database and it is fine. In a hosted container it is data loss: the "
               f"filesystem is replaced on every deploy and on most restarts, so every "
               f"artisan account, listing and order disappears. Nothing raises an "
               f"error - the tables are recreated empty, and the first person to find "
               f"out is an artisan who can no longer log in.",
               "DATABASE_URL=postgresql+psycopg://user:pass@host/dbname?sslmode=require")
        return

    h = db.health()
    if not h["ok"]:
        report(FAIL, title,
               f"Could not connect to {h['url']}: {h['error']}",
               "DATABASE_URL=postgresql+psycopg://user:pass@host/dbname?sslmode=require")
        return

    latency = h["latencyMs"] or 0
    detail = (f"Connected to {h['engine']} at {h['url']}. SELECT 1 in {latency} ms.")
    if latency > 400:
        detail += (" That is slow for a query this trivial. If the database is "
                   "scale-to-zero it is a cold start and the next query will be fast; "
                   "if it stays slow, the database is in a different region from the "
                   "web service and every request pays that cost.")

    expected = set(db.Base.metadata.tables)
    try:
        present = set(inspect(db.engine).get_table_names())
    except Exception as e:                              # noqa: BLE001
        report(WARN, title, detail + f"\nCould not list tables: {type(e).__name__}.")
        return

    missing = sorted(expected - present)
    if not missing:
        report(PASS, title, detail + f"\nAll {len(expected)} application tables exist.")
    elif not (expected & present):
        report(WARN, title, detail +
               "\nThe database is empty - none of the application tables exist yet. "
               "That is expected before the first deploy: db.init() calls "
               "Base.metadata.create_all at startup and will build them. Re-run this "
               "once the service has started.")
    else:
        report(FAIL, title, detail +
               f"\nThe schema is partial. Missing: {', '.join(missing)}. create_all "
               f"adds missing tables but never alters existing ones, so a table left "
               f"over with an older shape will not be repaired by restarting. This one "
               f"needs looking at by hand.")


# ----------------------------------------------------------- 3. object storage

def check_storage() -> None:
    title = "3. Object storage for product images"
    import media                                        # noqa: PLC0415

    missing = media.missing()
    if len(missing) == 4:
        report(harden(WARN), title,
               "No object storage configured, so images are written to the container "
               "filesystem under MEDIA_DIR. The same failure as SQLite and slightly "
               "worse: a marketplace has already published the image URL, so after the "
               "next deploy the listing is live with a dead photograph.",
               "MEDIA_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com\n"
               "MEDIA_S3_BUCKET=kalakriti-media\n"
               "MEDIA_S3_KEY=<access key id>\n"
               "MEDIA_S3_SECRET=<secret access key>\n"
               "MEDIA_PUBLIC_BASE=<the bucket's public https URL>")
        return

    if missing:
        # Worse than nothing configured, because media.py falls back to disk without
        # complaining and the operator believes the bucket is in use.
        report(FAIL, title,
               "Object storage is partly configured. media.py requires all four before "
               "it will build a client, so it is silently writing to local disk and "
               "the bucket is never touched.",
               "\n".join(f"{name}=..." for name in missing))
        return

    # media._s3() is private, and reusing it is deliberate: it builds the client from
    # exactly the variables the running server would use, so a typo in an endpoint is
    # caught here rather than on an artisan's first upload.
    client = media._s3()
    if client is None:
        report(FAIL, title,
               "All four MEDIA_S3_* variables are set but the client could not be "
               "built, because boto3 is not installed here. media.py logs a warning "
               "and writes to disk instead. boto3 is in requirements.txt, so this is a "
               "stale virtualenv rather than a deployment problem.",
               "pip install boto3")
        return

    cfg = media._config()
    key = f"preflight-{uuid.uuid4().hex[:12]}.txt"
    body = b"kalakriti preflight"
    try:
        client.put_object(Bucket=cfg["bucket"], Key=key, Body=body,
                          ContentType="text/plain")
    except Exception as e:                              # noqa: BLE001
        report(FAIL, title,
               f"Writing to bucket '{cfg['bucket']}' at {cfg['endpoint']} failed: "
               f"{type(e).__name__}: {str(e)[:180]}. The usual causes are a token "
               f"without object-write permission, a bucket name that does not exist, "
               f"and an endpoint belonging to a different account.")
        return

    try:
        back = client.get_object(Bucket=cfg["bucket"], Key=key)["Body"].read()
    except Exception as e:                              # noqa: BLE001
        _delete_quietly(client, cfg["bucket"], key)
        report(FAIL, title,
               f"Wrote an object but could not read it back: {type(e).__name__}: "
               f"{str(e)[:180]}. The token has write permission but not read.")
        return

    if back != body:
        _delete_quietly(client, cfg["bucket"], key)
        report(FAIL, title, "The object read back did not match what was written.")
        return

    public_ok, public_msg = _check_public_read(cfg, key)
    _delete_quietly(client, cfg["bucket"], key)

    detail = (f"Wrote, read back and deleted a test object in bucket "
              f"'{cfg['bucket']}'. Credentials and permissions are correct.")
    if public_ok:
        report(PASS, title, detail + "\n" + public_msg)
    else:
        report(harden(WARN), title, detail + "\n" + public_msg,
               "MEDIA_PUBLIC_BASE=<the bucket's public https URL>")


def _check_public_read(cfg: dict, key: str) -> tuple[bool, str]:
    """
    A buyer's browser fetches the image with no credentials.

    media.save returns a plain URL, so if the bucket is not publicly readable every
    product photograph is a broken image for everyone except us. A successful S3 API
    call says nothing about this, which is why it is tested separately.
    """
    base = cfg["public"].rstrip("/")
    if not base:
        return False, ("MEDIA_PUBLIC_BASE is not set, so media.py builds image URLs "
                       "from the S3 API endpoint. That endpoint requires a signature - "
                       "on R2 it answers an unsigned browser request with 401 - so "
                       "every product photograph would be a broken image to a buyer. "
                       "Set the bucket's public r2.dev URL or a custom domain.")
    try:
        import requests                                 # noqa: PLC0415
        r = requests.get(f"{base}/{key}", timeout=HTTP_TIMEOUT)
    except Exception as e:                              # noqa: BLE001
        return False, (f"MEDIA_PUBLIC_BASE is set but {base} could not be reached: "
                       f"{type(e).__name__}. Buyers load every product image from "
                       f"this host.")
    if r.status_code == 200:
        return True, f"The object was also readable without credentials at {base}."
    return False, (f"MEDIA_PUBLIC_BASE is set, but a plain unsigned GET returned HTTP "
                   f"{r.status_code}. The bucket is not publicly readable, so product "
                   f"images will be broken for buyers even though uploads succeed. "
                   f"Enable public access on the bucket, or point MEDIA_PUBLIC_BASE at "
                   f"a domain that serves it.")


def _delete_quietly(client, bucket: str, key: str) -> None:
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except Exception:                                   # noqa: BLE001
        print(f"      (could not delete test object {key}; remove it by hand)")


# ---------------------------------------------------------- 4. PUBLIC_BASE_URL

PRIVATE_PREFIXES = ("10.", "192.168.", "169.254.", "127.")


def _is_private(host: str) -> bool:
    if host in ("localhost", "0.0.0.0", "::1", "[::1]"):
        return True
    if host.startswith(PRIVATE_PREFIXES):
        return True
    if host.startswith("172."):
        try:
            return 16 <= int(host.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def check_public_base_url() -> None:
    title = "4. PUBLIC_BASE_URL"
    val = env("PUBLIC_BASE_URL")
    if not val:
        report(FAIL, title,
               "Not set. This is the address baked into every listing URL, every "
               "storefront link and every locally served image path, so it is not "
               "cosmetic: it decides whether a buyer who opens a link sees the product "
               "or nothing. media.py defaults it to http://localhost:8000, which is "
               "the wrong answer for everybody except the machine it runs on.",
               "PUBLIC_BASE_URL=https://your-service.onrender.com")
        return

    parsed = urlparse(val)
    host = parsed.hostname or ""
    if not parsed.scheme or not host:
        report(FAIL, title,
               f"'{val}' is not a URL. It needs a scheme and a host.",
               "PUBLIC_BASE_URL=https://your-service.onrender.com")
        return

    # Appended to whatever the verdict turns out to be, rather than printed on its
    # own, so the report stays one block per check.
    slash = ("\nIt also ends with a slash. URLs are built by appending, so paths will "
             "contain a double slash. Harmless on most hosts, but drop it."
             if val.endswith("/") else "")

    if _is_private(host):
        report(FAIL if STRICT else WARN, title,
               f"{val} is a private address. Every listing URL and every image path "
               f"points here, so only a device on this same network can open them. "
               f"That is exactly right while developing against a laptop, and wrong "
               f"the moment anything is hosted or shown to somebody else." + slash,
               "PUBLIC_BASE_URL=https://your-service.onrender.com")
        report(SKIP, "4b. PUBLIC_BASE_URL reachability",
               "Not tested. Nothing outside this network can reach a private address, "
               "which is the finding above rather than a separate one.")
        return

    if parsed.scheme != "https":
        report(FAIL if STRICT else WARN, title,
               f"{val} is a public host served over plain HTTP. Android blocks "
               f"cleartext traffic, and this app's exemption plugin only permits it "
               f"for private LAN ranges, so the APK will refuse to talk to this."
               + slash,
               f"PUBLIC_BASE_URL=https://{host}")
        return

    # It claims to be a public HTTPS host. Find out whether it is actually serving,
    # because a correct-looking URL for a service that is not up is the same outcome.
    try:
        import requests                                 # noqa: PLC0415
        t0 = time.time()
        r = requests.get(val.rstrip("/") + "/health", timeout=HTTP_TIMEOUT)
    except Exception as e:                              # noqa: BLE001
        report(WARN, title,
               f"{val} is a public HTTPS address but could not be reached from here: "
               f"{type(e).__name__}. Expected before the first deploy, and a real "
               f"problem after it.")
        return

    if r.status_code == 200:
        report(PASS, title,
               f"{val} answered /health with 200 in {time.time() - t0:.1f}s. A public "
               f"HTTPS address, so the phone no longer has to be on any particular "
               f"network." + slash)
    else:
        report(WARN, title,
               f"{val} is a public HTTPS address but /health returned HTTP "
               f"{r.status_code}. If the service has not been deployed yet that is "
               f"expected; if it has, the container is not serving." + slash)


# ------------------------------------------------------------------ 5. secrets

def check_secrets() -> None:
    _check_jwt_secret()
    _check_view_salt()


def _check_jwt_secret() -> None:
    title = "5a. JWT_SECRET"
    val = env("JWT_SECRET")
    if not val:
        # auth.py writes a random key to jwt_secret.txt when the variable is absent.
        # Correct on a laptop, useless in a container: the file belongs to the
        # filesystem that is wiped, so every deploy invalidates every session and logs
        # every artisan out with no explanation and no error.
        report(FAIL if STRICT else WARN, title,
               "Not set. auth.py generates a random key and persists it to "
               "jwt_secret.txt, which survives a restart on a laptop but not a deploy "
               "in a container: the file goes with the filesystem, so every session "
               "token signed with the old key stops verifying and everybody is "
               "silently logged out.",
               "JWT_SECRET=  48+ random characters, from "
               "python -c \"import secrets;print(secrets.token_urlsafe(48))\"")
        return
    problem = _weak(val)
    if problem:
        report(FAIL, title,
               f"Set, but {problem}. Session tokens are signed with this value, so a "
               f"guessable one lets anybody mint a token for any artisan's account.",
               "JWT_SECRET=  48+ random characters")
        return
    report(PASS, title, f"Set, {len(val)} characters, not a placeholder value.")


def _check_view_salt() -> None:
    title = "5b. VIEW_SALT"
    val = env("VIEW_SALT")
    if not val:
        report(WARN, title,
               "Not set. analytics.py derives one from JWT_SECRET, so view rows are "
               "still hashed rather than storing an address. What leaving it derived "
               "costs is stability: rotating JWT_SECRET also changes every viewer "
               "hash, so unique-view counts restart from zero. Setting it explicitly "
               "decouples the two.",
               "VIEW_SALT=  32+ random characters")
        return
    problem = _weak(val)
    if problem:
        report(FAIL, title,
               f"Set, but {problem}. This salts the hash that keeps a view row from "
               f"identifying the person who made it; a known salt makes the hash "
               f"reversible by trying candidate addresses.",
               "VIEW_SALT=  32+ random characters")
        return
    report(PASS, title, f"Set, {len(val)} characters, not a placeholder value.")


def _weak(val: str) -> str:
    if val.lower() in WEAK_SECRETS:
        return "it is a placeholder value"
    if len(val) < 32:
        return f"it is only {len(val)} characters, which is short enough to attack"
    if len(set(val)) < 5:
        return "it repeats only a handful of distinct characters"
    return ""


# ------------------------------------------------------------- 6. OTP delivery

def _msg91_zero_balance() -> tuple[bool, bool]:
    """
    Is the MSG91 wallet empty? Returns (is_zero, was_actually_checked).

    Uses the old balance.php endpoint because it is the one that answers to a plain
    auth key; the v5 route does not exist. Three route types are read, since credits
    are held per route and a transactional balance is what OTP spends.

    Unreachable or unparseable is reported as "not checked" rather than as fine. A
    balance we could not read is not evidence of a balance.
    """
    import os                                            # noqa: PLC0415

    import requests                                      # noqa: PLC0415

    key = os.getenv("MSG91_AUTH_KEY", "")
    if not key:
        return False, False

    seen_any = False
    for route in ("4", "1", "106"):                      # transactional, promo, intl
        try:
            r = requests.get("https://control.msg91.com/api/balance.php",
                             params={"authkey": key, "type": route}, timeout=15)
            if r.status_code != 200:
                continue
            raw = r.text.strip()
            value = float(raw)
        except Exception:
            continue
        seen_any = True
        if value > 0:
            return False, True
    return seen_any, seen_any


def check_otp() -> None:
    title = "6. OTP delivery"
    import sms                                          # noqa: PLC0415

    provider = sms.provider()
    echo = sms.dev_echo()

    if provider != "not_configured":
        # No test message is sent. It would cost money and would put a live code on
        # somebody's handset. The credentials are reported as configured, not as
        # verified, because that is the truth of what was checked.
        detail = (f"{provider} is configured. Delivery itself is not tested here: "
                  f"sending a real SMS costs money and puts a code on a real handset, "
                  f"so preflight will not do it. Confirm delivery once by hand with a "
                  f"real login.")

        # One thing worth checking, because it is free and it is the trap that cost
        # an afternoon: MSG91 accepts a send with an empty wallet and answers
        # {"type":"success"} with a request id. Nothing is delivered. The app then
        # tells the artisan a code is on its way and she waits for a message that
        # was never sent, which is indistinguishable from the app being broken.
        if provider == "msg91":
            zero, checked = _msg91_zero_balance()
            if zero:
                report(FAIL, title,
                       "MSG91 is configured and authenticating, but the account "
                       "balance is zero on every route. Sends will be accepted and "
                       "answered with type=success and a request id, and no SMS will "
                       "arrive. Nobody can log in, and nothing in the response says "
                       "so.\n"
                       "Note that MSG91's own signup verification codes still reach "
                       "your phone - those are MSG91 verifying you, not this app "
                       "sending anything, and they are easy to mistake for a working "
                       "integration.",
                       "Add SMS credits to the MSG91 account,\n"
                       "or set FAST2SMS_API_KEY instead (free trial credit, no DLT "
                       "template needed)")
                return
            if checked:
                detail += " The account has a non-zero SMS balance."
            else:
                detail += (" The account balance could not be read, so a zero-balance "
                           "wallet would still accept sends and deliver nothing.")
        if echo:
            report(FAIL if STRICT else WARN, title, detail +
                   "\nOTP_DEV_ECHO is also on, which returns the code in the API "
                   "response body. With a working provider that is redundant, and in "
                   "production it hands anybody who can call the endpoint a login for "
                   "any phone number.",
                   "OTP_DEV_ECHO=  remove it entirely")
        else:
            report(PASS, title, detail)
        return

    if echo:
        report(FAIL if STRICT else WARN, title,
               "No SMS provider is configured and OTP_DEV_ECHO is on. The OTP itself "
               "is real - random, hashed, expiring, rate-limited, and verification does "
               "not weaken - but it is returned in the API response instead of being "
               "delivered. That is the right setting on a laptop. In a hosted "
               "deployment it means nobody but the developer can log in, while the "
               "login screen looks like it works.",
               "FAST2SMS_API_KEY=...   (fastest: an API key only, no DLT template)\n"
               "or MSG91_AUTH_KEY=... and MSG91_TEMPLATE_ID=...   (own DLT template)\n"
               "then remove OTP_DEV_ECHO")
        return

    report(FAIL, title,
           "No SMS provider and no dev echo. sms.py returns not_configured and the "
           "code is never delivered, so nobody can complete a login - including the "
           "developer. This is not a hosted-only problem: login is impossible in every "
           "environment until one of these is set.",
           "FAST2SMS_API_KEY=...   (fastest: an API key only, no DLT template)\n"
           "or MSG91_AUTH_KEY=... and MSG91_TEMPLATE_ID=...   (own DLT template)\n"
           "or TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER\n"
           "or OTP_DEV_ECHO=true  (local development only)")


# ------------------------------------------------- 7 and 8. channels, logistics

def check_channels() -> None:
    title = "7. Marketplace channels"
    try:
        import channels                                 # noqa: PLC0415
        rows = channels.availability()
    except Exception as e:                              # noqa: BLE001
        report(FAIL, title,
               f"channels.availability() raised {type(e).__name__}: {str(e)[:160]}")
        return

    live = [r for r in rows if r["configured"]]
    todo = [r for r in rows if not r["configured"]]

    # Not a failure. The storefront channel needs no credentials, publishes
    # immediately and takes real orders, so the app is useful with none of the rest
    # configured. Each of these is an account somebody has to open, not missing code.
    lines = ["Publishable now: " + (", ".join(r["name"] for r in live) or "none")]
    for r in todo:
        lines.append(f"{r['name']}: needs {', '.join(r['missing'])}")
    report(PASS if live else WARN, title, "\n".join(lines))


def check_logistics() -> None:
    title = "8. Logistics"
    try:
        import logistics                                # noqa: PLC0415
        a = logistics.availability()
    except Exception as e:                              # noqa: BLE001
        report(FAIL, title,
               f"logistics.availability() raised {type(e).__name__}: {str(e)[:160]}")
        return

    if a["configured"]:
        report(PASS, title,
               f"{a['provider']} is configured. Shipment creation and tracking will "
               f"make real calls.")
    else:
        report(WARN, title,
               "No courier configured. Orders are still created and tracked in the "
               "database; what is absent is the pickup booking and the tracking "
               "number, which the seller can arrange herself.",
               "\n".join(a["missing"]))


# ---------------------------------------------------------------------- main

# --------------------------------------------------- 6b. demo accounts

def check_requirements_in_step() -> None:
    """
    Do the Space and this laptop install the same packages?

    There are two requirements files. `backend/requirements.txt` is the source of
    truth and what a developer installs; `requirements.txt` at the repository root is
    what a Hugging Face Space installs. It has to be a copy rather than an include,
    because a Space bind-mounts that one file at /tmp and `-r backend/...` resolves to
    a path that was never mounted.

    A copy nobody verifies is wrong within a fortnight, and this particular copy is
    worth checking because the failure only shows up in production: the Space quietly
    installs a different version from every laptop, and the bug that follows is
    unreproducible by the person asked to fix it.

    Only the shared pins are compared. The root file also lists `gradio`, which the
    application never imports and the backend must not depend on.
    """
    title = "9. Requirements files in step"
    here = os.path.dirname(os.path.abspath(__file__))
    backend_req = os.path.join(here, "requirements.txt")
    root_req = os.path.join(os.path.dirname(here), "requirements.txt")

    if not os.path.exists(root_req):
        report(WARN, title,
               "There is no requirements.txt at the repository root, so a Hugging "
               "Face Space has nothing to install. Only a problem if you deploy.")
        return

    def pins(path: str) -> dict[str, str]:
        out: dict[str, str] = {}
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if not line or line.startswith("-"):
                    continue
                if "==" in line:
                    name, version = line.split("==", 1)
                    out[name.strip().lower()] = version.strip()
                else:
                    out[line.strip().lower()] = ""
        return out

    try:
        mine, theirs = pins(backend_req), pins(root_req)
    except Exception as e:                                  # noqa: BLE001
        report(WARN, title, f"Could not compare: {type(e).__name__}: {e}")
        return

    # gradio belongs only to the Space. Everything else should match exactly.
    theirs.pop("gradio", None)

    missing = sorted(set(mine) - set(theirs))
    differing = sorted(n for n in set(mine) & set(theirs) if mine[n] != theirs[n])
    extra = sorted(set(theirs) - set(mine))

    if not (missing or differing or extra):
        report(PASS, title,
               f"Both requirements files pin the same {len(mine)} packages, so the "
               f"Space installs what a laptop installs.")
        return

    lines = []
    for n in missing:
        lines.append(f"  {n} is in backend/requirements.txt and not at the root")
    for n in differing:
        lines.append(f"  {n}: backend pins {mine[n]}, the root pins {theirs[n]}")
    for n in extra:
        lines.append(f"  {n} is at the root and not in backend/requirements.txt")

    report(FAIL if STRICT else WARN, title,
           "The two requirements files have drifted, so a deployed Space will not "
           "install what you are running here:\n" + "\n".join(lines),
           "Copy backend/requirements.txt into the root file, keeping the gradio "
           "line at the bottom")


def check_demo_accounts() -> None:
    """
    Are the seeded demo accounts still present with their seeded passwords?

    They exist so the product can be shown while SMS delivery is unpaid, and they are
    real accounts with real scrypt hashes - nothing about them is a bypass. What makes
    them dangerous is that their passwords are *memorable*, which is the same thing as
    guessable, and they are written down in a file in the repository.

    On a laptop that is fine and this warns. Anywhere hosted it is a published
    credential for a verified account, so it fails.
    """
    title = "6b. Demo accounts"
    try:
        import auth  # noqa: PLC0415
        import db  # noqa: PLC0415
        import seed_demo_users  # noqa: PLC0415
    except Exception as e:                              # noqa: BLE001
        report(WARN, title, f"Could not check: {type(e).__name__}: {e}")
        return

    s = db.session()
    try:
        live = []
        for spec in seed_demo_users.DEMO:
            a = s.query(db.Artisan).filter(db.Artisan.phone == spec["phone"]).first()
            if a and auth.check_password(spec["password"], a.password_hash or ""):
                live.append(f"{spec['phone']} ({spec['role']})")
    finally:
        s.close()

    if not live:
        report(PASS, title, "No seeded demo account is using its seeded password.")
        return

    report(FAIL if STRICT else WARN, title,
           "These accounts still have the password written in "
           "seed_demo_users.py:\n  " + "\n  ".join(live) + "\n"
           "Anyone who has seen this repository can sign in as them. They are "
           "verified accounts, so that is a real seller identity, not a sandbox.",
           "python seed_demo_users.py --remove")


def main() -> int:
    global STRICT
    ap = argparse.ArgumentParser(
        description="Check whether this Kalakriti deployment is actually usable.")
    ap.add_argument("--production", action="store_true",
                    help="treat hosted-only problems - SQLite, container-disk images, "
                         "undeliverable OTP, a private PUBLIC_BASE_URL - as failures "
                         "rather than warnings")
    args = ap.parse_args()

    # The first check waits on a remote model. Without line buffering a redirected run
    # shows nothing at all until it finishes, which reads as a hang.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    on_host = hosted()
    STRICT = args.production or on_host

    print()
    print("Kalakriti preflight")
    if args.production:
        mode = "production - hosted-only problems are failures"
    elif on_host:
        mode = ("hosted - a platform marker is set in this environment, so hosted-only "
                "problems are failures")
    else:
        mode = "local - hosted-only problems warn; use --production before deploying"
    print(f"mode: {mode}")
    print()

    for check in (check_llm, check_database, check_storage, check_public_base_url,
                  check_secrets, check_otp, check_demo_accounts, check_channels,
                  check_logistics, check_requirements_in_step):
        try:
            check()
        except Exception as e:                          # noqa: BLE001
            # A check that crashes must not hide the checks after it, and must never
            # be mistaken for a pass.
            report(FAIL, f"{check.__name__} crashed",
                   f"{type(e).__name__}: {str(e)[:200]}")

    print(f"{_counts[PASS]} passed, {_counts[WARN]} warnings, "
          f"{_counts[FAIL]} failed, {_counts[SKIP]} skipped")
    if _counts[FAIL]:
        print("Not ready. Fix the FAIL lines above; each one names the variable to set.")
        return 1
    if _counts[WARN]:
        print("No failures. Read the warnings - on a hosted deployment they are "
              "usually data loss.")
    else:
        print("Ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
