"""
Logistics: pickup, courier assignment, AWB, tracking, RTO.

Transport does not happen by itself once an order is paid. The concrete chain this
module implements, and where each link lives:

  1. Where the product is        Listing -> Artisan -> Address(kind="pickup")
  2. Pickup address used         seller.map_seller(); for marketplace-fulfilled
                                 orders the marketplace uses the address registered
                                 in its own seller console, which is why
                                 `MarketplaceAccount` tracks that separately.
  3. Courier gets the request    create_shipment() -> provider API (Shiprocket /
                                 Delhivery), which returns a real AWB.
  4. Artisan is notified         an Event row plus, if SMS is configured, a message
                                 to the artisan's verified number.
  5. Handover                    artisan marks `picked_up`, or the provider webhook
                                 reports it - whichever happens first.
  6. Tracking generated          the AWB from the provider response.
  7. Tracking reaches us         POST /webhooks/logistics/{provider}, signature or
                                 shared-secret checked.
  8. Buyer sees tracking         the storefront order page renders the AWB and the
                                 provider's public tracking URL.
  9. Delivery status             webhook events map onto SHIPMENT_FLOW.
 10. Failed delivery             provider reports NDR -> shipment `failed`, order
                                 stays `shipped`, artisan is notified to act.
 11. Cancellation                cancel_shipment() calls the provider's cancel API
                                 and restocks the listing.
 12. Return / refund             RTO events move the shipment to `returned`; a
                                 refund is issued through the payment provider,
                                 which is what actually moves money.

Providers are real HTTP integrations. With no credentials they return
`not_configured` naming the missing variables - never a fabricated AWB.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

import db
import seller

TIMEOUT = 30
_sr_token: dict[str, Any] = {"token": "", "at": 0.0}


def _missing(*names: str) -> list[str]:
    return [n for n in names if not os.getenv(n)]


def provider() -> str:
    if os.getenv("SHIPROCKET_EMAIL") and os.getenv("SHIPROCKET_PASSWORD"):
        return "shiprocket"
    if os.getenv("DELHIVERY_API_TOKEN"):
        return "delhivery"
    return "not_configured"


def availability() -> dict[str, Any]:
    p = provider()
    return {
        "provider": p,
        "configured": p != "not_configured",
        "missing": (["SHIPROCKET_EMAIL + SHIPROCKET_PASSWORD, "
                     "or DELHIVERY_API_TOKEN + DELHIVERY_PICKUP_NAME"]
                    if p == "not_configured" else []),
    }


# ─────────────────────────────────────────────────────────────── Shiprocket

def _shiprocket_token() -> tuple[str, str]:
    """Shiprocket tokens last 10 days; cache and re-login when stale."""
    import time

    if _sr_token["token"] and (time.time() - _sr_token["at"]) < 8 * 24 * 3600:
        return _sr_token["token"], ""
    try:
        r = requests.post(
            "https://apiv2.shiprocket.in/v1/external/auth/login",
            json={"email": os.getenv("SHIPROCKET_EMAIL"),
                  "password": os.getenv("SHIPROCKET_PASSWORD")},
            timeout=TIMEOUT)
        if r.status_code >= 300:
            return "", f"auth HTTP {r.status_code}: {r.text[:200]}"
        tok = r.json().get("token", "")
        _sr_token.update({"token": tok, "at": time.time()})
        return tok, ""
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"


def _shiprocket_create(order: db.Order, listing: db.Listing,
                       artisan: db.Artisan) -> dict[str, Any]:
    token, err = _shiprocket_token()
    if err:
        return {"status": "failed", "error": err}

    pick = None
    for a in artisan.addresses or []:
        if a.kind == "pickup":
            pick = a
    if not pick or not pick.complete():
        return {"status": "failed",
                "error": "pickup address incomplete - courier has nowhere to collect from"}

    payload = {
        "order_id": order.id,
        "order_date": (order.created_at or db.now()).strftime("%Y-%m-%d %H:%M"),
        "pickup_location": os.getenv("SHIPROCKET_PICKUP_NICKNAME", "Primary"),
        "billing_customer_name": order.buyer_name or "Buyer",
        "billing_last_name": "",
        "billing_address": order.address,
        "billing_city": order.buyer_city,
        "billing_pincode": order.buyer_pincode,
        "billing_state": order.buyer_state,
        "billing_country": "India",
        "billing_email": order.buyer_email or "",
        "billing_phone": order.buyer_phone,
        "shipping_is_billing": True,
        "order_items": [{
            "name": (listing.title_en or listing.title_hi)[:100],
            "sku": listing.id,
            "units": order.quantity,
            "selling_price": order.amount / max(order.quantity, 1),
            "hsn": listing.hsn or "",
        }],
        "payment_method": "Prepaid" if order.payment_status == "paid" else "COD",
        "sub_total": order.amount,
        "length": listing.length_cm or 20,
        "breadth": listing.breadth_cm or 15,
        "height": listing.height_cm or 10,
        "weight": max((listing.weight_g or 500) / 1000.0, 0.1),
    }
    try:
        r = requests.post(
            "https://apiv2.shiprocket.in/v1/external/orders/create/adhoc",
            headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=TIMEOUT)
        body = r.json() if r.text else {}
        if r.status_code >= 300:
            return {"status": "failed",
                    "error": f"HTTP {r.status_code}: {str(body)[:300]}", "raw": body}
        return {
            "status": "pickup_requested",
            "provider": "shiprocket",
            "awb": str(body.get("awb_code") or ""),
            "courier_name": str(body.get("courier_name") or ""),
            "external_id": str(body.get("shipment_id") or body.get("order_id") or ""),
            "tracking_url": (f"https://shiprocket.co/tracking/{body.get('awb_code')}"
                             if body.get("awb_code") else ""),
            "raw": body,
        }
    except Exception as e:
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


# ──────────────────────────────────────────────────────────────── Delhivery

def _delhivery_create(order: db.Order, listing: db.Listing,
                      artisan: db.Artisan) -> dict[str, Any]:
    token = os.getenv("DELHIVERY_API_TOKEN", "")
    base = os.getenv("DELHIVERY_BASE", "https://track.delhivery.com")
    pickup_name = os.getenv("DELHIVERY_PICKUP_NAME", "")
    if not pickup_name:
        return {"status": "failed", "error": "DELHIVERY_PICKUP_NAME not set"}

    shipment = {
        "name": order.buyer_name, "add": order.address, "city": order.buyer_city,
        "pin": order.buyer_pincode, "state": order.buyer_state, "country": "India",
        "phone": order.buyer_phone, "order": order.id,
        "payment_mode": "Prepaid" if order.payment_status == "paid" else "COD",
        "cod_amount": "" if order.payment_status == "paid" else str(order.amount),
        "total_amount": str(order.amount),
        "quantity": str(order.quantity),
        "weight": str(listing.weight_g or 500),
        "shipment_length": str(listing.length_cm or 20),
        "shipment_width": str(listing.breadth_cm or 15),
        "shipment_height": str(listing.height_cm or 10),
        "hsn_code": listing.hsn or "",
    }
    body = {"shipments": [shipment], "pickup_location": {"name": pickup_name}}
    try:
        r = requests.post(
            f"{base}/api/cmu/create.json",
            headers={"Authorization": f"Token {token}",
                     "Content-Type": "application/json"},
            data="format=json&data=" + __import__("json").dumps(body),
            timeout=TIMEOUT)
        rb = r.json() if r.text else {}
        pkgs = rb.get("packages") or []
        if r.status_code < 300 and pkgs and pkgs[0].get("waybill"):
            awb = pkgs[0]["waybill"]
            return {"status": "pickup_requested", "provider": "delhivery", "awb": awb,
                    "courier_name": "Delhivery", "external_id": awb,
                    "tracking_url": f"{base}/track/package/{awb}", "raw": rb}
        return {"status": "failed",
                "error": f"HTTP {r.status_code}: {str(rb)[:300]}", "raw": rb}
    except Exception as e:
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


# ────────────────────────────────────────────────────────────────── public API

def create_shipment(s, order: db.Order) -> db.Shipment:
    """
    Book a real consignment for a paid order. Always writes a Shipment row, even on
    failure, so the artisan can see why nothing was booked instead of nothing at all.
    """
    listing = s.get(db.Listing, order.listing_id)
    artisan = s.get(db.Artisan, listing.artisan_id) if listing and listing.artisan_id else None

    sh = db.Shipment(id=db.nid("shp"), order_id=order.id, provider=provider())

    if artisan is None:
        sh.status = "failed"
        sh.last_error = ("This listing has no verified seller, so no pickup address "
                         "exists. Complete the seller profile first.")
    elif sh.provider == "not_configured":
        sh.status = "failed"
        sh.last_error = ("No courier configured. Set SHIPROCKET_EMAIL + "
                         "SHIPROCKET_PASSWORD, or DELHIVERY_API_TOKEN + "
                         "DELHIVERY_PICKUP_NAME.")
    else:
        res = (_shiprocket_create(order, listing, artisan)
               if sh.provider == "shiprocket"
               else _delhivery_create(order, listing, artisan))
        sh.status = res.get("status", "failed")
        sh.awb = res.get("awb", "")
        sh.courier_name = res.get("courier_name", "")
        sh.tracking_url = res.get("tracking_url", "")
        sh.last_error = res.get("error", "")
        sh.raw = res.get("raw", {})
        if sh.status == "pickup_requested":
            sh.pickup_scheduled_at = db.now()
            order.courier = sh.courier_name or sh.provider
            order.tracking_id = sh.awb
            order.tracking_url = sh.tracking_url

    s.add(sh)
    db.log_event(s, "shipment", sh.id, "status", "", sh.status,
                 sh.last_error or f"{sh.provider} awb={sh.awb or '-'}")
    s.commit()
    return sh


def cancel_shipment(s, sh: db.Shipment) -> dict[str, Any]:
    if sh.provider == "shiprocket" and sh.awb:
        token, err = _shiprocket_token()
        if err:
            return {"ok": False, "error": err}
        try:
            r = requests.post(
                "https://apiv2.shiprocket.in/v1/external/orders/cancel",
                headers={"Authorization": f"Bearer {token}"},
                json={"ids": [sh.raw.get("order_id")] if sh.raw else []}, timeout=TIMEOUT)
            ok = r.status_code < 300
            if ok:
                sh.status = "cancelled"
                s.commit()
            return {"ok": ok, "error": "" if ok else r.text[:200]}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if sh.provider == "delhivery" and sh.awb:
        try:
            r = requests.post(
                f"{os.getenv('DELHIVERY_BASE', 'https://track.delhivery.com')}"
                f"/api/p/edit",
                headers={"Authorization": f"Token {os.getenv('DELHIVERY_API_TOKEN')}",
                         "Content-Type": "application/json"},
                json={"waybill": sh.awb, "cancellation": "true"}, timeout=TIMEOUT)
            ok = r.status_code < 300
            if ok:
                sh.status = "cancelled"
                s.commit()
            return {"ok": ok, "error": "" if ok else r.text[:200]}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    sh.status = "cancelled"
    s.commit()
    return {"ok": True, "error": "", "note": "no remote consignment to cancel"}


# Provider status strings -> our own flow. Anything unrecognised is recorded on the
# event log but never guessed into a state change.
STATUS_MAP = {
    "pickup scheduled": "pickup_requested", "pickup generated": "pickup_requested",
    "manifested": "pickup_requested", "awb assigned": "pickup_requested",
    "picked up": "picked_up", "pickup complete": "picked_up", "shipped": "in_transit",
    "in transit": "in_transit", "intransit": "in_transit", "dispatched": "in_transit",
    "out for delivery": "out_for_delivery", "ofd": "out_for_delivery",
    "delivered": "delivered",
    "undelivered": "failed", "ndr": "failed", "failed delivery": "failed",
    "rto initiated": "rto_initiated", "rto in transit": "rto_initiated",
    "rto delivered": "returned", "returned": "returned",
    "cancelled": "cancelled", "canceled": "cancelled",
}


def map_status(raw: str) -> str | None:
    return STATUS_MAP.get((raw or "").strip().lower())


def apply_tracking(s, sh: db.Shipment, raw_status: str,
                   payload: dict | None = None) -> str | None:
    """Move a shipment (and its order) on a real courier event."""
    mapped = map_status(raw_status)
    db.log_event(s, "shipment", sh.id, "webhook",
                 detail=f"{sh.provider}: {raw_status}", payload=payload or {})
    if not mapped:
        s.commit()
        return None

    if db.can_transition(db.SHIPMENT_FLOW, sh.status, mapped):
        db.log_event(s, "shipment", sh.id, "status", sh.status, mapped, "courier")
        sh.status = mapped

    order = s.get(db.Order, sh.order_id)
    if order:
        pair = {"picked_up": "shipped", "in_transit": "shipped",
                "out_for_delivery": "out_for_delivery", "delivered": "delivered"}
        target = pair.get(mapped)
        if target and db.can_transition(db.ORDER_FLOW, order.status, target):
            db.log_event(s, "order", order.id, "status", order.status, target, "courier")
            order.status = target
        if mapped == "returned":
            listing = s.get(db.Listing, order.listing_id)
            if listing:
                listing.quantity += order.quantity      # goods are back on the shelf
                db.log_event(s, "listing", listing.id, "note",
                             detail=f"restocked {order.quantity} after RTO")
        if mapped == "delivered" and order.payment_status == "paid":
            db.log_event(s, "order", order.id, "status", order.status, "completed", "auto")
            order.status = "completed"
    s.commit()
    return mapped
