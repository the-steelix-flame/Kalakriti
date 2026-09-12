"""
The ONDC Seller App (BPP) side: how a price gets onto the network, and how an order
gets back.

The thing to understand first, because it changes the shape of everything here
--------------------------------------------------------------------------------
ONDC is not a marketplace with an API we call. It is a protocol - Beckn - and on it
we are a **BPP**, a Seller Platform. Buyer apps (**BAP**s: Paytm, Pincode, Mystore,
ONDC Buyer App) call *us*. There is no ONDC server holding our catalogue and no
webhook telling us we sold something.

That is why `channels.ondc()` alone was never going to produce an order. It pushes
an unsolicited `on_search` at the gateway, which is how a catalogue gets indexed, and
then nothing - because every step after that is a buyer app POSTing to endpoints we
had not built. This module is those endpoints.

The flow, end to end
--------------------
Every exchange is two half-calls. The buyer app POSTs an action to us, we answer
immediately with a bare ACK, and then we POST the real answer back to the `bap_uri`
it gave us. Beckn is asynchronous on purpose: a seller may take seconds to price a
thing, and nobody holds an HTTP connection open for that.

    BAP  --/search-->   us        we ACK, then POST on_search  (our catalogue)
    BAP  --/select-->   us        we ACK, then POST on_select  (the quote)
    BAP  --/init-->     us        we ACK, then POST on_init    (billing, terms)
    BAP  --/confirm-->  us        we ACK, then POST on_confirm (ORDER EXISTS HERE)
    BAP  --/status-->   us        we ACK, then POST on_status  (fulfilment state)

**The price the buyer sees comes from one place: `Listing.price`.** It leaves in
`on_search` as the catalogue price, is restated in `on_select` as a quote with its
breakup, and is restated again in `on_confirm` as what was actually agreed. All three
are generated from the same row, so a price edited in the app is the price ONDC
quotes on the next search. There is no separate catalogue to keep in step.

**The order becomes real in `/confirm`, and only there.** `on_search` is browsing,
`on_select` is asking the price, `on_init` is agreeing terms. None of them is a sale.
`/confirm` is the buyer app saying the buyer has paid and committed, and it is the
one call in the protocol that writes an `Order` row. From that moment it is an
ordinary order: it appears in the Orders tab, it counts toward the artisan's
earnings, and Phase 4's settlement divides it like any other.

What is deliberately strict here
--------------------------------
Stock is decremented inside `/confirm` under the same transaction that writes the
order, and a confirm for more units than remain is refused with a Beckn NACK rather
than accepted and reconciled later. Overselling on a network where the buyer has
already paid is not a problem you fix with an apology.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from typing import Any

import requests

import db

log = logging.getLogger("ondc")

TIMEOUT = 20
DOMAIN = os.getenv("ONDC_DOMAIN", "ONDC:RET10")
CORE_VERSION = os.getenv("ONDC_CORE_VERSION", "2.0.2")
COUNTRY = "IND"
CITY = os.getenv("ONDC_CITY_CODE", "std:0542")          # Varanasi
TTL = "PT30S"


# ────────────────────────────────────────────────────────────── signing

def _priv_key():
    from cryptography.hazmat.primitives.asymmetric import ed25519
    raw = base64.b64decode(os.getenv("ONDC_SIGNING_PRIVATE_KEY", ""))
    return ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])


def sign_request(body: str) -> str:
    """
    The ONDC `Authorization` header: a BLAKE-512 digest of the body, signed Ed25519.

    Shared with `channels.ondc()` in shape but kept here as the canonical copy,
    because every callback this module sends needs it too.
    """
    sub = os.getenv("ONDC_SUBSCRIBER_ID", "")
    ukid = os.getenv("ONDC_UNIQUE_KEY_ID", "1")
    created = int(time.time())
    expires = created + 3600
    digest = base64.b64encode(
        hashlib.blake2b(body.encode(), digest_size=64).digest()).decode()
    signing_string = (f"(created): {created}\n(expires): {expires}\n"
                      f"digest: BLAKE-512={digest}")
    sig = base64.b64encode(_priv_key().sign(signing_string.encode())).decode()
    return (f'Signature keyId="{sub}|{ukid}|ed25519",algorithm="ed25519",'
            f'created="{created}",expires="{expires}",'
            f'headers="(created) (expires) digest",signature="{sig}"')


def configured() -> list[str]:
    """Which variables are still missing. Empty list means ONDC is live."""
    need = ["ONDC_SUBSCRIBER_ID", "ONDC_SIGNING_PRIVATE_KEY", "ONDC_GATEWAY_URL"]
    return [n for n in need if not os.getenv(n)]


# ────────────────────────────────────────────────────── context and replies

def context_for(incoming: dict, action: str) -> dict[str, Any]:
    """
    Build our reply context from theirs.

    `transaction_id` and `message_id` must be echoed exactly - they are how the buyer
    app matches our asynchronous callback to the request it made. Getting this wrong
    is the most common reason a technically correct response is silently dropped.
    """
    ctx = dict(incoming.get("context") or {})
    return {
        "domain": ctx.get("domain", DOMAIN),
        "country": ctx.get("country", COUNTRY),
        "city": ctx.get("city", CITY),
        "action": action,
        "core_version": ctx.get("core_version", CORE_VERSION),
        "version": ctx.get("version", CORE_VERSION),
        "bap_id": ctx.get("bap_id", ""),
        "bap_uri": ctx.get("bap_uri", ""),
        "bpp_id": os.getenv("ONDC_SUBSCRIBER_ID", ""),
        "bpp_uri": os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        "transaction_id": ctx.get("transaction_id", ""),
        "message_id": ctx.get("message_id", ""),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "ttl": TTL,
    }


def ack() -> dict[str, Any]:
    return {"message": {"ack": {"status": "ACK"}}}


def nack(code: str, message: str) -> dict[str, Any]:
    """
    A Beckn refusal, with a reason the buyer app can show its user.

    Used rather than an HTTP error because the protocol expects 200 with a NACK body:
    an HTTP 4xx is read as "this seller platform is broken", not "this order cannot
    be filled", and the two lead the buyer app to very different behaviour.
    """
    return {"message": {"ack": {"status": "NACK"}},
            "error": {"type": "DOMAIN-ERROR", "code": code, "message": message}}


def callback(incoming: dict, action: str, message: dict) -> dict[str, Any]:
    """
    POST the real answer back to the buyer app that asked.

    Returns what happened rather than raising: a failed callback must not roll back
    an order we have already committed to. The buyer app will re-ask with `/status`
    if it hears nothing, and an order that exists is recoverable where one that was
    rolled back because of a network blip is not.
    """
    ctx = context_for(incoming, action)
    bap_uri = (ctx.get("bap_uri") or "").rstrip("/")
    if not bap_uri:
        return {"sent": False, "why": "no bap_uri in the request context"}

    payload = {"context": ctx, "message": message}
    body = json.dumps(payload, separators=(",", ":"))
    try:
        r = requests.post(f"{bap_uri}/{action}", data=body, timeout=TIMEOUT,
                          headers={"Authorization": sign_request(body),
                                   "Content-Type": "application/json"})
        ok = r.status_code < 300
        if not ok:
            log.warning("ondc %s callback to %s: HTTP %s %s",
                        action, bap_uri, r.status_code, r.text[:200])
        return {"sent": ok, "status": r.status_code, "body": r.text[:400]}
    except Exception as e:                                  # noqa: BLE001
        log.warning("ondc %s callback to %s failed: %s", action, bap_uri, e)
        return {"sent": False, "why": f"{type(e).__name__}: {e}"}


# ──────────────────────────────────────────────────────── catalogue shapes

def _price(value: float) -> dict[str, str]:
    return {"currency": "INR", "value": f"{float(value or 0):.2f}"}


def item_for(listing: db.Listing) -> dict[str, Any]:
    """
    One listing as a Beckn catalogue item.

    The price here is `Listing.price` and nothing else. Whatever the artisan set in
    the app is what the network quotes, which is the only arrangement that cannot
    drift.
    """
    return {
        "id": listing.id,
        "descriptor": {
            "name": listing.title_en or listing.title_hi or "Handmade product",
            "long_desc": listing.desc_en or listing.desc_hi or "",
            "images": [listing.image_url] if listing.image_url else [],
        },
        "price": _price(listing.price),
        "category_id": listing.category or "",
        "quantity": {
            "available": {"count": str(max(int(listing.quantity or 0), 0))},
            "maximum": {"count": str(max(int(listing.quantity or 0), 0))},
        },
        "@ondc/org/returnable": False,
        "@ondc/org/cancellable": True,
        "@ondc/org/available_on_cod": False,
        "@ondc/org/contact_details_consumer_care":
            f"{os.getenv('ONDC_STORE_NAME', 'Kalakriti')},{os.getenv('ONDC_SUPPORT_EMAIL', '')}",
        "@ondc/org/statutory_reqs_packaged_commodities": {
            "hsn_code": listing.hsn or "",
        },
    }


def catalogue(s, artisan_id: str = "") -> dict[str, Any]:
    """Everything currently sellable, as a Beckn catalogue."""
    q = (s.query(db.Listing)
         .filter(db.Listing.status.in_(("active", "published")),
                 db.Listing.quantity > 0))
    if artisan_id:
        q = q.filter(db.Listing.artisan_id == artisan_id)
    rows = q.limit(200).all()

    by_artisan: dict[str, list[db.Listing]] = {}
    for r in rows:
        by_artisan.setdefault(r.artisan_id or "unclaimed", []).append(r)

    providers = []
    for aid, items in by_artisan.items():
        a = s.get(db.Artisan, aid)
        providers.append({
            "id": aid,
            "descriptor": {"name": (a.business_name or a.full_name) if a
                           else os.getenv("ONDC_STORE_NAME", "Kalakriti")},
            "items": [item_for(i) for i in items],
            "fulfillments": [{"id": "F1", "type": "Delivery"}],
        })

    return {"catalog": {
        "bpp/descriptor": {"name": os.getenv("ONDC_STORE_NAME", "Kalakriti")},
        "bpp/providers": providers,
        "bpp/fulfillments": [{"id": "F1", "type": "Delivery"}],
    }}


def _find_item(message: dict) -> tuple[str, int]:
    """The listing id and quantity a buyer app is asking about."""
    order = message.get("order") or {}
    items = order.get("items") or []
    if not items:
        return "", 0
    first = items[0]
    qty = int(((first.get("quantity") or {}).get("count")
               or (first.get("quantity") or {}).get("selected", {}).get("count")
               or 1))
    return str(first.get("id") or ""), max(qty, 1)


def quote_for(listing: db.Listing, quantity: int,
              delivery_charge: float = 0.0) -> dict[str, Any]:
    """
    The price breakup ONDC shows the buyer.

    Itemised rather than a single total because Beckn requires the breakup to sum to
    the price, and because a buyer seeing "delivery" as its own line is the same
    honesty the app applies to the artisan's own payout.
    """
    line = float(listing.price or 0) * quantity
    total = line + delivery_charge
    breakup = [{
        "@ondc/org/item_id": listing.id,
        "@ondc/org/item_quantity": {"count": str(quantity)},
        "@ondc/org/title_type": "item",
        "title": listing.title_en or listing.title_hi or "Item",
        "price": _price(line),
    }]
    if delivery_charge:
        breakup.append({
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "delivery",
            "title": "Delivery charges",
            "price": _price(delivery_charge),
        })
    return {"price": _price(total), "breakup": breakup, "ttl": "P1D"}


# ───────────────────────────────────────────────────────────── the actions

def handle_search(s, body: dict) -> dict[str, Any]:
    """A buyer app is browsing. Answer with everything currently sellable."""
    return callback(body, "on_search", catalogue(s))


def handle_select(s, body: dict) -> dict[str, Any]:
    """
    A buyer app is asking what one item would cost.

    An item that has gone out of stock between the search and the select is answered
    honestly here rather than at confirm, which is the difference between a buyer
    choosing something else and a buyer being refunded after paying.
    """
    message = body.get("message") or {}
    lid, qty = _find_item(message)
    listing = s.get(db.Listing, lid) if lid else None

    if listing is None or listing.status not in ("active", "published"):
        return {"nack": nack("30004", "That item is no longer listed.")}
    if (listing.quantity or 0) < qty:
        return {"nack": nack("40002",
                             f"Only {max(listing.quantity or 0, 0)} left in stock.")}

    order = dict(message.get("order") or {})
    order["provider"] = {"id": listing.artisan_id or "unclaimed"}
    order["items"] = [{"id": listing.id, "quantity": {"selected": {"count": str(qty)}}}]
    order["quote"] = quote_for(listing, qty)
    order["fulfillments"] = [{"id": "F1", "type": "Delivery",
                              "@ondc/org/provider_name":
                                  os.getenv("ONDC_STORE_NAME", "Kalakriti")}]
    return callback(body, "on_select", {"order": order})


def handle_init(s, body: dict) -> dict[str, Any]:
    """Terms, billing and the quote restated. Still not a sale."""
    message = body.get("message") or {}
    lid, qty = _find_item(message)
    listing = s.get(db.Listing, lid) if lid else None
    if listing is None:
        return {"nack": nack("30004", "That item is no longer listed.")}

    order = dict(message.get("order") or {})
    order["provider"] = {"id": listing.artisan_id or "unclaimed"}
    order["items"] = [{"id": listing.id, "quantity": {"count": str(qty)}}]
    order["quote"] = quote_for(listing, qty)
    order["payment"] = {
        "@ondc/org/buyer_app_finder_fee_type": "percent",
        "@ondc/org/buyer_app_finder_fee_amount": "3",
        "type": "ON-ORDER", "collected_by": "BAP",
    }
    return callback(body, "on_init", {"order": order})


def handle_confirm(s, body: dict) -> dict[str, Any]:
    """
    The sale. This is the only call in the protocol that creates an order.

    Stock is checked and decremented in the same transaction that writes the row, so
    two buyer apps confirming the last piece at the same moment cannot both succeed.
    The second gets a NACK naming the real remaining count, which its user sees
    before paying rather than after.
    """
    message = body.get("message") or {}
    lid, qty = _find_item(message)
    incoming = message.get("order") or {}
    ondc_order_id = str(incoming.get("id") or
                        (body.get("context") or {}).get("transaction_id") or "")

    listing = s.get(db.Listing, lid) if lid else None
    if listing is None or listing.status not in ("active", "published"):
        return {"nack": nack("30004", "That item is no longer listed.")}
    if (listing.quantity or 0) < qty:
        return {"nack": nack("40002",
                             f"Only {max(listing.quantity or 0, 0)} left in stock.")}

    # Same order arriving twice is the network retrying, not a second sale.
    existing = (s.query(db.Order)
                .filter(db.Order.channel == "ondc",
                        db.Order.external_id == ondc_order_id).first()
                if ondc_order_id else None)
    if existing is not None:
        return callback(body, "on_confirm",
                        {"order": _order_message(s, existing, listing, body)})

    billing = incoming.get("billing") or {}
    stops = ((incoming.get("fulfillments") or [{}])[0].get("stops") or [{}])
    end = next((st for st in stops if st.get("type") == "end"), stops[-1] if stops else {})
    loc = (end.get("location") or {})
    addr = loc.get("address") or {}
    contact = end.get("contact") or {}

    amount = float(listing.price or 0) * qty
    order = db.Order(
        id=db.nid("ord"),
        listing_id=listing.id,
        channel="ondc",
        external_id=ondc_order_id,
        buyer_name=str(billing.get("name") or end.get("person", {}).get("name") or "")[:120],
        buyer_phone=str(billing.get("phone") or contact.get("phone") or "")[:20],
        buyer_email=str(billing.get("email") or contact.get("email") or "")[:120],
        address=", ".join(str(v) for v in (
            addr.get("name"), addr.get("building"), addr.get("locality"),
            addr.get("street")) if v)[:400],
        buyer_city=str(addr.get("city") or "")[:80],
        buyer_state=str(addr.get("state") or "")[:80],
        buyer_pincode=str(addr.get("area_code") or "")[:12],
        quantity=qty,
        amount=amount,
        status="paid",
        # ONDC settles through the buyer app; the money is collected there before
        # confirm reaches us, which is what `collected_by: BAP` in on_init declared.
        payment_status="paid",
        payment_provider="ondc_bap",
        payment_ref=str((incoming.get("payment") or {}).get("id") or ondc_order_id),
    )
    s.add(order)
    listing.quantity = max((listing.quantity or 0) - qty, 0)

    db.log_event(s, "order", order.id, "status", "", "paid",
                 f"ONDC confirm from {(body.get('context') or {}).get('bap_id', '?')}, "
                 f"{qty} x {listing.id}")
    if listing.quantity == 0:
        db.log_event(s, "listing", listing.id, "note", detail="stock exhausted (ONDC)")
    s.commit()

    return callback(body, "on_confirm",
                    {"order": _order_message(s, order, listing, body)})


def _order_message(s, order: db.Order, listing: db.Listing,
                   body: dict) -> dict[str, Any]:
    """Our view of the order, in the shape Beckn expects back."""
    return {
        "id": order.external_id or order.id,
        "state": _beckn_state(order.status),
        "provider": {"id": listing.artisan_id or "unclaimed"},
        "items": [{"id": listing.id, "quantity": {"count": str(order.quantity)}}],
        "quote": quote_for(listing, order.quantity),
        "fulfillments": [{
            "id": "F1", "type": "Delivery",
            "state": {"descriptor": {"code": _fulfilment_state(order.status)}},
            "tracking": bool(order.tracking_url),
            **({"@ondc/org/AWB_no": order.tracking_id} if order.tracking_id else {}),
        }],
        "payment": {"status": "PAID" if order.payment_status == "paid" else "NOT-PAID",
                    "type": "ON-ORDER", "collected_by": "BAP"},
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }


def _beckn_state(status: str) -> str:
    return {
        "created": "Created", "paid": "Accepted", "packed": "In-progress",
        "shipped": "In-progress", "delivered": "Completed",
        "cancelled": "Cancelled", "refunded": "Cancelled",
    }.get((status or "").lower(), "Created")


def _fulfilment_state(status: str) -> str:
    return {
        "paid": "Pending", "packed": "Packed", "shipped": "Order-picked-up",
        "delivered": "Order-delivered", "cancelled": "Cancelled",
    }.get((status or "").lower(), "Pending")


def handle_status(s, body: dict) -> dict[str, Any]:
    """Where is my order. Answered from the same row the app shows the artisan."""
    message = body.get("message") or {}
    oid = str(message.get("order_id") or "")
    order = (s.query(db.Order)
             .filter(db.Order.channel == "ondc",
                     db.Order.external_id == oid).first()) if oid else None
    if order is None:
        return {"nack": nack("30005", "No such order.")}
    listing = s.get(db.Listing, order.listing_id)
    return callback(body, "on_status",
                    {"order": _order_message(s, order, listing, body)})


def handle_cancel(s, body: dict) -> dict[str, Any]:
    """
    A buyer cancelling. Stock goes back, because the piece is still on the shelf.
    """
    message = body.get("message") or {}
    oid = str(message.get("order_id") or "")
    reason = str(message.get("cancellation_reason_id") or "")
    order = (s.query(db.Order)
             .filter(db.Order.channel == "ondc",
                     db.Order.external_id == oid).first()) if oid else None
    if order is None:
        return {"nack": nack("30005", "No such order.")}
    if order.status in ("shipped", "delivered"):
        return {"nack": nack("30016",
                             "This order has already been despatched and cannot be "
                             "cancelled here.")}

    listing = s.get(db.Listing, order.listing_id)
    if listing is not None and order.status not in ("cancelled", "refunded"):
        listing.quantity = (listing.quantity or 0) + (order.quantity or 0)
    db.log_event(s, "order", order.id, "status", order.status, "cancelled",
                 f"ONDC cancel, reason {reason or 'unspecified'}")
    order.status = "cancelled"
    order.cancel_reason = f"ONDC: {reason}" if reason else "Cancelled on ONDC"
    s.commit()
    return callback(body, "on_cancel",
                    {"order": _order_message(s, order, listing, body)})
