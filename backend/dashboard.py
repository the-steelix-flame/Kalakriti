"""
The operations dashboard: five headline numbers, and the lists behind each of them.

Why this is not `analytics.summary`
-----------------------------------
`analytics.summary` answers "how is my own work doing" for one artisan. This answers a
different question, asked by whoever is running things: what needs a decision today,
across every product and order I am responsible for. The scope is the difference. An
artisan sees her own rows; a cluster owner sees every row their cluster is the seller
of record for, because they are the one a marketplace, a courier and a buyer will hold
answerable.

Every number here is a query. Not one of them is an estimate, and where a figure genuinely
cannot be known the field says so rather than showing a zero - a pending payout of zero
and a pending payout that has never been computed look identical on a card, and only one
of them means "nothing to do".

Each metric ships with the ids behind it, so tapping a card opens the rows that produced
the number instead of a screen that has to go and ask again. That is also what stops the
two disagreeing.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, or_

import db
import transit

log = logging.getLogger("dashboard")

# Orders still in flight. Anything not in this set is finished business: delivered and
# settled are done, cancelled is gone.
OPEN_STAGES = [k for k in transit.ORDER if k not in ("delivered", "settled")]


def _scope(s, artisan: db.Artisan) -> tuple[list[str], list[str]]:
    """
    Which listings this person is answerable for, and the clusters they own.

    Two sources, deliberately unioned rather than one or the other: a cluster owner is
    usually also a maker, and their own storefront listings belong on their dashboard
    next to the cluster's.
    """
    owned = [c.id for c in s.query(db.Cluster)
             .filter(db.Cluster.owner_artisan_id == artisan.id).all()]
    q = s.query(db.Listing.id)
    if owned:
        q = q.filter(or_(db.Listing.cluster_id.in_(owned),
                         db.Listing.artisan_id == artisan.id))
    else:
        q = q.filter(db.Listing.artisan_id == artisan.id)
    return [lid for (lid,) in q.all()], owned


def metrics(s, artisan: db.Artisan) -> dict[str, Any]:
    """The five headline numbers, each with the ids that produced it."""
    listing_ids, owned = _scope(s, artisan)

    lsts = (s.query(db.Listing).filter(db.Listing.id.in_(listing_ids)).all()
            if listing_ids else [])

    # 1. Pending reviews: made, but not yet on a shelf. These are the rows waiting on
    #    a person - finish the details, or publish it - which is what makes the number
    #    actionable rather than decorative.
    pending = [l for l in lsts if l.status in ("draft", "processing")]

    # 2. Live products: actually purchasable. The same rule the shop uses, including
    #    the photograph, so this count and the shop can never disagree.
    live = [l for l in lsts if l.status in ("active", "published")
            and (l.quantity or 0) > 0 and (l.image_url or "").strip()]

    orders = (s.query(db.Order).filter(db.Order.listing_id.in_(listing_ids)).all()
              if listing_ids else [])

    # 3. Active orders: somebody is waiting for a parcel.
    active = [o for o in orders
              if transit.stage_of(o) in OPEN_STAGES
              and transit.stage_of(o) != "cancelled"]

    # 4. Pending payouts. Only from settlements that have actually been computed:
    #    `distributable_amount` on a draft settlement is a real number somebody worked
    #    out, and a cluster with no settlements yet has an unknown payout rather than a
    #    zero one. Saying zero there would tell the owner there is nothing owed.
    setts = (s.query(db.Settlement).filter(db.Settlement.cluster_id.in_(owned)).all()
             if owned else [])
    unpaid = [x for x in setts if (x.status or "") != "paid"]
    payout_known = bool(setts)
    payout = round(sum(float(x.distributable_amount or 0) for x in unpaid), 2)

    # 5. Total revenue: money that actually arrived. Paid orders only - an unpaid order
    #    is a hope, and putting it in a revenue figure is how a dashboard starts lying.
    paid = [o for o in orders if (o.payment_status or "") == "paid"]
    revenue = round(sum(float(o.amount or 0) for o in paid), 2)

    return {
        "pendingReviews": {
            "value": len(pending), "available": True,
            "ids": [l.id for l in pending][:50],
        },
        "liveProducts": {
            "value": len(live), "available": True,
            "ids": [l.id for l in live][:50],
        },
        "activeOrders": {
            "value": len(active), "available": True,
            "ids": [o.id for o in active][:50],
        },
        "pendingPayouts": {
            "value": payout if payout_known else None,
            "available": payout_known,
            "why": ("" if payout_known else
                    "No settlement has been computed for your cluster yet, so there "
                    "is nothing to pay out from. This is not the same as zero owed."),
            "count": len(unpaid),
        },
        "totalRevenue": {
            "value": revenue, "available": True,
            "count": len(paid),
            "why": ("" if paid else
                    "No order has been paid for yet, so revenue really is zero."),
        },
        "clustersOwned": len(owned),
        "isOperator": bool(owned),
    }


def _card(s, l: db.Listing) -> dict[str, Any]:
    """A listing as the dashboard lists it: enough to recognise and act on."""
    pubs = (s.query(db.Publication)
            .filter(db.Publication.listing_id == l.id).all())
    return {
        "id": l.id,
        "title": l.title_en or l.title_hi or "",
        "imageUrl": l.image_url or "",
        "price": l.price or 0,
        "quantity": l.quantity or 0,
        "status": l.status or "",
        "clusterId": l.cluster_id or "",
        "channels": sorted({p.channel for p in pubs if p.status == "live"}),
        "updatedAt": l.updated_at.isoformat() if l.updated_at else None,
    }


def pending_reviews(s, artisan: db.Artisan) -> dict[str, Any]:
    """
    Products waiting on a person before they can sell.

    Split by what is actually missing, because "needs review" is two different jobs:
    a row with no title needs the details finishing, and a finished row that is still
    a draft needs publishing. Telling them apart is the difference between a queue and
    a pile.
    """
    listing_ids, _ = _scope(s, artisan)
    rows = (s.query(db.Listing)
            .filter(db.Listing.id.in_(listing_ids),
                    db.Listing.status.in_(("draft", "processing")))
            .order_by(db.Listing.updated_at.desc()).all()
            if listing_ids else [])

    incomplete, ready = [], []
    for l in rows:
        card = _card(s, l)
        missing = []
        if not (l.title_en or l.title_hi or "").strip():
            missing.append("name")
        if not (l.price or 0):
            missing.append("price")
        if not (l.image_url or "").strip():
            missing.append("photograph")
        if not (l.category or "").strip():
            missing.append("category")
        card["missing"] = missing
        (incomplete if missing else ready).append(card)

    return {"needsDetails": incomplete, "readyToPublish": ready,
            "total": len(rows)}


def live_products(s, artisan: db.Artisan) -> dict[str, Any]:
    """
    What is on sale, and where.

    Grouped by channel from the `publications` table rather than from the listing's own
    status, because "live on ONDC" is a fact about a publication that ONDC accepted -
    not about our intention to send it. A listing can be active here and rejected
    there, and the dashboard has to show the second one.
    """
    listing_ids, _ = _scope(s, artisan)
    rows = (s.query(db.Listing)
            .filter(db.Listing.id.in_(listing_ids),
                    db.Listing.status.in_(("active", "published")))
            .order_by(db.Listing.updated_at.desc()).all()
            if listing_ids else [])

    cards = [_card(s, l) for l in rows]
    by_channel: dict[str, list[dict]] = {}
    for c in cards:
        for ch in c["channels"]:
            by_channel.setdefault(ch, []).append(c)

    # The storefront needs no credentials and no GST, so it is always available and is
    # where everything lands first.
    on_storefront = [c for c in cards
                     if c["quantity"] > 0 and c["imageUrl"]]

    return {
        "all": cards,
        "storefront": on_storefront,
        "byChannel": by_channel,
        "channels": sorted(by_channel.keys()),
        "total": len(cards),
    }


def cluster_view(s, artisan: db.Artisan, cluster_id: str) -> dict[str, Any]:
    """
    One cluster, from its operator's side: its listings and its orders.

    Refuses anybody who does not own it. A cluster's order book names its buyers and
    their addresses, and a member is not entitled to that just by being a member.
    """
    cl = s.get(db.Cluster, cluster_id)
    if cl is None:
        raise ValueError("no such cluster")
    if cl.owner_artisan_id != artisan.id:
        raise PermissionError("only the person running this cluster can see its "
                              "orders and listings")

    lsts = (s.query(db.Listing)
            .filter(db.Listing.cluster_id == cluster_id)
            .order_by(db.Listing.updated_at.desc()).all())
    ids = [l.id for l in lsts]
    orders = (s.query(db.Order).filter(db.Order.listing_id.in_(ids)).all()
              if ids else [])

    cards = [_card(s, l) for l in lsts]
    pending = [c for c in cards if c["status"] in ("draft", "processing")]
    by_channel: dict[str, list[dict]] = {}
    for c in cards:
        for ch in c["channels"]:
            by_channel.setdefault(ch, []).append(c)

    order_rows = []
    for o in orders:
        stage = transit.stage_of(o)
        lst = next((l for l in lsts if l.id == o.listing_id), None)
        order_rows.append({
            "orderId": o.id,
            "stage": stage,
            "stageLabel": transit.BY_KEY.get(stage, {}).get("label", stage),
            "title": (lst.title_en or lst.title_hi) if lst else "",
            "imageUrl": lst.image_url if lst else "",
            "quantity": o.quantity or 0,
            "amount": o.amount or 0,
            "buyerName": o.buyer_name or "",
            "paymentStatus": o.payment_status or "",
            "at": o.created_at.isoformat() if o.created_at else None,
        })
    active = [r for r in order_rows if r["stage"] in OPEN_STAGES]

    # Bulk and custom enquiries are the splitting work: one buyer wants 200 pieces and
    # the cluster has to divide it across its members' capacity.
    enq = (s.query(db.Enquiry)
           .filter(db.Enquiry.listing_id.in_(ids)).all() if ids else [])
    splitting = [{
        "id": e.id,
        "buyer": e.buyer_name or e.organisation or "",
        "organisation": e.organisation or "",
        "quantity": e.quantity or 0,
        "targetPrice": e.target_price or 0,
        "neededBy": e.needed_by or "",
        "status": e.status or "new",
        "message": (e.message or "")[:280],
        "at": e.created_at.isoformat() if getattr(e, "created_at", None) else None,
    } for e in enq if (e.status or "new") in ("new", "replied", "quoted")]

    members = (s.query(db.ClusterMembership, db.Artisan)
               .join(db.Artisan, db.ClusterMembership.artisan_id == db.Artisan.id)
               .filter(db.ClusterMembership.cluster_id == cluster_id,
                       db.ClusterMembership.status == "active").all())

    return {
        "cluster": {"id": cl.id, "name": cl.name or "",
                    "craftCategory": cl.craft_category or "",
                    "district": cl.district or "", "state": cl.state or "",
                    "commissionPct": cl.commission_pct or 0,
                    "memberCount": len(members)},
        "listings": {
            "all": cards,
            "pendingReviews": pending,
            "byChannel": by_channel,
            "channels": sorted(by_channel.keys()),
            "total": len(cards),
        },
        "orders": {
            "active": active,
            "all": order_rows,
            "splittingTasks": splitting,
            "total": len(order_rows),
        },
        # The roster: who can actually take work, and how much is left of them. The
        # capacity pair lives on the artisan rather than the membership, so two
        # clusters cannot be promised the same weeks.
        "roster": [{
            "artisanId": a.id,
            "name": a.full_name or a.business_name or "",
            "phone": a.phone or "",
            # The role is on the artisan, not the membership: a person is an
            # artisan or a cluster creator as an account, and joining a cluster
            # does not change that.
            "role": a.role or "",
            "capacityUnits": a.capacity_units or 0,
            "capacityCommitted": a.capacity_committed or 0,
            # The number that actually decides whether she can take this order.
            "capacityAvailable": a.capacity_available,
            "joinedAt": m.joined_at.isoformat() if m.joined_at else None,
        } for m, a in members],
    }


def reports(s, artisan: db.Artisan) -> dict[str, Any]:
    """
    Revenue and payouts, with the arithmetic left visible.

    No projections and no growth percentages. Every line is either money that arrived
    or money a settlement says is owed, and the two are labelled differently because
    they are not the same thing.
    """
    listing_ids, owned = _scope(s, artisan)
    orders = (s.query(db.Order).filter(db.Order.listing_id.in_(listing_ids)).all()
              if listing_ids else [])
    paid = [o for o in orders if (o.payment_status or "") == "paid"]

    by_channel: dict[str, dict[str, float]] = {}
    for o in paid:
        row = by_channel.setdefault(o.channel or "storefront",
                                    {"orders": 0, "amount": 0.0})
        row["orders"] += 1
        row["amount"] = round(row["amount"] + float(o.amount or 0), 2)

    setts = (s.query(db.Settlement).filter(db.Settlement.cluster_id.in_(owned)).all()
             if owned else [])

    return {
        "revenue": {
            "paidOrders": len(paid),
            "gross": round(sum(float(o.amount or 0) for o in paid), 2),
            "byChannel": by_channel,
            "unpaidOrders": len(orders) - len(paid),
            "why": ("" if paid else
                    "Nothing has been paid for yet. Unpaid orders are counted "
                    "separately on purpose - an order is not revenue until the money "
                    "arrives."),
        },
        "payouts": {
            "available": bool(setts),
            "settlements": len(setts),
            "paid": round(sum(float(x.distributable_amount or 0) for x in setts
                              if (x.status or "") == "paid"), 2),
            "pending": round(sum(float(x.distributable_amount or 0) for x in setts
                                 if (x.status or "") != "paid"), 2),
            "rows": [{
                "id": x.id, "orderId": x.order_id or "",
                "gross": x.gross_amount or 0,
                "commission": x.commission_amount or 0,
                "distributable": x.distributable_amount or 0,
                "status": x.status or "draft",
                "at": x.computed_at.isoformat() if x.computed_at else None,
            } for x in setts][:50],
            "why": ("" if setts else
                    "No settlement has been computed yet. A settlement is created "
                    "when a cluster divides a paid order between its members."),
        },
        "products": {
            "total": len(listing_ids),
            "live": len([l for l in
                         (s.query(db.Listing)
                          .filter(db.Listing.id.in_(listing_ids),
                                  db.Listing.status.in_(("active", "published")))
                          .all() if listing_ids else [])]),
        },
    }
