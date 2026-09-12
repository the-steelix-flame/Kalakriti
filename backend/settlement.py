"""
Net settlement: what the buyer paid, what is left after everyone else takes a cut,
and how the remainder is divided between the people who actually made the thing.

The addendum's §9 is the whole argument for this module, and it is worth restating
because it is the difference between this being fair and this being a payslip nobody
can check. The split is on the **net realised amount**, not on the price on the
label. Three deductions come off the top first, in this order:

    gross            quantity x unit price, what the buyer paid
      - platform fee  the marketplace's own commission, taken before it settles
      - GST           by HSN heading, from the rate table, never assumed to be zero
      - logistics     the courier charge for getting it to the buyer
    = net realised    the only number anybody's percentage should be applied to

Then, and only then:

    net realised
      - commission    the Cluster Creator's share, for carrying the tax liability
    = distributable   divided across artisans by units RECEIVED AND ACCEPTED

That last clause is the one that connects this to Phase 3. Payment follows the goods
receipts, not the original commitment. Somebody who promised forty and delivered
thirty-seven with two cracked is paid for thirty-five, and the arithmetic says so
without anybody having to argue about it.

What this module refuses to do
------------------------------
It will not invent a platform fee. Most marketplace APIs do not expose the
settlement breakdown at all, and a plausible-looking guess is worse than a blank
here because it is money. An unknown fee stops the settlement in `needs_input` and
names what is missing, exactly as `analytics.py` reports an unavailable metric
rather than a zero.

It will not assume a GST rate either. `db.gst_for_hsn` returns unknown for any
heading not in the table, and unknown stops the settlement. Most handloom headings
really are nil-rated, which is precisely what would make defaulting to zero feel
right and be wrong for the 12% ones.
"""
from __future__ import annotations

from typing import Any

import clusters
import db
import grn

# Where a channel genuinely publishes its own fee, we read it. This records which
# ones can, so the app can explain the difference between "we did not ask" and "they
# do not say". Only the storefront is ours, so only the storefront is knowable
# without somebody reading a dashboard.
FEE_KNOWN_FROM_API = {"storefront"}

STATUS_DRAFT = "draft"
STATUS_NEEDS_INPUT = "needs_input"
STATUS_COMPUTED = "computed"
STATUS_APPROVED = "approved"
STATUS_PAID = "paid"


def _q(value: float) -> float:
    """Round to paise. Money that carries float noise is money somebody disputes."""
    return round(float(value or 0) + 0.0, 2)


def platform_fee(_s, order: db.Order, *, override: float | None = None
                 ) -> dict[str, Any]:
    """
    What the marketplace took before the money reached us.

    Takes a session it does not currently need, so that the three deduction
    functions share one shape - the moment any channel does expose a settlement
    report we will be reading rows here like the other two already do.

    `override` is the Cluster Creator reading the figure off that platform's own
    settlement report and typing it in. That is a legitimate source - it is the
    authoritative one, in fact - so it is accepted, recorded and attributed rather
    than treated as a workaround.
    """
    if override is not None:
        return {"value": _q(override), "available": True, "source": "entered",
                "why": ""}

    channel = (order.channel or "storefront").lower()
    if channel == "storefront":
        # Our own storefront charges nothing. That is a fact about this channel, not
        # a default applied because we could not find out.
        return {"value": 0.0, "available": True, "source": "storefront",
                "why": "The artisan's own storefront takes no commission."}

    return {
        "value": None, "available": False, "source": "",
        "why": f"{channel} does not publish its settlement breakdown through the "
               f"API. Open that platform's settlement report for this order and "
               f"enter the fee it shows.",
    }


def gst_amount(s, listing: db.Listing, gross: float) -> dict[str, Any]:
    """
    GST on the item, from the HSN rate table.

    The rate is applied to the gross sale value. Headings whose rate depends on the
    per-piece value carry `needsConfirmation`, and those stop rather than guess -
    picking one of two legal rates silently is how a seller ends up with a notice.
    """
    rate = db.gst_for_hsn(s, listing.hsn if listing else "")
    if not rate.get("available"):
        return {"value": None, "available": False, "rate": None,
                "why": rate.get("why", "GST rate unknown.")}
    if rate.get("needsConfirmation"):
        return {"value": None, "available": False, "rate": rate["value"],
                "needsConfirmation": True,
                "why": rate.get("conditionNote") or rate.get("why")
                or "This heading's rate depends on the sale value."}

    pct = float(rate["value"] or 0)
    return {"value": _q(gross * pct / 100.0), "available": True, "rate": pct,
            "hsn": rate.get("hsn"), "why": ""}


def logistics_fee(s, order: db.Order, *, override: float | None = None
                  ) -> dict[str, Any]:
    """What the courier charged. Read from the shipment row when one exists."""
    if override is not None:
        return {"value": _q(override), "available": True, "source": "entered",
                "why": ""}

    ship = (s.query(db.Shipment)
            .filter(db.Shipment.order_id == order.id)
            .order_by(db.Shipment.created_at.desc()).first())
    if ship is not None:
        charged = (ship.raw or {}).get("freight_charge")
        if charged is not None:
            return {"value": _q(charged), "available": True,
                    "source": ship.provider or "courier", "why": ""}
        return {"value": None, "available": False, "source": ship.provider or "",
                "why": f"A shipment exists with {ship.provider or 'the courier'} but "
                       f"no freight charge has come back yet. Enter it from their "
                       f"dashboard, or wait for the invoice."}

    return {"value": None, "available": False, "source": "",
            "why": "No shipment has been booked for this order, so there is no "
                   "courier charge yet. Enter it if the goods were sent another way."}


def compute(s, order_id: str, cluster_id: str, *,
            platform_fee_override: float | None = None,
            logistics_fee_override: float | None = None,
            gst_rate_override: float | None = None) -> db.Settlement:
    """
    Work out one order's settlement and write it, with every input recorded.

    Re-running replaces the lines rather than adding to them, so a settlement that
    stopped for a missing fee can simply be computed again once the figure is known.
    A settlement already `paid` is never recomputed - the money has moved, and the
    record of what was paid has to stay what was paid.
    """
    order = s.get(db.Order, order_id)
    if order is None:
        raise clusters.ClusterError("order_not_found", "No such order.")
    cluster = s.get(db.Cluster, cluster_id)
    if cluster is None:
        raise clusters.ClusterError("cluster_not_found", "No such cluster.")
    listing = s.get(db.Listing, order.listing_id)

    existing = (s.query(db.Settlement)
                .filter(db.Settlement.order_id == order_id,
                        db.Settlement.cluster_id == cluster_id).first())
    if existing is not None and existing.status == STATUS_PAID:
        raise clusters.ClusterError(
            "already_paid",
            "This settlement has already been paid. Recomputing it would change a "
            "record of money that has actually moved.")

    st = existing or db.Settlement(id=db.nid("stl"), order_id=order_id,
                                   cluster_id=cluster_id)
    if existing is None:
        s.add(st)

    gross = _q((order.amount or 0))
    fee = platform_fee(s, order, override=platform_fee_override)
    gst = (gst_amount(s, listing, gross) if gst_rate_override is None else
           {"value": _q(gross * float(gst_rate_override) / 100.0), "available": True,
            "rate": float(gst_rate_override), "why": ""})
    log = logistics_fee(s, order, override=logistics_fee_override)

    st.gross_amount = gross
    st.platform_fee = fee.get("value") or 0
    st.platform_fee_source = fee.get("source", "")
    st.gst_amount = gst.get("value") or 0
    st.gst_rate_applied = gst.get("rate") or 0
    st.hsn_used = (listing.hsn if listing else "") or ""
    st.logistics_fee = log.get("value") or 0
    st.commission_pct_applied = cluster.commission_pct or 0

    # Anything we could not establish stops the settlement here, named.
    missing = [name for name, part in (("platform fee", fee), ("GST", gst),
                                       ("logistics fee", log))
               if not part.get("available")]
    if missing:
        st.status = STATUS_NEEDS_INPUT
        st.note = " | ".join(
            f"{name}: {part.get('why', '')}"
            for name, part in (("platform fee", fee), ("GST", gst),
                               ("logistics fee", log))
            if not part.get("available"))
        st.net_amount = 0
        st.commission_amount = 0
        st.distributable_amount = 0
        s.query(db.SettlementLine).filter(
            db.SettlementLine.settlement_id == st.id).delete()
        s.flush()
        db.log_event(s, "settlement", st.id, "status", "", STATUS_NEEDS_INPUT,
                     f"waiting on: {', '.join(missing)}")
        return st

    net = _q(gross - st.platform_fee - st.gst_amount - st.logistics_fee)
    if net < 0:
        st.status = STATUS_NEEDS_INPUT
        st.note = (f"The deductions come to more than the sale. Gross {gross}, "
                   f"deductions {_q(gross - net)}. Check the figures entered.")
        s.flush()
        return st

    commission = _q(net * (st.commission_pct_applied or 0) / 100.0)
    distributable = _q(net - commission)

    st.net_amount = net
    st.commission_amount = commission
    st.distributable_amount = distributable
    st.note = ""

    # Divide by units actually received and accepted, never by units promised.
    tally = grn.tally(s, cluster_id, order_id=order_id)
    rows = [r for r in tally["byArtisan"] if r["accepted"] > 0]

    s.query(db.SettlementLine).filter(
        db.SettlementLine.settlement_id == st.id).delete()

    total_accepted = sum(r["accepted"] for r in rows)
    if not total_accepted:
        st.status = STATUS_NEEDS_INPUT
        st.note = ("No goods receipts have been logged against this order yet, so "
                   "there is nothing to divide. Record what each person delivered "
                   "first.")
        s.flush()
        db.log_event(s, "settlement", st.id, "status", "", STATUS_NEEDS_INPUT,
                     "no accepted units")
        return st

    rate = distributable / total_accepted
    allocated = 0.0
    lines: list[db.SettlementLine] = []
    for i, r in enumerate(rows):
        # The last line absorbs the rounding remainder, so the lines always sum to
        # the distributable amount exactly. A settlement that is two paise short is
        # a settlement somebody has to explain.
        if i == len(rows) - 1:
            amount = _q(distributable - allocated)
        else:
            amount = _q(rate * r["accepted"])
            allocated = _q(allocated + amount)

        artisan = s.get(db.Artisan, r["artisanId"])
        method = "route" if (artisan and (artisan.pan or "").strip()) else "manual_upi"
        line = db.SettlementLine(
            id=db.nid("stlline"), settlement_id=st.id, artisan_id=r["artisanId"],
            units=r["accepted"], rate=_q(rate), amount=amount,
            payout_method=method, payout_status="pending")
        s.add(line)
        lines.append(line)

    st.status = STATUS_COMPUTED
    st.computed_at = db.now()
    s.flush()
    db.log_event(s, "settlement", st.id, "status", "", STATUS_COMPUTED,
                 f"net {net}, commission {commission}, {len(lines)} payee(s)")
    return st


def explain(s, st: db.Settlement) -> dict[str, Any]:
    """
    The settlement as something a person can check line by line.

    Deliberately returns the deductions as an ordered list rather than a dict: the
    order is the argument. Somebody reading this should be able to follow the money
    down the page and arrive at their own number.
    """
    lines = (s.query(db.SettlementLine)
             .filter(db.SettlementLine.settlement_id == st.id).all())
    cluster = s.get(db.Cluster, st.cluster_id)

    payees = []
    for ln in lines:
        a = s.get(db.Artisan, ln.artisan_id)
        payees.append({
            **ln.public(),
            "name": (a.full_name or a.business_name or "") if a else "",
            "phone": a.phone if a else "",
            "hasPan": bool(a and (a.pan or "").strip()),
            "whyMethod": ("Paid automatically into their bank account."
                          if ln.payout_method == "route" else
                          "No PAN on file, so an automatic transfer cannot be set "
                          "up. The amount is worked out here and sent by UPI."),
        })

    return {
        "id": st.id, "orderId": st.order_id, "clusterId": st.cluster_id,
        "status": st.status, "note": st.note or "",
        "gross": st.gross_amount,
        "deductions": [
            {"label": "Marketplace fee", "amount": st.platform_fee,
             "source": st.platform_fee_source or ""},
            {"label": f"GST at {st.gst_rate_applied or 0}%",
             "amount": st.gst_amount, "source": f"HSN {st.hsn_used or '-'}"},
            {"label": "Shipping", "amount": st.logistics_fee, "source": ""},
        ],
        "net": st.net_amount,
        "commission": {"pct": st.commission_pct_applied,
                       "amount": st.commission_amount,
                       "to": (cluster.name if cluster else ""),
                       "why": "For being the seller of record and carrying the tax "
                              "liability on this sale."},
        "distributable": st.distributable_amount,
        "payees": payees,
        "computedAt": st.computed_at.isoformat() if st.computed_at else None,
    }


def quote_for_artisan(s, listing: db.Listing, cluster: db.Cluster,
                      quantity: int = 1) -> dict[str, Any]:
    """
    What one artisan would actually take home at this listing's price, before the
    sale happens.

    This is the addendum's §9 closing point and the reason it matters: the Fair-Floor
    promise is dishonest if the figure shown is the sticker price. Where a deduction
    cannot be known in advance - the marketplace fee usually cannot - it is reported
    as unknown and the estimate is marked incomplete rather than quietly treated as
    zero.
    """
    gross = _q((listing.price or 0) * max(int(quantity or 1), 1))
    rate = db.gst_for_hsn(s, listing.hsn or "")
    unknown: list[str] = []

    gst_val = 0.0
    if rate.get("available") and not rate.get("needsConfirmation"):
        gst_val = _q(gross * float(rate["value"] or 0) / 100.0)
    else:
        unknown.append("GST")

    # Shipping and the marketplace's cut are not knowable until a channel and a
    # courier exist, so they are named as unknown rather than folded in as nothing.
    unknown.extend(["shipping", "marketplace fee"])

    net_known = _q(gross - gst_val)
    commission = _q(net_known * (cluster.commission_pct or 0) / 100.0)
    take_home = _q(net_known - commission)

    return {
        "gross": gross,
        "gstRate": rate.get("value") if rate.get("available") else None,
        "gst": gst_val if "GST" not in unknown else None,
        "commissionPct": cluster.commission_pct,
        "commission": commission,
        "estimatedTakeHome": take_home,
        "complete": not unknown,
        "unknown": unknown,
        "why": ("This is what reaches you after the cluster's share"
                + (f", but before {', '.join(unknown)}, which are not known until "
                   f"the sale happens." if unknown else ".")),
    }
