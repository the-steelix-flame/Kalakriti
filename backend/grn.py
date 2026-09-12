"""
Goods Receipt Notes: what physically arrived at the dispatch point.

The whole of Phase 4's settlement math reads these rows and never the original
commitment, because "promised forty" and "handed over thirty-seven, two of them
cracked" are different numbers and only the second one describes anything that
happened. An artisan is paid for units received and passing inspection. That is the
single rule this module exists to make true.

Three things are deliberately *not* here:

  - No courier. The leg from an artisan's home to the dispatch point is walking
    distance or a shared local run, and already happens today under the trader system
    this replaces. Recording what arrived is the useful part; booking a van is not.

  - No editing. A receipt is a statement about a moment, so a wrong one is corrected
    by logging a correction that points at it, not by rewriting history. Settlement
    disputes are resolved by reading the log, and a log that can be quietly amended
    resolves nothing. `void_receipt` marks rather than deletes.

  - No trust in the client's arithmetic. Tallies are computed from rows every time
    they are asked for. A running total held in a column is a running total that
    drifts.
"""
from __future__ import annotations

from typing import Any

import clusters
import db

# Quality outcomes. `partial` is the common one and the reason `quantity_rejected`
# exists separately: a delivery of twenty with two cracked pots is not a pass and is
# certainly not a reject, and forcing it into either loses the two.
QUALITY_PASS = "pass"
QUALITY_PARTIAL = "partial"
QUALITY_REJECT = "reject"
QUALITY = [QUALITY_PASS, QUALITY_PARTIAL, QUALITY_REJECT]


def _derive_quality(received: int, rejected: int) -> str:
    if rejected <= 0:
        return QUALITY_PASS
    if rejected >= received:
        return QUALITY_REJECT
    return QUALITY_PARTIAL


def _require_owner(s, logger: db.Artisan, cluster_id: str) -> db.Cluster:
    """
    Only the cluster owner logs receipts.

    This is an access rule and a liability one at once. The owner is the seller of
    record and carries the tax exposure for everything published under their GSTIN,
    so the count that decides who gets paid has to be theirs. A member logging their
    own deliveries would be writing their own payslip.
    """
    c = s.get(db.Cluster, cluster_id)
    if c is None:
        raise clusters.ClusterError("cluster_not_found", "No such cluster.")
    if c.owner_artisan_id != logger.id:
        raise clusters.ClusterError(
            "not_cluster_owner",
            "Only the person who runs this cluster can record what was delivered.",
            "जो समूह चलाते हैं वही दर्ज कर सकते हैं कि क्या आया।")
    return c


def _require_member(s, cluster_id: str, artisan_id: str) -> db.Artisan:
    m = (s.query(db.ClusterMembership)
         .filter(db.ClusterMembership.cluster_id == cluster_id,
                 db.ClusterMembership.artisan_id == artisan_id).first())
    if m is None:
        raise clusters.ClusterError(
            "not_a_member",
            "That artisan is not in this cluster, so there is nothing to receive "
            "from them.")
    a = s.get(db.Artisan, artisan_id)
    if a is None:
        raise clusters.ClusterError("artisan_not_found", "No such artisan.")
    # A member who has left can still deliver work they promised before leaving -
    # refusing that would strand goods that are physically on the table.
    return a


def log_receipt(s, logger: db.Artisan, *, cluster_id: str, artisan_id: str,
                quantity_received: int, quantity_rejected: int = 0,
                order_id: str = "", enquiry_id: str = "",
                note: str = "") -> db.GoodsReceipt:
    """
    Record one delivery. Accepted units are what the artisan will be paid for.

    Capacity is released by the *total* handed over, rejects included, because the
    weeks of work were genuinely spent - her hands were busy either way. Replacing a
    rejected batch is new work and needs a new commitment, which is the honest way
    round: it makes the cost of a reject visible as extra capacity somebody has to
    find, rather than hiding it in a number that never moved.
    """
    received = int(quantity_received or 0)
    rejected = int(quantity_rejected or 0)

    if received <= 0:
        raise clusters.ClusterError("bad_quantity",
                                    "Quantity received must be more than zero.")
    if rejected < 0:
        raise clusters.ClusterError("bad_quantity",
                                    "Rejected quantity cannot be negative.")
    if rejected > received:
        raise clusters.ClusterError(
            "bad_quantity",
            f"You cannot reject {rejected} out of {received}. The rejected count is "
            f"part of what arrived, not on top of it.")

    _require_owner(s, logger, cluster_id)
    artisan = _require_member(s, cluster_id, artisan_id)

    g = db.GoodsReceipt(
        id=db.nid("grn"),
        cluster_id=cluster_id,
        artisan_id=artisan_id,
        order_id=(order_id or None),
        enquiry_id=(enquiry_id or None),
        quantity_received=received,
        quantity_rejected=rejected,
        quality_status=_derive_quality(received, rejected),
        note=(note or "").strip(),
        logged_by_artisan_id=logger.id,
        received_at=db.now(),
    )
    s.add(g)

    # Free the capacity this delivery used up, but never more than is outstanding -
    # a duplicate receipt must not hand somebody phantom capacity.
    freed = min(received, artisan.capacity_committed or 0)
    if freed:
        clusters.release_capacity(s, artisan, freed, reason=f"delivered {g.id}")

    s.flush()
    db.log_event(s, "cluster", cluster_id, "grn", "", g.quality_status,
                 f"{artisan_id}: {received} received, {rejected} rejected")
    return g


def void_receipt(s, logger: db.Artisan, receipt_id: str,
                 reason: str = "") -> db.GoodsReceipt:
    """
    Mark a receipt void. Kept, never deleted - see this module's docstring.

    Capacity is not re-committed on void. Voiding says the count was wrong, not that
    the work un-happened, and guessing which of those a person meant is how numbers
    stop matching the pots on the table.
    """
    g = s.get(db.GoodsReceipt, receipt_id)
    if g is None:
        raise clusters.ClusterError("receipt_not_found", "No such receipt.")
    _require_owner(s, logger, g.cluster_id)
    if g.quality_status == "void":
        return g
    was = g.quality_status
    g.quality_status = "void"
    g.note = ((g.note or "") + f"  [voided: {reason or 'no reason given'}]").strip()
    s.flush()
    db.log_event(s, "cluster", g.cluster_id, "grn", was, "void",
                 f"{g.id} voided - {reason}")
    return g


def receipts(s, cluster_id: str, *, order_id: str = "", enquiry_id: str = "",
             artisan_id: str = "") -> list[db.GoodsReceipt]:
    q = (s.query(db.GoodsReceipt)
         .filter(db.GoodsReceipt.cluster_id == cluster_id))
    if order_id:
        q = q.filter(db.GoodsReceipt.order_id == order_id)
    if enquiry_id:
        q = q.filter(db.GoodsReceipt.enquiry_id == enquiry_id)
    if artisan_id:
        q = q.filter(db.GoodsReceipt.artisan_id == artisan_id)
    return q.order_by(db.GoodsReceipt.received_at.desc()).all()


def tally(s, cluster_id: str, *, order_id: str = "",
          enquiry_id: str = "") -> dict[str, Any]:
    """
    The running total, per artisan and for the order, computed from rows.

    `target` comes from the enquiry when the receipts are against one, because that
    is what the buyer actually asked for. Where there is no enquiry the shortfall is
    reported as unknown rather than as zero - the same rule the marketplace metrics
    follow. Not knowing the target and hitting it exactly are different claims.
    """
    rows = [g for g in receipts(s, cluster_id, order_id=order_id,
                                enquiry_id=enquiry_id)
            if g.quality_status != "void"]

    per: dict[str, dict[str, Any]] = {}
    for g in rows:
        e = per.setdefault(g.artisan_id, {
            "artisanId": g.artisan_id, "name": "", "phone": "",
            "received": 0, "rejected": 0, "accepted": 0, "deliveries": 0,
        })
        e["received"] += g.quantity_received or 0
        e["rejected"] += g.quantity_rejected or 0
        e["deliveries"] += 1
    for aid, e in per.items():
        e["accepted"] = e["received"] - e["rejected"]
        a = s.get(db.Artisan, aid)
        if a is not None:
            e["name"] = a.full_name or a.business_name or ""
            e["phone"] = a.phone
        m = (s.query(db.ClusterMembership)
             .filter(db.ClusterMembership.cluster_id == cluster_id,
                     db.ClusterMembership.artisan_id == aid).first())
        e["stillInCluster"] = bool(m and m.status == "active")

    accepted = sum(e["accepted"] for e in per.values())
    received = sum(e["received"] for e in per.values())
    rejected = sum(e["rejected"] for e in per.values())

    target = None
    if enquiry_id:
        enq = s.get(db.Enquiry, enquiry_id)
        if enq is not None:
            target = enq.quantity or None

    out: dict[str, Any] = {
        "clusterId": cluster_id,
        "orderId": order_id or "",
        "enquiryId": enquiry_id or "",
        "received": received,
        "rejected": rejected,
        "accepted": accepted,
        "deliveries": len(rows),
        "byArtisan": sorted(per.values(), key=lambda e: -e["accepted"]),
    }

    if target:
        out["target"] = target
        out["shortfall"] = max(target - accepted, 0)
        out["complete"] = accepted >= target
        out["targetKnown"] = True
    else:
        # No enquiry behind these receipts, so there is no number to be short of.
        out["target"] = None
        out["shortfall"] = None
        out["complete"] = None
        out["targetKnown"] = False
        out["why"] = ("These receipts are not against a bulk enquiry, so there is no "
                      "agreed quantity to measure them against.")
    return out
