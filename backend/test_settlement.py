"""
Phase 4: the settlement arithmetic.

The thing worth testing here is not that multiplication works. It is that the money
is divided on the *net realised amount* and by units *actually received*, and that
every figure the system cannot establish stops the settlement instead of defaulting
to zero. Those three properties are the whole argument of addendum §9, and each one
is an assertion below.

    python test_settlement.py
"""
from __future__ import annotations

import sys
from datetime import timedelta

from fastapi.testclient import TestClient

import auth
import db
import main
import settlement

results: list[tuple[bool, str, str]] = []
PHONES = ["9000000401", "9000000402", "9000000403"]


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


def artisan(s, phone: str, *, gstin: str = "", pan: str = "", capacity: int = 0):
    a = s.query(db.Artisan).filter(db.Artisan.phone == phone).first()
    if a is None:
        a = db.Artisan(id=db.nid("art"), phone=phone, phone_verified=1)
        s.add(a)
    a.phone_verified = 1
    a.gstin, a.pan = gstin, pan
    a.capacity_units, a.capacity_committed = capacity, 0
    a.role = db.ROLE_ARTISAN
    a.full_name = a.full_name or f"Settle test {phone[-3:]}"
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
            for st in s.query(db.Settlement).filter(
                    db.Settlement.cluster_id == c.id).all():
                s.query(db.SettlementLine).filter(
                    db.SettlementLine.settlement_id == st.id).delete()
                s.delete(st)
            s.query(db.GoodsReceipt).filter(
                db.GoodsReceipt.cluster_id == c.id).delete()
            s.query(db.ClusterMembership).filter(
                db.ClusterMembership.cluster_id == c.id).delete()
            s.delete(c)
        for lst in s.query(db.Listing).filter(db.Listing.artisan_id == a.id).all():
            s.query(db.Order).filter(db.Order.listing_id == lst.id).delete()
            s.delete(lst)
        s.query(db.GoodsReceipt).filter(db.GoodsReceipt.artisan_id == a.id).delete()
        s.query(db.ClusterMembership).filter(
            db.ClusterMembership.artisan_id == a.id).delete()
        s.query(db.Session).filter(db.Session.artisan_id == a.id).delete()
        s.delete(a)
    s.commit()


def run() -> int:
    client = TestClient(main.app)
    s = db.session()
    wipe(s)

    owner_id, owner_tok = artisan(s, PHONES[0], gstin="09AAACH7409R1ZZ",
                                  pan="AAACH7409R")
    # One weaver with a PAN (payable automatically), one without (manual UPI).
    w1_id, w1_tok = artisan(s, PHONES[1], pan="ABCDE1234F", capacity=60)
    w2_id, w2_tok = artisan(s, PHONES[2], capacity=60)
    H = lambda t: {"Authorization": f"Bearer {t}"}

    c = client.post("/v1/clusters", headers=H(owner_tok),
                    json={"name": "Settlement Test Cluster",
                          "craftCategory": "handloom", "commissionPct": 10}).json()
    cid = c["id"]
    for tok in (w1_tok, w2_tok):
        client.post(f"/v1/clusters/{cid}/join", headers=H(tok))

    # A listing on a 5%-rated woven silk heading, and a storefront order for 40.
    lst = db.Listing(id=db.nid("lst"), artisan_id=owner_id, title_en="Banarasi stole",
                     hsn="5007", price=500, quantity=100, status="active")
    s.add(lst)
    order = db.Order(id=db.nid("ord"), listing_id=lst.id, channel="storefront",
                     buyer_name="Test Buyer", buyer_phone="9999999999",
                     quantity=40, amount=20000, status="paid",
                     payment_status="paid")
    s.add(order)
    s.commit()

    # ── nothing delivered yet ────────────────────────────────────────────────
    r = client.post(f"/v1/clusters/{cid}/settlements", headers=H(owner_tok),
                    json={"orderId": order.id, "logisticsFee": 500})
    j = r.json()
    check("With no goods receipts there is nothing to divide, and it says so",
          r.status_code == 200 and j["status"] == "needs_input"
          and "deliver" in j["note"].lower(),
          f"status={j.get('status')} note={j.get('note','')[:60]}")

    # ── deliveries: 25 accepted and 12 accepted ─────────────────────────────
    client.post(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                json={"artisanId": w1_id, "quantityReceived": 25, "orderId": order.id})
    client.post(f"/v1/clusters/{cid}/grn", headers=H(owner_tok),
                json={"artisanId": w2_id, "quantityReceived": 15,
                      "quantityRejected": 3, "orderId": order.id})

    # ── an unknown logistics fee must stop it ───────────────────────────────
    r = client.post(f"/v1/clusters/{cid}/settlements", headers=H(owner_tok),
                    json={"orderId": order.id})
    j = r.json()
    check("An unknown shipping cost stops the settlement rather than counting as 0",
          j["status"] == "needs_input" and "courier" in j["note"].lower(),
          f"note={j.get('note','')[:70]}")

    # ── the real computation ────────────────────────────────────────────────
    r = client.post(f"/v1/clusters/{cid}/settlements", headers=H(owner_tok),
                    json={"orderId": order.id, "logisticsFee": 500})
    j = r.json()
    check("With every figure known, the settlement computes",
          j["status"] == "computed", f"status={j['status']} note={j.get('note','')[:70]}")

    ded = {d["label"].split(" at ")[0]: d["amount"] for d in j["deductions"]}
    check("The storefront's own fee is genuinely zero, not merely unknown",
          ded.get("Marketplace fee") == 0
          and j["deductions"][0]["source"] == "storefront")
    # 5007 (woven silk) is 5% in the seeded table. Asserting the real published
    # rate rather than a convenient zero is the point: an earlier draft of this test
    # assumed handloom was nil-rated, which is exactly the wrong instinct the rate
    # table exists to stop.
    check("GST comes from the HSN table at the real published rate, 5% for 5007",
          ded.get("GST") == 1000 and j["deductions"][1]["source"] == "HSN 5007",
          f"gst={ded.get('GST')} src={j['deductions'][1]['source']}")
    check("Shipping is deducted", ded.get("Shipping") == 500)

    # gross 20000 - 0 fee - 1000 GST - 500 shipping = 18500 net
    # 10% commission = 1850, leaving 16650 to divide
    check("Net is gross minus all three deductions", j["net"] == 18500,
          f"net={j['net']}")
    check("Commission is a share of the NET, not of the sticker price",
          j["commission"]["amount"] == 1850 and j["commission"]["pct"] == 10,
          f"commission={j['commission']['amount']} (10% of 18500 is 1850; "
          f"10% of the 20000 sticker price would be 2000)")
    check("Distributable is what is left after the commission",
          j["distributable"] == 16650, f"distributable={j['distributable']}")

    # 25 and 12 accepted = 37 units, and 16650 / 37 does not divide evenly
    by = {p["artisanId"]: p for p in j["payees"]}
    check("§11.5   Payment follows units ACCEPTED, not units promised",
          by[w1_id]["units"] == 25 and by[w2_id]["units"] == 12,
          f"w1={by[w1_id]['units']} w2={by[w2_id]['units']} "
          f"(w2 delivered 15 with 3 rejected)")
    # Relational, not a hardcoded total: the property that matters is that no
    # rounding remainder goes missing, whatever the numbers happen to be.
    check("The payouts sum to the distributable amount exactly, no rounding lost",
          round(by[w1_id]["amount"] + by[w2_id]["amount"], 2) == j["distributable"],
          f"{by[w1_id]['amount']} + {by[w2_id]['amount']} = "
          f"{round(by[w1_id]['amount'] + by[w2_id]['amount'], 2)}, "
          f"distributable {j['distributable']}")
    check("The artisan who delivered more is paid more",
          by[w1_id]["amount"] > by[w2_id]["amount"])

    # ── §11.8 no PAN means no automatic transfer ────────────────────────────
    check("§11.8   A payee with a PAN is routed automatically",
          by[w1_id]["payoutMethod"] == "route" and by[w1_id]["hasPan"] is True)
    check("§11.8   A payee with no PAN falls back to manual UPI, with the reason",
          by[w2_id]["payoutMethod"] == "manual_upi"
          and by[w2_id]["hasPan"] is False
          and "PAN" in by[w2_id]["whyMethod"],
          f"method={by[w2_id]['payoutMethod']}")

    # ── an unknown GST rate must also stop it ───────────────────────────────
    lst.hsn = "9999"          # not a heading in the rate table
    s.commit()
    r = client.post(f"/v1/clusters/{cid}/settlements", headers=H(owner_tok),
                    json={"orderId": order.id, "logisticsFee": 500})
    j2 = r.json()
    check("An HSN with no published rate stops the settlement, never assumes nil",
          j2["status"] == "needs_input" and "9999" in j2["note"],
          f"note={j2.get('note','')[:70]}")
    lst.hsn = "5007"
    s.commit()
    client.post(f"/v1/clusters/{cid}/settlements", headers=H(owner_tok),
                json={"orderId": order.id, "logisticsFee": 500})

    # ── who may compute, and who may read ───────────────────────────────────
    r = client.post(f"/v1/clusters/{cid}/settlements", headers=H(w1_tok),
                    json={"orderId": order.id, "logisticsFee": 500})
    check("A member cannot settle the cluster's orders", r.status_code == 403,
          f"HTTP {r.status_code}")

    r = client.get(f"/v1/clusters/{cid}/settlements", headers=H(w2_tok))
    j3 = r.json()
    payees = [p for st in j3["settlements"] for p in st["payees"]]
    check("A member sees only their own line, never another artisan's payout",
          r.status_code == 200 and payees
          and all(p["artisanId"] == w2_id for p in payees)
          and all("commission" not in st for st in j3["settlements"]),
          f"{len(payees)} line(s) visible")

    r = client.get(f"/v1/clusters/{cid}/settlements", headers=H(owner_tok))
    check("The owner sees the whole settlement",
          r.json()["isOwner"] is True
          and "commission" in r.json()["settlements"][0])

    # ── the take-home estimate ──────────────────────────────────────────────
    r = client.get(f"/v1/listings/{lst.id}/take-home",
                   headers=H(w1_tok), params={"clusterId": cid, "quantity": 1})
    q = r.json()
    # 500 sticker, less 5% GST = 475, less 10% cluster commission = 427.50
    check("Take-home shows what reaches the artisan, not the sticker price",
          r.status_code == 200 and q["gross"] == 500
          and q["estimatedTakeHome"] == 427.5,
          f"gross={q.get('gross')} take-home={q.get('estimatedTakeHome')}")
    check("Take-home admits which deductions are not yet knowable",
          q["complete"] is False and "shipping" in q["unknown"]
          and "marketplace fee" in q["unknown"],
          f"unknown={q.get('unknown')}")

    # ── a paid settlement is immutable ──────────────────────────────────────
    st = s.query(db.Settlement).filter(db.Settlement.cluster_id == cid).first()
    st.status = settlement.STATUS_PAID
    s.commit()
    r = client.post(f"/v1/clusters/{cid}/settlements", headers=H(owner_tok),
                    json={"orderId": order.id, "logisticsFee": 500})
    check("A settlement already paid cannot be recomputed",
          r.status_code == 400
          and r.json().get("detail", {}).get("error") == "already_paid",
          f"HTTP {r.status_code}")

    wipe(s)
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
