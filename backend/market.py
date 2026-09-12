"""
Our own marketplace: every artisan's work in one browsable shop, with a real
checkout.

Why this exists, and why it is not a retreat
--------------------------------------------
Amazon, Flipkart, GeM and ONDC all require a GST number and PAN before a seller
account can exist. Nobody on this team holds one, so those four cannot be live for a
demo no matter how correct the integration code is - and it is correct; it sits ready
in `channels.py` and `ondc.py` waiting only for credentials.

What was always available is the channel nobody has to approve: the artisan's own
storefront. This module turns it from a set of deep links into an actual shop with a
front door. That is the honest version of "we have a marketplace" - it is ours, it is
real, and every listing in it was made by a real seller in the app.

On the payment being "mock"
---------------------------
It is not, and it does not need to be. Razorpay **test mode** is a real API, real
order objects, real signatures and real webhooks, with money that does not exist.
That is strictly better than a mock for a demo, because nothing is being pretended:
the same code path runs in production, and only the key changes. A hand-rolled fake
would have to be torn out later and would prove nothing about whether the real thing
works.

So the checkout below is genuine Razorpay. The card numbers are test cards, the
rupees are imaginary, and the integration is the one that ships.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
from string import Template
from typing import Any

import db

CURRENCY = "INR"


def listings(s, *, q: str = "", category: str = "", limit: int = 60) -> list[dict]:
    """
    Everything currently for sale.

    Drafts are excluded, and so is anything out of stock - a shop that shows you
    things you cannot buy is wasting the one screen of attention you get.

    A listing with no photograph is excluded for the same reason. Nobody buys a grey
    rectangle, and one of these was sitting in the shop as a broken image: an old test
    row that had been published to two channels without a picture ever being taken. It
    is filtered here rather than deleted or forced back to draft, because the row is
    real and its publications are real - it is only unfit for a shelf.
    """
    query = (s.query(db.Listing)
             .filter(db.Listing.status.in_(("active", "published")),
                     db.Listing.quantity > 0,
                     db.Listing.image_url != "",
                     db.Listing.image_url.isnot(None)))
    if category:
        query = query.filter(db.Listing.category == category)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(db.Listing.title_en.ilike(like)
                             | db.Listing.title_hi.ilike(like))
    rows = query.order_by(db.Listing.updated_at.desc()).limit(limit).all()

    out = []
    for r in rows:
        a = s.get(db.Artisan, r.artisan_id) if r.artisan_id else None
        out.append({
            "id": r.id,
            "title": r.title_en or r.title_hi or "Handmade piece",
            "titleHi": r.title_hi or "",
            "price": r.price or 0,
            "currency": r.currency or CURRENCY,
            "imageUrl": r.image_url or "",
            "category": r.category or "",
            "quantity": r.quantity or 0,
            "maker": (a.business_name or a.full_name) if a else "",
            "makerCluster": (a.cluster or "") if a else "",
            "url": f"/l/{r.id}",
        })
    return out


def item(s, lid: str) -> dict | None:
    """
    One product, for the buyer's detail screen inside the app.

    A superset of what `listings` returns: the description, the dimensions and the
    seller of record, which a grid tile has no room for but a buyer deciding to spend
    money does want. Returns None rather than raising, so the route decides the status
    code.

    The same shelf rules apply. A draft, an out-of-stock row or one with no photograph
    is not for sale, and answering with it would let the app offer a buy button for
    something the order endpoint will refuse.
    """
    r = s.get(db.Listing, lid)
    if r is None:
        return None
    if (r.status not in ("active", "published") or (r.quantity or 0) <= 0
            or not (r.image_url or "").strip()):
        return None

    a = s.get(db.Artisan, r.artisan_id) if r.artisan_id else None

    # Who the buyer is actually paying. A listing sold through a cluster is sold by the
    # cluster owner - they hold the GSTIN and are the seller of record - and the maker
    # is still named, because that is the whole point of the platform.
    seller, seller_kind = "", ""
    if r.cluster_id:
        cl = s.get(db.Cluster, r.cluster_id)
        if cl is not None:
            seller, seller_kind = cl.name or "", "cluster"
    if not seller and a is not None:
        seller = a.business_name or a.full_name or ""
        seller_kind = "artisan"

    return {
        "id": r.id,
        "title": r.title_en or r.title_hi or "Handmade piece",
        "titleHi": r.title_hi or "",
        "description": r.desc_en or "",
        "descriptionHi": r.desc_hi or "",
        "price": r.price or 0,
        "currency": r.currency or CURRENCY,
        "imageUrl": r.image_url or "",
        "category": r.category or "",
        "hsn": r.hsn or "",
        "quantity": r.quantity or 0,
        "maker": (a.business_name or a.full_name) if a else "",
        "makerCluster": (a.cluster or "") if a else "",
        "seller": seller,
        "sellerKind": seller_kind,
        "weightG": r.weight_g or 0,
        "dimensionsCm": {"length": r.length_cm or 0, "breadth": r.breadth_cm or 0,
                         "height": r.height_cm or 0},
        "passportId": r.passport_id or "",
        "url": f"/l/{r.id}",
    }


def categories(s) -> list[str]:
    rows = (s.query(db.Listing.category)
            .filter(db.Listing.status.in_(("active", "published")),
                    db.Listing.quantity > 0,
                    db.Listing.image_url != "",
                    db.Listing.image_url.isnot(None),
                    db.Listing.category != "")
            .distinct().all())
    return sorted({c for (c,) in rows if c})


# ────────────────────────────────────────────────────── payment verification

def verify_payment(s, order: db.Order, *, razorpay_order_id: str,
                   razorpay_payment_id: str, signature: str) -> dict[str, Any]:
    """
    Confirm a payment actually happened, without waiting for the webhook.

    Two independent checks, because either alone has a hole:

      1. The signature. Razorpay returns HMAC-SHA256 of "order_id|payment_id" keyed
         with our secret. Only Razorpay and we can produce it, so a forged success
         posted straight at this endpoint fails here.

      2. The payment itself, fetched from Razorpay. The signature proves the message
         is authentic; it does not prove the payment was captured, nor that the
         amount matches what we asked for. A buyer who authorises 1 rupee against a
         2,000 rupee order produces a perfectly valid signature.

    The webhook remains the backstop for the case this path cannot cover: a buyer who
    pays and then closes the browser before the callback runs. Both routes converge
    on the same status change, and both are idempotent.
    """
    key_id = os.getenv("RAZORPAY_KEY_ID", "")
    secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    if not (key_id and secret):
        return {"ok": False, "error": "not_configured",
                "why": "No Razorpay keys are set on the server."}

    expected = hmac.new(secret.encode(),
                        f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        db.log_event(s, "order", order.id, "error",
                     detail="razorpay signature did not verify")
        return {"ok": False, "error": "bad_signature",
                "why": "That payment confirmation did not come from Razorpay."}

    import requests                                          # noqa: PLC0415
    try:
        r = requests.get(f"https://api.razorpay.com/v1/payments/{razorpay_payment_id}",
                         auth=(key_id, secret), timeout=30)
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "error": "unreachable",
                "why": f"Could not reach Razorpay to confirm: {type(e).__name__}"}
    if r.status_code >= 300:
        return {"ok": False, "error": "lookup_failed",
                "why": f"Razorpay returned HTTP {r.status_code} for that payment."}

    pay = r.json()
    paise = int(pay.get("amount") or 0)
    expected_paise = int(round(float(order.amount or 0) * 100))
    if paise != expected_paise:
        db.log_event(s, "order", order.id, "error",
                     detail=f"amount mismatch: paid {paise}, expected {expected_paise}")
        return {"ok": False, "error": "amount_mismatch",
                "why": f"The payment was for {paise / 100:.2f}, and this order is "
                       f"{expected_paise / 100:.2f}."}

    status = str(pay.get("status") or "")
    if status not in ("captured", "authorized"):
        return {"ok": False, "error": "not_captured",
                "why": f"Razorpay reports this payment as {status or 'unknown'}."}

    if order.payment_status != "paid":
        db.log_event(s, "order", order.id, "status", order.status, "paid",
                     f"razorpay {razorpay_payment_id} verified, {status}")
        order.payment_status = "paid"
        order.status = "paid"
        order.payment_ref = razorpay_payment_id
        order.payment_provider = "razorpay"
        s.commit()

    return {"ok": True, "orderId": order.id, "status": order.status,
            "paymentStatus": order.payment_status, "paymentId": razorpay_payment_id,
            "method": pay.get("method", ""), "testMode": not key_id.startswith("rzp_live")}


# ───────────────────────────────────────────────────────────── the shop page

_PAGE = Template("""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kalakriti - handmade, direct from the maker</title>
<style>
 :root{--ink:#171331;--mid:#4A4468;--soft:#7C7596;--line:#E9E1D8;--bg:#FBF8F4;
       --primary:#D4541F;--deep:#A93C12;--money:#0B7A54;--indigo:#2E2A6B}
 *{box-sizing:border-box}
 body{margin:0;font:16px/1.6 'Segoe UI',system-ui,sans-serif;background:var(--bg);
      color:var(--ink)}
 header{background:var(--indigo);color:#fff;padding:26px 20px 30px}
 .wrap{max-width:1080px;margin:0 auto}
 h1{margin:0;font-size:30px;letter-spacing:.5px}
 .sub{color:#C9C4E8;margin-top:4px;font-size:15px}
 .bar{background:#fff;border-bottom:1px solid var(--line);padding:14px 20px;
      position:sticky;top:0;z-index:5}
 .bar form{display:flex;gap:10px;max-width:1080px;margin:0 auto}
 input[type=search]{flex:1;padding:12px 14px;border:1px solid var(--line);
      border-radius:12px;font-size:16px}
 button{padding:12px 20px;border:0;border-radius:12px;background:var(--primary);
      color:#fff;font-size:15px;font-weight:700;cursor:pointer}
 .cats{display:flex;gap:8px;flex-wrap:wrap;margin:16px auto 0;max-width:1080px;
       padding:0 20px}
 .cat{padding:7px 13px;border-radius:99px;background:#fff;border:1px solid var(--line);
      font-size:13px;color:var(--mid);text-decoration:none}
 .cat.on{background:var(--indigo);color:#fff;border-color:var(--indigo)}
 .grid{display:grid;gap:18px;padding:22px 20px 60px;max-width:1080px;margin:0 auto;
       grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}
 .card{background:#fff;border:1px solid var(--line);border-radius:18px;overflow:hidden;
       text-decoration:none;color:inherit;display:flex;flex-direction:column}
 .card img{width:100%;aspect-ratio:1;object-fit:cover;background:#F4EEE6}
 .card .b{padding:13px 14px 16px;display:flex;flex-direction:column;gap:3px;flex:1}
 .t{font-weight:700;line-height:1.3}
 .m{font-size:13px;color:var(--soft)}
 .p{font-size:20px;font-weight:800;color:var(--money);margin-top:auto;padding-top:8px}
 .empty{padding:70px 20px;text-align:center;color:var(--soft)}
 .note{background:#FDEEE4;border:1px solid #F3DCCB;border-radius:14px;
       padding:14px 16px;margin:20px auto 0;max-width:1040px;font-size:14px;
       color:var(--mid)}
 footer{padding:30px 20px 60px;text-align:center;color:var(--soft);font-size:13px}
</style></head><body>
<header><div class="wrap">
  <h1>Kalakriti</h1>
  <div class="sub">Handmade, direct from the maker</div>
</div></header>

<div class="bar"><form method="get" action="/market">
  <input type="search" name="q" value="$q" placeholder="Search for a saree, a pot, a scarf…">
  <button>Search</button>
</form></div>

<div class="cats">$cats</div>

$note

<div class="grid">$cards</div>

<footer>$count piece(s) listed &middot; every one made by a seller using Kalakriti</footer>
</body></html>""")


def page(s, *, q: str = "", category: str = "") -> str:
    """The shop front. Plain server-rendered HTML so it opens on anything."""
    e = html.escape
    items = listings(s, q=q, category=category)
    cats = categories(s)

    cat_html = ['<a class="cat%s" href="/market">All</a>'
                % ("" if category else " on")]
    for c in cats:
        cat_html.append(
            f'<a class="cat{" on" if c == category else ""}" '
            f'href="/market?category={e(c)}">{e(c)}</a>')

    if items:
        cards = "".join(
            f'<a class="card" href="/l/{e(i["id"])}">'
            f'<img src="{e(i["imageUrl"])}" alt="{e(i["title"])}" loading="lazy">'
            f'<div class="b"><div class="t">{e(i["title"])}</div>'
            f'<div class="m">{e(i["maker"] or "Kalakriti seller")}</div>'
            f'<div class="p">&#8377;{i["price"]:,.0f}</div></div></a>'
            for i in items)
    else:
        cards = ('<div class="empty">Nothing is listed yet. '
                 'An artisan has to publish a piece from the app first.</div>')

    # No test-mode banner on the shop front. It is the first thing a visitor reads
    # and it is about our plumbing, not about the craft. The product page still says
    # it plainly at the point it matters - next to the card field, where somebody is
    # about to type a number and deserves to know it will not be charged.
    return _PAGE.substitute(q=e(q), cats="".join(cat_html), cards=cards,
                            note="", count=len(items))
