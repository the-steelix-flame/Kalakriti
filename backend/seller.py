"""
Seller profile: marketplace requirements, readiness, and field mapping.

The point of this module is that seller data is not collected and filed away. Each
channel genuinely needs different things, at different points, for different real
reasons, and the app should be able to say *why* before asking:

  storefront  We ship it ourselves, so we need a pickup address for the courier and
              a return address for an RTO. Nothing else.
  shopify     Product creation needs no seller identity, but the store's shipping
              origin does, or rates cannot be computed.
  ondc        A Beckn provider carries `locations` (with area_code and gps) and a
              fulfillment contact. Taxable goods carry the seller's GSTIN in the
              statutory block, so buyer apps can render a compliant invoice.
  amazon      Seller identity lives in Seller Central, not in the listing payload,
              but merchant-fulfilled orders need a real ship-from address, and
              Indian disbursement needs PAN + GSTIN + a bank account.
  gem         Government procurement: PAN, GSTIN, bank details and a registered
              business address are mandatory before a seller can list at all.

`readiness()` is what the UI renders as the per-marketplace checklist, and it is
computed from the actual profile rather than assumed.
"""
from __future__ import annotations

from typing import Any

import db

# Field -> the plain-language reason it is needed. Shown to the artisan verbatim,
# because "why do you want my PAN" is a fair question.
WHY = {
    "full_name": "आपका नाम बिल और कूरियर की पर्ची पर छपेगा",
    "phone_verified": "ऑर्डर और कूरियर के लिए आपका नंबर पक्का होना ज़रूरी है",
    "pickup_address": "कूरियर वाला सामान यहीं से उठाएगा",
    "return_address": "अगर सामान वापस आया तो यहाँ भेजा जाएगा",
    "business_address": "सरकारी कागज़ों और बिल पर यही पता जाता है",
    "gstin": "GST वाले सामान का बिल बनाने के लिए ज़रूरी है",
    "pan": "सरकारी खरीद और भुगतान के लिए ज़रूरी है",
    "bank_account": "बिका हुआ पैसा सीधे इसी खाते में आएगा",
    "email": "ऑर्डर की सूचना ईमेल पर भी भेजी जाएगी",
}

WHY_EN = {
    "full_name": "Printed on the invoice and the courier label",
    "phone_verified": "Couriers and buyers call this number about the order",
    "pickup_address": "Where the courier physically collects the parcel",
    "return_address": "Where an undelivered parcel is sent back to",
    "business_address": "The registered address on tax documents",
    "gstin": "Required to raise a compliant tax invoice",
    "pan": "Required for government procurement and payouts",
    "bank_account": "Where the money from a sale is deposited",
    "email": "Order notifications are also sent here",
}

# What each channel genuinely blocks on, versus merely prefers.
REQUIREMENTS: dict[str, dict[str, list[str]]] = {
    "storefront": {
        "required": ["full_name", "phone_verified", "pickup_address"],
        "recommended": ["return_address"],
    },
    "shopify": {
        "required": ["full_name", "phone_verified", "pickup_address"],
        "recommended": ["email"],
    },
    "ondc": {
        "required": ["full_name", "phone_verified", "pickup_address", "business_address"],
        "recommended": ["gstin", "email", "return_address"],
    },
    "amazon": {
        "required": ["full_name", "phone_verified", "pickup_address", "return_address",
                     "pan", "gstin", "bank_account"],
        "recommended": ["email", "business_address"],
    },
    "gem": {
        "required": ["full_name", "phone_verified", "business_address", "pan", "gstin",
                     "bank_account"],
        "recommended": ["pickup_address", "email"],
    },
}


def _addr(artisan: db.Artisan, kind: str) -> db.Address | None:
    for a in artisan.addresses or []:
        if a.kind == kind:
            return a
    return None


def _has(artisan: db.Artisan, field: str) -> bool:
    if field == "phone_verified":
        return bool(artisan.phone_verified)
    if field.endswith("_address"):
        a = _addr(artisan, field.replace("_address", ""))
        return bool(a and a.complete())
    return bool((getattr(artisan, field, "") or "").strip())


def readiness(artisan: db.Artisan | None, channel: str) -> dict[str, Any]:
    """
    Per-channel checklist. `ready` means publishing to this channel is not blocked
    by anything about the seller (it says nothing about whether that channel's API
    credentials exist - that is a separate, server-side concern).
    """
    spec = REQUIREMENTS.get(channel, REQUIREMENTS["storefront"])
    if artisan is None:
        return {
            "channel": channel, "ready": False, "loggedIn": False,
            "missing": spec["required"], "missingRecommended": spec["recommended"],
            "checks": [{"field": f, "ok": False, "why": WHY.get(f, ""),
                        "whyEn": WHY_EN.get(f, ""), "required": True}
                       for f in spec["required"]],
        }

    checks = []
    missing, missing_rec = [], []
    for f in spec["required"]:
        ok = _has(artisan, f)
        if not ok:
            missing.append(f)
        checks.append({"field": f, "ok": ok, "why": WHY.get(f, ""),
                       "whyEn": WHY_EN.get(f, ""), "required": True})
    for f in spec["recommended"]:
        ok = _has(artisan, f)
        if not ok:
            missing_rec.append(f)
        checks.append({"field": f, "ok": ok, "why": WHY.get(f, ""),
                       "whyEn": WHY_EN.get(f, ""), "required": False})

    return {"channel": channel, "ready": not missing, "loggedIn": True,
            "missing": missing, "missingRecommended": missing_rec, "checks": checks}


def readiness_all(artisan: db.Artisan | None) -> list[dict[str, Any]]:
    return [readiness(artisan, c) for c in REQUIREMENTS]


# ─────────────────────────────────────────────────────── channel field mapping

def map_seller(artisan: db.Artisan | None, channel: str) -> dict[str, Any]:
    """
    Translate one central profile into the shape a given marketplace expects.

    This is deliberately explicit per channel rather than a generic dict spread:
    ONDC wants `area_code`, Amazon wants `PostalCode`, Shiprocket wants `pin_code`,
    and assuming they are interchangeable is how integrations quietly break.
    """
    if artisan is None:
        return {}
    pick = _addr(artisan, "pickup")
    ret = _addr(artisan, "return") or pick
    biz = _addr(artisan, "business") or pick

    if channel == "ondc":
        return {
            "provider": {
                "id": artisan.id,
                "descriptor": {"name": artisan.business_name or artisan.full_name},
                "locations": ([{
                    "id": pick.id,
                    "gps": "",                       # filled by geocoding if configured
                    "address": {
                        "locality": pick.line1,
                        "street": pick.line2 or pick.line1,
                        "city": pick.city,
                        "area_code": pick.pincode,
                        "state": pick.state,
                        "country": "IND",
                    },
                }] if pick else []),
                "fulfillments": [{
                    "id": "F1", "type": "Delivery",
                    "contact": {"phone": artisan.phone, "email": artisan.email},
                }],
                "@ondc/org/fssai_license_no": "",
                "tax_number": artisan.gstin,
            },
        }

    if channel == "amazon":
        # Seller identity is configured in Seller Central; what the API actually
        # needs per shipment is the ship-from address.
        return {
            "sellerId": artisan.id,
            "shipFromAddress": ({
                "Name": pick.contact_name or artisan.full_name,
                "AddressLine1": pick.line1, "AddressLine2": pick.line2,
                "City": pick.city, "StateOrProvinceCode": pick.state,
                "PostalCode": pick.pincode, "CountryCode": "IN",
                "Phone": pick.contact_phone or artisan.phone,
            } if pick else {}),
            "returnAddress": ({
                "Name": ret.contact_name or artisan.full_name,
                "AddressLine1": ret.line1, "City": ret.city,
                "StateOrProvinceCode": ret.state, "PostalCode": ret.pincode,
                "CountryCode": "IN",
            } if ret else {}),
            "taxRegistration": {"gstin": artisan.gstin, "pan": artisan.pan},
        }

    if channel == "gem":
        return {
            "sellerName": artisan.business_name or artisan.full_name,
            "pan": artisan.pan, "gstin": artisan.gstin,
            "bank": {"account": artisan.bank_account, "ifsc": artisan.bank_ifsc},
            "registeredAddress": ({
                "line1": biz.line1, "line2": biz.line2, "city": biz.city,
                "state": biz.state, "pincode": biz.pincode, "country": biz.country,
            } if biz else {}),
        }

    if channel == "shopify":
        return {
            "vendor": artisan.business_name or artisan.full_name,
            "originAddress": ({
                "address1": pick.line1, "address2": pick.line2, "city": pick.city,
                "province": pick.state, "zip": pick.pincode, "country": "IN",
                "phone": pick.contact_phone or artisan.phone,
            } if pick else {}),
        }

    # storefront
    return {
        "sellerName": artisan.full_name or artisan.business_name,
        "sellerPhone": artisan.phone,
        "pickup": pick.public() if pick else {},
        "return": ret.public() if ret else {},
    }
