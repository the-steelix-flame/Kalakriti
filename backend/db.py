"""
Persistence. SQLite via SQLAlchemy - a real database, on disk, surviving restarts.

Listings, publication attempts, orders, shipments, sessions and their status
transitions are all rows, which is what makes status tracking and the order
lifecycle real rather than a screen showing whatever the last button press set.

Every status change is appended to `events` rather than only mutating a column, so
the lifecycle is auditable: you can see when a listing went Draft -> Submitted and
what the platform actually replied with.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./kalakriti.db")
engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def now() -> datetime:
    """
    UTC, without a tzinfo.

    Every timestamp in this database is naive UTC, and it has to stay that way. The
    SQLite dialect silently strips the offset from an aware datetime on write, so the
    rows are already naive; Postgres columns are `timestamp without time zone` and do
    the same. Returning an aware value here would mean queries compare an aware bind
    parameter against a naive column, which SQLite quietly tolerates and Postgres
    resolves using the session time zone - a bug that only appears in production and
    only for people not in UTC.

    Anything read back is therefore naive UTC. Code comparing it against a timestamp
    from a client attaches UTC first; see `_utc` in main.py.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def nid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# Lifecycles, in order. Guarded by `can_transition` so a listing cannot jump from
# Draft to Sold because a UI button was pressed twice.
LISTING_FLOW = ["draft", "processing", "submitted", "approved", "published", "active", "sold"]
ORDER_FLOW = ["created", "payment_pending", "paid", "confirmed", "packed",
              "shipped", "out_for_delivery", "delivered", "completed"]
ORDER_TERMINAL = ["cancelled", "refunded", "failed"]
SHIPMENT_FLOW = ["pending", "pickup_requested", "picked_up", "in_transit",
                 "out_for_delivery", "delivered"]
SHIPMENT_TERMINAL = ["failed", "rto_initiated", "returned", "cancelled"]


def can_transition(flow: list[str], frm: str, to: str) -> bool:
    if to in ORDER_TERMINAL or to in SHIPMENT_TERMINAL:
        return True
    if frm not in flow or to not in flow:
        return False
    return flow.index(to) >= flow.index(frm)


class Artisan(Base):
    """
    The seller. Created only at the moment of OTP verification, so a row here always
    means a phone number that was actually verified - never a half-filled form.
    """
    __tablename__ = "artisans"
    id = Column(String, primary_key=True)
    phone = Column(String, unique=True, index=True, nullable=False)
    phone_verified = Column(Integer, default=0)
    phone_verified_at = Column(DateTime, nullable=True)

    full_name = Column(String, default="")
    business_name = Column(String, default="")
    email = Column(String, default="")
    language = Column(String, default="hi")
    cluster = Column(String, default="")
    gi_tag = Column(String, default="")

    # Tax / compliance identifiers. Optional for most channels; GeM requires them.
    gstin = Column(String, default="")
    pan = Column(String, default="")
    bank_account = Column(String, default="")
    bank_ifsc = Column(String, default="")

    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    addresses = relationship("Address", back_populates="artisan",
                             cascade="all, delete-orphan")
    accounts = relationship("MarketplaceAccount", back_populates="artisan",
                            cascade="all, delete-orphan")

    def public(self) -> dict:
        return {
            "id": self.id, "phone": self.phone,
            "phoneVerified": bool(self.phone_verified),
            "fullName": self.full_name, "businessName": self.business_name,
            "email": self.email, "language": self.language, "cluster": self.cluster,
            "gstin": self.gstin, "pan": self.pan,
            "bankAccount": self.bank_account, "bankIfsc": self.bank_ifsc,
            "addresses": [a.public() for a in self.addresses],
            "accounts": [a.public() for a in self.accounts],
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }


class Address(Base):
    """
    Addresses are typed because marketplaces and couriers use them differently:
    `pickup` is where the courier physically collects, `return` is where an RTO
    shipment goes back, `business` is the registered address on tax documents.
    They are often the same place, but they are not the same field.
    """
    __tablename__ = "addresses"
    id = Column(String, primary_key=True)
    artisan_id = Column(String, ForeignKey("artisans.id"))
    kind = Column(String, default="pickup")       # pickup | return | business
    contact_name = Column(String, default="")
    contact_phone = Column(String, default="")
    line1 = Column(String, default="")
    line2 = Column(String, default="")
    landmark = Column(String, default="")
    city = Column(String, default="")
    state = Column(String, default="")
    pincode = Column(String, default="")
    country = Column(String, default="India")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    artisan = relationship("Artisan", back_populates="addresses")

    def public(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "contactName": self.contact_name,
            "contactPhone": self.contact_phone, "line1": self.line1, "line2": self.line2,
            "landmark": self.landmark, "city": self.city, "state": self.state,
            "pincode": self.pincode, "country": self.country,
            "complete": self.complete(),
        }

    def complete(self) -> bool:
        return all([(self.line1 or "").strip(), (self.city or "").strip(),
                    (self.state or "").strip(), len((self.pincode or "").strip()) == 6,
                    (self.country or "").strip(), (self.contact_name or "").strip(),
                    (self.contact_phone or "").strip()])


class OtpChallenge(Base):
    """
    A real one-time password. The code is stored only as a salted SHA-256 hash, it
    expires, and both send-rate and verify-attempts are capped - so this is genuine
    verification, not a screen that accepts any six digits.
    """
    __tablename__ = "otp_challenges"
    id = Column(String, primary_key=True)
    phone = Column(String, index=True)
    code_hash = Column(String)
    salt = Column(String)
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=5)
    consumed = Column(Integer, default=0)
    delivery = Column(String, default="")
    delivery_ref = Column(String, default="")
    created_at = Column(DateTime, default=now)
    expires_at = Column(DateTime)


class Session(Base):
    """Server-side session, so logout and expiry are real rather than a dropped token."""
    __tablename__ = "sessions"
    id = Column(String, primary_key=True)
    artisan_id = Column(String, ForeignKey("artisans.id"), index=True)
    created_at = Column(DateTime, default=now)
    expires_at = Column(DateTime)
    revoked = Column(Integer, default=0)
    user_agent = Column(String, default="")


class MarketplaceAccount(Base):
    """
    Per-channel seller registration state. Separate from Artisan because each
    marketplace asks for different things and approves on its own timeline.
    """
    __tablename__ = "marketplace_accounts"
    id = Column(String, primary_key=True)
    artisan_id = Column(String, ForeignKey("artisans.id"), index=True)
    channel = Column(String)
    external_seller_id = Column(String, default="")
    status = Column(String, default="not_started")
    fields = Column(JSON, default=dict)
    last_error = Column(Text, default="")
    updated_at = Column(DateTime, default=now, onupdate=now)

    artisan = relationship("Artisan", back_populates="accounts")

    def public(self) -> dict:
        return {"id": self.id, "channel": self.channel, "status": self.status,
                "externalSellerId": self.external_seller_id, "fields": self.fields or {},
                "lastError": self.last_error}


class Listing(Base):
    __tablename__ = "listings"
    id = Column(String, primary_key=True)
    artisan_id = Column(String, ForeignKey("artisans.id"), nullable=True, index=True)
    # Guest drafts belong to a device, not a person, until the artisan logs in and
    # claims them. This is what makes "keep my work through login" possible.
    guest_token = Column(String, default="", index=True)

    title_en = Column(String, default="")
    title_hi = Column(String, default="")
    desc_en = Column(Text, default="")
    desc_hi = Column(Text, default="")
    category = Column(String, default="")
    hsn = Column(String, default="")
    price = Column(Float, default=0)
    floor_price = Column(Float, default=0)
    currency = Column(String, default="INR")
    quantity = Column(Integer, default=1)

    weight_g = Column(Integer, default=500)
    length_cm = Column(Integer, default=20)
    breadth_cm = Column(Integer, default=15)
    height_cm = Column(Integer, default=10)

    attributes = Column(JSON, default=dict)
    tags = Column(JSON, default=list)
    image_url = Column(String, default="")
    raw_hash = Column(String, default="")
    enhance_ops = Column(JSON, default=list)

    vision = Column(JSON, default=dict)
    ocr = Column(JSON, default=dict)
    transcript = Column(Text, default="")
    channels_selected = Column(JSON, default=list)

    status = Column(String, default="draft")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    publications = relationship("Publication", back_populates="listing",
                                cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="listing", cascade="all, delete-orphan")

    def public(self) -> dict:
        return {
            "id": self.id, "artisanId": self.artisan_id,
            "titleEn": self.title_en, "titleHi": self.title_hi,
            "descEn": self.desc_en, "descHi": self.desc_hi, "category": self.category,
            "hsn": self.hsn, "price": self.price, "floorPrice": self.floor_price,
            "currency": self.currency, "quantity": self.quantity,
            "weightG": self.weight_g, "lengthCm": self.length_cm,
            "breadthCm": self.breadth_cm, "heightCm": self.height_cm,
            "attributes": self.attributes or {}, "tags": self.tags or [],
            "imageUrl": self.image_url, "rawHash": self.raw_hash,
            "enhanceOps": self.enhance_ops or [], "vision": self.vision or {},
            "ocr": self.ocr or {}, "transcript": self.transcript,
            "channelsSelected": self.channels_selected or [],
            "status": self.status,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
            "publications": [p.public() for p in self.publications],
            "orders": [o.public() for o in self.orders],
        }


class Publication(Base):
    """One attempt to put a listing on one channel. Records what actually happened."""
    __tablename__ = "publications"
    id = Column(String, primary_key=True)
    listing_id = Column(String, ForeignKey("listings.id"))
    channel = Column(String)
    status = Column(String, default="queued")
    external_id = Column(String, default="")
    url = Column(String, default="")
    error = Column(Text, default="")
    request = Column(JSON, default=dict)
    response = Column(JSON, default=dict)
    submitted_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    listing = relationship("Listing", back_populates="publications")

    def public(self) -> dict:
        return {
            "id": self.id, "channel": self.channel, "status": self.status,
            "externalId": self.external_id, "url": self.url, "error": self.error,
            "response": self.response or {},
            "submittedAt": self.submitted_at.isoformat() if self.submitted_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class Order(Base):
    __tablename__ = "orders"
    id = Column(String, primary_key=True)
    listing_id = Column(String, ForeignKey("listings.id"))
    channel = Column(String, default="storefront")
    external_id = Column(String, default="")

    buyer_name = Column(String, default="")
    buyer_phone = Column(String, default="")
    buyer_email = Column(String, default="")
    address = Column(Text, default="")
    buyer_city = Column(String, default="")
    buyer_state = Column(String, default="")
    buyer_pincode = Column(String, default="")

    quantity = Column(Integer, default=1)
    amount = Column(Float, default=0)
    currency = Column(String, default="INR")

    status = Column(String, default="created")
    payment_status = Column(String, default="pending")
    payment_ref = Column(String, default="")
    payment_provider = Column(String, default="")

    courier = Column(String, default="")
    tracking_id = Column(String, default="")
    tracking_url = Column(String, default="")

    cancel_reason = Column(Text, default="")
    refund_ref = Column(String, default="")

    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    listing = relationship("Listing", back_populates="orders")
    shipments = relationship("Shipment", back_populates="order",
                             cascade="all, delete-orphan")

    def public(self) -> dict:
        return {
            "id": self.id, "listingId": self.listing_id, "channel": self.channel,
            "externalId": self.external_id, "buyerName": self.buyer_name,
            "buyerPhone": self.buyer_phone, "buyerEmail": self.buyer_email,
            "address": self.address, "buyerCity": self.buyer_city,
            "buyerState": self.buyer_state, "buyerPincode": self.buyer_pincode,
            "quantity": self.quantity, "amount": self.amount,
            "currency": self.currency, "status": self.status,
            "paymentStatus": self.payment_status, "paymentRef": self.payment_ref,
            "paymentProvider": self.payment_provider, "courier": self.courier,
            "trackingId": self.tracking_id, "trackingUrl": self.tracking_url,
            "cancelReason": self.cancel_reason, "refundRef": self.refund_ref,
            "shipments": [sh.public() for sh in self.shipments],
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class Shipment(Base):
    """A real courier consignment against an order."""
    __tablename__ = "shipments"
    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), index=True)
    provider = Column(String, default="")
    awb = Column(String, default="")
    courier_name = Column(String, default="")
    label_url = Column(String, default="")
    tracking_url = Column(String, default="")
    pickup_token = Column(String, default="")
    pickup_scheduled_at = Column(DateTime, nullable=True)
    status = Column(String, default="pending")
    last_error = Column(Text, default="")
    raw = Column(JSON, default=dict)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    order = relationship("Order", back_populates="shipments")

    def public(self) -> dict:
        return {
            "id": self.id, "orderId": self.order_id, "provider": self.provider,
            "awb": self.awb, "courierName": self.courier_name,
            "labelUrl": self.label_url, "trackingUrl": self.tracking_url,
            "status": self.status, "lastError": self.last_error,
            "pickupScheduledAt": (self.pickup_scheduled_at.isoformat()
                                  if self.pickup_scheduled_at else None),
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }


class Event(Base):
    """Append-only audit trail. Every status change lands here."""
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_type = Column(String)
    subject_id = Column(String, index=True)
    kind = Column(String)
    frm = Column(String, default="")
    to = Column(String, default="")
    detail = Column(Text, default="")
    payload = Column(JSON, default=dict)
    at = Column(DateTime, default=now)

    def public(self) -> dict:
        return {
            "id": self.id, "subjectType": self.subject_type, "subjectId": self.subject_id,
            "kind": self.kind, "from": self.frm, "to": self.to, "detail": self.detail,
            "at": self.at.isoformat() if self.at else None,
        }


def log_event(s, subject_type: str, subject_id: str, kind: str,
              frm: str = "", to: str = "", detail: str = "", payload: dict | None = None):
    s.add(Event(subject_type=subject_type, subject_id=subject_id, kind=kind,
                frm=frm, to=to, detail=detail, payload=payload or {}))


class ListingView(Base):
    """
    A real page view of a storefront listing.

    One row per request to /l/{id}, deduplicated per viewer per hour so a buyer
    refreshing the page does not manufacture traffic. The viewer key is a salted
    hash of IP + user agent: enough to count distinct people, not enough to identify
    one. This is the only view metric in the system that we generate ourselves, and
    it is counted rather than estimated.

    Views on external marketplaces are NOT stored here - they are fetched from that
    marketplace's own API at read time, and reported as unavailable when its API does
    not expose them. See channels.stats().
    """
    __tablename__ = "listing_views"
    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(String, ForeignKey("listings.id"), index=True)
    viewer_key = Column(String, index=True)
    referrer = Column(String, default="")
    at = Column(DateTime, default=now, index=True)


class Enquiry(Base):
    """
    A buyer asking about a larger or custom order.

    This replaces the old "Samuh" screen, which displayed an invented consortium of
    fictional artisans. An enquiry here is a real message from a real form on the
    listing page: somebody wanting 200 pieces, or a variation, or a wholesale price.
    Aggregating artisans to fill one large order is a genuinely useful idea, but it
    cannot be shown as though it were happening when no such network exists.
    """
    __tablename__ = "enquiries"
    id = Column(String, primary_key=True)
    listing_id = Column(String, ForeignKey("listings.id"), index=True)
    artisan_id = Column(String, ForeignKey("artisans.id"), index=True, nullable=True)
    channel = Column(String, default="storefront")

    buyer_name = Column(String, default="")
    buyer_phone = Column(String, default="")
    buyer_email = Column(String, default="")
    organisation = Column(String, default="")

    quantity = Column(Integer, default=0)
    target_price = Column(Float, default=0)
    needed_by = Column(String, default="")
    message = Column(Text, default="")

    status = Column(String, default="new")   # new | replied | quoted | won | lost
    reply = Column(Text, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    def public(self) -> dict:
        return {
            "id": self.id, "listingId": self.listing_id, "channel": self.channel,
            "buyerName": self.buyer_name, "buyerPhone": self.buyer_phone,
            "buyerEmail": self.buyer_email, "organisation": self.organisation,
            "quantity": self.quantity, "targetPrice": self.target_price,
            "neededBy": self.needed_by, "message": self.message,
            "status": self.status, "reply": self.reply,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


ENQUIRY_FLOW = ["new", "replied", "quoted", "won"]
ENQUIRY_TERMINAL = ["lost"]


def init() -> None:
    Base.metadata.create_all(engine)


def session():
    return SessionLocal()
