"""
Order lifecycle: the stages an order passes through, who may move it, and the history.

Why a module rather than a column
---------------------------------
`Order.status` already exists and already changes. What it cannot do is answer "who
said the product was ready, and when" - it holds one word and overwrites it. An order
that a buyer is waiting on and three parties are handling needs the opposite: an
append-only record where every transition keeps its author, its time and any evidence.

That record already exists too. `db.Event` is append-only and every status change in
the app is supposed to land there. So this module writes the stage into `Order.status`
for the fast read, and the transition into `db.Event` for the history, and nothing
here invents a second table to disagree with them.

Permissions are the substance of this file
------------------------------------------
The stages are not a free-for-all. An artisan owns the part of the journey her hands
are on - preparing, packaging, handing over - and nothing after it. Operations owns
receiving, checking and dispatch. A buyer owns none of it and may only read.

Getting this wrong is not a cosmetic bug: an artisan who can mark an order delivered
can be paid for a parcel nobody received, and settlement is computed from these
stages. So every transition is checked against the caller's role AND their
relationship to the order, and a refusal says which of the two failed.
"""
from __future__ import annotations

import logging
from typing import Any

import db

log = logging.getLogger("transit")

# ─────────────────────────────────────────────────────────────────── the stages

# The order of this list is the order of the journey, and the tracker draws it in
# exactly this sequence. `key` is what goes in Order.status; `label` is the English
# shown to operations - the app translates for the artisan through its own catalogue.
#
# `actor` is who may move an order INTO this stage. Deliberately the entering role
# rather than a set of allowed editors, because that is how the real handover works:
# the artisan says "handed over" and the collection point says "received" - the same
# physical moment recorded from both sides, by both parties.
STAGES: list[dict[str, Any]] = [
    {"key": "initiated", "label": "Order initiated", "actor": "system"},
    {"key": "confirmed", "label": "Order confirmed", "actor": "system"},
    {"key": "assigned", "label": "Artisan assigned", "actor": "ops"},
    {"key": "preparing", "label": "Artisan preparing", "actor": "artisan"},
    {"key": "ready", "label": "Product ready", "actor": "artisan"},
    {"key": "packaging", "label": "Packaging", "actor": "artisan"},
    {"key": "packaged", "label": "Packaging complete", "actor": "artisan",
     "needs_proof": True},
    {"key": "pickup_ready", "label": "Ready for pickup", "actor": "artisan"},
    {"key": "picked_up", "label": "Picked up from artisan", "actor": "ops"},
    {"key": "at_collection", "label": "At collection point", "actor": "ops"},
    {"key": "qc", "label": "Quality and quantity check", "actor": "ops"},
    {"key": "grn", "label": "GRN generated", "actor": "ops"},
    {"key": "at_warehouse", "label": "At warehouse / dispatch", "actor": "ops"},
    {"key": "shipment", "label": "Shipment created", "actor": "ops"},
    {"key": "in_transit", "label": "In transit", "actor": "ops"},
    {"key": "out_for_delivery", "label": "Out for delivery", "actor": "ops"},
    {"key": "delivered", "label": "Delivered", "actor": "ops"},
    {"key": "settled", "label": "Payment settled", "actor": "ops"},
]

ORDER = [s["key"] for s in STAGES]
BY_KEY = {s["key"]: s for s in STAGES}

# `Order.status` predates this module and already holds these words. Mapping them in
# rather than rewriting the rows means an order created last week still draws a
# tracker, instead of showing an empty one because its status is not in the list.
LEGACY = {
    "created": "initiated",
    # What /v1/orders actually writes when Razorpay is configured: the row exists and
    # the buyer has not paid yet. Missing from this map, it fell through to the
    # `initiated` default anyway - but by accident rather than by decision, and an
    # unmapped status silently means "stage 0" for every future status too.
    "payment_pending": "initiated",
    "paid": "confirmed",
    "confirmed": "confirmed",
    "shipped": "in_transit",
    "delivered": "delivered",
    "cancelled": "cancelled",
}

# Derived from STAGES rather than repeated, so the two can never drift apart.
ARTISAN_STAGES = [s["key"] for s in STAGES if s["actor"] == "artisan"]
OPS_STAGES = [s["key"] for s in STAGES if s["actor"] == "ops"]


def stage_of(order: db.Order) -> str:
    """The order's current stage, with legacy statuses translated."""
    raw = (order.status or "").strip()
    return LEGACY.get(raw, raw if raw in BY_KEY else "initiated")


def index_of(stage: str) -> int:
    try:
        return ORDER.index(stage)
    except ValueError:
        return -1


# ────────────────────────────────────────────────────────────────── permissions

def role_of(s, artisan: db.Artisan | None, order: db.Order) -> str:
    """
    What is this caller, relative to THIS order?

    Not a global role. The same person is `artisan` on an order for a pot she made and
    `ops` on an order her cluster is coordinating, and both are true at once. So the
    answer depends on the pair, not on the account.
    """
    if artisan is None:
        return "guest"

    lst = s.get(db.Listing, order.listing_id) if order.listing_id else None

    # The cluster owner coordinates the journey for everything the cluster sells and
    # is the seller of record on it. That is the operations role in this system.
    if lst is not None and lst.cluster_id:
        cl = s.get(db.Cluster, lst.cluster_id)
        if cl is not None and cl.owner_artisan_id == artisan.id:
            return "ops"

    if lst is not None and lst.artisan_id == artisan.id:
        # A solo seller running her own storefront is both maker and operations,
        # because there is nobody else. Pretending otherwise would leave her unable to
        # mark her own parcel dispatched.
        return "artisan" if lst.cluster_id else "solo"

    return "guest"


def may_enter(role: str, stage: str) -> tuple[bool, str]:
    """May this role move an order into this stage? Returns (allowed, why not)."""
    spec = BY_KEY.get(stage)
    if spec is None:
        return False, f"{stage!r} is not a stage of an order"
    actor = spec["actor"]
    if role == "solo":
        # One person doing both jobs. Still not `system`: initiated and confirmed are
        # written by the checkout, not by a seller deciding an order exists.
        return (actor in ("artisan", "ops"),
                "that stage is recorded by the checkout, not by the seller")
    if role == "ops":
        return (actor == "ops",
                "that stage belongs to the artisan who is making the product")
    if role == "artisan":
        return (actor == "artisan",
                "that stage belongs to whoever is coordinating the order")
    return False, "you are not a party to this order"


# ───────────────────────────────────────────────────────────────── transitions

def advance(s, order: db.Order, stage: str, *, artisan: db.Artisan | None,
            note: str = "", location: str = "",
            media_url: str = "") -> dict[str, Any]:
    """
    Move an order to `stage`, recording who did it.

    Raises ValueError with a readable message on anything refused, so the caller can
    turn it into one HTTP status and one sentence rather than guessing.
    """
    role = role_of(s, artisan, order)
    if role == "guest":
        raise ValueError("this order is not yours to update")

    allowed, why = may_enter(role, stage)
    if not allowed:
        raise ValueError(why)

    current = stage_of(order)
    if current == "cancelled":
        raise ValueError("this order was cancelled and cannot move on")

    here, there = index_of(current), index_of(stage)
    if there < 0:
        raise ValueError(f"{stage!r} is not a stage of an order")
    if there == here:
        raise ValueError(f"this order is already at {BY_KEY[stage]['label'].lower()}")
    if there < here and role in ("artisan",):
        # Going backwards is a correction, and corrections are somebody's decision to
        # own rather than a side effect of tapping the wrong row. Ops may do it; the
        # artisan cannot walk an order back out of the warehouse.
        raise ValueError(f"this order has already moved past "
                         f"{BY_KEY[stage]['label'].lower()}")

    spec = BY_KEY[stage]
    if spec.get("needs_proof") and not (media_url or _proof_url(s, order.id)):
        raise ValueError("a packaging video is required before packaging can be "
                         "marked complete")

    order.status = stage
    db.log_event(
        s, "order", order.id, "status", current, stage,
        detail=note or spec["label"],
        payload={
            "stage": stage,
            "label": spec["label"],
            "byArtisanId": artisan.id if artisan else "",
            "byName": (artisan.full_name or artisan.business_name) if artisan else "",
            "role": role,
            "note": note,
            "location": location,
            "mediaUrl": media_url,
        })
    log.info("order %s %s -> %s by %s (%s)", order.id, current, stage,
             artisan.id if artisan else "-", role)
    return {"orderId": order.id, "stage": stage, "role": role}


def _proof_url(s, order_id: str) -> str:
    """The packaging video already on this order, if one was uploaded earlier."""
    rows = (s.query(db.Event)
            .filter(db.Event.subject_type == "order",
                    db.Event.subject_id == order_id,
                    db.Event.kind == "packaging_proof")
            .order_by(db.Event.at.desc()).all())
    for ev in rows:
        url = (ev.payload or {}).get("mediaUrl") or ""
        if url:
            return url
    return ""


def attach_proof(s, order: db.Order, *, artisan: db.Artisan, url: str,
                 note: str = "", seconds: float = 0.0) -> dict[str, Any]:
    """
    Record a packaging video against an order.

    Its own event kind rather than a status change, because it is evidence and not a
    stage: the artisan may retake it, and each attempt is kept, so the record shows
    what was actually submitted and when.
    """
    role = role_of(s, artisan, order)
    if role not in ("artisan", "solo"):
        raise ValueError("only the artisan making this product can add packaging proof")
    db.log_event(
        s, "order", order.id, "packaging_proof", "", "",
        detail=note or "packaging video uploaded",
        payload={"mediaUrl": url, "seconds": round(float(seconds or 0), 1),
                 "byArtisanId": artisan.id,
                 "byName": artisan.full_name or artisan.business_name,
                 "role": role, "note": note})
    return {"orderId": order.id, "mediaUrl": url}


# ───────────────────────────────────────────────────────────────── exceptions

EXCEPTIONS = ["damaged", "missing", "quantity_mismatch", "quality_failed",
              "pickup_delayed", "shipment_delayed", "wrong_product",
              "address_problem", "packaging_problem"]


def raise_exception(s, order: db.Order, *, artisan: db.Artisan, kind: str,
                    description: str, media_url: str = "") -> dict[str, Any]:
    """Flag something wrong with an order, without moving its stage."""
    if kind not in EXCEPTIONS:
        raise ValueError(f"{kind!r} is not a known exception type")
    role = role_of(s, artisan, order)
    if role == "guest":
        raise ValueError("this order is not yours to flag")
    db.log_event(
        s, "order", order.id, "exception", "", kind,
        detail=description or kind.replace("_", " "),
        payload={"exceptionType": kind, "description": description,
                 "byArtisanId": artisan.id,
                 "byName": artisan.full_name or artisan.business_name,
                 "role": role, "mediaUrl": media_url, "resolved": False})
    return {"orderId": order.id, "exception": kind}


# ─────────────────────────────────────────────────────────────────── the tracker

def timeline(s, order: db.Order, *, viewer: db.Artisan | None = None) -> dict[str, Any]:
    """
    The whole journey of one order: what is done, where it is, what is left.

    `steps` carries the real event for each completed stage - its time, its author and
    any evidence - so the tracker is drawn from the history rather than from the single
    current status. A stage that was never recorded shows as done-without-a-time when
    the order is past it, because claiming a timestamp we do not have would be
    inventing the record this exists to keep.
    """
    events = (s.query(db.Event)
              .filter(db.Event.subject_type == "order",
                      db.Event.subject_id == order.id)
              .order_by(db.Event.at.asc(), db.Event.id.asc()).all())

    first_at: dict[str, Any] = {}
    for ev in events:
        if ev.kind != "status":
            continue
        key = (ev.payload or {}).get("stage") or ev.to
        if key in BY_KEY and key not in first_at:
            first_at[key] = ev

    current = stage_of(order)
    here = index_of(current)
    proof = _proof_url(s, order.id)

    steps = []
    for i, spec in enumerate(STAGES):
        ev = first_at.get(spec["key"])
        p = (ev.payload or {}) if ev is not None else {}
        steps.append({
            "key": spec["key"],
            "label": spec["label"],
            "actor": spec["actor"],
            "state": ("done" if (ev is not None or i < here)
                      else "current" if i == here else "upcoming"),
            "at": ev.at.isoformat() if (ev is not None and ev.at) else None,
            "by": p.get("byName") or "",
            "role": p.get("role") or "",
            "note": p.get("note") or "",
            "location": p.get("location") or "",
            "mediaUrl": p.get("mediaUrl") or "",
        })

    role = role_of(s, viewer, order)
    # Only the next stage forward is offered. A list of every stage the role could
    # ever enter would let an artisan skip from preparing straight to handover, and the
    # tracker would then claim stages that never happened.
    nxt = [k for k in ORDER if index_of(k) > here and may_enter(role, k)[0]][:1]

    lst = s.get(db.Listing, order.listing_id) if order.listing_id else None
    maker = s.get(db.Artisan, lst.artisan_id) if (lst and lst.artisan_id) else None

    exceptions = [{
        "type": (e.payload or {}).get("exceptionType") or e.to,
        "description": (e.payload or {}).get("description") or e.detail,
        "by": (e.payload or {}).get("byName") or "",
        "at": e.at.isoformat() if e.at else None,
        "mediaUrl": (e.payload or {}).get("mediaUrl") or "",
    } for e in events if e.kind == "exception"]

    return {
        "orderId": order.id,
        "stage": current,
        "stageLabel": BY_KEY.get(current, {}).get("label", current),
        "cancelled": current == "cancelled",
        "steps": steps,
        "history": [{
            "kind": e.kind,
            "from": e.frm, "to": e.to,
            "detail": e.detail,
            "at": e.at.isoformat() if e.at else None,
            "by": (e.payload or {}).get("byName") or "",
            "role": (e.payload or {}).get("role") or "",
            "mediaUrl": (e.payload or {}).get("mediaUrl") or "",
        } for e in events],
        "packagingVideoUrl": proof,
        "exceptions": exceptions,
        "viewerRole": role,
        "canAdvanceTo": nxt,
        "product": {
            "listingId": lst.id if lst else "",
            "title": (lst.title_en or lst.title_hi) if lst else "",
            "imageUrl": lst.image_url if lst else "",
        },
        "artisan": {
            "id": maker.id if maker else "",
            "name": (maker.full_name or maker.business_name) if maker else "",
        },
        "quantity": order.quantity or 0,
        "amount": order.amount or 0,
        "buyerName": order.buyer_name or "",
        "updatedAt": order.updated_at.isoformat() if order.updated_at else None,
    }


def board(s, artisan: db.Artisan) -> dict[str, Any]:
    """
    Counts per stage for the operations dashboard, scoped to what this person runs.

    Only orders on listings sold through a cluster this artisan owns, or listings that
    are their own. Anything else is somebody else's business and must not be counted
    into their board.
    """
    owned = [c.id for c in s.query(db.Cluster)
             .filter(db.Cluster.owner_artisan_id == artisan.id).all()]
    q = (s.query(db.Order, db.Listing)
         .join(db.Listing, db.Order.listing_id == db.Listing.id))
    if owned:
        q = q.filter((db.Listing.cluster_id.in_(owned))
                     | (db.Listing.artisan_id == artisan.id))
    else:
        q = q.filter(db.Listing.artisan_id == artisan.id)

    counts: dict[str, int] = {k: 0 for k in ORDER}
    counts["cancelled"] = 0
    rows = []
    for order, lst in q.order_by(db.Order.created_at.desc()).limit(200).all():
        st = stage_of(order)
        counts[st] = counts.get(st, 0) + 1
        rows.append({
            "orderId": order.id,
            "stage": st,
            "stageLabel": BY_KEY.get(st, {}).get("label", st),
            "title": lst.title_en or lst.title_hi or "",
            "imageUrl": lst.image_url or "",
            "quantity": order.quantity or 0,
            "amount": order.amount or 0,
            "buyerName": order.buyer_name or "",
            "at": order.created_at.isoformat() if order.created_at else None,
        })

    return {
        "stages": [{"key": x["key"], "label": x["label"], "actor": x["actor"],
                    "count": counts.get(x["key"], 0)} for x in STAGES],
        "cancelled": counts.get("cancelled", 0),
        "orders": rows,
        "total": len(rows),
    }
