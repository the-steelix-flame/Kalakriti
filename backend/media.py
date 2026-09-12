"""
Where product images live.

Local disk is correct on a laptop and wrong on a host. Every free tier - Render,
Railway, Fly - gives a container an ephemeral filesystem: it is wiped on every
redeploy and on most restarts. A listing whose image URL 404s after a deploy is worse
than one with no image, because the marketplace has already published the dead link.

So: if object storage is configured, images go there and the URL points at the bucket.
Otherwise they go to disk and are served by this process, which is right for local
development and honest about its limits everywhere else.

Any S3-compatible store works, and the free options are real:

    Cloudflare R2      10 GB, no egress charge
    Supabase Storage   1 GB on the free project, same API
    Backblaze B2       10 GB
    MinIO              self-hosted

Configure with:
    MEDIA_S3_ENDPOINT   https://<account>.r2.cloudflarestorage.com
    MEDIA_S3_BUCKET     kalakriti-media
    MEDIA_S3_KEY        access key id
    MEDIA_S3_SECRET     secret access key
    MEDIA_PUBLIC_BASE   https://media.example.com   (the bucket's public URL)

If the first four are set and boto3 is installed, uploads go to the bucket. If any is
missing the module says so once at startup rather than failing quietly on the first
photograph.
"""
from __future__ import annotations

import io
import logging
import os
import threading

log = logging.getLogger("media")

LOCAL_DIR = os.getenv("MEDIA_DIR", "media")
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

_client = None
_checked = False
_lock = threading.Lock()


def _config() -> dict[str, str]:
    return {
        "endpoint": os.getenv("MEDIA_S3_ENDPOINT", ""),
        "bucket": os.getenv("MEDIA_S3_BUCKET", ""),
        "key": os.getenv("MEDIA_S3_KEY", ""),
        "secret": os.getenv("MEDIA_S3_SECRET", ""),
        "region": os.getenv("MEDIA_S3_REGION", "auto"),
        "public": os.getenv("MEDIA_PUBLIC_BASE", ""),
    }


def missing() -> list[str]:
    c = _config()
    names = {"endpoint": "MEDIA_S3_ENDPOINT", "bucket": "MEDIA_S3_BUCKET",
             "key": "MEDIA_S3_KEY", "secret": "MEDIA_S3_SECRET"}
    return [v for k, v in names.items() if not c[k]]


def _s3():
    """The bucket client, or None when object storage is not configured."""
    global _client, _checked
    with _lock:
        if _checked:
            return _client
        _checked = True
        if missing():
            return None
        try:
            import boto3                                    # noqa: PLC0415
            from botocore.config import Config              # noqa: PLC0415
        except ImportError:
            log.warning("MEDIA_S3_* is set but boto3 is not installed; "
                        "falling back to local disk. pip install boto3")
            return None
        c = _config()
        _client = boto3.client(
            "s3", endpoint_url=c["endpoint"], region_name=c["region"],
            aws_access_key_id=c["key"], aws_secret_access_key=c["secret"],
            config=Config(signature_version="s3v4",
                          retries={"max_attempts": 3, "mode": "standard"}),
        )
        log.info("media: object storage at %s/%s", c["endpoint"], c["bucket"])
        return _client


def backend() -> str:
    return "s3" if _s3() else "local"


def save(img, listing_id: str, kind: str) -> str:
    """
    Write a PIL image and return a URL that will still resolve after a redeploy.

    JPEG at quality 90 with optimise on: the difference from 95 is invisible on a
    phone and the file is roughly a third smaller, which matters when the artisan is
    uploading over a 2G connection and paying for the megabytes.
    """
    name = f"{listing_id}_{kind}.jpg"
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    data = buf.getvalue()

    s3 = _s3()
    if s3:
        c = _config()
        try:
            s3.put_object(Bucket=c["bucket"], Key=name, Body=data,
                          ContentType="image/jpeg",
                          CacheControl="public, max-age=31536000, immutable")
            base = c["public"].rstrip("/") or f"{c['endpoint'].rstrip('/')}/{c['bucket']}"
            return f"{base}/{name}"
        except Exception as e:
            # Falling through to disk keeps the artisan's work rather than losing the
            # photograph because a bucket policy is wrong. The URL will be local, and
            # /health reports the storage backend so this is visible.
            log.error("media: S3 upload failed (%s: %s); wrote to disk instead",
                      type(e).__name__, e)

    os.makedirs(LOCAL_DIR, exist_ok=True)
    with open(os.path.join(LOCAL_DIR, name), "wb") as f:
        f.write(data)
    return f"{PUBLIC_BASE}/media/{name}"


def save_bytes(data: bytes, name: str, content_type: str) -> str:
    """
    Store a file we are not re-encoding, and return a URL that survives a redeploy.

    `save` above exists for photographs: it takes a PIL image and writes JPEG, which
    is right for a product picture and wrong for anything else. A packaging video is
    already an H.264 file the phone produced; decoding and re-encoding it here would
    cost minutes of CPU and lose quality for no reason, so the bytes go up as they
    came off the camera.

    Deliberately the same bucket, the same public base and the same
    fall-through-to-disk behaviour as `save`, so there is one storage story to reason
    about rather than two. `name` is the full object key including its extension,
    because the caller knows what it recorded and this function should not guess.

    Videos are NOT marked immutable for a year like photographs are. A packaging video
    is evidence attached to one order and may need replacing if the artisan retakes it,
    so a shorter cache window keeps a corrected file from being served stale.
    """
    s3 = _s3()
    if s3:
        c = _config()
        try:
            s3.put_object(Bucket=c["bucket"], Key=name, Body=data,
                          ContentType=content_type,
                          CacheControl="public, max-age=86400")
            base = c["public"].rstrip("/") or f"{c['endpoint'].rstrip('/')}/{c['bucket']}"
            return f"{base}/{name}"
        except Exception as e:
            log.error("media: S3 upload of %s failed (%s: %s); wrote to disk instead",
                      name, type(e).__name__, e)

    os.makedirs(LOCAL_DIR, exist_ok=True)
    with open(os.path.join(LOCAL_DIR, name), "wb") as f:
        f.write(data)
    return f"{PUBLIC_BASE}/media/{name}"


def hosted() -> bool:
    """
    Are we running on somebody else's container rather than a developer's laptop?

    Each of these is set by the platform itself, not by us, so it cannot be forgotten
    the way our own flag could be. It decides whether "your data is on an ephemeral
    disk" is a warning worth showing or just noise on a laptop.
    """
    return bool(os.getenv("RENDER") or os.getenv("FLY_APP_NAME")
                or os.getenv("RAILWAY_ENVIRONMENT"))


def status() -> dict:
    """For /health, so a deployment can be checked without uploading a photo."""
    # The parentheses are the whole point. Written without them - as this was - `and`
    # binds tighter than `or`, so the condition read `(local and RENDER) or FLY`,
    # which warned about local storage on Fly even when the bucket was configured,
    # and never warned on Railway at all.
    return {
        "backend": backend(),
        "missing": missing(),
        "warning": ("Images are on the container filesystem and will be lost on the "
                    "next deploy. Set MEDIA_S3_* for anything but local development.")
        if (backend() == "local" and hosted())
        else "",
    }
