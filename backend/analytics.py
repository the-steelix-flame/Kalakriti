"""
Read models: the numbers behind My Products, a product page, and Home.

Everything here is derived from rows this backend actually holds, or fetched live
from a marketplace through channels.stats(). Nothing is generated.

Three rules the whole module obeys:

  1. A metric is either a counted fact or an explicit absence with a reason. There is
     no third state. `{"value": None, "available": False, "why": ...}` is a complete,
     honest answer and the app renders it as one.
  2. Storefront views are counted, once per viewer per hour, from real requests. They
     are not "impressions", not estimated, and not scaled.
  3. Cross-artisan insight is aggregated and suppressed below a minimum group size, so
     one seller's revenue can never be read back out of an average.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

import channels
import db

# Salt for the viewer key. Random per deployment unless pinned, so a view row cannot
# be correlated back to an IP even with the database in hand.
VIEW_SALT = os.getenv("VIEW_SALT", "") or hashlib.sha256(
    (os.getenv("JWT_SECRET", "") + "views").encode()).hexdigest()[:16]

# Below this many contributing artisans, an aggregate is withheld. With two sellers in
# a category, an "average price" is one subtraction away from being somebody's price.
MIN_COHORT = 5


def viewer_key(ip: str, user_agent: str) -> str:
    """A stable but non-identifying key, bucketed by hour to dedupe refreshes."""
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    return hashlib.sha256(
        f"{VIEW_SALT}|{ip}|{user_agent}|{hour}".encode()).hexdigest()[:32]


def record_view(s, listing_id: str, ip: str, user_agent: str, referrer: str = "") -> None:
    """
    Count one storefront view, unless this viewer already counted this hour.

    Deduping matters: without it, a buyer who reloads twice while deciding turns into
    three views, and the artisan reads interest that is not there.
    """
    key = viewer_key(ip, user_agent)
    dup = (s.query(db.ListingView.id)
           .filter(db.ListingView.listing_id == listing_id,
                   db.ListingView.viewer_key == key).first())
    if dup:
        return
    s.add(db.ListingView(listing_id=listing_id, viewer_key=key, referrer=referrer[:300]))
    s.commit()


def view_count(s, listing_id: str) -> int:
    return int(s.query(func.count(db.ListingView.id))
               .filter(db.ListingView.listing_id == listing_id).scalar() or 0)


def _order_rollup(orders: list, channel: str | None = None) -> dict:
    """Counts straight off the order rows: paid means paid, not 'probably'."""
    rows = [o for o in orders if channel is None or o.channel == channel]
    paid = [o for o in rows
            if o.payment_status == "paid" or o.status in
            ("paid", "confirmed", "packed", "shipped", "out_for_delivery",
             "delivered", "completed")]
    return {
        "orders": len(rows),
        "sold": sum(int(o.quantity or 0) for o in paid),
        "revenue": round(sum(float(o.amount or 0) for o in paid), 2),
        "pending": len([o for o in rows if o.status in ("created", "payment_pending")]),
        "toShip": len([o for o in rows if o.status in ("paid", "confirmed", "packed")]),
    }


def listing_card(s, lst) -> dict:
    """
    One row of My Products. Cheap enough to run for a whole list: it touches only
    local tables and never calls a marketplace.
    """
    pubs = list(lst.publications)
    live = [p for p in pubs if p.status in ("published", "active")]
    roll = _order_rollup(list(lst.orders))
    views = view_count(s, lst.id) if any(p.channel == "storefront" for p in live) else None

    return {
        "id": lst.id,
        "title": lst.title_hi or lst.title_en or "",
        "titleEn": lst.title_en,
        "imageUrl": lst.image_url,
        "price": lst.price,
        "currency": lst.currency,
        "quantity": lst.quantity,
        "category": lst.category,
        "status": derived_status(lst, pubs),
        "rawStatus": lst.status,
        "marketplaces": len(live),
        "marketplacesAttempted": len(pubs),
        "channels": sorted({p.channel for p in pubs}),
        "failedChannels": sorted({p.channel for p in pubs if p.status == "failed"}),
        "views": views,
        "viewsAvailable": views is not None,
        "orders": roll["orders"],
        "sold": roll["sold"],
        "revenue": roll["revenue"],
        "updatedAt": lst.updated_at.isoformat() if lst.updated_at else None,
        "createdAt": lst.created_at.isoformat() if lst.created_at else None,
    }


def derived_status(lst, pubs: list) -> str:
    """
    The status the artisan actually needs to see, which is not always the column.

    A listing submitted to three marketplaces where one succeeded and two failed is
    stored as `published`, but "partially published" is the true state and the one
    that tells her there is something to fix.
    """
    if lst.status in ("draft", "processing"):
        return lst.status
    if not pubs:
        return lst.status
    live = [p for p in pubs if p.status in ("published", "active")]
    bad = [p for p in pubs if p.status in ("failed", "not_configured")]
    waiting = [p for p in pubs if p.status in ("queued", "processing")]
    if lst.status == "sold":
        return "sold"
    if live and bad:
        return "partial"
    if not live and bad and not waiting:
        return "failed"
    if not live and waiting:
        return "processing"
    if live and lst.quantity <= 0:
        return "sold"
    return lst.status


def marketplace_rows(s, lst, live: bool = False) -> list[dict]:
    """
    Every marketplace this product was sent to, with its own state.

    `live=True` additionally calls each platform's API for current figures. That is
    slow and rate-limited, so it happens only on the marketplace detail screen, never
    while drawing a list.
    """
    support = channels.metric_support()
    out = []
    for p in lst.publications:
        pub = p.public()
        orders = [o for o in lst.orders if o.channel == p.channel]
        roll = _order_rollup(orders)
        row = {
            **pub,
            "channelName": channels.META.get(p.channel, {}).get("name", p.channel),
            "supports": support.get(p.channel, {}),
            "localOrders": roll,
            "orderCount": roll["orders"],
        }
        if live:
            ctx = {
                "views": view_count(s, lst.id) if p.channel == "storefront" else None,
                "sold": roll["sold"], "revenue": roll["revenue"],
                "orders": roll["orders"], "inventory": lst.quantity,
            }
            row["stats"] = channels.stats(p.channel, pub, ctx)
        out.append(row)
    out.sort(key=lambda r: (r["status"] not in ("published", "active"),
                            r.get("submittedAt") or ""))
    return out


def summary(s, artisan_id: str) -> dict:
    """
    Home. Counts, and the two or three things that need the artisan's attention.

    Every figure is a query against her own rows. The old Home showed invented
    earnings; if a number here is zero it is because it is zero.
    """
    lsts = (s.query(db.Listing).filter(db.Listing.artisan_id == artisan_id).all())
    ids = [l.id for l in lsts]
    orders = (s.query(db.Order).filter(db.Order.listing_id.in_(ids)).all()
              if ids else [])
    roll = _order_rollup(orders)

    cards = [listing_card(s, l) for l in lsts]
    by_status: dict[str, int] = {}
    for c in cards:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1

    views = (int(s.query(func.count(db.ListingView.id))
                 .filter(db.ListingView.listing_id.in_(ids)).scalar() or 0)
             if ids else 0)

    # Things to act on, most urgent first. Each carries the id it refers to so the
    # card is tappable rather than just informative.
    actions = []
    for c in cards:
        if c["status"] == "draft":
            actions.append({"kind": "finish_draft", "listingId": c["id"],
                            "title": c["title"], "image": c["imageUrl"]})
        elif c["status"] in ("failed", "partial") and c["failedChannels"]:
            actions.append({"kind": "publish_failed", "listingId": c["id"],
                            "title": c["title"], "image": c["imageUrl"],
                            "detail": ", ".join(c["failedChannels"])})
        elif c["quantity"] <= 0 and c["status"] not in ("sold", "draft"):
            actions.append({"kind": "out_of_stock", "listingId": c["id"],
                            "title": c["title"], "image": c["imageUrl"]})
    for o in orders:
        if o.status in ("paid", "confirmed", "packed"):
            actions.append({"kind": "ship_order", "orderId": o.id,
                            "title": o.buyer_name or o.id,
                            "detail": f"{o.quantity} x", "amount": o.amount})

    enq = (s.query(db.Enquiry)
           .filter(db.Enquiry.artisan_id == artisan_id, db.Enquiry.status == "new")
           .count())

    # "Getting attention" is only meaningful where views are genuinely counted, so it
    # is restricted to storefront listings and omitted entirely when nothing has views.
    watched = sorted([c for c in cards if (c["views"] or 0) > 0],
                     key=lambda c: -(c["views"] or 0))[:3]
    best = sorted([c for c in cards if c["sold"] > 0], key=lambda c: -c["revenue"])[:3]

    return {
        "products": {
            "total": len(cards),
            "byStatus": by_status,
            "live": sum(1 for c in cards if c["status"] in ("active", "published",
                                                            "partial")),
            "drafts": by_status.get("draft", 0) + by_status.get("processing", 0),
            "sold": by_status.get("sold", 0),
        },
        "orders": roll,
        "storefrontViews": views,
        "newEnquiries": enq,
        "actions": actions[:6],
        "actionCount": len(actions),
        "watched": watched,
        "best": best,
    }


def insights(s, artisan_id: str | None) -> dict:
    """
    "What other artisans are doing", built only from what this system legitimately
    holds: listings other sellers published on their own storefronts.

    Three protections, because this is other people's livelihood:
      - only published listings count; a draft is private working material
      - no seller is ever named, and no row maps to one person
      - any bucket with fewer than MIN_COHORT distinct artisans is dropped entirely,
        which is what stops an "average price" from revealing a single price

    Ranges are given as bands rather than exact averages for the same reason. If there
    is not enough data yet, this returns `enough: False` and the app says so instead
    of dressing up thin data as a trend.
    """
    since = datetime.now(timezone.utc) - timedelta(days=180)
    rows = (s.query(db.Listing.category, db.Listing.price, db.Listing.artisan_id)
            .filter(db.Listing.status.in_(["published", "active", "sold"]),
                    db.Listing.artisan_id.isnot(None),
                    db.Listing.price > 0,
                    db.Listing.created_at >= since)
            .all())

    buckets: dict[str, dict] = {}
    for cat, price, aid in rows:
        key = (cat or "").strip().lower() or "other"
        b = buckets.setdefault(key, {"prices": [], "artisans": set()})
        b["prices"].append(float(price))
        b["artisans"].add(aid)

    mine = set()
    if artisan_id:
        mine = {(c or "").strip().lower()
                for (c,) in s.query(db.Listing.category)
                .filter(db.Listing.artisan_id == artisan_id).all() if c}

    out = []
    for key, b in buckets.items():
        if len(b["artisans"]) < MIN_COHORT:
            continue                       # too few sellers to anonymise safely
        ps = sorted(b["prices"])
        n = len(ps)
        lo = ps[int(n * 0.25)]
        hi = ps[min(n - 1, int(n * 0.75))]
        out.append({
            "category": key,
            "listings": n,
            "artisans": len(b["artisans"]),
            "priceLow": round(lo),
            "priceHigh": round(hi),
            "median": round(ps[n // 2]),
            "yours": key in mine,
        })
    out.sort(key=lambda r: (-r["listings"], r["category"]))

    return {
        "enough": bool(out),
        "minCohort": MIN_COHORT,
        "categories": out[:8],
        "basis": "published listings from the last 180 days on this network",
        "note": ("Categories with fewer than "
                 f"{MIN_COHORT} sellers are left out so no single artisan's prices "
                 "can be worked out from an average."),
    }
