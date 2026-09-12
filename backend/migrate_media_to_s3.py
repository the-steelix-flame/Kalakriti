#!/usr/bin/env python
"""
Move the product photographs off the laptop and into the bucket.

Configuring `MEDIA_S3_*` only changes where the *next* photograph goes. Every image
taken before that is still a file under `backend/media/`, and every listing still
holds a URL pointing at whatever hostname was serving it at the time - a tunnel that
has since died, or a container filesystem that is wiped on the next deploy. The
listings survive the move to a hosted backend and their pictures do not.

    python migrate_media_to_s3.py --dry-run     # report what would move
    python migrate_media_to_s3.py               # upload, then rewrite the URLs

This matters more than it looks. The storefront page is a dead photograph without it,
and the Provenance Passport page is worse than dead: its entire claim is "here is the
photograph as taken, and here is the one buyers see", and two broken images make that
claim unverifiable while still appearing to be made.

Safe to run more than once. An object already in the bucket is uploaded again - the
key is derived from the filename, so it overwrites itself rather than multiplying -
and a URL already pointing at the bucket is left alone.
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import db
import media


def _public_base() -> str:
    c = media._config()
    return (c["public"].rstrip("/")
            or f"{c['endpoint'].rstrip('/')}/{c['bucket']}")


def run() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would move, change nothing")
    ap.add_argument("--dir", default=media.LOCAL_DIR,
                    help=f"local media directory (default: {media.LOCAL_DIR})")
    args = ap.parse_args()

    missing = media.missing()
    if missing:
        print("Object storage is not configured, so there is nowhere to move the "
              "images to.\nSet these in backend/.env first:")
        for name in missing:
            print(f"  {name}")
        print("\ndocs/DEPLOY.md section 2 has the four minutes of clicking that "
              "produces them.")
        return 1

    s3 = media._s3()
    if s3 is None:
        print("boto3 is not installed, so the bucket cannot be reached.\n"
              "  pip install boto3")
        return 1

    base = _public_base()
    bucket = media._config()["bucket"]
    print(f"bucket   {bucket}")
    print(f"public   {base}")
    print(f"local    {os.path.abspath(args.dir)}\n")

    files = sorted(f for f in os.listdir(args.dir)) if os.path.isdir(args.dir) else []
    if not files:
        print(f"no files under {args.dir}; nothing to upload")

    uploaded, failed = 0, 0
    for name in files:
        path = os.path.join(args.dir, name)
        if not os.path.isfile(path):
            continue
        if args.dry_run:
            kb = os.path.getsize(path) // 1024
            print(f"  would upload  {name}  ({kb} KB)")
            uploaded += 1
            continue
        try:
            with open(path, "rb") as f:
                s3.put_object(Bucket=bucket, Key=name, Body=f.read(),
                              ContentType="image/jpeg",
                              CacheControl="public, max-age=31536000, immutable")
            uploaded += 1
            print(f"  uploaded  {name}")
        except Exception as exc:                             # noqa: BLE001
            failed += 1
            print(f"  FAILED    {name}  {type(exc).__name__}: {exc}")

    # Rewrite the stored URLs. Only the filename is carried across: the old host in
    # the URL is exactly the part that has stopped being true.
    s = db.session()
    rewritten = 0
    try:
        for lst in s.query(db.Listing).all():
            changed = False
            for field in ("image_url", "raw_url"):
                url = getattr(lst, field) or ""
                if not url or url.startswith(base):
                    continue
                if "/media/" not in url:
                    continue
                new = f"{base}/{url.rsplit('/', 1)[-1]}"
                if args.dry_run:
                    print(f"  would rewrite  {lst.id}.{field}\n"
                          f"      {url}\n   -> {new}")
                else:
                    setattr(lst, field, new)
                changed = True
            # vision.thumbUrl is stored inside a JSON blob rather than a column, and
            # the app reads it on every list screen, so it has to move too.
            vis = dict(lst.vision or {})
            thumb = vis.get("thumbUrl") or ""
            if thumb and not thumb.startswith(base) and "/media/" in thumb:
                if not args.dry_run:
                    vis["thumbUrl"] = f"{base}/{thumb.rsplit('/', 1)[-1]}"
                    lst.vision = vis
                changed = True
            if changed:
                rewritten += 1
        if not args.dry_run:
            s.commit()
    finally:
        s.close()

    if args.dry_run:
        print(f"\nwould upload {uploaded} file(s) and rewrite the URLs on "
              f"{rewritten} listing(s)")
        print("dry run: nothing was changed.")
    else:
        print(f"\nuploaded {uploaded} file(s); rewrote the URLs on "
              f"{rewritten} listing(s)"
              + (f"; {failed} upload(s) failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
