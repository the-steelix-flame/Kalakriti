"""
Persistence via SQLAlchemy - a real database, surviving restarts.

The deployment target is a hosted Postgres (Neon, Supabase or Render), named by
DATABASE_URL. SQLite is the fallback so the project still runs from a clean checkout
with nothing configured, but it is only a development convenience: on a hosted
container the SQLite file sits on an ephemeral disk and every artisan, listing and
order disappears at the next deploy.

Listings, publication attempts, orders, shipments, sessions and their status
transitions are all rows, which is what makes status tracking and the order
lifecycle real rather than a screen showing whatever the last button press set.

Every status change is appended to `events` rather than only mutating a column, so
the lifecycle is auditable: you can see when a listing went Draft -> Submitted and
what the platform actually replied with.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

log = logging.getLogger("db")


def _env(name: str, default: str) -> str:
    """
    Read an environment variable, treating a blank value as if it were unset.

    `os.getenv(name, default)` returns the default only when the variable is absent.
    An empty string is present, so it wins - and .env ships every variable as a blank
    placeholder for the user to paste a value next to, which means `DATABASE_URL=`
    silently becomes the connection URL. SQLAlchemy is then handed "" and the whole
    application fails at import, on a line that reads as obviously correct.

    Blank means "not configured yet" and falls back. A non-empty value that happens to
    be wrong is a different thing entirely and is still allowed to fail loudly, because
    someone who typed a URL needs to be told it is broken rather than quietly ignored.
    """
    value = os.getenv(name)
    return default if value is None or not value.strip() else value.strip()


RAW_DB_URL = _env("DATABASE_URL", "sqlite:///./kalakriti.db")

# How long a connection attempt may hang before it is abandoned. Without this a wrong
# host makes the TCP connect wait for the operating system's default timeout, which on
# Linux is around two minutes: the container looks hung at startup and the platform
# kills it before any error is logged.
CONNECT_TIMEOUT_S = int(_env("DB_CONNECT_TIMEOUT", "10"))


def _normalise_url(raw: str) -> str:
    """
    Turn whatever a hosting provider handed out into a URL SQLAlchemy 2.x accepts.

    Neon, Supabase, Render and Heroku all print connection strings beginning with
    `postgres://`. SQLAlchemy 2.x removed that alias and raises `NoSuchModuleError`
    for it, so the deploy fails on a purely cosmetic difference. Both `postgres://`
    and `postgresql://` are rewritten to name a driver explicitly, because leaving
    the driver unspecified makes SQLAlchemy reach for psycopg2, which is not what
    this project installs (requirements.txt pins psycopg 3).

    An explicit driver the caller already chose - `postgresql+psycopg2://`, say - is
    left alone.
    """
    raw = (raw or "").strip()
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        raw = "postgresql+" + _pg_driver() + "://" + raw[len("postgresql://"):]
    return raw


def _pg_driver() -> str:
    """
    Prefer psycopg 3, fall back to psycopg2 if that is what is actually installed.

    Naming a driver that is absent produces `ModuleNotFoundError: No module named
    'psycopg'` from deep inside the dialect loader, which reads like a bug in the app
    rather than a missing dependency. Choosing the one that imports keeps the failure
    honest on machines where only the older driver is present.
    """
    try:
        import psycopg  # noqa: F401
        return "psycopg"
    except ImportError:
        try:
            import psycopg2  # noqa: F401
            return "psycopg2"
        except ImportError:
            # Neither is installed. Name psycopg anyway so the resulting error points
            # at the dependency this project declares.
            return "psycopg"


DB_URL = _normalise_url(RAW_DB_URL)
IS_SQLITE = DB_URL.startswith("sqlite")


def safe_url() -> str:
    """The connection URL with the password removed, for logs and health output."""
    try:
        return make_url(DB_URL).render_as_string(hide_password=True)
    except Exception:
        # Unparseable, so there is no password field to hide. Anything before an "@"
        # is dropped anyway, because a URL malformed enough to fail parsing can still
        # contain real credentials and this string is going into a log.
        shown = DB_URL.split("@")[-1] if "@" in DB_URL else DB_URL
        return f"<unparseable: ...{shown}>"


def _engine_kwargs() -> dict:
    if IS_SQLITE:
        # SQLite is the local-development fallback. The one thing it needs is
        # permission to be used from more than one thread, because FastAPI serves
        # requests on a thread pool and the background job workers have their own.
        return {"connect_args": {"check_same_thread": False}}

    return {
        # The single most important setting for a hosted Postgres. Neon and Supabase
        # close idle connections, and a scale-to-zero instance drops every one of them
        # when it suspends. A pooled connection that has been sitting since before
        # that is dead, and the next query on it raises OperationalError - which the
        # artisan sees as a random 500 on whatever screen she happened to open first
        # after lunch. pre_ping spends one cheap round trip verifying the connection
        # and transparently replaces it if it has gone.
        "pool_pre_ping": True,

        # Discard connections before anything upstream does it for us. Supabase's
        # pooler and most cloud load balancers cut idle connections at five minutes;
        # 280 seconds keeps us inside that window, so recycling is our decision rather
        # than a surprise mid-query reset.
        "pool_recycle": 280,

        # Sized for one uvicorn worker on a small container. Hosted Postgres plans
        # cap total connections aggressively - Neon's free tier and Supabase's pooler
        # both count them - so a large pool per container is how a two-instance deploy
        # locks itself out of its own database.
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,

        "connect_args": {
            "connect_timeout": CONNECT_TIMEOUT_S,
            # Shows up in pg_stat_activity, which is the only way to tell this app's
            # connections apart from a migration script's when the pool fills up.
            "application_name": _env("APP_NAME", "kalakriti"),
        },
    }


try:
    engine = create_engine(DB_URL, **_engine_kwargs())
except Exception as exc:  # pragma: no cover - configuration failure, not logic
    # create_engine does not connect, but it does load the dialect and its driver, so
    # this is where a missing psycopg or a malformed URL surfaces. Left as a raw
    # traceback it looks like an import bug somewhere in SQLAlchemy; naming DATABASE_URL
    # points at the thing that actually needs changing.
    hint = ("\nInstall the Postgres driver with: pip install 'psycopg[binary]'"
            if isinstance(exc, ImportError) or "no module named" in str(exc).lower()
            else "")
    raise RuntimeError(
        f"Cannot configure the database from DATABASE_URL={safe_url()}: {exc}.{hint}"
    ) from exc
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()

# JSONB on Postgres, plain JSON everywhere else. JSONB is stored parsed rather than as
# text, so it can be indexed and queried by key; plain JSON on Postgres is a string
# column that happens to be validated on write. Python-facing behaviour is identical -
# dicts and lists in, dicts and lists out - so nothing else in the codebase changes.
# On SQLite the variant is ignored and this stays the ordinary JSON type.
JSONType = JSON().with_variant(postgresql.JSONB, "postgresql")


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
    fields = Column(JSONType, default=dict)
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

    attributes = Column(JSONType, default=dict)
    tags = Column(JSONType, default=list)
    image_url = Column(String, default="")
    raw_hash = Column(String, default="")
    enhance_ops = Column(JSONType, default=list)

    vision = Column(JSONType, default=dict)
    ocr = Column(JSONType, default=dict)
    transcript = Column(Text, default="")
    channels_selected = Column(JSONType, default=list)

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
    request = Column(JSONType, default=dict)
    response = Column(JSONType, default=dict)
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
    raw = Column(JSONType, default=dict)
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
    payload = Column(JSONType, default=dict)
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


def _diagnose(exc: BaseException) -> str:
    """
    Turn a driver exception into the one sentence that says what to fix.

    A failed startup normally logs a traceback ending in `OperationalError`, whose
    text is a wall of libpq detail. Whoever is deploying at the time needs to know
    which of four different mistakes they made, so the categories are matched on the
    message the driver actually produces.
    """
    msg = str(exc).lower()
    if "password authentication failed" in msg or ("role" in msg and "does not exist" in msg):
        return "the database rejected the username or password in DATABASE_URL"
    if "database" in msg and "does not exist" in msg:
        return "the database named in DATABASE_URL does not exist on that server"
    if ("could not translate host name" in msg or "name or service not known" in msg
            or "nodename nor servname" in msg or "getaddrinfo" in msg):
        return "the host in DATABASE_URL does not resolve - check the hostname"
    if "connection refused" in msg or "could not connect to server" in msg:
        return ("nothing is listening at that host and port - check the port, and "
                "whether the database is paused")
    if "timeout" in msg or "timed out" in msg:
        return (f"the connection timed out after {CONNECT_TIMEOUT_S}s - the host may be "
                "unreachable from here, or blocked by an IP allow-list")
    if "ssl" in msg:
        return "the TLS handshake failed - hosted Postgres usually needs ?sslmode=require"
    if "no such module" in msg or "modulenotfounderror" in msg or "can't load plugin" in msg:
        return "the Postgres driver is not installed - pip install 'psycopg[binary]'"
    return "the database could not be reached"


def init(attempts: int = 4) -> None:
    """
    Create any missing tables, retrying while the database wakes up.

    A serverless Postgres that has scaled to zero takes a few seconds to resume, and
    the first connection during that window fails outright. If startup gives up on
    that first failure the container exits, the platform restarts it, and it fails
    again for exactly as long as the database is cold - so the deploy looks broken
    when nothing is wrong. Backing off and trying again costs a few seconds and
    removes that whole failure mode.

    Retrying cannot fix a wrong password or a bad hostname, so on final failure this
    raises with the specific problem named rather than the raw driver traceback.
    """
    delay = 1.0
    last: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            Base.metadata.create_all(engine)
            if attempt > 1:
                log.info("database ready after %d attempts", attempt)
            return
        except (SQLAlchemyError, OSError) as exc:
            last = exc
            if attempt >= attempts:
                break
            log.warning("database not ready (attempt %d/%d): %s", attempt, attempts, exc)
            time.sleep(delay)
            delay *= 2

    raise RuntimeError(
        f"Cannot initialise the database at {safe_url()}: {_diagnose(last)}. "
        f"Underlying error: {last}"
    ) from last


def health() -> dict:
    """
    What this process can actually see of its database, right now.

    Deliberately never raises. It is meant to be called from a health endpoint, where
    an exception would turn a report of a broken database into a broken report - the
    caller gets `ok: False` and the error text instead.
    """
    out: dict = {
        "engine": engine.name,
        "url": safe_url(),
        "ok": False,
        "latencyMs": None,
        "pool": {},
        "error": None,
    }
    started = time.perf_counter()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1")).scalar()
        out["ok"] = True
        out["latencyMs"] = round((time.perf_counter() - started) * 1000, 1)
    except Exception as exc:
        # Latency is still recorded on failure: a timeout at the full connect timeout
        # tells a different story from an instant refusal.
        out["latencyMs"] = round((time.perf_counter() - started) * 1000, 1)
        out["error"] = f"{_diagnose(exc)}: {exc}"

    # Pool counters exist on QueuePool but not on every pool implementation, so each
    # one is read defensively rather than assumed.
    try:
        pool = engine.pool
        for key, attr in (("size", "size"), ("checkedIn", "checkedin"),
                          ("checkedOut", "checkedout"), ("overflow", "overflow")):
            fn = getattr(pool, attr, None)
            if callable(fn):
                out["pool"][key] = fn()
        out["pool"]["class"] = type(pool).__name__
    except Exception as exc:
        out["pool"] = {"error": str(exc)}

    return out


def session():
    return SessionLocal()
