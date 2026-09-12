"""
The Provenance Passport: minted, stored, and checkable by a stranger.

What was wrong with the previous version
----------------------------------------
The cryptography was already right. `/v1/passport` signed a set of claims with
Ed25519 and returned them, and the app drew a QR code. But the passport was never
written down: it existed for exactly one HTTP response and then nothing in the system
had ever heard of it. The QR code encoded the bare id string, so a camera app pointed
at a finished product opened nothing at all, and there was no page to open even if it
had encoded a URL.

So the claim "this photograph is the artisan's, and here is every operation applied
to it" was signed and then discarded. A signature nobody can fetch proves nothing.

What this module does instead
-----------------------------
`mint` writes the passport onto the listing - the id, the signature, the public key,
and the exact bytes that were signed. `page` renders a public, unauthenticated HTML
page at /passport/{id} showing the raw photograph beside the enhanced one, the
itemised operation log, and the result of checking the signature. `pubkey_doc`
publishes the key, the signature and the payload openly, because the entire argument
for using an open standard rather than a private database is that somebody else can
repeat the check without asking us.

Why the signed bytes are stored verbatim
----------------------------------------
Ed25519 verifies bytes, not meaning. Re-serialising the claims from columns would
reorder a key or move a space, and a signature that is genuinely valid would be
reported as forged. `passport_payload` is therefore the canonical JSON string exactly
as it was signed, and every field shown on the page is parsed back out of it - so the
page cannot show one thing while the signature covers another.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
from datetime import datetime, timezone
from string import Template
from typing import Any

import db

KEY_PATH = os.getenv("PASSPORT_KEY_PATH", "passport_key.pem")
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")


def signing_key():
    """The Ed25519 private key, generated once and kept on disk.

    On a host with an ephemeral filesystem this file is regenerated after a redeploy,
    which would leave older passports signed by a key nobody publishes any more. That
    is why the public key is stored per passport rather than looked up globally: an
    old passport still verifies against the key that actually signed it.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    key = ed25519.Ed25519PrivateKey.generate()
    with open(KEY_PATH, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()))
    return key


def verify(payload: str, signature: str, public_key: str) -> tuple[bool, str]:
    """
    Check a passport the way an outsider's tool would: public key, signature, bytes.

    Deliberately never touches the private key. A verifier that needs the secret
    proves nothing to anyone who does not hold it.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    if not (payload and signature and public_key):
        return False, "this passport is missing its payload, signature or public key"
    try:
        pub = base64.b64decode(public_key.split(":", 1)[-1])
        sig = base64.b64decode(signature.split(":", 1)[-1])
        ed25519.Ed25519PublicKey.from_public_bytes(pub).verify(sig, payload.encode())
        return True, "the signature matches these claims and the published public key"
    except InvalidSignature:
        return False, "the signature does not match these claims - they were altered"
    except Exception as exc:                       # malformed base64, wrong key length
        return False, f"the signature could not be checked: {exc}"


def mint(s, *, raw_hash: str, ops: list[str], artisan_id: str,
         listing_id: str | None = None, gi_tag: str | None = None,
         geo: str | None = None, commit: bool = True) -> dict[str, Any]:
    """
    Sign the claims and, when a listing is named, store them on it.

    `stored: False` in the result is not a silent failure - it means the caller minted
    a passport without a listing to attach it to, so /passport/{id} will not find it.

    `commit=False` is for callers that already own the transaction, like the image
    pipeline, which must not have a passport committed underneath a half-written
    listing.
    """
    from cryptography.hazmat.primitives import serialization

    key = signing_key()
    claims = {"artisanId": artisan_id, "giTag": gi_tag, "geo": geo,
              "rawHash": raw_hash, "enhanceOps": ops,
              "capturedAt": datetime.now(timezone.utc).isoformat()}
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    sig = key.sign(payload)
    pub = key.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                        format=serialization.PublicFormat.Raw)
    pid = (f"KK-BNS-{datetime.now().year}-"
           f"{hashlib.sha256(payload).hexdigest()[:10].upper()}")
    signature = "ed25519:" + base64.b64encode(sig).decode()
    public_key = "ed25519:" + base64.b64encode(pub).decode()

    stored = False
    if listing_id:
        lst = s.get(db.Listing, listing_id)
        if lst:
            lst.passport_id = pid
            lst.passport_payload = payload.decode()
            lst.passport_signature = signature
            lst.passport_public_key = public_key
            lst.passport_at = datetime.now(timezone.utc)
            db.log_event(s, "listing", lst.id, "passport", "", pid,
                         "provenance passport minted and stored")
            if commit:
                s.commit()
            stored = True

    return {**claims, "id": pid, "signature": signature, "publicKey": public_key,
            "listingId": listing_id, "stored": stored,
            # The QR code encodes this, not the bare id. A camera app can open a URL.
            # It can do nothing whatsoever with "KK-BNS-2026-4F2A19C0D3".
            "verifyUrl": verify_url(pid)}


def for_listing(s, listing, *, commit: bool = False) -> dict[str, Any]:
    """
    Mint the passport for a listing the pipeline has just filled in.

    The maker, the GI tag and the place come off the artisan's own row rather than
    from a constant. The app used to send `artisanId: 'ART-UP-VNS-4471'` and a
    Varanasi GI tag for every photograph taken anywhere in India, which put three
    false statements inside a signature whose entire purpose is that its statements
    are true. Where the row does not say, the claim is left null - an unsigned blank
    is honest and an invented origin is not.
    """
    maker = s.get(db.Artisan, listing.artisan_id) if listing.artisan_id else None
    return mint(
        s,
        raw_hash=listing.raw_hash,
        ops=list(listing.enhance_ops or []),
        artisan_id=listing.artisan_id or "unclaimed-draft",
        listing_id=listing.id,
        gi_tag=(maker.gi_tag or None) if maker else None,
        geo=(maker.cluster or None) if maker else None,
        commit=commit,
    )


def verify_url(pid: str) -> str:
    return f"{PUBLIC_BASE.rstrip('/')}/passport/{pid}"


def find(s, pid: str):
    """The listing carrying this passport id, or None."""
    return (s.query(db.Listing)
            .filter(db.Listing.passport_id == pid)
            .order_by(db.Listing.updated_at.desc())
            .first())


def pubkey_doc(s, pid: str) -> dict[str, Any] | None:
    """Everything a third-party tool needs to verify this passport without us."""
    lst = find(s, pid)
    if not lst:
        return None
    ok, why = verify(lst.passport_payload, lst.passport_signature,
                     lst.passport_public_key)
    return {
        "passportId": pid,
        "algorithm": "Ed25519 (RFC 8032), over the payload bytes exactly as given",
        "publicKey": lst.passport_public_key,
        "publicKeyBase64": lst.passport_public_key.split(":", 1)[-1],
        "signature": lst.passport_signature,
        "signatureBase64": lst.passport_signature.split(":", 1)[-1],
        "payload": lst.passport_payload,
        "claims": json.loads(lst.passport_payload or "{}"),
        "rawImageUrl": lst.raw_url or "",
        "enhancedImageUrl": lst.image_url or "",
        # Reported, not asserted: our own answer, which a caller is free to ignore in
        # favour of running the check below themselves.
        "selfCheck": {"valid": ok, "why": why},
        "howToVerify": [
            "Fetch this document.",
            "base64-decode publicKeyBase64 into a 32-byte Ed25519 public key.",
            "base64-decode signatureBase64 into a 64-byte signature.",
            "Verify that signature over payload.encode('utf-8'), unmodified.",
            "Hash the image at rawImageUrl with SHA-256 and compare it to "
            "claims.rawHash, which proves the photograph is the one that was signed.",
        ],
    }


_PAGE = Template("""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Provenance Passport &middot; $title</title>
<style>
 body{margin:0;font:16px/1.6 'Segoe UI',system-ui,sans-serif;background:#F6F4FB;color:#171331}
 .wrap{max-width:880px;margin:0 auto;padding:24px}
 h1{font-size:24px;margin:6px 0 2px}
 .sub{color:#7C7596;margin:0 0 18px;font-size:14px}
 .verdict{border-radius:18px;padding:18px 20px;margin:0 0 22px;color:#fff}
 .valid{background:#0B7A54} .invalid{background:#B3261E}
 .verdict b{font-size:19px} .verdict p{margin:6px 0 0;opacity:.93;font-size:14.5px}
 .pair{display:flex;gap:16px;flex-wrap:wrap}
 figure{flex:1 1 280px;margin:0}
 figure img{width:100%;border-radius:16px;background:#fff;display:block}
 figcaption{font-size:13.5px;color:#7C7596;padding-top:8px;text-align:center}
 .none{background:#fff;border:1px dashed #CFC7E6;border-radius:16px;padding:44px 18px;
   text-align:center;color:#7C7596;font-size:14px}
 .none span{font-size:12.5px;opacity:.85}
 .card{background:#fff;border:1px solid #E6E1F2;border-radius:18px;padding:18px 20px;
   margin-top:20px}
 .card h2{font-size:13px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.07em;
   color:#2E2A6B}
 ul{margin:0;padding-left:20px} li{margin:5px 0}
 code{font:13px/1.5 ui-monospace,Consolas,monospace;background:#F2EFFA;padding:2px 6px;
   border-radius:6px;word-break:break-all}
 table{width:100%;border-collapse:collapse}
 td{padding:8px 0;border-bottom:1px solid #EFECF8;vertical-align:top;font-size:14.5px}
 td:first-child{color:#7C7596;width:34%}
 a{color:#A93C12}
</style></head><body><div class="wrap">
<h1>$title</h1>
<p class="sub">Provenance Passport $pid &middot; issued $minted</p>

<div class="verdict $verdict_class">
  <b>$verdict</b>
  <p>$why</p>
</div>

<div class="pair">
  $rawfig
  <figure><img src="$image_url" alt="The image buyers are shown">
    <figcaption>As shown to buyers</figcaption></figure>
</div>

<div class="card">
  <h2>Every operation applied, in order</h2>
  <ul>$ops</ul>
</div>

<div class="card">
  <h2>The signed claims</h2>
  <table>
    <tr><td>Maker</td><td>$artisan</td></tr>
    <tr><td>GI tag</td><td>$gi</td></tr>
    <tr><td>Place</td><td>$geo</td></tr>
    <tr><td>Photographed</td><td>$captured</td></tr>
    <tr><td>Hash of the original file</td><td><code>$raw_hash</code></td></tr>
    <tr><td>Signature</td><td><code>$signature</code></td></tr>
    <tr><td>Public key</td><td><code>$public_key</code></td></tr>
  </table>
</div>

<div class="card">
  <h2>Check this yourself</h2>
  <p style="margin:0;font-size:14.5px">The verdict above was computed on this server,
  so it is worth exactly as much as your trust in this server. The public key, the
  signature and the precise bytes that were signed are published openly at
  <a href="/passport/$pid/pubkey">/passport/$pid/pubkey</a>, so any Ed25519 tool can
  repeat the check without us.</p>
</div>

$shoplink
</div></body></html>""")


def page(s, pid: str) -> str | None:
    """
    The page a buyer reaches by pointing a plain camera app at the QR code.

    No login, no app, and no JavaScript needed to read it - a passport that only opens
    inside Kalakriti would be evidence only to people who already trust Kalakriti.
    """
    lst = find(s, pid)
    if not lst:
        return None
    ok, why = verify(lst.passport_payload, lst.passport_signature,
                     lst.passport_public_key)
    claims = json.loads(lst.passport_payload or "{}")
    e = html.escape
    ops = claims.get("enhanceOps") or lst.enhance_ops or []
    raw = lst.raw_url or ""
    rawfig = (
        f'<figure><img src="{e(raw)}" alt="The photograph as it was taken">'
        f'<figcaption>As the artisan photographed it</figcaption></figure>'
        if raw else
        '<figure><div class="none">The original file was not kept for this listing.'
        '<br><span>Only the hash below records it. Listings photographed after this '
        'page existed keep both images.</span></div>'
        '<figcaption>As the artisan photographed it</figcaption></figure>')
    return _PAGE.substitute(
        title=e(lst.title_en or lst.title_hi or "Handmade product"),
        pid=e(pid),
        minted=lst.passport_at.strftime("%d %B %Y") if lst.passport_at else "-",
        verdict_class="valid" if ok else "invalid",
        verdict="Signature valid." if ok else "Signature could NOT be verified.",
        why=e(why),
        rawfig=rawfig,
        image_url=e(lst.image_url or ""),
        ops="".join(f"<li><code>{e(str(o))}</code></li>" for o in ops)
            or "<li>No operation was recorded.</li>",
        artisan=e(str(claims.get("artisanId") or "-")),
        gi=e(str(claims.get("giTag") or "-")),
        geo=e(str(claims.get("geo") or "-")),
        captured=e(str(claims.get("capturedAt") or "-")),
        raw_hash=e(str(claims.get("rawHash") or "-")),
        signature=e(lst.passport_signature or "-"),
        public_key=e(lst.passport_public_key or "-"),
        shoplink=(f'<p style="margin:22px 0 0"><a href="/l/{e(lst.id)}">'
                  f'See this piece in the shop &#8594;</a></p>'
                  if lst.status in ("published", "active") else ""),
    )
