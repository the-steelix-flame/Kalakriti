"""
Clusters: creating one, browsing them, joining and leaving, and the capacity guard.

This is the operating model from the Business Model Addendum §3, §10.2 and §11. A
cluster exists because of one hard fact: a marketplace will not let an artisan sell
without GST, PAN and a bank account, and the artisans this app is for do not have
them. So one GST-holding person becomes the seller of record for a group, carries
the compliance burden, and is paid a coordination commission for carrying it.

Three rules are enforced here rather than in the UI, because a rule that lives only
in a screen is not a rule:

  1. A cluster cannot exist without a GSTIN on its owner. The owner is named on the
     invoice; there is no version of this where that is optional.

  2. Terms are readable before joining, never after. `browse()` returns the
     commission percentage, the connected platforms and the rating to anybody, in
     the same shape whether or not they are a member. The addendum is explicit that
     visible terms are what makes this trustworthy rather than a better-dressed
     version of the middleman it replaces.

  3. Capacity is committed once per artisan, never once per cluster. Two clusters in
     the same craft can both want the same weeks of somebody's work, and the app has
     to refuse the second - see `commit_capacity`.

A fourth rule is about absence rather than action: a cluster with no settled cycles
has *no rating*, and says so. It never shows zero stars. Zero stars is a claim that
people rated it badly; the truth is that nobody has rated it at all, and an artisan
deciding where to send months of work deserves to be told which of those it is.
"""
from __future__ import annotations

import secrets
from typing import Any

import channels
import db
import seller

# Ambiguous characters are left out. This code gets read down a phone line by a field
# officer, written on paper, and typed by somebody who may not read easily.
_CODE_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY3479"
_CODE_LEN = 6

# Below this many settled cycles a cluster is treated as having no track record. One
# cycle is an anecdote and rounds to a suspiciously perfect score.
MIN_REVIEWS_FOR_RATING = 3


class ClusterError(Exception):
    """Something the caller can fix, carrying language the app can show as-is."""

    def __init__(self, code: str, message: str, message_hi: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.message_hi = message_hi

    def payload(self) -> dict[str, Any]:
        return {"error": self.code, "why": self.message, "whyHi": self.message_hi}


# ───────────────────────────────────────────────────────────── invite codes

def _fresh_invite_code(s) -> str:
    """A code no active cluster is already using."""
    for _ in range(40):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
        clash = s.query(db.Cluster).filter(db.Cluster.invite_code == code).first()
        if not clash:
            return code
    # 25^6 is 244 million; forty collisions means something is wrong with the RNG
    # rather than that we were unlucky, so fail loudly instead of looping forever.
    raise ClusterError("invite_code_exhausted",
                       "Could not allocate an invite code. Try again.")


def normalise_code(raw: str) -> str:
    """
    Accept what a person actually types: lower case, spaces, hyphens.

    Deliberately does *not* try to guess past look-alike characters. The temptation
    is to map O to 0 and I to 1, but `_CODE_ALPHABET` already excludes both halves of
    every confusable pair, so a typed O is not an ambiguous Q - it is a typo, and
    silently rewriting it could match a different cluster than the one on the paper.
    Rejecting it and asking again is the safer failure.
    """
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())


# ───────────────────────────────────────────────────────────── roles

def set_role(s, artisan: db.Artisan, role: str) -> db.Artisan:
    """
    Move somebody between the three roles (addendum §10.2, edge case 11).

    Becoming a `solo_seller` or a `cluster_creator` requires a GSTIN, because both
    mean being named on an invoice. Going back down to `artisan` is always allowed,
    but not while still owning an active cluster - that would leave members whose
    seller of record has quietly stopped being a seller.
    """
    if role not in db.ROLES:
        raise ClusterError("bad_role", f"Unknown role {role!r}.")

    if role in db.GST_ROLES and not (artisan.gstin or "").strip():
        raise ClusterError(
            "gstin_required",
            "This role means being the seller of record, so it needs a GSTIN on the "
            "profile first. Your own listings on the storefront do not need one.",
            "इस भूमिका में बिल आपके नाम पर बनेगा, इसलिए पहले प्रोफ़ाइल में GSTIN डालना ज़रूरी है।")

    if role != db.ROLE_CLUSTER_CREATOR:
        owned = (s.query(db.Cluster)
                 .filter(db.Cluster.owner_artisan_id == artisan.id,
                         db.Cluster.status == "active").count())
        if owned:
            raise ClusterError(
                "owns_active_cluster",
                f"You still run {owned} active cluster(s). Close them first - their "
                f"members are selling under your GSTIN and need to know it is ending.",
                "आपके चलाए हुए समूह अभी सक्रिय हैं। पहले उन्हें बंद करें।")

    artisan.role = role
    s.flush()
    return artisan


# ───────────────────────────────────────────────────────────── create

def create_cluster(s, owner: db.Artisan, *, name: str, craft_category: str,
                   commission_pct: float, max_order_units: int = 0,
                   district: str = "", state: str = "") -> db.Cluster:
    """
    Create a cluster. The caller becomes a `cluster_creator` if they are not already.
    """
    if not (owner.gstin or "").strip():
        raise ClusterError(
            "gstin_required",
            "A cluster publishes its members' work under your GSTIN, and the invoice "
            "carries your name. Add your GSTIN to your profile first.",
            "समूह का सामान आपके GSTIN पर बिकेगा और बिल आपके नाम से बनेगा। "
            "पहले प्रोफ़ाइल में GSTIN डालें।")

    if not (name or "").strip():
        raise ClusterError("name_required", "Give the cluster a name members will "
                                            "recognise.")

    # A commission has to be a number somebody can read and argue with. The addendum
    # is explicit that the *right* percentage is a policy question for a cooperative
    # or a CA, not something to hardcode - so this only rejects the impossible.
    try:
        pct = float(commission_pct)
    except (TypeError, ValueError):
        raise ClusterError("bad_commission", "Commission must be a number.")
    if pct < 0 or pct >= 100:
        raise ClusterError(
            "bad_commission",
            "Commission must be between 0 and 100 percent of the net realised "
            "amount - what is left after the marketplace fee, GST and shipping.")

    if owner.role != db.ROLE_CLUSTER_CREATOR:
        set_role(s, owner, db.ROLE_CLUSTER_CREATOR)

    cluster = db.Cluster(
        id=db.nid("clu"),
        owner_artisan_id=owner.id,
        name=name.strip(),
        craft_category=(craft_category or "").strip(),
        district=(district or "").strip(),
        state=(state or "").strip(),
        commission_pct=pct,
        max_order_units=max(int(max_order_units or 0), 0),
        invite_code=_fresh_invite_code(s),
        status="active",
    )
    s.add(cluster)
    s.flush()
    db.log_event(s, "cluster", cluster.id, "status", "", "active",
                 f"created by {owner.id} at {pct}% commission")
    return cluster


# ───────────────────────────────────────────────────────────── read models

def rating(s, cluster: db.Cluster) -> dict[str, Any]:
    """
    The cluster's standing, or an honest statement that it has none yet.

    `available: False` with a reason, never a zero - the same rule the marketplace
    metrics already follow. A new cluster is not a bad cluster.
    """
    rows = (s.query(db.Review)
            .filter(db.Review.cluster_id == cluster.id).all())
    if len(rows) < MIN_REVIEWS_FOR_RATING:
        return {
            "available": False,
            "count": len(rows),
            "why": ("New on platform, no reviews yet" if not rows else
                    f"Only {len(rows)} completed cycle(s) so far - too few to average "
                    f"into a rating that would mean anything"),
            "overall": None, "paidOnTime": None,
            "commissionFair": None, "ordersRegular": None,
        }

    def avg(field: str) -> float:
        vals = [getattr(r, field) for r in rows if getattr(r, field)]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    overall = [r.average for r in rows if r.average]
    return {
        "available": True,
        "count": len(rows),
        "why": "",
        "overall": round(sum(overall) / len(overall), 2) if overall else 0.0,
        "paidOnTime": avg("paid_on_time"),
        "commissionFair": avg("commission_fair"),
        "ordersRegular": avg("orders_regular"),
    }


def connected_platforms(s, cluster: db.Cluster) -> list[dict[str, Any]]:
    """
    Which marketplaces this cluster can actually publish to - names only.

    Two separate things have to be true, and conflating them is how a member ends up
    told their work will reach GeM when it will not:

      1. The server holds that channel's credentials at all (`channels.availability`).
      2. The *owner* satisfies that channel's seller requirements - GeM and Amazon
         want PAN, GSTIN, a bank account and a registered address before a listing is
         accepted, and the owner is the seller of record here, so it is their profile
         that decides, not the member's (`seller.readiness`).

    A joining member is entitled to know where their work will end up. They are not
    entitled to the owner's API credentials, and this returns none: only the display
    name, whether it is live, and which of the two reasons it is not.
    """
    owner = cluster.owner
    out = []
    for ch in channels.availability():
        cid = ch.get("id", "")
        configured = bool(ch.get("configured"))
        ready = seller.readiness(owner, cid) if owner else {"ready": False,
                                                            "missing": []}
        live = configured and bool(ready.get("ready"))

        if live:
            why = ""
        elif not configured:
            why = "Not connected yet"
        else:
            # Name the fields, not the values. "Needs PAN and a bank account" is
            # something the owner can act on and a member can weigh.
            missing = ", ".join(seller.WHY_EN.get(f, f).lower()
                                for f in ready.get("missing", [])[:3])
            why = f"Waiting on the cluster owner: {missing}" if missing \
                else "Waiting on the cluster owner's seller details"

        out.append({"channel": cid, "name": ch.get("name", ""),
                    "live": live, "why": why})
    return out


def payout_history(s, cluster: db.Cluster) -> dict[str, Any]:
    """
    What this cluster has actually paid out, if anything.

    Averages come from `settlement_lines` that genuinely reached `settled`. A
    settlement that was computed but never paid says nothing about whether this
    cluster pays, so it is not counted.
    """
    rows = (s.query(db.SettlementLine)
            .join(db.Settlement, db.SettlementLine.settlement_id == db.Settlement.id)
            .filter(db.Settlement.cluster_id == cluster.id,
                    db.SettlementLine.payout_status == "settled").all())
    if not rows:
        return {"available": False, "count": 0,
                "why": "No completed payouts yet",
                "averagePerArtisan": None, "totalPaid": None}
    total = sum(r.amount or 0 for r in rows)
    return {
        "available": True,
        "count": len(rows),
        "why": "",
        "averagePerArtisan": round(total / len(rows), 2),
        "totalPaid": round(total, 2),
    }


def card(s, cluster: db.Cluster, viewer: db.Artisan | None = None) -> dict[str, Any]:
    """
    Everything a member should be able to read *before* they join, in one object.
    """
    owner = cluster.owner
    active = [m for m in (cluster.memberships or []) if m.status == "active"]
    mine = None
    if viewer is not None:
        mine = next((m for m in (cluster.memberships or [])
                     if m.artisan_id == viewer.id), None)

    return {
        **cluster.public(),
        "ownerName": (owner.business_name or owner.full_name or "") if owner else "",
        # Enough to show the cluster is a real registered entity, not enough to
        # reproduce the number. A GSTIN is a tax identifier, not a public credential.
        "ownerGstinLast4": ((owner.gstin or "")[-4:] if owner and owner.gstin else ""),
        "memberCount": len(active),
        "capacityAvailableUnits": sum(m.artisan.capacity_available
                                      for m in active if m.artisan),
        "rating": rating(s, cluster),
        "platforms": connected_platforms(s, cluster),
        "payouts": payout_history(s, cluster),
        "viewerMembership": mine.public() if mine else None,
        "viewerIsOwner": bool(viewer and owner and viewer.id == owner.id),
    }


def browse(s, viewer: db.Artisan | None = None, *, craft_category: str = "",
           district: str = "", state: str = "", query: str = "",
           limit: int = 50) -> list[dict[str, Any]]:
    """Open clusters, filtered. Readable by anyone, member or not."""
    q = s.query(db.Cluster).filter(db.Cluster.status == "active")
    if craft_category:
        q = q.filter(db.Cluster.craft_category == craft_category)
    if district:
        q = q.filter(db.Cluster.district == district)
    if state:
        q = q.filter(db.Cluster.state == state)
    if query:
        like = f"%{query.strip()}%"
        q = q.filter(db.Cluster.name.ilike(like))
    rows = q.order_by(db.Cluster.created_at.desc()).limit(limit).all()
    return [card(s, c, viewer) for c in rows]


def by_code(s, raw_code: str) -> db.Cluster | None:
    code = normalise_code(raw_code)
    if not code:
        return None
    for c in s.query(db.Cluster).filter(db.Cluster.status == "active").all():
        if normalise_code(c.invite_code) == code:
            return c
    return None


# ───────────────────────────────────────────────────────────── join / leave

def join(s, artisan: db.Artisan, *, cluster_id: str = "",
         invite_code: str = "") -> db.ClusterMembership:
    """
    Join by id (browsed) or by invite code (onboarded in person).

    Re-joining flips an existing `left` row back to active rather than inserting a
    second one, which is what the unique constraint is there to make impossible.
    """
    cluster = None
    if invite_code:
        cluster = by_code(s, invite_code)
        if cluster is None:
            raise ClusterError(
                "bad_invite_code",
                "No cluster has that code. Check it with whoever gave it to you.",
                "इस कोड का कोई समूह नहीं मिला। जिसने कोड दिया है उनसे दोबारा पूछें।")
    elif cluster_id:
        cluster = s.get(db.Cluster, cluster_id)

    if cluster is None or cluster.status != "active":
        raise ClusterError("cluster_not_found", "That cluster is not open to join.")

    if cluster.owner_artisan_id == artisan.id:
        raise ClusterError("owner_cannot_join",
                           "You run this cluster - you are already in it.")

    existing = (s.query(db.ClusterMembership)
                .filter(db.ClusterMembership.cluster_id == cluster.id,
                        db.ClusterMembership.artisan_id == artisan.id).first())
    if existing and existing.status == "active":
        return existing
    if existing:
        existing.status = "active"
        existing.left_at = None
        s.flush()
        db.log_event(s, "cluster", cluster.id, "member", "left", "active", artisan.id)
        return existing

    m = db.ClusterMembership(id=db.nid("mem"), cluster_id=cluster.id,
                             artisan_id=artisan.id, status="active")
    s.add(m)
    s.flush()
    db.log_event(s, "cluster", cluster.id, "member", "", "active", artisan.id)
    return m


def leave(s, artisan: db.Artisan, cluster_id: str) -> db.ClusterMembership:
    """
    Leave a cluster. The row is kept, because settlement history has to resolve.

    Work already committed is deliberately *not* released. Somebody who promised
    forty pieces and walks away has not un-promised them, and quietly freeing that
    capacity would let them commit the same weeks somewhere else.
    """
    m = (s.query(db.ClusterMembership)
         .filter(db.ClusterMembership.cluster_id == cluster_id,
                 db.ClusterMembership.artisan_id == artisan.id).first())
    if m is None or m.status != "active":
        raise ClusterError("not_a_member", "You are not in this cluster.")

    m.status = "left"
    m.left_at = db.now()
    s.flush()
    db.log_event(s, "cluster", cluster_id, "member", "active", "left", artisan.id)
    return m


def memberships_of(s, artisan: db.Artisan, active_only: bool = True) -> list[dict]:
    q = (s.query(db.ClusterMembership)
         .filter(db.ClusterMembership.artisan_id == artisan.id))
    if active_only:
        q = q.filter(db.ClusterMembership.status == "active")
    out = []
    for m in q.all():
        c = s.get(db.Cluster, m.cluster_id)
        if c is None:
            continue
        out.append({**card(s, c, artisan), "membership": m.public()})
    return out


# ───────────────────────────────────────────────────────── the capacity guard

def commit_capacity(s, artisan: db.Artisan, units: int, *,
                    cluster_id: str = "", reason: str = "") -> dict[str, Any]:
    """
    Reserve some of one artisan's production capacity (addendum §11, cases 1 and 2).

    The whole point is in where the number lives. `capacity_committed` is a column on
    the *artisan*, not on the membership, so two clusters in the same craft asking
    for the same weeks hit the same counter and the second one is refused. Which
    cluster asked first is irrelevant; which order the artisan accepted first is what
    decides it.

    Refusal is not an error state to route around. It is the app doing the one thing
    that stops somebody promising work they cannot deliver, which in this trade is
    how a person loses a buyer and a cluster at the same time.
    """
    units = int(units or 0)
    if units <= 0:
        raise ClusterError("bad_units", "Units must be a positive number.")

    available = artisan.capacity_available
    if units > available:
        raise ClusterError(
            "insufficient_capacity",
            f"You have {available} unit(s) free of {artisan.capacity_units or 0}. "
            f"This order needs {units}. Finish or release other work first.",
            f"आपके पास अभी {available} नग की गुंजाइश है, और इस ऑर्डर के लिए {units} चाहिए।")

    artisan.capacity_committed = (artisan.capacity_committed or 0) + units
    s.flush()
    db.log_event(s, "artisan", artisan.id, "capacity",
                 str(available), str(artisan.capacity_available),
                 f"committed {units} - {reason or cluster_id or 'order'}")
    return {"artisanId": artisan.id, "committed": units,
            "capacityUnits": artisan.capacity_units or 0,
            "capacityCommitted": artisan.capacity_committed or 0,
            "capacityAvailable": artisan.capacity_available}


def review_eligibility(s, artisan: db.Artisan, cluster_id: str) -> dict[str, Any]:
    """
    Which settled cycles this artisan may still review, and why not if none.

    A review cannot exist until money has actually reached the reviewer. That is the
    whole anti-gaming mechanism from §10.1, and it is a stronger one than moderation:
    a friend cannot leave five stars for a cluster that has never paid anybody,
    because there is no settlement row to hang the review on.
    """
    paid = (s.query(db.Settlement)
            .join(db.SettlementLine,
                  db.SettlementLine.settlement_id == db.Settlement.id)
            .filter(db.Settlement.cluster_id == cluster_id,
                    db.Settlement.status == "paid",
                    db.SettlementLine.artisan_id == artisan.id).all())
    if not paid:
        return {"canReview": False, "settlements": [],
                "why": "You can review a cluster once it has actually paid you for a "
                       "completed order. Nothing has settled yet."}

    already = {r.settlement_id for r in
               s.query(db.Review)
               .filter(db.Review.cluster_id == cluster_id,
                       db.Review.reviewer_artisan_id == artisan.id).all()}
    open_ones = [st.id for st in paid if st.id not in already]
    if not open_ones:
        return {"canReview": False, "settlements": [],
                "why": "You have already reviewed every cycle this cluster has paid "
                       "you for. There will be another after the next one."}
    return {"canReview": True, "settlements": open_ones, "why": ""}


def add_review(s, artisan: db.Artisan, cluster_id: str, *, settlement_id: str = "",
               paid_on_time: int = 0, commission_fair: int = 0,
               orders_regular: int = 0, note: str = "") -> db.Review:
    """
    Leave one review, against one settled cycle.

    Scores are 1 to 5 and at least one must be given - a review with nothing in it
    would still move the count, which is how a rating gets padded without anybody
    lying outright.
    """
    scores = {"paid_on_time": int(paid_on_time or 0),
              "commission_fair": int(commission_fair or 0),
              "orders_regular": int(orders_regular or 0)}
    for name, v in scores.items():
        if v and not 1 <= v <= 5:
            raise ClusterError("bad_score", f"{name} must be between 1 and 5.")
    if not any(scores.values()):
        raise ClusterError(
            "empty_review",
            "Rate at least one of the three things. An empty review would still "
            "count towards this cluster's score.")

    elig = review_eligibility(s, artisan, cluster_id)
    if not elig["canReview"]:
        raise ClusterError("not_eligible", elig["why"])

    sid = settlement_id or elig["settlements"][0]
    if sid not in elig["settlements"]:
        raise ClusterError(
            "not_eligible",
            "That settlement is not one this cluster has paid you for, or you have "
            "already reviewed it.")

    r = db.Review(id=db.nid("rev"), cluster_id=cluster_id,
                  reviewer_artisan_id=artisan.id, settlement_id=sid,
                  note=(note or "").strip()[:1000], **scores)
    s.add(r)
    s.flush()
    db.log_event(s, "cluster", cluster_id, "review", "", str(r.average),
                 f"from {artisan.id} against {sid}")
    return r


def reviews_for(s, cluster_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """
    The reviews themselves, with the reviewer named only by their first name.

    A cluster is often a handful of people in one town. "Sunita, 2 stars on paid on
    time" is not a review, it is an accusation with an address attached, and the
    person who left it has to keep working with the person it is about.
    """
    rows = (s.query(db.Review)
            .filter(db.Review.cluster_id == cluster_id)
            .order_by(db.Review.created_at.desc()).limit(limit).all())
    out = []
    for r in rows:
        a = s.get(db.Artisan, r.reviewer_artisan_id)
        first = ((a.full_name or a.business_name or "").strip().split(" ") or [""])[0]
        out.append({**r.public(), "reviewerFirstName": first or "A member"})
    return out


def release_capacity(s, artisan: db.Artisan, units: int,
                     reason: str = "") -> dict[str, Any]:
    """Give capacity back when an order is cancelled, rejected or completed."""
    units = int(units or 0)
    if units <= 0:
        raise ClusterError("bad_units", "Units must be a positive number.")
    before = artisan.capacity_available
    artisan.capacity_committed = max((artisan.capacity_committed or 0) - units, 0)
    s.flush()
    db.log_event(s, "artisan", artisan.id, "capacity", str(before),
                 str(artisan.capacity_available), f"released {units} - {reason}")
    return {"artisanId": artisan.id, "released": units,
            "capacityUnits": artisan.capacity_units or 0,
            "capacityCommitted": artisan.capacity_committed or 0,
            "capacityAvailable": artisan.capacity_available}
