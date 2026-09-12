"""
Phase 3: Goods Receipt Notes, and the tally that decides who gets paid.

The spec's "Done when" is three separate deliveries against one bulk order with a
correct running tally including a rejected unit. That is asserted here, plus the two
§11 rows this phase is responsible for: a short delivery measured against what
actually arrived, and a rejected batch reducing one artisan's accepted count without
touching anybody else's.

    python test_grn.py
"""
from __future__ import annotations

import sys
from datetime import timedelta

from fastapi.testclient import TestClient

import auth
import db
import main

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name, detail))
    return ok


def artisan(s, phone: str, *, gstin: str = "", capacity: int = 0):
    a = s.query(db.Artisan).filter(db.Artisan.phone == phone).first()
    if a is None:
        a = db.Artisan(id=db.nid("art"), phone=phone, phone_verified=1)
        s.add(a)
    a.phone_verified = 1
    a.gstin = gstin
    a.capacity_units = capacity
    a.capacity_committed = 0
    a.role = db.ROLE_ARTISAN
    a.full_name = a.full_name or f"GRN test {phone[-3:]}"
    s.flush()
    sess = db.Session(id=db.nid("ses"), artisan_id=a.id,
                      expires_at=db.now() + timedelta(days=1))
    s.add(sess)
    s.commit()
    return a.id, auth.issue_token(sess)


def wipe(s, phones: list[str]) -> None:
    for phone in phones:
        a = s.query(db.Artisan).filter(db.Artisan.phone == phone).first()
        if a is None:
            continue
        s.query(db.GoodsReceipt).filter(
            db.GoodsReceipt.artisan_id == a.id).delete()
        s.query(db.GoodsReceipt).filter(
            db.GoodsReceipt.logged_by_artisan_id == a.id).delete()
        for c in s.query(db.Cluster).filter(db.Cluster.owner_artisan_id == a.id).all():
            s.query(db.GoodsReceipt).filter(
                db.GoodsReceipt.cluster_id == c.id).delete()
            s.query(db.ClusterMembership).filter(
                db.ClusterMembership.cluster_id == c.id).delete()
            s.delete(c)
        s.query(db.ClusterMembership).filter(
            db.ClusterMembership.artisan_id == a.id).delete()
        s.query(db.Session).filter(db.Session.artisan_id == a.id).delete()
        s.delete(a)
    s.commit()


def run() -> int:
    client = TestClient(main.app)
    s = db.session()
    phones = ["9000000301", "9000000302", "9000000303"]
    wipe(s, phones)

    owner_id, owner_tok = artisan(s, phones[0], gstin="09AAACH7409R1ZZ")
    w1_id, w1_tok = artisan(s, phones[1], capacity=50)
    w2_id, w2_tok = artisan(s, phones[2], capacity=50)
    H = lambda t: {"Authorization": f"Bearer {t}"}

    c = client.post("/v1/clusters", headers=H(owner_tok),
                    json={"name": "GRN Test Cluster", "craftCategory": "pottery",
                          "commissionPct": 10}).json()
    cid = c["id"]
    for tok in (w1_tok, w2_tok):
        client.post(f"/v1/clusters/{cid}/join", headers=H(tok))

    # Both weavers promise work against the order.
    client.post("/v1/me/capacity/commit", headers=H(w1_tok),
                json={"units": 20, "clusterId": cid})
    client.post("/v1/me/capacity/commit", headers=H(w2_tok),
                json={"units": 20, "clusterId": cid})

    # A real bulk enquiry, so the tally has a target to measure against.
    lst = db.Listing(id=db.nid("lst"), artisan_id=owner_id, title_en="Test pot",
                     price=500, quantity=100, status="active")
    s.add(lst)
    enq = db.Enquiry(id=db.nid("enq"), listing_id=lst.id, artisan_id=owner_id,
                     buyer_name="Test Buyer", buyer_phone="9999999999",
                     quantity=40, status="new")
    s.add(enq)
    s.commit()

    # ── three deliveries, one of them with a reject ──────────────────────────
    r1 = client.post(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                     json={"artisanId": w1_id, "quantityReceived": 12,
                           "enquiryId": enq.id})
    check("Delivery 1 logged", r1.status_code == 200
          and r1.json()["receipt"]["qualityStatus"] == "pass",
          f"HTTP {r1.status_code} {str(r1.json())[:80]}")

    r2 = client.post(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                     json={"artisanId": w1_id, "quantityReceived": 8,
                           "quantityRejected": 1, "enquiryId": enq.id,
                           "note": "one cracked in the kiln"})
    check("Delivery 2 with one rejected unit is 'partial', not a reject",
          r2.status_code == 200
          and r2.json()["receipt"]["qualityStatus"] == "partial"
          and r2.json()["receipt"]["quantityAccepted"] == 7,
          f"HTTP {r2.status_code}")

    r3 = client.post(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                     json={"artisanId": w2_id, "quantityReceived": 15,
                           "enquiryId": enq.id})
    check("Delivery 3 from a second artisan", r3.status_code == 200)

    t = r3.json()["tally"]
    check("Running tally counts everything received", t["received"] == 35,
          f"received={t['received']}")
    check("Running tally counts the rejected unit separately", t["rejected"] == 1,
          f"rejected={t['rejected']}")
    check("Accepted is received minus rejected", t["accepted"] == 34,
          f"accepted={t['accepted']}")
    check("Three deliveries are counted as three", t["deliveries"] == 3,
          f"deliveries={t['deliveries']}")

    by = {e["artisanId"]: e for e in t["byArtisan"]}
    check("§11.6   A reject reduces only that artisan's accepted count",
          by[w1_id]["accepted"] == 19 and by[w2_id]["accepted"] == 15,
          f"w1={by[w1_id]['accepted']} w2={by[w2_id]['accepted']}")

    check("§11.5   Shortfall measures against what actually arrived",
          t["target"] == 40 and t["shortfall"] == 6 and t["complete"] is False,
          f"target={t['target']} short={t['shortfall']}")

    # ── capacity is freed by delivering, not by promising ────────────────────
    a1 = s.get(db.Artisan, w1_id)
    s.refresh(a1)
    check("Delivering frees the capacity it used, rejects included",
          a1.capacity_committed == 0,
          f"committed={a1.capacity_committed} (20 promised, 20 delivered)")

    # ── who may log, and who may read ────────────────────────────────────────
    r = client.post(f"/v1/clusters/{cid}/grn", headers=H(w1_tok),
                    json={"artisanId": w1_id, "quantityReceived": 99})
    # 403, not 409: the request is well formed, the caller is simply not allowed.
    check("A member cannot write their own receipt", r.status_code == 403
          and r.json().get("detail", {}).get("error") == "not_cluster_owner",
          f"HTTP {r.status_code}")

    r = client.get(f"/v1/clusters/{cid}/grn", headers=H(w2_tok))
    rows = r.json().get("receipts", [])
    check("A member sees only their own receipts",
          r.status_code == 200 and rows and all(g["artisanId"] == w2_id for g in rows)
          and "tally" not in r.json(),
          f"{len(rows)} row(s) visible")

    r = client.get(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                   params={"enquiryId": enq.id})
    check("The owner sees every receipt and the tally",
          r.status_code == 200 and len(r.json()["receipts"]) == 3
          and r.json()["isOwner"] is True and "tally" in r.json())

    # ── refusals ─────────────────────────────────────────────────────────────
    r = client.post(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                    json={"artisanId": w1_id, "quantityReceived": 5,
                          "quantityRejected": 9})
    check("Rejecting more than arrived is refused", r.status_code == 400,
          f"HTTP {r.status_code}")

    r = client.post(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                    json={"artisanId": w1_id, "quantityReceived": 0})
    check("A delivery of zero is refused", r.status_code == 400)

    stranger_id, _ = artisan(s, "9000000399")
    r = client.post(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                    json={"artisanId": stranger_id, "quantityReceived": 5})
    check("Cannot receive from somebody who is not in the cluster",
          r.status_code == 409, f"HTTP {r.status_code}")

    # ── voiding keeps the row and removes it from the tally ──────────────────
    gid = r2.json()["receipt"]["id"]
    r = client.post(f"/v1/grn/{gid}/void", headers=H(owner_tok),
                    json={"reason": "counted twice"})
    check("A receipt can be voided", r.status_code == 200
          and r.json()["qualityStatus"] == "void")

    still_there = s.get(db.GoodsReceipt, gid)
    check("A voided receipt is kept, not deleted", still_there is not None)

    r = client.get(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                   params={"enquiryId": enq.id})
    t2 = r.json()["tally"]
    check("A voided receipt leaves the tally", t2["received"] == 27
          and t2["rejected"] == 0 and t2["accepted"] == 27,
          f"received={t2['received']} accepted={t2['accepted']}")

    # ── no enquiry means no target, and it says so ───────────────────────────
    r = client.post(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                    json={"artisanId": w1_id, "quantityReceived": 3})
    t3 = r.json()["tally"]
    check("With no bulk enquiry behind it, shortfall is unknown rather than zero",
          t3["targetKnown"] is False and t3["shortfall"] is None
          and bool(t3.get("why")),
          f"target={t3['target']} short={t3['shortfall']}")

    s.query(db.GoodsReceipt).filter(db.GoodsReceipt.cluster_id == cid).delete()
    s.query(db.Enquiry).filter(db.Enquiry.id == enq.id).delete()
    s.query(db.Listing).filter(db.Listing.id == lst.id).delete()
    s.commit()
    wipe(s, phones + ["9000000399"])
    s.close()

    width = max(len(n) for _, n, _ in results) + 2
    print()
    for ok, name, detail in results:
        line = f"{'PASS' if ok else 'FAIL'}  {name:<{width}}"
        if not ok and detail:
            line += f"  {detail}"
        print(line)
    failed = sum(1 for ok, _, _ in results if not ok)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
