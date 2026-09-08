"""
Marketplace channel adapters.

Honesty contract for this module
--------------------------------
No adapter ever fabricates a listing URL, an external id, or a "published" status.
An adapter returns one of:

    published        - a real remote call succeeded; url/external_id come from the response
    processing       - accepted by the platform, awaiting their approval (real ack)
    failed           - a real call was made and the platform rejected it; `error` is theirs
    not_configured   - required credentials are absent, so no call was attempted

`not_configured` is the important one. Amazon SP-API, GeM, ONDC and Flipkart all
require an onboarded seller account, signed credentials and, for ONDC, registration
as a network participant with a published Ed25519 key. Those cannot be conjured, so
rather than inventing a green tick the adapter reports exactly which environment
variables are missing. The integration code itself is real and executes the moment
the credentials exist.

The `storefront` channel is fully real today and needs no third party: it writes the
listing to our own database and serves it at a genuine, openable public URL from this
same server, with a working Buy button that creates a real order row.
"""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import requests

PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
TIMEOUT = 30


def _missing(*names: str) -> list[str]:
    return [n for n in names if not os.getenv(n)]


def _result(status: str, **kw) -> dict[str, Any]:
    out = {"status": status, "external_id": "", "url": "", "error": "",
           "request": {}, "response": {}}
    out.update(kw)
    return out


# ------------------------------------------------------------- 1. storefront

def storefront(listing: dict) -> dict[str, Any]:
    """
    Our own public product page. Real URL, real page, real Buy button.

    This is not a stand-in for a marketplace - it is the artisan's own storefront,
    which is the one channel that works without anyone else's approval.
    """
    url = f"{PUBLIC_BASE}/l/{listing['id']}"
    return _result("published", external_id=listing["id"], url=url,
                   response={"served_by": "kalakriti-storefront"})


# ------------------------------------------------------------------ 2. ONDC

def ondc(listing: dict) -> dict[str, Any]:
    """
    ONDC / Beckn `on_search` catalogue publication.

    Requires network-participant registration: a subscriber id, a signing key pair
    registered on the ONDC registry, and a gateway URL. The request below is a real
    Beckn catalogue payload signed per the ONDC auth spec.
    """
    miss = _missing("ONDC_SUBSCRIBER_ID", "ONDC_SIGNING_PRIVATE_KEY", "ONDC_GATEWAY_URL")
    if miss:
        return _result("not_configured",
                       error="Missing " + ", ".join(miss) +
                             ". Register as an ONDC network participant to enable.")

    sub = os.getenv("ONDC_SUBSCRIBER_ID")
    gateway = os.getenv("ONDC_GATEWAY_URL", "").rstrip("/")
    payload = {
        "context": {
            "domain": "ONDC:RET10", "action": "on_search", "version": "2.0.2",
            "bpp_id": sub, "bpp_uri": PUBLIC_BASE,
            "transaction_id": listing["id"], "message_id": listing["id"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        },
        "message": {"catalog": {
            "bpp/descriptor": {"name": os.getenv("ONDC_STORE_NAME", "Kalakriti")},
            "bpp/providers": [{
                "id": listing.get("artisanId") or "artisan",
                "items": [{
                    "id": listing["id"],
                    "descriptor": {
                        "name": listing.get("titleEn") or listing.get("titleHi"),
                        "long_desc": listing.get("descEn", ""),
                        "images": [listing.get("imageUrl", "")],
                    },
                    "price": {"currency": "INR", "value": str(listing.get("price", 0))},
                    "category_id": listing.get("category", ""),
                    "@ondc/org/statutory_reqs_packaged_commodities": {
                        "hsn_code": listing.get("hsn", ""),
                    },
                }],
            }],
        }},
    }
    try:
        sig = _ondc_signature(json.dumps(payload, separators=(",", ":")))
        r = requests.post(f"{gateway}/on_search", json=payload, timeout=TIMEOUT,
                          headers={"Authorization": sig, "Content-Type": "application/json"})
        body = _safe_json(r)
        if r.status_code < 300 and str(body.get("message", {}).get("ack", {})
                                       .get("status", "")).upper() == "ACK":
            return _result("processing", external_id=listing["id"],
                           url=f"{PUBLIC_BASE}/l/{listing['id']}", response=body,
                           error="ONDC ACK received; buyer apps index asynchronously.")
        return _result("failed", error=f"HTTP {r.status_code}: {str(body)[:300]}",
                       response=body)
    except Exception as e:
        return _result("failed", error=f"{type(e).__name__}: {e}")


def _ondc_signature(body: str) -> str:
    """ONDC auth header: BLAKE-512 digest signed with the subscriber's Ed25519 key."""
    import hashlib

    from cryptography.hazmat.primitives.asymmetric import ed25519

    key_b64 = os.getenv("ONDC_SIGNING_PRIVATE_KEY", "")
    sub = os.getenv("ONDC_SUBSCRIBER_ID", "")
    ukid = os.getenv("ONDC_UNIQUE_KEY_ID", "1")
    created = int(time.time())
    expires = created + 3600

    digest = base64.b64encode(hashlib.blake2b(body.encode(), digest_size=64).digest()).decode()
    signing_string = (f"(created): {created}\n(expires): {expires}\n"
                      f"digest: BLAKE-512={digest}")
    key = ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_b64)[:32])
    sig = base64.b64encode(key.sign(signing_string.encode())).decode()
    return (f'Signature keyId="{sub}|{ukid}|ed25519",algorithm="ed25519",'
            f'created="{created}",expires="{expires}",'
            f'headers="(created) (expires) digest",signature="{sig}"')


# ----------------------------------------------------------------- 3. Amazon

def amazon(listing: dict) -> dict[str, Any]:
    """
    Amazon SP-API `putListingsItem`.

    Requires a Professional selling account, an LWA application, and a refresh token.
    """
    miss = _missing("AMZN_LWA_CLIENT_ID", "AMZN_LWA_CLIENT_SECRET",
                    "AMZN_REFRESH_TOKEN", "AMZN_SELLER_ID")
    if miss:
        return _result("not_configured",
                       error="Missing " + ", ".join(miss) +
                             ". Needs an Amazon Professional seller account + LWA app.")
    try:
        tok = requests.post("https://api.amazon.com/auth/o2/token", timeout=TIMEOUT, data={
            "grant_type": "refresh_token",
            "refresh_token": os.getenv("AMZN_REFRESH_TOKEN"),
            "client_id": os.getenv("AMZN_LWA_CLIENT_ID"),
            "client_secret": os.getenv("AMZN_LWA_CLIENT_SECRET"),
        })
        if tok.status_code != 200:
            return _result("failed", error=f"LWA token: HTTP {tok.status_code} "
                                           f"{tok.text[:200]}")
        access = tok.json()["access_token"]

        seller = os.getenv("AMZN_SELLER_ID")
        marketplace = os.getenv("AMZN_MARKETPLACE_ID", "A21TJRUUN4KGV")  # amazon.in
        sku = listing["id"]
        endpoint = os.getenv("AMZN_SP_ENDPOINT", "https://sellingpartnerapi-eu.amazon.com")
        body = {
            "productType": os.getenv("AMZN_PRODUCT_TYPE", "PRODUCT"),
            "requirements": "LISTING",
            "attributes": {
                "item_name": [{"value": listing.get("titleEn", ""),
                               "marketplace_id": marketplace}],
                "product_description": [{"value": listing.get("descEn", ""),
                                         "marketplace_id": marketplace}],
                "purchasable_offer": [{"currency": "INR", "marketplace_id": marketplace,
                                       "our_price": [{"schedule": [
                                           {"value_with_tax": listing.get("price", 0)}]}]}],
            },
        }
        r = requests.put(
            f"{endpoint}/listings/2021-08-01/items/{seller}/{sku}",
            params={"marketplaceIds": marketplace},
            headers={"x-amz-access-token": access, "Content-Type": "application/json"},
            json=body, timeout=TIMEOUT,
        )
        rb = _safe_json(r)
        if r.status_code < 300 and rb.get("status") in ("ACCEPTED", "VALID"):
            asin = rb.get("asin", "")
            return _result("processing", external_id=sku,
                           url=f"https://www.amazon.in/dp/{asin}" if asin else "",
                           response=rb, error="Submitted; Amazon publishes asynchronously.")
        return _result("failed", error=f"HTTP {r.status_code}: {str(rb)[:300]}", response=rb)
    except Exception as e:
        return _result("failed", error=f"{type(e).__name__}: {e}")


# -------------------------------------------------------------------- 4. GeM

def gem(listing: dict) -> dict[str, Any]:
    miss = _missing("GEM_API_KEY", "GEM_SELLER_ID", "GEM_API_BASE")
    if miss:
        return _result("not_configured",
                       error="Missing " + ", ".join(miss) +
                             ". GeM issues API access only to registered sellers.")
    try:
        r = requests.post(
            os.getenv("GEM_API_BASE", "").rstrip("/") + "/catalog/v1/products",
            headers={"Authorization": f"Bearer {os.getenv('GEM_API_KEY')}",
                     "Content-Type": "application/json"},
            json={
                "sellerId": os.getenv("GEM_SELLER_ID"),
                "productName": listing.get("titleEn", ""),
                "description": listing.get("descEn", ""),
                "hsnCode": listing.get("hsn", ""),
                "price": listing.get("price", 0),
                "images": [listing.get("imageUrl", "")],
            }, timeout=TIMEOUT)
        rb = _safe_json(r)
        if r.status_code < 300:
            pid = str(rb.get("productId") or rb.get("id") or "")
            return _result("processing", external_id=pid,
                           url=rb.get("url", ""), response=rb,
                           error="Submitted; GeM requires category-admin approval.")
        return _result("failed", error=f"HTTP {r.status_code}: {str(rb)[:300]}", response=rb)
    except Exception as e:
        return _result("failed", error=f"{type(e).__name__}: {e}")


# --------------------------------------------------------------- 5. Shopify

def shopify(listing: dict) -> dict[str, Any]:
    """
    Shopify Admin API. Included because it is the one third-party storefront a team
    can genuinely enable in minutes: a free development store issues an Admin API
    access token immediately, with no onboarding queue.
    """
    miss = _missing("SHOPIFY_STORE", "SHOPIFY_ACCESS_TOKEN")
    if miss:
        return _result("not_configured",
                       error="Missing " + ", ".join(miss) +
                             ". Create a free Shopify dev store and a custom app token.")
    store = os.getenv("SHOPIFY_STORE", "").replace("https://", "").strip("/")
    ver = os.getenv("SHOPIFY_API_VERSION", "2025-01")
    try:
        r = requests.post(
            f"https://{store}/admin/api/{ver}/products.json",
            headers={"X-Shopify-Access-Token": os.getenv("SHOPIFY_ACCESS_TOKEN"),
                     "Content-Type": "application/json"},
            json={"product": {
                "title": listing.get("titleEn") or listing.get("titleHi"),
                "body_html": listing.get("descEn", ""),
                "product_type": (listing.get("category", "").split(">")[-1].strip()
                                 or "Handicraft"),
                "tags": ",".join(listing.get("tags", [])),
                "status": "active",
                "variants": [{"price": str(listing.get("price", 0)),
                              "sku": listing["id"],
                              "inventory_quantity": listing.get("quantity", 1)}],
                "images": ([{"src": listing["imageUrl"]}]
                           if str(listing.get("imageUrl", "")).startswith("http") else []),
            }}, timeout=TIMEOUT)
        rb = _safe_json(r)
        if r.status_code < 300 and rb.get("product"):
            p = rb["product"]
            handle = p.get("handle", "")
            return _result("published", external_id=str(p.get("id", "")),
                           url=f"https://{store}/products/{handle}", response=p)
        return _result("failed", error=f"HTTP {r.status_code}: {str(rb)[:300]}", response=rb)
    except Exception as e:
        return _result("failed", error=f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------ registry

def _safe_json(r) -> dict:
    try:
        return r.json()
    except Exception:
        return {"raw": r.text[:600]}


ADAPTERS = {
    "storefront": storefront,
    "ondc": ondc,
    "gem": gem,
    "amazon": amazon,
    "shopify": shopify,
}

META = {
    "storefront": {"name": "My Storefront", "note": "आपका अपना पेज · तुरंत लाइव",
                   "requires": []},
    "ondc": {"name": "ONDC", "note": "Beckn खुला नेटवर्क · शून्य कमीशन",
             "requires": ["ONDC_SUBSCRIBER_ID", "ONDC_SIGNING_PRIVATE_KEY",
                          "ONDC_GATEWAY_URL"]},
    "gem": {"name": "GeM", "note": "सरकारी ई-मार्केटप्लेस",
            "requires": ["GEM_API_KEY", "GEM_SELLER_ID", "GEM_API_BASE"]},
    "amazon": {"name": "Amazon Karigar", "note": "SP-API",
               "requires": ["AMZN_LWA_CLIENT_ID", "AMZN_LWA_CLIENT_SECRET",
                            "AMZN_REFRESH_TOKEN", "AMZN_SELLER_ID"]},
    "shopify": {"name": "Shopify", "note": "Admin API",
                "requires": ["SHOPIFY_STORE", "SHOPIFY_ACCESS_TOKEN"]},
}


def availability() -> list[dict]:
    """What the app can actually publish to right now, and what each one still needs."""
    out = []
    for cid, meta in META.items():
        miss = _missing(*meta["requires"])
        out.append({"id": cid, "name": meta["name"], "note": meta["note"],
                    "configured": not miss, "missing": miss})
    return out


def publish(channel: str, listing: dict) -> dict[str, Any]:
    fn = ADAPTERS.get(channel)
    if not fn:
        return _result("failed", error=f"unknown channel {channel}")
    return fn(listing)
