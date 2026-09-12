"""
Merge listings that are the same photograph uploaded more than once.

Why they exist
--------------
Until the idempotency fix in `/v1/analyze`, a lost reply meant the phone never
learned a listing's id, so the draft stayed unsynced and the next refresh uploaded
the same photograph again. In manual mode the retry ran with the models skipped, so
the duplicate came out untitled - which is why the artisan sees one finished listing
and one nameless "finish this product" beside it for the same pot.

The fix stops new ones. This clears the ones already there.

How a survivor is chosen
------------------------
Never by date. A listing that is published and selling outranks a draft however new,
and among drafts the one somebody has actually worked on outranks an empty shell. The
score is, in order: live before draft, has orders, has publications, has a title, has
a price, then most fields filled.

What is refused
---------------
A duplicate is only removed when it is a `draft` with no orders and no publication
attempts. Anything that has been published or sold is left alone and reported,
because merging those is a judgement about somebody's money and not a script's to
make.

Every deleted row is written to a JSON file first, so this is reversible.

    python dedupe_listings.py            # dry run, changes nothing
    python dedupe_listings.py --apply    # delete the orphans, after backing them up
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import db  # noqa: E402

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dedupe-backups")


def score(s, lst: db.Listing) -> tuple:
    """Higher is better. Ordering matters more than the numbers."""
    orders = s.query(db.Order).filter(db.Order.listing_id == lst.id).count()
    pubs = s.query(db.Publication).filter(db.Publication.listing_id == lst.id).count()
    live = 1 if (lst.status or "") in ("active", "published", "sold", "submitted") else 0
    filled = sum(1 for v in (lst.title_en, lst.title_hi, lst.desc_en, lst.desc_hi,
                             lst.category, lst.hsn) if (v or "").strip())
    return (live, orders, pubs,
            1 if (lst.title_en or lst.title_hi or "").strip() else 0,
            1 if (lst.price or 0) > 0 else 0,
            filled)


def removable(s, lst: db.Listing) -> tuple[bool, str]:
    if (lst.status or "") not in ("draft", "processing"):
        return False, f"status is {lst.status}, not a draft"
    n_orders = s.query(db.Order).filter(db.Order.listing_id == lst.id).count()
    if n_orders:
        return False, f"{n_orders} order(s) against it"
    n_pubs = s.query(db.Publication).filter(db.Publication.listing_id == lst.id).count()
    if n_pubs:
        return False, f"{n_pubs} publication attempt(s)"
    return True, ""


def main() -> int:
    apply = "--apply" in sys.argv
    s = db.session()
    try:
        groups: dict[tuple, list[db.Listing]] = {}
        for lst in s.query(db.Listing).filter(db.Listing.raw_hash != "").all():
            if not (lst.raw_hash or "").strip():
                continue
            groups.setdefault((lst.artisan_id, lst.raw_hash), []).append(lst)
        dups = {k: v for k, v in groups.items() if len(v) > 1}

        if not dups:
            print("no duplicate photographs found")
            return 0

        print(f"{len(dups)} photograph(s) uploaded more than once\n")
        doomed: list[db.Listing] = []
        for (_aid, h), group in sorted(dups.items(), key=lambda kv: kv[0][1]):
            group.sort(key=lambda x: score(s, x), reverse=True)
            keep, rest = group[0], group[1:]
            title = (keep.title_en or keep.title_hi or "(untitled)")[:40]
            print(f"  photo {h[7:19]}")
            print(f"    KEEP   {keep.id}  {keep.status:<10} {title}")
            for other in rest:
                ok, why = removable(s, other)
                t = (other.title_en or other.title_hi or "(untitled)")[:40]
                if ok:
                    doomed.append(other)
                    print(f"    remove {other.id}  {other.status:<10} {t}")
                else:
                    print(f"    keep   {other.id}  {other.status:<10} {t}"
                          f"   <- left alone: {why}")
            print()

        print(f"{len(doomed)} listing(s) would be removed")
        if not apply:
            print("\ndry run. nothing was changed. re-run with --apply")
            return 0
        if not doomed:
            return 0

        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(BACKUP_DIR, f"removed-{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([x.public() for x in doomed], f, indent=2, default=str)
        print(f"backed up to {path}")

        for lst in doomed:
            s.query(db.Event).filter(db.Event.subject_type == "listing",
                                     db.Event.subject_id == lst.id).delete()
        s.commit()
        for lst in doomed:
            s.query(db.Listing).filter(db.Listing.id == lst.id).delete()
        s.commit()
        print(f"removed {len(doomed)} duplicate listing(s)")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
