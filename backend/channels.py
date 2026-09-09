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


# ══════════════════════════════════════════════════════════════ statistics
#
# What each marketplace will actually tell us about a live listing.
#
# Deliberately conservative. Every metric below is either fetched from the platform's
# own API or reported as unavailable with the reason. Nothing is estimated,
# extrapolated, or filled in with a plausible-looking number - a made-up view count is
# worse than none at all, because the artisan would price against it.
#
# The honest state of each platform:
#
#   storefront - we serve the page, so we count views ourselves (db.ListingView) and
#                we hold the orders. Every metric is available except watchers, which
#                the page has no feature for.
#   amazon     - SP-API exposes no per-listing view or watcher metric. Traffic lives
#                in Brand Analytics / Business Reports, which needs Brand Registry and
#                a separate asynchronous report request. Orders and inventory are real
#                calls and are made here.
#   shopify    - Admin GraphQL gives inventory and orders. Views need the Analytics
#                API under a read_analytics scope custom apps are rarely granted.
#   ondc       - a transaction network, not an analytics platform. Beckn has no view
#                or watcher concept at all. Order state arrives by callback.
#   gem        - seller APIs cover catalogue, bids and orders; no traffic analytics.
#
# `available: False` with a `why` is a first-class answer here, not a failure.

METRICS = ["views", "watchers", "sold", "inventory", "revenue", "orders"]


def _metric(value=None, source=""):
    return {"value": value, "available": True, "why": "", "source": source}


def _none(why: str, config: str = ""):
    """
    An absent metric.

    `why` is written for the artisan and says what she can understand: this platform
    does not share this number. `config` carries the exact environment variables an
    operator has to set, kept separate so a seller is not shown AMZN_LWA_CLIENT_ID as
    though it were an explanation. The brief asks for both, and they are not the same
    audience.
    """
    return {"value": None, "available": False, "why": why, "config": config,
            "source": ""}


def _all_unconfigured(name: str, missing: list[str]) -> dict[str, Any]:
    why = (f"{name} is not connected to this app yet, so it cannot report anything. "
           f"Nothing is wrong with your product.")
    config = ("Set " + ", ".join(missing)) if missing else ""
    return {k: _none(why, config) for k in METRICS}


def _amazon_token() -> str:
    """LWA access token. Raises on failure so the caller reports the real reason."""
    r = requests.post("https://api.amazon.com/auth/o2/token", timeout=TIMEOUT, data={
        "grant_type": "refresh_token",
        "refresh_token": os.getenv("AMZN_REFRESH_TOKEN"),
        "client_id": os.getenv("AMZN_LWA_CLIENT_ID"),
        "client_secret": os.getenv("AMZN_LWA_CLIENT_SECRET"),
    })
    if r.status_code != 200:
        raise RuntimeError(f"LWA token HTTP {r.status_code}: {r.text[:160]}")
    return r.json()["access_token"]


def stats_storefront(pub, ctx) -> dict[str, Any]:
    """Our own page: these are counted, not inferred."""
    return {
        "views": _metric(ctx.get("views", 0), "counted on /l/{id}, deduped per hour"),
        "watchers": _none("The storefront page has no follow or watchlist feature, so "
                          "there is nothing to count. It is not a missing integration."),
        "sold": _metric(ctx.get("sold", 0), "orders table"),
        "inventory": _metric(ctx.get("inventory"), "listing quantity"),
        "revenue": _metric(ctx.get("revenue", 0), "paid orders"),
        "orders": _metric(ctx.get("orders", 0), "orders table"),
    }


def stats_amazon(pub, ctx) -> dict[str, Any]:
    miss = _missing("AMZN_LWA_CLIENT_ID", "AMZN_LWA_CLIENT_SECRET",
                    "AMZN_REFRESH_TOKEN", "AMZN_SELLER_ID")
    if miss:
        return _all_unconfigured("Amazon", miss)

    out = {
        "views": _none("SP-API exposes no per-listing view count. Traffic sits behind "
                       "the Business Reports / Brand Analytics API, which requires "
                       "Brand Registry and an asynchronous report request."),
        "watchers": _none("Amazon has no watcher concept for a seller listing."),
    }
    sku = pub.get("externalId") or pub.get("external_id") or ""
    market = os.getenv("AMZN_MARKETPLACE_ID", "A21TJRUUN4KGV")
    base = os.getenv("AMZN_SP_ENDPOINT", "https://sellingpartnerapi-eu.amazon.com")
    try:
        h = {"x-amz-access-token": _amazon_token(), "Accept": "application/json"}
        inv = requests.get(f"{base}/fba/inventory/v1/summaries",
                           params={"granularityType": "Marketplace",
                                   "granularityId": market, "marketplaceIds": market,
                                   "sellerSkus": sku},
                           headers=h, timeout=TIMEOUT)
        if inv.status_code < 300:
            rows = (_safe_json(inv).get("payload", {}).get("inventorySummaries") or [])
            out["inventory"] = _metric(
                sum(int(r.get("totalQuantity") or 0) for r in rows),
                "SP-API FBA Inventory")
        else:
            out["inventory"] = _none(
                f"FBA Inventory API returned HTTP {inv.status_code}. This is expected "
                f"for a merchant-fulfilled listing, which reports quantity through "
                f"the Listings Items API instead.")

        since = os.getenv("AMZN_ORDERS_SINCE", "2024-01-01T00:00:00Z")
        od = requests.get(f"{base}/orders/v0/orders",
                          params={"MarketplaceIds": market, "CreatedAfter": since},
                          headers=h, timeout=TIMEOUT)
        if od.status_code < 300:
            items = (_safe_json(od).get("payload", {}).get("Orders") or [])
            out["orders"] = _metric(len(items), "SP-API Orders v0")
            out["revenue"] = _metric(
                round(sum(float((o.get("OrderTotal") or {}).get("Amount") or 0)
                          for o in items), 2), "SP-API Orders v0")
            out["sold"] = _none(
                "Units sold for one SKU needs a getOrderItems call per order. Not "
                "fetched here because SP-API allows roughly one request per second and "
                "it would exhaust the quota on a listing page.")
        else:
            for k in ("orders", "revenue", "sold"):
                out[k] = _none(f"SP-API Orders returned HTTP {od.status_code}.")
    except Exception as e:
        for k in ("inventory", "orders", "revenue", "sold"):
            out.setdefault(k, _none(f"{type(e).__name__}: {e}"))
    for k in METRICS:
        out.setdefault(k, _none("Not retrieved."))
    return out


def stats_shopify(pub, ctx) -> dict[str, Any]:
    miss = _missing("SHOPIFY_STORE", "SHOPIFY_ACCESS_TOKEN")
    if miss:
        return _all_unconfigured("Shopify", miss)

    out = {
        "views": _none("Storefront traffic comes from the Shopify Analytics API under "
                       "a read_analytics scope, which custom apps are rarely granted. "
                       "This integration does not request it."),
        "watchers": _none("Shopify has no watcher or follower metric on a product."),
    }
    gid = pub.get("externalId") or pub.get("external_id") or ""
    store = os.getenv("SHOPIFY_STORE", "")
    ver = os.getenv("SHOPIFY_API_VERSION", "2024-10")
    hdr = {"X-Shopify-Access-Token": os.getenv("SHOPIFY_ACCESS_TOKEN", ""),
           "Content-Type": "application/json"}
    try:
        r = requests.post(
            f"https://{store}/admin/api/{ver}/graphql.json", headers=hdr, timeout=TIMEOUT,
            json={"query": "query($id: ID!) { product(id: $id) { totalInventory } }",
                  "variables": {"id": gid}})
        prod = ((_safe_json(r).get("data") or {}).get("product") or {})
        if r.status_code < 300 and prod:
            out["inventory"] = _metric(prod.get("totalInventory"),
                                       "Admin GraphQL product.totalInventory")
        else:
            out["inventory"] = _none(f"Admin API returned HTTP {r.status_code}.")
    except Exception as e:
        out["inventory"] = _none(f"{type(e).__name__}: {e}")

    if os.getenv("SHOPIFY_READ_ORDERS"):
        try:
            r = requests.post(
                f"https://{store}/admin/api/{ver}/graphql.json", headers=hdr,
                timeout=TIMEOUT,
                json={"query": """query($q: String!) { orders(first: 100, query: $q) {
                        nodes { totalPriceSet { shopMoney { amount } }
                                lineItems(first: 20) { nodes {
                                  quantity product { id } } } } } }""",
                      "variables": {"q": "financial_status:paid"}})
            nodes = (((_safe_json(r).get("data") or {}).get("orders") or {})
                     .get("nodes") or [])
            mine = [o for o in nodes
                    if any((li.get("product") or {}).get("id") == gid
                           for li in ((o.get("lineItems") or {}).get("nodes") or []))]
            out["orders"] = _metric(len(mine), "Admin GraphQL orders")
            out["sold"] = _metric(
                sum(li.get("quantity", 0)
                    for o in mine
                    for li in ((o.get("lineItems") or {}).get("nodes") or [])
                    if (li.get("product") or {}).get("id") == gid),
                "Admin GraphQL order line items")
            out["revenue"] = _metric(
                round(sum(float(((o.get("totalPriceSet") or {}).get("shopMoney") or {})
                                .get("amount") or 0) for o in mine), 2),
                "Admin GraphQL orders")
        except Exception as e:
            for k in ("orders", "sold", "revenue"):
                out[k] = _none(f"{type(e).__name__}: {e}")
    else:
        why = ("Per-product sales need the read_orders scope. Set SHOPIFY_READ_ORDERS=1 "
               "once your app has been granted it.")
        for k in ("orders", "sold", "revenue"):
            out[k] = _none(why)
    for k in METRICS:
        out.setdefault(k, _none("Not retrieved."))
    return out


def stats_ondc(pub, ctx) -> dict[str, Any]:
    miss = _missing("ONDC_SUBSCRIBER_ID", "ONDC_SIGNING_PRIVATE_KEY", "ONDC_GATEWAY_URL")
    if miss:
        return _all_unconfigured("ONDC", miss)
    why = ("ONDC is a transaction network, not an analytics platform. Beckn carries "
           "search, order and fulfilment messages and has no concept of a listing "
           "view or a watcher, so no seller can obtain these numbers.")
    return {
        "views": _none(why),
        "watchers": _none(why),
        "orders": _metric(ctx.get("orders", 0), "on_confirm callbacks to this backend"),
        "sold": _metric(ctx.get("sold", 0), "on_confirm callbacks"),
        "revenue": _metric(ctx.get("revenue", 0), "on_confirm callbacks"),
        "inventory": _metric(ctx.get("inventory"), "listing quantity"),
    }


def stats_gem(pub, ctx) -> dict[str, Any]:
    miss = _missing("GEM_API_KEY", "GEM_SELLER_ID", "GEM_API_BASE")
    if miss:
        return _all_unconfigured("GeM", miss)
    why = ("GeM seller APIs cover catalogue, bids and orders. There is no public "
           "endpoint for listing traffic or watchers.")
    out = {"views": _none(why), "watchers": _none(why),
           "inventory": _metric(ctx.get("inventory"), "listing quantity")}
    try:
        r = requests.get(
            f"{os.getenv('GEM_API_BASE', '').rstrip('/')}/seller/orders",
            params={"sellerId": os.getenv("GEM_SELLER_ID"),
                    "productId": pub.get("externalId") or pub.get("external_id") or ""},
            headers={"Authorization": f"Bearer {os.getenv('GEM_API_KEY')}"},
            timeout=TIMEOUT)
        if r.status_code < 300:
            rows = _safe_json(r).get("orders") or []
            out["orders"] = _metric(len(rows), "GeM seller orders")
            out["sold"] = _metric(sum(int(o.get("quantity") or 0) for o in rows),
                                  "GeM seller orders")
            out["revenue"] = _metric(
                round(sum(float(o.get("amount") or 0) for o in rows), 2),
                "GeM seller orders")
        else:
            for k in ("orders", "sold", "revenue"):
                out[k] = _none(f"GeM orders endpoint returned HTTP {r.status_code}.")
    except Exception as e:
        for k in ("orders", "sold", "revenue"):
            out[k] = _none(f"{type(e).__name__}: {e}")
    for k in METRICS:
        out.setdefault(k, _none("Not retrieved."))
    return out


STATS_ADAPTERS = {
    "storefront": stats_storefront,
    "amazon": stats_amazon,
    "shopify": stats_shopify,
    "ondc": stats_ondc,
    "gem": stats_gem,
}


def stats(channel: str, pub: dict, ctx: dict | None = None) -> dict[str, Any]:
    """
    Real figures for one published listing on one channel.

    `ctx` carries what this backend knows for certain - its own order rows and its own
    counted storefront views. An adapter uses it only where that genuinely is the
    source of truth (we serve the storefront; ONDC order state arrives here by
    callback), never to paper over a metric the platform does not expose.
    """
    fn = STATS_ADAPTERS.get(channel)
    if not fn:
        return _all_unconfigured(channel, [])
    try:
        return fn(pub, ctx or {})
    except Exception as e:
        return {k: _none(f"{type(e).__name__}: {e}") for k in METRICS}


def metric_support() -> dict[str, dict[str, bool]]:
    """
    Which metrics each channel can ever provide, regardless of credentials. The app
    uses this to explain a blank as a platform limit rather than a setup mistake.
    """
    return {
        "storefront": {"views": True, "watchers": False, "sold": True,
                       "inventory": True, "revenue": True, "orders": True},
        "amazon": {"views": False, "watchers": False, "sold": False,
                   "inventory": True, "revenue": True, "orders": True},
        "shopify": {"views": False, "watchers": False, "sold": True,
                    "inventory": True, "revenue": True, "orders": True},
        "ondc": {"views": False, "watchers": False, "sold": True,
                 "inventory": True, "revenue": True, "orders": True},
        "gem": {"views": False, "watchers": False, "sold": True,
                "inventory": True, "revenue": True, "orders": True},
    }
