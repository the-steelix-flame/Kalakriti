"""
Phase 5: reviews that cannot be bought.

The rating is only worth reading if leaving one costs something, and what it costs
here is a completed, paid cycle of work. Every check below is about that constraint
holding when somebody pushes on it - a member who has not been paid, a member paid
once trying to review twice, an empty review that would still move the average.

    python test_reviews.py
"""
from __future__ import annotations

import sys
from datetime import timedelta

from fastapi.testclient import TestClient

import auth
import db
import main

results: list[tuple[bool, str, str]] = []
PHONES = ["9000000601", "9000000602", "9000000603"]


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


def artisan(s, phone: str, *, gstin: str = "", capacity: int = 0):
    a = s.query(db.Artisan).filter(db.Artisan.phone == phone).first()
    if a is None:
        a = db.Artisan(id=db.nid("art"), phone=phone, phone_verified=1)
        s.add(a)
    a.phone_verified, a.gstin = 1, gstin
    a.capacity_units, a.capacity_committed = capacity, 0
    a.role = db.ROLE_ARTISAN
    a.full_name = a.full_name or f"Review test {phone[-3:]}"
    s.flush()
    sess = db.Session(id=db.nid("ses"), artisan_id=a.id,
                      expires_at=db.now() + timedelta(days=1))
    s.add(sess)
    s.commit()
    return a.id, auth.issue_token(sess)


def wipe(s) -> None:
    for phone in PHONES:
        a = s.query(db.Artisan).filter(db.Artisan.phone == phone).first()
        if a is None:
            continue
        for c in s.query(db.Cluster).filter(db.Cluster.owner_artisan_id == a.id).all():
            s.query(db.Review).filter(db.Review.cluster_id == c.id).delete()
            for st in s.query(db.Settlement).filter(
                    db.Settlement.cluster_id == c.id).all():
                s.query(db.SettlementLine).filter(
                    db.SettlementLine.settlement_id == st.id).delete()
                s.delete(st)
            s.query(db.ClusterMembership).filter(
                db.ClusterMembership.cluster_id == c.id).delete()
            s.commit()
            s.delete(c)
        s.query(db.Review).filter(db.Review.reviewer_artisan_id == a.id).delete()
        s.query(db.ClusterMembership).filter(
            db.ClusterMembership.artisan_id == a.id).delete()
        s.query(db.Session).filter(db.Session.artisan_id == a.id).delete()
        s.commit()
        s.query(db.Artisan).filter(db.Artisan.id == a.id).delete()
        s.commit()


def run() -> int:
    client = TestClient(main.app)
    s = db.session()
    wipe(s)

    owner_id, owner_tok = artisan(s, PHONES[0], gstin="09AAACH7409R1ZZ")
    w1_id, w1_tok = artisan(s, PHONES[1], capacity=40)
    w2_id, w2_tok = artisan(s, PHONES[2], capacity=40)
    H = lambda t: {"Authorization": f"Bearer {t}"}

    c = client.post("/v1/clusters", headers=H(owner_tok),
                    json={"name": "Review Test Cluster", "craftCategory": "handloom",
                          "commissionPct": 9}).json()
    cid = c["id"]
    for tok in (w1_tok, w2_tok):
        client.post(f"/v1/clusters/{cid}/join", headers=H(tok))

    # ── cold start ──────────────────────────────────────────────────────────
    r = client.get(f"/v1/clusters/{cid}/reviews")
    j = r.json()
    check("A brand-new cluster reports no rating, never zero stars",
          r.status_code == 200 and j["rating"]["available"] is False
          and j["rating"]["overall"] is None
          and "no reviews yet" in j["rating"]["why"].lower(),
          f"rating={j['rating']}")

    # ── a member who has not been paid cannot review ────────────────────────
    r = client.post(f"/v1/clusters/{cid}/reviews", headers=H(w1_tok),
                    json={"paidOnTime": 5, "commissionFair": 5, "ordersRegular": 5})
    d = r.json().get("detail", {})
    check("A member who has never been paid cannot leave a review",
          r.status_code == 400 and d.get("error") == "not_eligible"
          and "settled" in d.get("why", "").lower(),
          f"HTTP {r.status_code} {d.get('why', '')[:60]}")

    # ── a settlement that exists but is not paid is not enough ─────────────
    st = db.Settlement(id=db.nid("stl"), order_id="", cluster_id=cid,
                       gross_amount=1000, net_amount=1000,
                       distributable_amount=900, status="computed")
    s.add(st)
    s.add(db.SettlementLine(id=db.nid("stlline"), settlement_id=st.id,
                            artisan_id=w1_id, units=10, rate=90, amount=900,
                            payout_method="manual_upi", payout_status="pending"))
    s.commit()

    r = client.post(f"/v1/clusters/{cid}/reviews", headers=H(w1_tok),
                    json={"paidOnTime": 5})
    check("A settlement that is computed but NOT paid still does not unlock a review",
          r.status_code == 400
          and r.json().get("detail", {}).get("error") == "not_eligible",
          f"HTTP {r.status_code}")

    # ── once it is paid, the review becomes possible ───────────────────────
    st.status = "paid"
    s.commit()

    r = client.get(f"/v1/clusters/{cid}/reviews", headers=H(w1_tok))
    check("Once paid, the member is told they may review",
          r.json()["eligibility"]["canReview"] is True
          and st.id in r.json()["eligibility"]["settlements"])

    r = client.post(f"/v1/clusters/{cid}/reviews", headers=H(w1_tok),
                    json={"paidOnTime": 5, "commissionFair": 4, "ordersRegular": 3,
                          "note": "Paid the same week, no argument about the count."})
    check("A paid member can leave a review", r.status_code == 200,
          f"HTTP {r.status_code} {r.text[:90]}")
    check("The average is of the criteria actually rated",
          r.status_code == 200 and r.json()["review"]["average"] == 4.0,
          f"average={r.json().get('review', {}).get('average')}")

    # ── the same cycle cannot be reviewed twice ────────────────────────────
    r = client.post(f"/v1/clusters/{cid}/reviews", headers=H(w1_tok),
                    json={"paidOnTime": 5, "settlementId": st.id})
    check("§11.9   The same settled cycle cannot be reviewed a second time",
          r.status_code == 400
          and r.json().get("detail", {}).get("error") == "not_eligible",
          f"HTTP {r.status_code}")

    # ── another member still cannot, because they were not paid ────────────
    r = client.post(f"/v1/clusters/{cid}/reviews", headers=H(w2_tok),
                    json={"paidOnTime": 5})
    check("§11.9   A second member cannot pile on without having been paid either",
          r.status_code == 400,
          "this is the 'friends leaving five stars' case, refused at the database")

    # ── an empty review would still move the count ─────────────────────────
    st2 = db.Settlement(id=db.nid("stl"), order_id="", cluster_id=cid,
                        gross_amount=500, net_amount=500,
                        distributable_amount=450, status="paid")
    s.add(st2)
    s.add(db.SettlementLine(id=db.nid("stlline"), settlement_id=st2.id,
                            artisan_id=w1_id, units=5, rate=90, amount=450,
                            payout_method="manual_upi", payout_status="settled"))
    s.commit()
    r = client.post(f"/v1/clusters/{cid}/reviews", headers=H(w1_tok),
                    json={"settlementId": st2.id})
    check("A review rating nothing is refused - it would still pad the count",
          r.status_code == 400
          and r.json().get("detail", {}).get("error") == "empty_review")

    r = client.post(f"/v1/clusters/{cid}/reviews", headers=H(w1_tok),
                    json={"settlementId": st2.id, "paidOnTime": 9})
    check("A score outside 1 to 5 is refused", r.status_code == 400
          and r.json().get("detail", {}).get("error") == "bad_score")

    # ── one review is not a rating ─────────────────────────────────────────
    r = client.get(f"/v1/clusters/{cid}/reviews")
    j = r.json()
    check("One review is still not enough to publish an average",
          j["rating"]["available"] is False and j["rating"]["count"] == 1
          and "too few" in j["rating"]["why"].lower(),
          f"count={j['rating']['count']} why={j['rating']['why'][:50]}")

    check("The reviewer is shown by first name only, not in full",
          j["reviews"] and " " not in j["reviews"][0]["reviewerFirstName"].strip(),
          f"shown as {j['reviews'][0]['reviewerFirstName']!r}" if j["reviews"] else "")

    # ── the cluster card carries the rating for somebody deciding to join ──
    card = client.get(f"/v1/clusters/{cid}").json()
    check("The rating travels with the cluster card, readable before joining",
          "rating" in card and card["rating"]["count"] == 1)

    width = max(len(n) for _, n, _ in results) + 2
    print()
    for ok, name, detail in results:
        line = f"{'PASS' if ok else 'FAIL'}  {name:<{width}}"
        if detail and (not ok or name.startswith("§")):
            line += f"  {detail}"
        print(line)
    failed = sum(1 for ok, _, _ in results if not ok)
    print(f"\n{len(results) - failed} passed, {failed} failed")

    try:
        wipe(s)
    except Exception as e:                                   # noqa: BLE001
        s.rollback()
        print(f"(cleanup left rows behind: {type(e).__name__})")
    s.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
