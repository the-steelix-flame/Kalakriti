"""
Phase 2 end-to-end check: roles, cluster creation, joining, and the capacity guard.

Runs against the real FastAPI routes through TestClient, so it exercises the HTTP
layer, the auth header, the error shapes and the database - not just the functions in
`clusters.py`.

Every numbered case below is a row from the Business Model Addendum §11 edge-case
matrix. They are asserted here rather than described, because §11 is the part of that
document most likely to be waved at rather than built.

    python test_clusters.py
"""
from __future__ import annotations

import sys
from datetime import timedelta

from fastapi.testclient import TestClient

import auth
import db
import main

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((PASS if ok else FAIL, name, detail))
    return ok


def make_artisan(s, phone: str, *, gstin: str = "", capacity: int = 0) -> tuple[str, str]:
    """A verified artisan and a signed token for them, without going through OTP."""
    a = s.query(db.Artisan).filter(db.Artisan.phone == phone).first()
    if a is None:
        a = db.Artisan(id=db.nid("art"), phone=phone, phone_verified=1)
        s.add(a)
    a.phone_verified = 1
    a.full_name = a.full_name or f"Test {phone[-4:]}"
    a.gstin = gstin
    a.capacity_units = capacity
    a.capacity_committed = 0
    a.role = db.ROLE_ARTISAN
    s.flush()

    sess = db.Session(id=db.nid("ses"), artisan_id=a.id,
                      expires_at=db.now() + timedelta(days=1))
    s.add(sess)
    s.commit()
    return a.id, auth.issue_token(sess)


def hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def cleanup(s, phones: list[str]) -> None:
    """Remove anything an earlier run left behind, so the script is re-runnable."""
    for phone in phones:
        a = s.query(db.Artisan).filter(db.Artisan.phone == phone).first()
        if a is None:
            continue
        owned = s.query(db.Cluster).filter(db.Cluster.owner_artisan_id == a.id).all()
        for c in owned:
            s.query(db.ClusterMembership).filter(
                db.ClusterMembership.cluster_id == c.id).delete()
            s.delete(c)
        s.query(db.ClusterMembership).filter(
            db.ClusterMembership.artisan_id == a.id).delete()
        s.query(db.Session).filter(db.Session.artisan_id == a.id).delete()
        s.delete(a)
    s.commit()


def main_test() -> int:
    client = TestClient(main.app)
    s = db.session()

    weaver_phone, owner_phone, owner2_phone = "9000000101", "9000000102", "9000000103"
    cleanup(s, [weaver_phone, owner_phone, owner2_phone])

    # A weaver with no GST and 40 units of capacity; two GST holders.
    weaver_id, weaver_tok = make_artisan(s, weaver_phone, capacity=40)
    owner_id, owner_tok = make_artisan(s, owner_phone, gstin="09AAACH7409R1ZZ")
    owner2_id, owner2_tok = make_artisan(s, owner2_phone, gstin="27AAACH7409R1Z1")

    # ── no GST, no cluster ────────────────────────────────────────────────────
    r = client.post("/v1/clusters", headers=hdr(weaver_tok),
                    json={"name": "Should not exist", "commissionPct": 10})
    body = r.json()
    check("A non-GST artisan cannot create a cluster",
          r.status_code == 400 and body.get("detail", {}).get("error") == "gstin_required",
          f"HTTP {r.status_code} {str(body)[:90]}")

    # ── create ────────────────────────────────────────────────────────────────
    r = client.post("/v1/clusters", headers=hdr(owner_tok),
                    json={"name": "Banaras Handloom Samuh", "craftCategory": "handloom",
                          "commissionPct": 8.0, "maxOrderUnits": 500,
                          "district": "Varanasi", "state": "UP"})
    c1 = r.json()
    check("A GST holder creates a cluster", r.status_code == 200 and bool(c1.get("id")),
          f"HTTP {r.status_code}")
    check("Creating a cluster promotes the owner to cluster_creator",
          client.get("/v1/clusters", headers=hdr(owner_tok),
                     params={"mine": True}).status_code == 200
          and s.get(db.Artisan, owner_id).role == db.ROLE_CLUSTER_CREATOR,
          f"role={s.get(db.Artisan, owner_id).role}")
    check("An invite code is issued", len(c1.get("inviteCode") or "") == 6,
          f"code={c1.get('inviteCode')!r}")

    r = client.post("/v1/clusters", headers=hdr(owner_tok),
                    json={"name": "Nonsense commission", "commissionPct": 140})
    check("A commission of 140% is refused", r.status_code == 400,
          f"HTTP {r.status_code}")

    # A second cluster, same craft, different terms - owned by somebody else.
    r = client.post("/v1/clusters", headers=hdr(owner2_tok),
                    json={"name": "Chandauli Weavers Co-op", "craftCategory": "handloom",
                          "commissionPct": 12.5, "maxOrderUnits": 300,
                          "district": "Chandauli", "state": "UP"})
    c2 = r.json()
    check("A second cluster in the same craft can exist", r.status_code == 200)

    # ── §11.14 terms readable before joining, and §11.10 cold start ───────────
    r = client.get("/v1/clusters", params={"craftCategory": "handloom"})
    listed = r.json().get("clusters", [])
    mine = next((c for c in listed if c["id"] == c1["id"]), None)
    check("§11.14  Commission and platforms are readable before joining, "
          "without signing in",
          r.status_code == 200 and mine is not None
          and mine.get("commissionPct") == 8.0 and "platforms" in mine,
          f"{len(listed)} cluster(s) listed")
    # The question is not whether a key mentions GSTIN - `ownerGstinLast4` does, on
    # purpose, to show the cluster is a registered entity. It is whether the full
    # number can be reconstructed from anything in the payload.
    full_gstin = "09AAACH7409R1ZZ"
    serialised = str(mine)
    check("A cluster never exposes the owner's full GSTIN",
          mine is not None and full_gstin not in serialised
          and len(mine.get("ownerGstinLast4", "")) <= 4,
          f"last4={mine.get('ownerGstinLast4') if mine else None!r}")

    rating = (mine or {}).get("rating", {})
    check("§11.10  A new cluster reports no rating, never zero stars",
          rating.get("available") is False and rating.get("overall") is None
          and "no reviews yet" in (rating.get("why") or "").lower(),
          f"rating={rating}")

    # ── §11.3 two clusters, different commission, both visible ───────────────
    pcts = sorted(c.get("commissionPct") for c in listed
                  if c["id"] in (c1["id"], c2["id"]))
    check("§11.3   Two clusters offering different commission are both visible",
          pcts == [8.0, 12.5], f"commissions={pcts}")

    # ── join by code ──────────────────────────────────────────────────────────
    r = client.get(f"/v1/clusters/by-code/{c1['inviteCode']}")
    check("An invite code resolves to the terms before it enrols anybody",
          r.status_code == 200 and r.json().get("id") == c1["id"],
          f"HTTP {r.status_code}")

    r = client.post("/v1/clusters/join", headers=hdr(weaver_tok),
                    json={"inviteCode": c1["inviteCode"].lower()})
    check("Joining by invite code works, and the code is case-insensitive",
          r.status_code == 200 and r.json()["membership"]["status"] == "active",
          f"HTTP {r.status_code}")

    r = client.post("/v1/clusters/join", headers=hdr(weaver_tok),
                    json={"inviteCode": "ZZZZZZ"})
    check("A wrong invite code is refused with a reason, not a stack trace",
          r.status_code == 404
          and r.json().get("detail", {}).get("error") == "bad_invite_code")

    r = client.post(f"/v1/clusters/{c1['id']}/join", headers=hdr(owner_tok))
    check("The owner cannot join their own cluster", r.status_code == 409,
          f"HTTP {r.status_code}")

    # Double-tap on Join must not create a second membership.
    client.post(f"/v1/clusters/{c1['id']}/join", headers=hdr(weaver_tok))
    n = s.query(db.ClusterMembership).filter(
        db.ClusterMembership.cluster_id == c1["id"],
        db.ClusterMembership.artisan_id == weaver_id).count()
    check("Joining twice leaves exactly one membership row", n == 1, f"rows={n}")

    # ── §11.1 two clusters at once ────────────────────────────────────────────
    r = client.post(f"/v1/clusters/{c2['id']}/join", headers=hdr(weaver_tok))
    check("§11.1   One artisan can belong to two clusters at once",
          r.status_code == 200, f"HTTP {r.status_code}")
    r = client.get("/v1/clusters", headers=hdr(weaver_tok), params={"mine": True})
    check("Both memberships are listed back to the artisan",
          len(r.json().get("clusters", [])) == 2,
          f"{len(r.json().get('clusters', []))} membership(s)")

    # ── §11.2 the capacity guard, which is the whole point ───────────────────
    r = client.post("/v1/me/capacity/commit", headers=hdr(weaver_tok),
                    json={"units": 30, "clusterId": c1["id"], "reason": "order A"})
    check("Committing 30 of 40 units succeeds",
          r.status_code == 200 and r.json()["capacityAvailable"] == 10,
          f"HTTP {r.status_code} {str(r.json())[:70]}")

    r = client.post("/v1/me/capacity/commit", headers=hdr(weaver_tok),
                    json={"units": 30, "clusterId": c2["id"], "reason": "order B"})
    detail = r.json().get("detail", {})
    check("§11.2   The SECOND cluster cannot commit the same weeks of work",
          r.status_code == 409 and detail.get("error") == "insufficient_capacity",
          f"HTTP {r.status_code} {detail.get('why', '')[:60]}")

    r = client.post("/v1/me/capacity/commit", headers=hdr(weaver_tok),
                    json={"units": 10, "clusterId": c2["id"]})
    check("The second cluster can still take what genuinely remains",
          r.status_code == 200 and r.json()["capacityAvailable"] == 0)

    r = client.patch("/v1/me/capacity", headers=hdr(weaver_tok), json={"units": 5})
    check("Capacity cannot be set below work already promised",
          r.status_code == 409, f"HTTP {r.status_code}")

    # ── leaving ───────────────────────────────────────────────────────────────
    r = client.post(f"/v1/clusters/{c2['id']}/leave", headers=hdr(weaver_tok))
    check("Leaving a cluster works", r.status_code == 200
          and r.json()["status"] == "left")

    a = s.get(db.Artisan, weaver_id)
    s.refresh(a)
    check("Leaving does NOT silently free work already committed",
          (a.capacity_committed or 0) == 40,
          f"committed={a.capacity_committed}")

    r = client.post(f"/v1/clusters/{c2['id']}/join", headers=hdr(weaver_tok))
    n = s.query(db.ClusterMembership).filter(
        db.ClusterMembership.cluster_id == c2["id"],
        db.ClusterMembership.artisan_id == weaver_id).count()
    check("Re-joining reuses the same row rather than adding a second",
          r.status_code == 200 and n == 1, f"rows={n}")

    r = client.post("/v1/me/capacity/release", headers=hdr(weaver_tok),
                    json={"units": 40, "reason": "orders delivered"})
    check("Releasing capacity gives it back",
          r.status_code == 200 and r.json()["capacityAvailable"] == 40)

    # ── §11.11 role toggle, both directions ──────────────────────────────────
    r = client.patch("/v1/me/role", headers=hdr(weaver_tok),
                     json={"role": db.ROLE_SOLO_SELLER})
    check("§11.11  An artisan with no GSTIN cannot become a solo seller",
          r.status_code == 400
          and r.json().get("detail", {}).get("error") == "gstin_required")

    r = client.patch("/v1/me/role", headers=hdr(owner_tok),
                     json={"role": db.ROLE_SOLO_SELLER})
    check("A cluster owner cannot step down while the cluster is still running",
          r.status_code == 409
          and r.json().get("detail", {}).get("error") == "owns_active_cluster",
          f"HTTP {r.status_code}")

    r = client.patch("/v1/me/role", headers=hdr(owner2_tok),
                     json={"role": db.ROLE_SOLO_SELLER})
    # owner2 still owns c2, so this must also be refused - then close c2 and retry.
    c2row = s.get(db.Cluster, c2["id"])
    c2row.status = "closed"
    s.commit()
    r = client.patch("/v1/me/role", headers=hdr(owner2_tok),
                     json={"role": db.ROLE_SOLO_SELLER})
    check("§11.11  Once no cluster is active, the role can be switched back",
          r.status_code == 200 and r.json()["role"] == db.ROLE_SOLO_SELLER,
          f"HTTP {r.status_code}")

    # ── auth ─────────────────────────────────────────────────────────────────
    r = client.post("/v1/clusters", json={"name": "no token", "commissionPct": 5})
    check("Creating a cluster without a token is refused", r.status_code == 401)

    cleanup(s, [weaver_phone, owner_phone, owner2_phone])
    s.close()

    # ── report ───────────────────────────────────────────────────────────────
    width = max(len(n) for _, n, _ in results) + 2
    print()
    for status, name, detail in results:
        line = f"{status}  {name:<{width}}"
        if status == FAIL and detail:
            line += f"  {detail}"
        print(line)
    failed = sum(1 for st, _, _ in results if st == FAIL)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_test())
