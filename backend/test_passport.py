"""
Phase 6: a Provenance Passport a stranger can check.

The signing was already correct before this phase; what was missing was everything
that turns a signature into evidence. So these checks are about reachability and
tamper-detection rather than about Ed25519 itself: that the passport is still there
after the response that minted it, that a page opens with no token at all, that the
public key is published, and - the one that matters - that editing a claim by a single
character makes the page say so instead of saying "valid".

    python test_passport.py
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta

from fastapi.testclient import TestClient

import auth
import db
import main
import passport

results: list[tuple[bool, str, str]] = []
PHONE = "9000000651"


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


def seed(s):
    """One verified artisan with a GI tag, and one listing photographed by them."""
    a = s.query(db.Artisan).filter(db.Artisan.phone == PHONE).first()
    if a is None:
        a = db.Artisan(id=db.nid("art"), phone=PHONE, phone_verified=1)
        s.add(a)
    a.phone_verified = 1
    a.full_name = "Passport test weaver"
    a.gi_tag = "GI-99 Banaras Brocades & Sarees"
    a.cluster = "Varanasi"
    s.flush()
    sess = db.Session(id=db.nid("ses"), artisan_id=a.id,
                      expires_at=db.now() + timedelta(days=1))
    s.add(sess)

    lst = db.Listing(id=db.nid("lst"), artisan_id=a.id, status="active",
                     title_en="Passport test dupatta", price=2400, quantity=1,
                     image_url="https://example.invalid/final.jpg",
                     raw_url="https://example.invalid/raw.jpg",
                     raw_hash="sha256:" + "ab" * 32,
                     enhance_ops=["ocr:rapidocr 0 text boxes",
                                  "matte:u2net", "bg:studio gradient"])
    s.add(lst)
    s.commit()
    return a.id, lst.id, auth.issue_token(sess)


def wipe(s) -> None:
    a = s.query(db.Artisan).filter(db.Artisan.phone == PHONE).first()
    if a is None:
        return
    for lst in s.query(db.Listing).filter(db.Listing.artisan_id == a.id).all():
        s.query(db.Event).filter(db.Event.subject_id == lst.id).delete()
        s.delete(lst)
    s.query(db.Session).filter(db.Session.artisan_id == a.id).delete()
    s.delete(a)
    s.commit()


def run() -> int:
    s = db.session()
    wipe(s)
    art_id, lid, token = seed(s)
    c = TestClient(main.app)

    # -- minting, and surviving the response that minted it -------------------
    r = c.post("/v1/passport", json={
        "rawHash": "sha256:" + "ab" * 32,
        "ops": ["ocr:rapidocr 0 text boxes", "matte:u2net", "bg:studio gradient"],
        "artisanId": art_id, "listingId": lid})
    minted = r.json() if r.status_code == 200 else {}
    pid = minted.get("id", "")
    check("mint returns a passport", r.status_code == 200 and bool(pid),
          f"HTTP {r.status_code}")
    check("mint reports it was stored", bool(minted.get("stored")))
    check("mint returns a URL, not a bare id",
          str(minted.get("verifyUrl", "")).endswith(f"/passport/{pid}"),
          str(minted.get("verifyUrl", "")))

    s.expire_all()
    lst = s.get(db.Listing, lid)
    check("the passport is on the listing afterwards",
          lst.passport_id == pid and bool(lst.passport_payload),
          f"stored id {lst.passport_id!r}")

    # -- the page a buyer reaches with no app and no login --------------------
    page = c.get(f"/passport/{pid}")
    body = page.text
    check("the page opens unauthenticated", page.status_code == 200,
          f"HTTP {page.status_code}")
    check("it says the signature is valid", "Signature valid." in body)
    check("it shows both photographs",
          "https://example.invalid/raw.jpg" in body
          and "https://example.invalid/final.jpg" in body)
    check("it itemises every operation",
          all(op in body for op in ("ocr:rapidocr 0 text boxes", "matte:u2net",
                                    "bg:studio gradient")))
    check("an unknown passport is a 404",
          c.get("/passport/KK-BNS-1999-DEADBEEF00").status_code == 404)

    # -- the public key, published openly -------------------------------------
    pk = c.get(f"/passport/{pid}/pubkey")
    doc = pk.json() if pk.status_code == 200 else {}
    check("the public key is published", pk.status_code == 200
          and doc.get("publicKeyBase64", "") != "")
    check("the signed bytes are published verbatim",
          doc.get("payload") == lst.passport_payload)

    # -- verification is real: it has to fail when a claim is edited ----------
    ok, _ = passport.verify(lst.passport_payload, lst.passport_signature,
                            lst.passport_public_key)
    check("an untouched passport verifies", ok)

    tampered = json.loads(lst.passport_payload)
    tampered["giTag"] = "GI-01 Kanchipuram Silk"
    bad = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    ok_bad, why_bad = passport.verify(bad, lst.passport_signature,
                                      lst.passport_public_key)
    check("a passport with an edited GI tag does NOT verify", not ok_bad, why_bad)

    # And the page has to report that, rather than quietly rendering the new claim.
    lst.passport_payload = bad
    s.commit()
    page2 = c.get(f"/passport/{pid}")
    check("the page reports the tamper rather than the claim",
          "Signature could NOT be verified." in page2.text)

    # A passport signed by a different key must not verify either - this is the
    # check that would catch a forged page pointing at our own public key.
    other = passport.mint(s, raw_hash="sha256:" + "cd" * 32, ops=[],
                          artisan_id=art_id, listing_id=None)
    ok_x, _ = passport.verify(lst.passport_payload, other["signature"],
                              lst.passport_public_key)
    check("another passport's signature does NOT verify against these claims",
          not ok_x)

    width = max(len(n) for _, n, _ in results)
    print()
    for ok_, name, detail in results:
        line = f"{'PASS' if ok_ else 'FAIL'}  {name:<{width}}"
        if detail and not ok_:
            line += f"  {detail}"
        print(line)
    failed = sum(1 for ok_, _, _ in results if not ok_)
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
