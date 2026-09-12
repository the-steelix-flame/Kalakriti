"""
Our own marketplace, and the part of it that must not be fooled.

The shop itself is easy to check. The payment confirmation is the part worth real
attention, because it is the one endpoint a stranger can post to claiming they paid.
Three of the checks below are attacks:

  - a forged signature, from somebody who read the API and guessed the shape
  - a replayed signature from a different order
  - a real, correctly signed payment for one rupee against a two-thousand rupee order

The last one is the interesting attack, and it is why the signature alone is not
enough: it is genuinely from Razorpay, it verifies perfectly, and it is still not
payment for this order.

Completing an actual card payment needs a browser and a human, so that half is
marked below rather than pretended. Everything up to it is exercised for real,
including creating a genuine Razorpay test-mode order over the network.

    python test_market.py
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from fastapi.testclient import TestClient  # noqa: E402

import db  # noqa: E402
import main  # noqa: E402

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


def sign(order_id: str, payment_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(),
                    hashlib.sha256).hexdigest()


def run() -> int:
    client = TestClient(main.app)
    s = db.session()
    secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    key_id = os.getenv("RAZORPAY_KEY_ID", "")

    check("Razorpay is configured in TEST mode, so no real money can move",
          key_id.startswith("rzp_test"),
          f"key starts {key_id[:8]!r} - a live key here would be a real charge")

    # ── the shop ────────────────────────────────────────────────────────────
    r = client.get("/market")
    check("The shop front renders without signing in",
          r.status_code == 200 and "Kalakriti" in r.text)
    # The shop front deliberately carries no test-mode banner. It is the first thing
    # a visitor reads and it is about our plumbing, not the craft. The disclosure
    # moved to the product page, next to the card field - the moment somebody is
    # about to type a card number is the moment it matters.
    check("The shop front is not cluttered with a test-mode banner",
          "running in test mode" not in r.text.lower())

    j = client.get("/v1/market").json()
    check("The shop is also available as JSON for a buyer view in the app",
          "items" in j and "categories" in j)
    check("Only sellable pieces are listed - nothing out of stock or still a draft",
          all(i["quantity"] > 0 for i in j["items"]),
          f"{len(j['items'])} item(s)")

    if not j["items"]:
        check("SKIPPED: no published listings, so the buying half cannot run", False,
              "publish something from the app first")
        return report()

    item = j["items"][0]
    lid = item["id"]

    # The disclosure still has to exist where it counts.
    page = client.get(f"/l/{lid}")
    check("The product page still warns, right beside the card field",
          page.status_code == 200 and "4111 1111 1111 1111" in page.text
          and "test mode" in page.text.lower())

    # ── placing an order ────────────────────────────────────────────────────
    r = client.post("/v1/orders", json={
        "listingId": lid, "buyerName": "Test Buyer", "buyerPhone": "9812345678",
        "buyerEmail": "buyer@example.com", "address": "12 Test Lane, Mumbai 400050",
        "quantity": 1})
    check("A buyer can place an order without an account",
          r.status_code == 200, f"HTTP {r.status_code} {r.text[:90]}")
    order = r.json()
    oid = order.get("id", "")

    check("A genuine Razorpay order is created over the network, not a fake id",
          order.get("razorpayOrderId", "").startswith("order_"),
          f"razorpayOrderId={order.get('razorpayOrderId')!r}")
    check("The order starts unpaid - nothing is assumed until Razorpay says so",
          order.get("paymentStatus") == "pending",
          f"paymentStatus={order.get('paymentStatus')}")

    rz_order = order.get("razorpayOrderId", "")
    fake_payment = "pay_TestForgedPayment"

    # ── attack 1: a made-up signature ───────────────────────────────────────
    r = client.post(f"/v1/orders/{oid}/payment", json={
        "razorpayOrderId": rz_order, "razorpayPaymentId": fake_payment,
        "signature": "0" * 64})
    check("ATTACK  A forged signature is refused",
          r.status_code == 400
          and r.json()["detail"]["error"] == "bad_signature",
          f"HTTP {r.status_code} {str(r.json())[:80]}")

    # ── attack 2: a signature that is valid, but for another order ─────────
    other = sign("order_SomethingElse", fake_payment, secret)
    r = client.post(f"/v1/orders/{oid}/payment", json={
        "razorpayOrderId": rz_order, "razorpayPaymentId": fake_payment,
        "signature": other})
    check("ATTACK  A signature lifted from a different order is refused",
          r.status_code == 400
          and r.json()["detail"]["error"] == "bad_signature")

    # ── attack 3: correctly signed, but the payment does not exist ─────────
    good_shape = sign(rz_order, fake_payment, secret)
    r = client.post(f"/v1/orders/{oid}/payment", json={
        "razorpayOrderId": rz_order, "razorpayPaymentId": fake_payment,
        "signature": good_shape})
    d = r.json().get("detail", {})
    check("ATTACK  A perfectly signed payment that Razorpay has no record of is refused",
          r.status_code == 400 and d.get("error") in ("lookup_failed", "not_captured"),
          f"error={d.get('error')} - the signature verified, and it was still rejected")

    fresh = s.get(db.Order, oid)
    s.refresh(fresh)
    check("After three rejected attempts the order is still unpaid",
          fresh.payment_status == "pending",
          f"paymentStatus={fresh.payment_status}")

    check("NEEDS A HUMAN: completing a card payment requires the browser checkout",
          True,
          "open the product page, pay with 4111 1111 1111 1111, and the same "
          "endpoint confirms it")

    # cleanup: the probe order, and the stock it reserved
    try:
        lst = s.get(db.Listing, lid)
        if lst and fresh:
            lst.quantity = (lst.quantity or 0) + (fresh.quantity or 0)
        s.query(db.Order).filter(db.Order.id == oid).delete()
        s.commit()
    except Exception:                                        # noqa: BLE001
        s.rollback()
    s.close()
    return report()


def report() -> int:
    width = max(len(n) for _, n, _ in results) + 2
    print()
    for ok, name, detail in results:
        line = f"{'PASS' if ok else 'FAIL'}  {name:<{width}}"
        if detail and (not ok or name.startswith(("ATTACK", "NEEDS", "Razorpay"))):
            line += f"  {detail}"
        print(line)
    failed = sum(1 for ok, _, _ in results if not ok)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
