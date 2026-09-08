"""
Routes for onboarding, authentication, seller profile, addresses, marketplace
readiness and logistics.

Kept in its own router so main.py stays about the listing pipeline.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

import auth
import db
import logistics
import seller
import sms

router = APIRouter()


def current(s, authorization: str | None) -> db.Artisan | None:
    return auth.artisan_from_token(s, authorization)


def require(s, authorization: str | None) -> db.Artisan:
    a = current(s, authorization)
    if not a:
        raise HTTPException(401, "login required")
    return a


# ═════════════════════════════════════════════════════════════ 1. bootstrap

@router.get("/v1/bootstrap")
def bootstrap(authorization: str | None = Header(None),
              x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    What the app asks on every launch: am I logged in, what language did I choose,
    do I have a draft to resume? This is what makes onboarding show only once.
    """
    s = db.session()
    try:
        a = current(s, authorization)
        drafts = []
        q = s.query(db.Listing).filter(db.Listing.status.in_(["draft", "processing"]))
        if a:
            q = q.filter(db.Listing.artisan_id == a.id)
        elif x_guest_token:
            q = q.filter(db.Listing.guest_token == x_guest_token,
                         db.Listing.artisan_id.is_(None))
        else:
            q = None
        if q is not None:
            drafts = [r.public() for r in
                      q.order_by(db.Listing.updated_at.desc()).limit(10).all()]
        return {
            "authenticated": bool(a),
            "artisan": a.public() if a else None,
            "language": a.language if a else None,
            "drafts": drafts,
            "otpDelivery": sms.provider(),
            "otpDevEcho": sms.dev_echo(),
            "logistics": logistics.availability(),
            "marketplaceReadiness": seller.readiness_all(a),
        }
    finally:
        s.close()


# ══════════════════════════════════════════════════════════════════ 2. auth

class OtpRequestIn(BaseModel):
    phone: str


@router.post("/v1/auth/otp/request")
def otp_request(body: OtpRequestIn) -> dict[str, Any]:
    s = db.session()
    try:
        return auth.request_otp(s, body.phone)
    finally:
        s.close()


class OtpVerifyIn(BaseModel):
    challengeId: str
    code: str
    guestToken: str = ""


@router.post("/v1/auth/otp/verify")
def otp_verify(body: OtpVerifyIn, request: Request) -> dict[str, Any]:
    s = db.session()
    try:
        out = auth.verify_otp(s, body.challengeId, body.code,
                              user_agent=request.headers.get("user-agent", ""),
                              guest_token=body.guestToken)
        if not out.get("ok"):
            raise HTTPException(400, out.get("message") or out.get("error", "failed"))
        return out
    finally:
        s.close()


@router.get("/v1/auth/me")
def me(authorization: str | None = Header(None)) -> dict[str, Any]:
    s = db.session()
    try:
        a = current(s, authorization)
        if not a:
            raise HTTPException(401, "not logged in")
        return {"artisan": a.public(),
                "marketplaceReadiness": seller.readiness_all(a)}
    finally:
        s.close()


@router.post("/v1/auth/logout")
def logout(authorization: str | None = Header(None)) -> dict[str, Any]:
    s = db.session()
    try:
        return {"ok": auth.logout(s, authorization)}
    finally:
        s.close()


# ═══════════════════════════════════════════════════════════════ 3. profile

class ProfileIn(BaseModel):
    fullName: str | None = None
    businessName: str | None = None
    email: str | None = None
    language: str | None = None
    cluster: str | None = None
    gstin: str | None = None
    pan: str | None = None
    bankAccount: str | None = None
    bankIfsc: str | None = None


@router.patch("/v1/profile")
def patch_profile(body: ProfileIn,
                  authorization: str | None = Header(None)) -> dict[str, Any]:
    s = db.session()
    try:
        a = require(s, authorization)
        m = {"fullName": "full_name", "businessName": "business_name", "email": "email",
             "language": "language", "cluster": "cluster", "gstin": "gstin",
             "pan": "pan", "bankAccount": "bank_account", "bankIfsc": "bank_ifsc"}
        changed = []
        for k, col in m.items():
            v = getattr(body, k)
            if v is not None:
                setattr(a, col, v)
                changed.append(k)
        if changed:
            db.log_event(s, "artisan", a.id, "note", detail="profile: " + ", ".join(changed))
        s.commit()
        return {"artisan": a.public(), "marketplaceReadiness": seller.readiness_all(a)}
    finally:
        s.close()


class AddressIn(BaseModel):
    kind: str = "pickup"
    contactName: str = ""
    contactPhone: str = ""
    line1: str = ""
    line2: str = ""
    landmark: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    country: str = "India"
    copyToReturn: bool = False      # "use pickup address as return address"


@router.put("/v1/profile/address")
def put_address(body: AddressIn,
                authorization: str | None = Header(None)) -> dict[str, Any]:
    """One address per kind; PUT replaces that kind rather than piling up duplicates."""
    s = db.session()
    try:
        a = require(s, authorization)

        def upsert(kind: str) -> db.Address:
            row = next((x for x in a.addresses if x.kind == kind), None)
            if row is None:
                row = db.Address(id=db.nid("adr"), artisan_id=a.id, kind=kind)
                s.add(row)
            row.contact_name = body.contactName or a.full_name
            row.contact_phone = body.contactPhone or a.phone
            row.line1, row.line2, row.landmark = body.line1, body.line2, body.landmark
            row.city, row.state = body.city, body.state
            row.pincode, row.country = body.pincode, body.country
            return row

        upsert(body.kind)
        if body.copyToReturn and body.kind == "pickup":
            upsert("return")
        db.log_event(s, "artisan", a.id, "note", detail=f"address:{body.kind} saved")
        s.commit()
        s.refresh(a)
        return {"artisan": a.public(), "marketplaceReadiness": seller.readiness_all(a)}
    finally:
        s.close()


@router.get("/v1/marketplaces/readiness")
def readiness(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Per-marketplace seller checklist, with the reason each field is needed."""
    s = db.session()
    try:
        a = current(s, authorization)
        return {"loggedIn": bool(a), "readiness": seller.readiness_all(a)}
    finally:
        s.close()


@router.get("/v1/marketplaces/{channel}/mapping")
def mapping(channel: str, authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Shows exactly which of our profile fields become which marketplace fields.
    Useful for the setup screen, and it keeps the mapping honest and inspectable.
    """
    s = db.session()
    try:
        a = current(s, authorization)
        return {"channel": channel,
                "readiness": seller.readiness(a, channel),
                "mapped": seller.map_seller(a, channel)}
    finally:
        s.close()


# ═════════════════════════════════════════════════════════════ 4. logistics

@router.post("/v1/orders/{oid}/ship")
def ship(oid: str, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Book the courier. Only for a paid order whose seller profile is complete."""
    s = db.session()
    try:
        a = require(s, authorization)
        o = s.get(db.Order, oid)
        if not o:
            raise HTTPException(404, "order not found")
        listing = s.get(db.Listing, o.listing_id)
        if not listing or listing.artisan_id != a.id:
            raise HTTPException(403, "not your order")
        if o.payment_status != "paid":
            raise HTTPException(400, "cannot ship before payment is confirmed")
        sh = logistics.create_shipment(s, o)
        return {"shipment": sh.public(), "order": o.public()}
    finally:
        s.close()


@router.post("/v1/shipments/{sid}/cancel")
def cancel_ship(sid: str, authorization: str | None = Header(None)) -> dict[str, Any]:
    s = db.session()
    try:
        require(s, authorization)
        sh = s.get(db.Shipment, sid)
        if not sh:
            raise HTTPException(404, "shipment not found")
        return logistics.cancel_shipment(s, sh)
    finally:
        s.close()


@router.post("/webhooks/logistics/{prov}")
async def logistics_webhook(prov: str, request: Request,
                            x_api_key: str | None = Header(None)) -> dict[str, Any]:
    """
    Courier status callbacks. Shiprocket and Delhivery both authenticate with a
    shared secret, so an unset secret rejects rather than trusting the caller.
    """
    secret = os.getenv("LOGISTICS_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "LOGISTICS_WEBHOOK_SECRET not configured")
    if (x_api_key or "") != secret:
        raise HTTPException(401, "bad webhook key")

    body = await request.json()
    awb = str(body.get("awb") or body.get("awb_code")
              or body.get("Waybill") or body.get("waybill") or "")
    raw_status = str(body.get("current_status") or body.get("status")
                     or body.get("Status", {}).get("Status", "") if isinstance(
                         body.get("Status"), dict) else body.get("status") or "")

    s = db.session()
    try:
        sh = s.query(db.Shipment).filter(db.Shipment.awb == awb).first() if awb else None
        if not sh:
            db.log_event(s, "shipment", awb or prov, "webhook",
                         detail=f"{prov}: no matching shipment", payload=body)
            s.commit()
            return {"ok": True, "matched": False}
        mapped = logistics.apply_tracking(s, sh, raw_status, body)
        return {"ok": True, "matched": True, "awb": awb,
                "rawStatus": raw_status, "mapped": mapped, "status": sh.status}
    finally:
        s.close()
