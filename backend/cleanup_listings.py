"""
Remove listing rows that carry no work, and nothing else.

Why this exists
---------------
Yesterday's create screen could not fail. `/v1/analyze` held the request open while a
congested vision endpoint queued, the client gave up, and the artisan tapped again -
and every tap had already written a `processing` row. The result was 22 rows with no
title, no photograph and no price, all dated the same evening, showing up on the home
screen as "Finish this product" for something that does not exist.

`dedupe_listings.py` does not catch these. It matches listings by the hash of their
photograph, and these have no photograph to hash.

What counts as removable
------------------------
A row is only deleted when it holds nothing a person could have typed or taken:

    no title (English or Hindi), no description, no photograph, no price

and nothing downstream depends on it:

    no orders, no publications, no settlement lines, no passport

Anything else is left alone, including a listing that is merely untitled but has a
photograph - that is unfinished work, not junk, and it belongs to somebody.

Every row is written to a JSON backup before it goes, because "it had nothing in it"
is a judgement made by this script and the artisan is the one who gets to be wrong
about it.

    python cleanup_listings.py --dry-run     # look first
    python cleanup_listings.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import db  # noqa: E402

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "cleanup-backups")


def _blank(l) -> bool:
    """Does this row hold anything a person put there?"""
    return not any([
        (l.title_en or "").strip(),
        (l.title_hi or "").strip(),
        (l.desc_en or "").strip(),
        (l.desc_hi or "").strip(),
        (l.image_url or "").strip(),
        (l.raw_url or "").strip(),
        (l.transcript or "").strip(),
        float(l.price or 0) > 0,
    ])


def _dependencies(s, lid: str) -> list[str]:
    """Anything that would be orphaned by deleting this listing."""
    found = []
    n = s.query(db.Order).filter(db.Order.listing_id == lid).count()
    if n:
        found.append(f"{n} order(s)")
    for name, model in (("publication", "Publication"),
                        ("settlement line", "SettlementLine"),
                        ("view", "ListingView")):
        cls = getattr(db, model, None)
        if cls is None:
            continue
        col = getattr(cls, "listing_id", None)
        if col is None:
            continue
        c = s.query(cls).filter(col == lid).count()
        if c and name != "view":          # views are counters, not work
            found.append(f"{c} {name}(s)")
    return found


def run() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would go, change nothing")
    args = ap.parse_args()

    s = db.session()
    try:
        rows = s.query(db.Listing).order_by(db.Listing.created_at).all()
        print(f"{len(rows)} listings in the database\n")

        removable, kept_blocked = [], []
        for l in rows:
            if not _blank(l):
                continue
            if (l.passport_id or "").strip():
                kept_blocked.append((l, ["a passport"]))
                continue
            deps = _dependencies(s, l.id)
            if deps:
                kept_blocked.append((l, deps))
            else:
                removable.append(l)

        for l, deps in kept_blocked:
            print(f"  keeping  {l.id}  empty but has {', '.join(deps)}")
        if kept_blocked:
            print()

        if not removable:
            print("nothing to remove: every empty row has something depending on it, "
                  "or there are no empty rows.")
            return 0

        print(f"{len(removable)} row(s) hold no title, description, photograph, "
              f"transcript or price:")
        for l in removable:
            print(f"  {l.id}  status={l.status:<11} created={l.created_at} "
                  f"artisan={l.artisan_id or l.guest_token or '(nobody)'}")

        if args.dry_run:
            print("\ndry run: nothing was changed.")
            return 0

        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = os.path.join(BACKUP_DIR, f"empty-listings-{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([l.public() for l in removable], f, indent=2, default=str,
                      ensure_ascii=False)
        print(f"\nbacked up to {path}")

        # The audit trail outlives the row it describes, so the events go too -
        # otherwise the events table accumulates entries pointing at nothing.
        gone = 0
        for l in removable:
            s.query(db.Event).filter(db.Event.subject_type == "listing",
                                     db.Event.subject_id == l.id).delete()
            cls = getattr(db, "ListingView", None)
            if cls is not None:
                s.query(cls).filter(cls.listing_id == l.id).delete()
            s.delete(l)
            gone += 1
        s.commit()
        print(f"removed {gone} empty listing(s)")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(run())
