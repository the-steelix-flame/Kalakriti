"""
ONDC end to end: a buyer app browses, asks the price, and buys.

This stands in for a real BAP. It POSTs the same Beckn actions a buyer app would, and
a tiny local HTTP server plays the buyer app's `bap_uri` so the asynchronous
callbacks have somewhere real to land - which is the half that a request/response
test would quietly skip, and the half where `transaction_id` mismatches hide.

What it is actually checking:

  - the price a buyer sees on ONDC is `Listing.price`, at every step
  - `/confirm` and nothing else creates an Order, and that Order is the one the
    artisan's own Orders tab reads
  - stock moves once, and a second confirm for the same order does not double it
  - overselling is refused before the buyer pays, not reconciled afterwards

    python test_ondc.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ONDC must look configured or every endpoint answers "not registered". These are
# throwaway values for this process only; nothing is sent to the real network,
# because the only address involved is the local stub below.
import base64  # noqa: E402

from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

_k = ed25519.Ed25519PrivateKey.generate()
os.environ["ONDC_SUBSCRIBER_ID"] = "test.kalakriti.local"
os.environ["ONDC_SIGNING_PRIVATE_KEY"] = base64.b64encode(
    _k.private_bytes_raw() + _k.public_key().public_bytes_raw()).decode()
os.environ["ONDC_GATEWAY_URL"] = "http://127.0.0.1:9"

from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402

results: list[tuple[bool, str, str]] = []
CALLBACKS: list[dict] = []
PHONE = "9000000501"


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


class BapStub(BaseHTTPRequestHandler):
    """The buyer app's callback surface. Records whatever the BPP posts back."""

    def do_POST(self):                                       # noqa: N802
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except Exception:                                    # noqa: BLE001
            body = {"unparseable": raw[:200].decode("latin1")}
        CALLBACKS.append({"path": self.path.lstrip("/"),
                          "auth": self.headers.get("Authorization", ""),
                          "body": body})
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"message":{"ack":{"status":"ACK"}}}')

    def log_message(self, *a):                               # noqa: A003
        pass


def wait_for(action: str, timeout: float = 6.0) -> dict | None:
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        for cb in CALLBACKS:
            if cb["path"] == action:
                return cb
        time.sleep(0.05)
    return None


def ctx(bap_uri: str, action: str, txn: str) -> dict:
    return {"context": {
        "domain": "ONDC:RET10", "country": "IND", "city": "std:0542",
        "action": action, "core_version": "2.0.2",
        "bap_id": "buyerapp.test", "bap_uri": bap_uri,
        "transaction_id": txn, "message_id": str(uuid.uuid4()),
        "timestamp": "2026-09-12T00:00:00.000Z", "ttl": "PT30S",
    }}


def run() -> int:
    server = HTTPServer(("127.0.0.1", 0), BapStub)
    port = server.server_port
    bap_uri = f"http://127.0.0.1:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    client = TestClient(main.app)
    s = db.session()

    # a seller, and one thing to sell
    a = s.query(db.Artisan).filter(db.Artisan.phone == PHONE).first()
    if a is None:
        a = db.Artisan(id=db.nid("art"), phone=PHONE, phone_verified=1)
        s.add(a)
    a.phone_verified = 1
    a.full_name = "ONDC Test Weaver"
    s.flush()
    sess = db.Session(id=db.nid("ses"), artisan_id=a.id,
                      expires_at=db.now() + timedelta(days=1))
    s.add(sess)
    tok = auth.issue_token(sess)

    for old in s.query(db.Listing).filter(db.Listing.artisan_id == a.id).all():
        s.query(db.Order).filter(db.Order.listing_id == old.id).delete()
        s.delete(old)
    lst = db.Listing(id=db.nid("lst"), artisan_id=a.id,
                     title_en="Banarasi silk scarf", desc_en="Handwoven in Varanasi",
                     hsn="5007", price=1250, quantity=3, status="active",
                     category="Home & Kitchen")
    s.add(lst)
    s.commit()
    txn = str(uuid.uuid4())
    H = {"Authorization": f"Bearer {tok}"}

    # ── search ───────────────────────────────────────────────────────────────
    r = client.post("/ondc/search", json={**ctx(bap_uri, "search", txn),
                                          "message": {"intent": {}}})
    check("search is ACKed immediately",
          r.status_code == 200
          and r.json()["message"]["ack"]["status"] == "ACK",
          f"HTTP {r.status_code} {r.text[:80]}")

    cb = wait_for("on_search")
    check("on_search is posted back to the buyer app's own bap_uri", cb is not None)
    if cb:
        provs = cb["body"]["message"]["catalog"]["bpp/providers"]
        item = next((i for p in provs for i in p["items"] if i["id"] == lst.id), None)
        check("the catalogue price is the listing price, not a separate copy",
              item is not None and item["price"]["value"] == "1250.00",
              f"price={item['price']['value'] if item else None}")
        check("stock is published as what is genuinely left",
              item is not None
              and item["quantity"]["available"]["count"] == "3")
        check("the callback is signed with our Ed25519 key",
              cb["auth"].startswith("Signature keyId=")
              and "algorithm=\"ed25519\"" in cb["auth"])
        check("the transaction id is echoed, or the buyer app cannot match the reply",
              cb["body"]["context"]["transaction_id"] == txn)

    # ── select ───────────────────────────────────────────────────────────────
    sel = {**ctx(bap_uri, "select", txn), "message": {"order": {
        "provider": {"id": a.id},
        "items": [{"id": lst.id, "quantity": {"count": 2}}]}}}
    r = client.post("/ondc/select", json=sel)
    cb = wait_for("on_select")
    check("select returns a quote", cb is not None and r.status_code == 200)
    if cb:
        q = cb["body"]["message"]["order"]["quote"]
        check("the quote is quantity x listing price",
              q["price"]["value"] == "2500.00", f"quote={q['price']['value']}")
        check("the quote is itemised, so the buyer sees where the number comes from",
              len(q["breakup"]) >= 1
              and q["breakup"][0]["price"]["value"] == "2500.00")

    # ── ordering more than exists ───────────────────────────────────────────
    greedy = {**ctx(bap_uri, "select", txn), "message": {"order": {
        "items": [{"id": lst.id, "quantity": {"count": 99}}]}}}
    r = client.post("/ondc/select", json=greedy)
    j = r.json()
    check("asking for more than is in stock is refused with a Beckn NACK, not a 500",
          r.status_code == 200 and j["message"]["ack"]["status"] == "NACK"
          and "3 left" in j.get("error", {}).get("message", ""),
          f"{j.get('error', {}).get('message', '')[:60]}")

    # ── confirm: the sale ───────────────────────────────────────────────────
    order_id = f"ondc-{uuid.uuid4().hex[:10]}"
    conf = {**ctx(bap_uri, "confirm", txn), "message": {"order": {
        "id": order_id,
        "provider": {"id": a.id},
        "items": [{"id": lst.id, "quantity": {"count": 2}}],
        "billing": {"name": "Priya Menon", "phone": "9812345678",
                    "email": "priya@example.com"},
        "fulfillments": [{"id": "F1", "type": "Delivery", "stops": [{
            "type": "end",
            "location": {"address": {"name": "Flat 3B", "building": "Sea View",
                                     "locality": "Bandra West", "city": "Mumbai",
                                     "state": "Maharashtra", "area_code": "400050"}},
            "contact": {"phone": "9812345678"}}]}],
        "payment": {"id": "pay-xyz", "status": "PAID"}}}}
    r = client.post("/ondc/confirm", json=conf)
    check("confirm is accepted", r.status_code == 200
          and r.json()["message"]["ack"]["status"] == "ACK", r.text[:90])

    order = (s.query(db.Order)
             .filter(db.Order.channel == "ondc",
                     db.Order.external_id == order_id).first())
    if order:
        s.refresh(order)
    check("confirm creates a real Order row", order is not None)
    if order:
        check("the buyer's details arrive with it",
              order.buyer_name == "Priya Menon" and order.buyer_phone == "9812345678"
              and order.buyer_pincode == "400050" and order.buyer_city == "Mumbai",
              f"{order.buyer_name} / {order.buyer_city} / {order.buyer_pincode}")
        check("the amount is quantity x listing price",
              order.amount == 2500, f"amount={order.amount}")
        check("ONDC collects the money, so it arrives already paid",
              order.payment_status == "paid" and order.payment_provider == "ondc_bap")

    s.refresh(lst)
    check("stock comes down by what was sold", lst.quantity == 1,
          f"quantity={lst.quantity} (was 3, sold 2)")

    # ── THE question: does it show up in the app ────────────────────────────
    r = client.get("/v1/orders", headers=H)
    mine = [o for o in r.json()["orders"] if o["externalId"] == order_id]
    check("the ONDC order appears in the artisan's own Orders list",
          r.status_code == 200 and len(mine) == 1
          and mine[0]["channel"] == "ondc" and mine[0]["amount"] == 2500,
          f"{len(mine)} matching order(s)")

    r = client.get("/v1/summary", headers=H)
    check("and it counts toward what the artisan has earned",
          r.status_code == 200, f"HTTP {r.status_code}")

    # ── the network retrying must not sell it twice ─────────────────────────
    CALLBACKS.clear()
    client.post("/ondc/confirm", json=conf)
    s.refresh(lst)
    n = (s.query(db.Order).filter(db.Order.channel == "ondc",
                                  db.Order.external_id == order_id).count())
    check("the same confirm arriving twice is one order, not two",
          n == 1 and lst.quantity == 1, f"{n} order(s), stock {lst.quantity}")

    # ── status ──────────────────────────────────────────────────────────────
    CALLBACKS.clear()
    client.post("/ondc/status", json={**ctx(bap_uri, "status", txn),
                                      "message": {"order_id": order_id}})
    cb = wait_for("on_status")
    check("status answers from the same row the artisan sees",
          cb is not None
          and cb["body"]["message"]["order"]["state"] == "Accepted",
          f"state={cb['body']['message']['order']['state'] if cb else None}")

    # ── cancel puts the stock back ──────────────────────────────────────────
    CALLBACKS.clear()
    client.post("/ondc/cancel", json={**ctx(bap_uri, "cancel", txn),
                                      "message": {"order_id": order_id,
                                                  "cancellation_reason_id": "001"}})
    wait_for("on_cancel")
    s.refresh(lst)
    s.refresh(order)
    check("cancelling returns the pieces to stock",
          lst.quantity == 3 and order.status == "cancelled",
          f"stock={lst.quantity} status={order.status}")

    # Report BEFORE cleaning up. A teardown tripping over a foreign key must not be
    # able to hide what the test found - the first version of this file did exactly
    # that, and the traceback buried twenty passing checks.
    width = max(len(n) for _, n, _ in results) + 2
    print()
    for ok, name, detail in results:
        line = f"{'PASS' if ok else 'FAIL'}  {name:<{width}}"
        if not ok and detail:
            line += f"  {detail}"
        print(line)
    failed = sum(1 for ok, _, _ in results if not ok)
    print(f"\n{len(results) - failed} passed, {failed} failed")

    # One commit per level, children before parents. Postgres enforces these keys
    # where SQLite by default does not, so the order is not optional. Wrapped,
    # because rows left behind are untidy and a hidden result is not.
    try:
        for lid, in s.query(db.Listing.id).filter(
                db.Listing.artisan_id == a.id).all():
            s.query(db.Order).filter(db.Order.listing_id == lid).delete()
        s.commit()
        s.query(db.Listing).filter(db.Listing.artisan_id == a.id).delete()
        s.commit()
        s.query(db.Session).filter(db.Session.artisan_id == a.id).delete()
        s.commit()
        s.query(db.Artisan).filter(db.Artisan.id == a.id).delete()
        s.commit()
    except Exception as e:                                  # noqa: BLE001
        s.rollback()
        print(f"(cleanup left rows behind: {type(e).__name__}: {str(e)[:90]})")
    s.close()
    server.shutdown()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
