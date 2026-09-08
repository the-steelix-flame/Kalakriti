"""
Kalakriti backend.

Pipeline, in the order the brief asks for it:

    upload -> enhance -> detect + OCR -> background removal -> background generation
           -> editable draft -> publish -> status tracking -> order lifecycle

Open source throughout; nothing here bills anyone.
    language   NVIDIA Nemotron 3 Ultra           (user's own key)
    vision     llama-3.2-11b-vision-instruct     (same key) - detection + OCR reading
    OCR        RapidOCR / ONNX                   Apache-2.0, fully local
    matting    rembg / U^2-Net                   MIT, fully local
    imaging    Pillow                            HPND
    signing    cryptography Ed25519              Apache-2.0
    storage    SQLite + SQLAlchemy               MIT

    uvicorn main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

load_dotenv()

import analytics  # noqa: E402
import auth  # noqa: E402
import bg  # noqa: E402
import channels  # noqa: E402
import db  # noqa: E402
import logistics  # noqa: E402
import seller  # noqa: E402
import imaging  # noqa: E402
import llm  # noqa: E402
import vision  # noqa: E402
from routes_auth import router as auth_router

app = FastAPI(title="Kalakriti API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])
app.include_router(auth_router)

MEDIA_DIR = os.getenv("MEDIA_DIR", "media")
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
os.makedirs(MEDIA_DIR, exist_ok=True)
db.init()


def _b64(raw: str) -> bytes:
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    return base64.b64decode(raw)


def _save_media(img, listing_id: str, kind: str) -> str:
    """Write an image to disk and return a real, openable URL on this server."""
    name = f"{listing_id}_{kind}.jpg"
    img.convert("RGB").save(os.path.join(MEDIA_DIR, name), "JPEG", quality=90, optimize=True)
    return f"{PUBLIC_BASE}/media/{name}"


@app.get("/media/{name}")
def media(name: str):
    path = os.path.join(MEDIA_DIR, os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(404, "not found")
    with open(path, "rb") as f:
        return Response(f.read(), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/", response_class=HTMLResponse)
def preview() -> str:
    with open(os.path.join(os.path.dirname(__file__), "preview.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "llm": "nemotron-3-ultra" if llm.available() else "not configured",
        "model": llm.MODEL,
        "vlm": vision.VLM_MODEL if llm.available() else "not configured",
        "ocr": "rapidocr-onnxruntime (local)",
        "matting": "rembg/u2net (local)",
        "channels": channels.availability(),
        "logistics": logistics.availability(),
    }


# ══════════════════════════════════════════════════ 1. analyse (the big one)

class AnalyzeIn(BaseModel):
    imageBase64: str
    transcript: str = ""
    lang: str = "hi-IN"
    background: str = "studio"      # studio | flux | none
    listingId: str | None = None


@app.post("/v1/analyze")
def analyze(body: AnalyzeIn,
            authorization: str | None = Header(None),
            x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    One call runs the whole image pipeline and creates (or updates) a draft listing.

    enhance -> OCR -> vision detect -> matte -> background generation -> draft row

    Returns the fields it is confident about separately from the ones it is not, so
    the UI can pre-fill the former and merely suggest the latter. Nothing here is
    hardcoded: if the model cannot identify the product, the fields come back empty
    rather than as a sample product.
    """
    data = _b64(body.imageBase64)
    raw_hash = "sha256:" + hashlib.sha256(data).hexdigest()
    t0 = datetime.now(timezone.utc)

    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        listing = None
        if body.listingId:
            listing = s.get(db.Listing, body.listingId)
        if listing is None:
            # A draft belongs to the account when there is one, and otherwise to the
            # device, so guest work survives until it can be claimed at login.
            listing = db.Listing(id=db.nid("lst"), status="processing", raw_hash=raw_hash,
                                 artisan_id=me.id if me else None,
                                 guest_token="" if me else (x_guest_token or ""))
            s.add(listing)
        elif me and listing.artisan_id is None:
            listing.artisan_id = me.id
            db.log_event(s, "listing", listing.id, "status", "", "processing",
                         "image received")
        lid = listing.id

        ops: list[str] = []

        # -- OCR (local, deterministic) ------------------------------------
        ocr = vision.run_ocr(data)
        ops.append(f"ocr:rapidocr {len(ocr.get('boxes', []))} text boxes")

        # -- detection + image-grounded extraction --------------------------
        analysis = vision.analyse(data, ocr=ocr, transcript=body.transcript)
        mapped = (vision.apply_confident(analysis["fields"])
                  if analysis.get("ok") else
                  {"fields": {}, "suggestions": {}, "confidence": {}, "ocr_used": [],
                   "notes": analysis.get("error", "")})
        ops.append("detect:" + (vision.VLM_MODEL if analysis.get("ok")
                                else f"failed ({analysis.get('error','')[:60]})"))

        # -- matting + background generation --------------------------------
        cut, mops = imaging.matte(data)
        ops += mops
        if cut is not None and body.background != "none":
            hint = mapped["fields"].get("object") or mapped["suggestions"].get("object") or ""
            final, bops = bg.compose(cut, provider=body.background, product_hint=str(hint))
            ops += bops
        else:
            uri, eops = imaging.enhance(data)
            ops += eops
            final = imaging.Image.open(io.BytesIO(base64.b64decode(uri.split(",")[1])))

        image_url = _save_media(final, lid, "final")
        if cut is not None:
            _save_media(cut, lid, "cut")

        f = mapped["fields"]
        listing.raw_hash = raw_hash
        listing.image_url = image_url
        listing.enhance_ops = ops
        listing.vision = {"confidence": mapped["confidence"],
                          "suggestions": mapped["suggestions"],
                          "notes": mapped["notes"], "ocrUsed": mapped["ocr_used"],
                          "model": analysis.get("model", "")}
        listing.ocr = ocr
        listing.transcript = body.transcript or listing.transcript
        # Only fill blanks - never clobber something the artisan has already edited.
        listing.title_en = listing.title_en or str(f.get("title", "") or "")
        listing.desc_en = listing.desc_en or str(f.get("description", "") or "")
        listing.category = listing.category or str(f.get("category", "") or "")
        listing.hsn = listing.hsn or str(f.get("hsn", "") or "")
        if not listing.price and isinstance(f.get("price"), (int, float)):
            listing.price = float(f["price"])
        attrs = dict(listing.attributes or {})
        for k in ("materials", "colors", "dimensions", "craft", "object"):
            if f.get(k) and not attrs.get(k):
                attrs[k] = f[k]
        listing.attributes = attrs
        listing.status = "draft"
        db.log_event(s, "listing", lid, "status", "processing", "draft",
                     "analysis complete")
        s.commit()

        ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
        return {
            "listingId": lid,
            "imageUrl": image_url,
            "rawHash": raw_hash,
            "ops": ops,
            "ms": ms,
            "ocr": ocr,
            "detected": f,
            "suggestions": mapped["suggestions"],
            "confidence": mapped["confidence"],
            "notes": mapped["notes"],
            "ocrUsed": mapped["ocr_used"],
            "source": "vision" if analysis.get("ok") else "unavailable",
            "listing": listing.public(),
        }
    finally:
        s.close()


# Kept for the existing Studio screen: enhancement only, unchanged behaviour.
class EnhanceIn(BaseModel):
    imageBase64: str
    removeBackground: bool = True


@app.post("/v1/enhance")
def enhance(body: EnhanceIn) -> dict[str, Any]:
    data = _b64(body.imageBase64)
    raw_hash = "sha256:" + hashlib.sha256(data).hexdigest()
    t0 = datetime.now(timezone.utc)
    uri, ops = imaging.enhance(data, remove_bg=body.removeBackground)
    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    return {"uri": uri, "ops": ops, "ms": ms, "rawHash": raw_hash}


# ══════════════════════════════════════════════════════ 2. copy + pricing

CATALOG_SYSTEM = """You write marketplace listings for Indian artisans' handmade goods.

You are given what a vision model saw in the product photo, any text OCR read from it,
and optionally what the artisan said out loud.

Rules:
- Never invent a material, technique, region or dimension. If it was not seen, read or
  said, leave it out. A wrong claim causes a return the artisan personally pays for.
- Prefer the artisan's spoken facts over the vision model where they conflict; they
  made the object.
- Hindi must read as native Hindi prose, not translated English.

Return ONE JSON object, no prose, no fence:
  titleEn, titleHi   (<=140 chars, keyword-led)
  descEn, descHi     (60-110 words)
  bullets            (3-5 short strings)
  tags               (5-8 keywords buyers actually type)
  category           ("A > B > C")
  hsn                (4-digit HSN heading)"""


class CatalogIn(BaseModel):
    transcript: str = ""
    lang: str = "hi-IN"
    detected: dict[str, Any] | None = None
    ocrText: str = ""
    listingId: str | None = None


@app.post("/v1/catalog")
def catalog(body: CatalogIn) -> dict[str, Any]:
    """
    Generate listing copy. Unlike the previous version, this is grounded in what was
    actually seen in the photo - and if there is nothing to work from, it says so
    instead of returning a sample product.
    """
    has_input = bool(body.transcript.strip() or body.detected or body.ocrText.strip())
    if not llm.available():
        return {"ok": False, "error": "NVIDIA_API_KEY not configured", "source": "unavailable"}
    if not has_input:
        return {"ok": False, "error": "nothing to describe: no photo analysis, no OCR text, "
                                      "and no voice note", "source": "no_input"}

    parts = []
    if body.detected:
        parts.append("Vision model saw:\n" + json.dumps(body.detected, ensure_ascii=False)[:2000])
    if body.ocrText.strip():
        parts.append("OCR read this text on the product:\n" + body.ocrText[:1200])
    if body.transcript.strip():
        parts.append(f'The artisan said (language {body.lang}):\n"{body.transcript[:1000]}"')

    try:
        out = llm.chat_json(CATALOG_SYSTEM, "\n\n".join(parts))
        out["ok"] = True
        out["source"] = "nemotron"
        out["transcript"] = body.transcript
        if body.listingId:
            s = db.session()
            try:
                lst = s.get(db.Listing, body.listingId)
                if lst:
                    lst.title_en = out.get("titleEn", lst.title_en)
                    lst.title_hi = out.get("titleHi", lst.title_hi)
                    lst.desc_en = out.get("descEn", lst.desc_en)
                    lst.desc_hi = out.get("descHi", lst.desc_hi)
                    lst.category = out.get("category", lst.category)
                    lst.hsn = out.get("hsn", lst.hsn)
                    lst.tags = out.get("tags", lst.tags)
                    lst.transcript = body.transcript or lst.transcript
                    s.commit()
                    out["listing"] = lst.public()
            finally:
                s.close()
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "source": "error"}


PRICE_SYSTEM = """You price handmade Indian craft goods for a government artisan-support app.

You are on the artisan's side. Two rules override everything:
1. Never suggest a price below the cost floor you are given.
2. Exclude machine-made or power-loom lookalikes from comparison.

Return JSON: suggested (int >= floor), range [low, high], comps (3-4 of
{title, price, source}, at least one machine-made marked excluded in `source`),
rationale (2-3 sentences), underpricedWarning (string)."""


class PriceIn(BaseModel):
    catalog: dict[str, Any] | None = None
    detected: dict[str, Any] | None = None
    materialCost: float = 1400
    days: float = 3
    wagePerDay: float = 650
    listingId: str | None = None


@app.post("/v1/price")
def price(body: PriceIn) -> dict[str, Any]:
    material = float(body.materialCost)
    wage = float(body.days) * float(body.wagePerDay)
    overhead = round((material + wage) * 0.12)
    floor = int(material + wage + overhead)
    breakdown = [
        {"label": "Materials", "amount": int(material)},
        {"label": f"Your labour ({body.days:g} days)", "amount": int(wage)},
        {"label": "Loom, dyeing, overhead", "amount": int(overhead)},
    ]

    advice = None
    if llm.available():
        try:
            advice = llm.chat_json(
                PRICE_SYSTEM,
                "Product:\n" + json.dumps(body.catalog or body.detected or {},
                                          ensure_ascii=False)[:2500] +
                f"\n\nCost floor (must not be undercut): Rs {floor}"
                f"\nMaterials Rs {material:.0f}, {body.days:g} days at "
                f"Rs {body.wagePerDay:.0f}/day.")
        except Exception:
            advice = None

    if advice is None:
        # No model: return the arithmetic only. The floor is real; a market estimate
        # without a model would be invention, so none is offered.
        suggested = int(round(floor * 1.35))
        advice = {"suggested": suggested, "range": [floor, int(floor * 1.9)], "comps": [],
                  "rationale": "Market comparison unavailable; this is your cost floor "
                               "plus a 35% margin.",
                  "underpricedWarning": None, "source": "floor_only"}
    else:
        advice["source"] = "nemotron"

    suggested = int(max(int(advice.get("suggested", floor)), floor))
    shipping = 380
    breakdown += [{"label": "Platform + shipping", "amount": shipping},
                  {"label": "Your profit", "amount": suggested - floor - shipping}]
    rng = advice.get("range") or [floor, suggested]

    if body.listingId:
        s = db.session()
        try:
            lst = s.get(db.Listing, body.listingId)
            if lst:
                lst.price = lst.price or suggested
                lst.floor_price = floor
                s.commit()
        finally:
            s.close()

    return {"suggested": suggested, "floor": floor,
            "range": [int(max(rng[0], floor)), int(max(rng[1], suggested))],
            "comps": advice.get("comps", []), "breakdown": breakdown,
            "rationale": advice.get("rationale", ""),
            "underpricedWarning": advice.get("underpricedWarning"),
            "source": advice["source"]}


# ══════════════════════════════════════════════════════════ 3. listings CRUD

class ListingPatch(BaseModel):
    # The row version this edit was made against, for offline replay. See patch_listing.
    baseUpdatedAt: str | None = None
    titleEn: str | None = None
    titleHi: str | None = None
    descEn: str | None = None
    descHi: str | None = None
    category: str | None = None
    hsn: str | None = None
    price: float | None = None
    floorPrice: float | None = None
    quantity: int | None = None
    tags: list[str] | None = None
    attributes: dict[str, Any] | None = None
    transcript: str | None = None
    status: str | None = None


@app.get("/v1/listings")
def list_listings(limit: int = 50,
                  authorization: str | None = Header(None),
                  x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    Scoped to the caller: a signed-in artisan sees their own listings, a guest sees
    the drafts made on this device. Previously this returned every listing in the
    database to anyone who asked, which is both a privacy leak and, on the My
    Products tab, simply wrong.
    """
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        q = s.query(db.Listing)
        if me:
            q = q.filter(db.Listing.artisan_id == me.id)
        elif x_guest_token:
            q = q.filter(db.Listing.guest_token == x_guest_token,
                         db.Listing.artisan_id.is_(None))
        else:
            return {"listings": []}
        rows = q.order_by(db.Listing.updated_at.desc()).limit(limit).all()
        return {"listings": [r.public() for r in rows],
                "cards": [analytics.listing_card(s, r) for r in rows]}
    finally:
        s.close()


def _own_listing(s, lid: str, authorization, x_guest_token):
    """
    Fetch a listing the caller is entitled to see.

    Previously any id could be read or edited by anyone who guessed it, which leaks a
    seller's unpublished drafts, costs and buyer orders. A guest may reach only the
    drafts made on their own device, and only while those drafts have no owner.
    """
    lst = s.get(db.Listing, lid)
    if not lst:
        raise HTTPException(404, "listing not found")
    me = auth.artisan_from_token(s, authorization)
    if me and lst.artisan_id == me.id:
        return lst, me
    if lst.artisan_id is None and x_guest_token and lst.guest_token == x_guest_token:
        return lst, me
    raise HTTPException(403, "this product belongs to another seller")


@app.get("/v1/listings/{lid}")
def get_listing(lid: str,
                authorization: str | None = Header(None),
                x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    s = db.session()
    try:
        lst, _ = _own_listing(s, lid, authorization, x_guest_token)
        evs = (s.query(db.Event).filter(db.Event.subject_id == lid)
               .order_by(db.Event.at.asc()).all())
        return {**lst.public(), "events": [e.public() for e in evs],
                "views": analytics.view_count(s, lid)}
    finally:
        s.close()


@app.get("/v1/listings/{lid}/detail")
def listing_detail(lid: str,
                   authorization: str | None = Header(None),
                   x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    Everything the product page shows: the product, where it was sent, and what each
    marketplace reports. Marketplace figures are not fetched here - that costs a round
    trip per channel against rate-limited APIs - so this stays fast enough to open on
    a tap. The marketplace screen fetches live figures for the one channel it shows.
    """
    s = db.session()
    try:
        lst, _ = _own_listing(s, lid, authorization, x_guest_token)
        evs = (s.query(db.Event)
               .filter(db.Event.subject_id.in_(
                   [lid] + [p.id for p in lst.publications]
                   + [o.id for o in lst.orders]))
               .order_by(db.Event.at.desc()).limit(60).all())
        return {
            "listing": lst.public(),
            "card": analytics.listing_card(s, lst),
            "marketplaces": analytics.marketplace_rows(s, lst, live=False),
            "orders": [o.public() for o in lst.orders],
            "events": [e.public() for e in evs],
        }
    finally:
        s.close()


@app.get("/v1/listings/{lid}/marketplaces/{channel}")
def marketplace_detail(lid: str, channel: str,
                       authorization: str | None = Header(None),
                       x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    One product on one marketplace, with live figures fetched from that platform.

    Metrics a platform does not expose come back as `available: false` with the
    reason - Amazon has no per-listing view count for a seller application, ONDC has
    no view concept at all - rather than a zero, which would read as "nobody looked".
    """
    s = db.session()
    try:
        lst, _ = _own_listing(s, lid, authorization, x_guest_token)
        rows = analytics.marketplace_rows(s, lst, live=True)
        row = next((r for r in rows if r["channel"] == channel), None)
        if not row:
            raise HTTPException(404, "this product was not sent to that marketplace")
        orders = [o.public() for o in lst.orders if o.channel == channel]
        evs = (s.query(db.Event).filter(db.Event.subject_id == row["id"])
               .order_by(db.Event.at.desc()).limit(40).all())
        return {"listingId": lid, "marketplace": row, "orders": orders,
                "events": [e.public() for e in evs]}
    finally:
        s.close()


@app.patch("/v1/listings/{lid}")
def patch_listing(lid: str, body: ListingPatch,
                  authorization: str | None = Header(None),
                  x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    Every field stays editable right up to submission. The artisan's edit always wins
    over anything a model produced.

    Offline edits arrive here late, carrying `baseUpdatedAt` - the version of the row
    the artisan was actually looking at when she typed. If the row has moved since,
    only the fields that both sides changed are in conflict; those keep the server
    value and are returned in `conflicts`, and everything else applies normally. The
    alternative, last-write-wins, would silently throw away whichever side lost, and
    both sides here are somebody's real work.
    """
    s = db.session()
    try:
        lst, _ = _own_listing(s, lid, authorization, x_guest_token)
        m = {"titleEn": "title_en", "titleHi": "title_hi", "descEn": "desc_en",
             "descHi": "desc_hi", "category": "category", "hsn": "hsn", "price": "price",
             "floorPrice": "floor_price", "quantity": "quantity", "tags": "tags",
             "attributes": "attributes", "transcript": "transcript"}

        # SQLite hands back naive datetimes, and the client echoes whatever it was
        # given, so both sides are normalised to UTC before they are compared. The one
        # second of slack absorbs the sub-second difference between the value the
        # client read and the value the database rounded on write; without it every
        # edit would look stale against itself.
        def _utc(dt):
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

        # Which fields somebody else changed after the version this edit was made
        # against. The event log already records every edit as "edited: a, b", so it
        # is the history we need - and using it is what makes the merge field-level
        # rather than row-level. Comparing values instead would flag every field the
        # artisan changed offline as a conflict with itself.
        moved: set[str] = set()
        if body.baseUpdatedAt:
            try:
                base = _utc(datetime.fromisoformat(
                    body.baseUpdatedAt.replace("Z", "+00:00")))
                later = (s.query(db.Event)
                         .filter(db.Event.subject_id == lid,
                                 db.Event.subject_type == "listing")
                         .all())
                for ev in later:
                    if not ev.at or _utc(ev.at) <= base + timedelta(seconds=1):
                        continue
                    if (ev.detail or "").startswith("edited: "):
                        moved.update(f.strip()
                                     for f in ev.detail[len("edited: "):].split(","))
            except ValueError:
                moved = set()

        conflicts = []
        changed = []
        for k, col in m.items():
            v = getattr(body, k)
            if v is None:
                continue
            current = getattr(lst, col)
            if k in moved and current != v:
                # Somebody else changed this exact field while the edit was queued.
                # Keep the server value and hand both back, rather than picking a
                # winner behind the artisan's back.
                conflicts.append({"field": k, "mine": v, "theirs": current})
                continue
            setattr(lst, col, v)
            changed.append(k)
        if body.status and db.can_transition(db.LISTING_FLOW, lst.status, body.status):
            db.log_event(s, "listing", lid, "status", lst.status, body.status, "manual")
            lst.status = body.status
        if changed:
            db.log_event(s, "listing", lid, "note", detail="edited: " + ", ".join(changed))
        if conflicts:
            db.log_event(s, "listing", lid, "conflict",
                         detail="kept server value for: "
                                + ", ".join(c["field"] for c in conflicts))
        s.commit()
        return {**lst.public(), "conflicts": conflicts}
    finally:
        s.close()


# ══════════════════════════════════════════════════════ 4. publish + status

@app.get("/v1/channels")
def get_channels() -> dict[str, Any]:
    return {"channels": channels.availability()}


class PublishIn(BaseModel):
    channels: list[str] = ["storefront"]


@app.post("/v1/listings/{lid}/publish")
def publish(lid: str, body: PublishIn,
            authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Really attempts each channel. Statuses come from the adapters, which never invent
    a success: a channel without credentials reports `not_configured` and names the
    variables it needs.
    """
    s = db.session()
    try:
        lst = s.get(db.Listing, lid)
        if not lst:
            raise HTTPException(404, "listing not found")
        if not lst.price or not (lst.title_en or lst.title_hi):
            raise HTTPException(400, "listing needs at least a title and a price")

        # Publishing is the one action that genuinely needs an identity: the
        # marketplace, the courier and the buyer all need a real, reachable seller.
        me = auth.artisan_from_token(s, authorization)
        if not me:
            raise HTTPException(401, "login required to publish")
        if not me.phone_verified:
            raise HTTPException(403, "verify your phone number before publishing")
        if lst.artisan_id is None:
            lst.artisan_id = me.id
        elif lst.artisan_id != me.id:
            raise HTTPException(403, "this listing belongs to another seller")

        blocked = {}
        for ch in body.channels:
            r = seller.readiness(me, ch)
            if not r["ready"]:
                blocked[ch] = r["missing"]
        if blocked:
            raise HTTPException(status_code=428, detail={
                "error": "seller_profile_incomplete",
                "message": "Some seller details are still needed for these marketplaces.",
                "blocked": blocked,
            })

        prev = lst.status
        lst.status = "submitted"
        db.log_event(s, "listing", lid, "status", prev, "submitted",
                     "publish requested: " + ", ".join(body.channels))
        s.commit()

        payload = lst.public()
        out = []
        any_live = False
        for ch in body.channels:
            res = channels.publish(ch, {**payload, "seller": seller.map_seller(me, ch)})
            pub = db.Publication(
                id=db.nid("pub"), listing_id=lid, channel=ch, status=res["status"],
                external_id=res.get("external_id", ""), url=res.get("url", ""),
                error=res.get("error", ""), response=res.get("response", {}))
            s.add(pub)
            db.log_event(s, "publication", pub.id, "status", "", res["status"],
                         f"{ch}: {res.get('error') or res.get('url') or 'ok'}")
            if res["status"] == "published":
                any_live = True
            out.append(pub)
        s.commit()

        if any_live:
            db.log_event(s, "listing", lid, "status", "submitted", "published",
                         "at least one channel live")
            lst.status = "published"
            s.commit()
            lst.status = "active"
            db.log_event(s, "listing", lid, "status", "published", "active", "buyable")
            s.commit()

        return {"listing": lst.public(),
                "publications": [p.public() for p in out]}
    finally:
        s.close()


@app.get("/v1/listings/{lid}/status")
def listing_status(lid: str) -> dict[str, Any]:
    s = db.session()
    try:
        lst = s.get(db.Listing, lid)
        if not lst:
            raise HTTPException(404, "listing not found")
        evs = (s.query(db.Event)
               .filter(db.Event.subject_id.in_(
                   [lid] + [p.id for p in lst.publications] + [o.id for o in lst.orders]))
               .order_by(db.Event.at.asc()).all())
        return {"id": lid, "status": lst.status, "flow": db.LISTING_FLOW,
                "publications": [p.public() for p in lst.publications],
                "orders": [o.public() for o in lst.orders],
                "events": [e.public() for e in evs]}
    finally:
        s.close()


# ═══════════════════════════════════════════════════ 5. storefront + orders

@app.get("/l/{lid}", response_class=HTMLResponse)
def storefront_page(lid: str, request: Request) -> str:
    """
    The real, openable product page behind every storefront listing URL.

    Serving it is also the only place in the system where a view is counted, and it is
    counted here because it genuinely happened: somebody requested this page. The
    viewer is reduced to a salted hash bucketed by the hour, so a buyer reloading
    while they decide counts once, and the row cannot be traced back to a person.
    """
    s = db.session()
    try:
        lst = s.get(db.Listing, lid)
        if not lst:
            raise HTTPException(404, "listing not found")
        try:
            fwd = request.headers.get("x-forwarded-for", "")
            ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "")
            analytics.record_view(s, lid, ip,
                                  request.headers.get("user-agent", ""),
                                  request.headers.get("referer", ""))
        except Exception:
            pass          # a counter must never stop a buyer seeing the product
        d = lst.public()
        e = html.escape
        title = e(d["titleEn"] or d["titleHi"] or "Handmade product")
        sold_out = d["quantity"] <= 0
        attrs = "".join(
            f"<tr><td>{e(str(k))}</td><td>{e(', '.join(v) if isinstance(v, list) else str(v))}</td></tr>"
            for k, v in (d["attributes"] or {}).items())
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
 body{{margin:0;font:16px/1.6 'Segoe UI',system-ui,sans-serif;background:#FBF8F4;color:#171331}}
 .wrap{{max-width:860px;margin:0 auto;padding:24px}}
 img{{width:100%;border-radius:20px;background:#fff}}
 h1{{font-size:26px;margin:18px 0 6px}}
 .price{{font-size:34px;font-weight:800;color:#0B7A54;margin:10px 0}}
 .badge{{display:inline-block;background:#EAE8F6;color:#2E2A6B;padding:6px 12px;
   border-radius:99px;font-size:13px;font-weight:700;margin-right:6px}}
 table{{width:100%;border-collapse:collapse;margin:16px 0}}
 td{{padding:8px 0;border-bottom:1px solid #E9E1D8;vertical-align:top}}
 td:first-child{{color:#7C7596;width:36%;text-transform:capitalize}}
 form{{background:#fff;border:1px solid #E9E1D8;border-radius:20px;padding:18px;margin-top:20px}}
 input,textarea{{width:100%;padding:12px;margin:6px 0 12px;border:1px solid #D8CDC0;
   border-radius:10px;font-size:16px;font-family:inherit;box-sizing:border-box}}
 button{{width:100%;padding:16px;border:0;border-radius:14px;background:#0B7A54;color:#fff;
   font-size:17px;font-weight:700;cursor:pointer}}
 button[disabled]{{background:#B9B3C6;cursor:not-allowed}}
 .ok{{background:#E3F3EC;border:1px solid #0B7A54;padding:16px;border-radius:14px;margin-top:16px}}
</style></head><body><div class="wrap">
<img src="{e(d['imageUrl'])}" alt="{title}">
<h1>{title}</h1>
<div class="price">₹{d['price']:,.0f}</div>
<span class="badge">Handmade · verified</span>
<span class="badge">{e(d['category'] or 'Handicraft')}</span>
<span class="badge">HSN {e(d['hsn'] or '-')}</span>
<p>{e(d['descEn'] or d['descHi'])}</p>
<table>{attrs}</table>
<form id="f">
  <h3 style="margin:0 0 10px">{'Sold out' if sold_out else 'Buy this piece'}</h3>
  <input name="buyerName" placeholder="Your name" required>
  <input name="buyerPhone" placeholder="Phone number" required>
  <input name="buyerEmail" type="email" placeholder="Email (optional)">
  <textarea name="address" rows="3" placeholder="Delivery address" required></textarea>
  <button {'disabled' if sold_out else ''}>Place order · ₹{d['price']:,.0f}</button>
</form>
<div id="done"></div>

<form id="bulk">
  <h3 style="margin:0 0 4px">Need a larger quantity?</h3>
  <p style="margin:0 0 10px;color:#7C7596;font-size:14px">
    Ordering many pieces, or want a custom variation? Send the maker your
    requirement and they will reply with a price.</p>
  <input name="buyerName" placeholder="Your name" required>
  <input name="organisation" placeholder="Shop or organisation (optional)">
  <input name="buyerPhone" placeholder="Phone number" required>
  <input name="buyerEmail" type="email" placeholder="Email (optional)">
  <input name="quantity" type="number" min="2" placeholder="How many pieces?" required>
  <input name="neededBy" placeholder="Needed by (e.g. 15 March)">
  <textarea name="message" rows="3"
    placeholder="Anything specific - colours, sizes, packaging"></textarea>
  <button style="background:#2E2A6B">Send enquiry</button>
</form>
<div id="bulkdone"></div>
</div><script>
document.getElementById('f').addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  const fd = Object.fromEntries(new FormData(ev.target).entries());
  fd.listingId = {json.dumps(lid)};
  const btn = ev.target.querySelector('button');
  btn.disabled = true; btn.textContent = 'Placing order…';
  const r = await fetch('/v1/orders', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(fd)}});
  const j = await r.json();
  if (!r.ok) {{ btn.disabled=false; btn.textContent='Try again'; alert(j.detail||'failed'); return; }}
  ev.target.style.display='none';
  document.getElementById('done').innerHTML =
    '<div class="ok"><b>Order ' + j.id + ' created.</b><br>Status: ' + j.status +
    '<br>Payment: ' + j.paymentStatus +
    (j.payLink ? '<br><br><a href="'+j.payLink+'">Pay ₹' + j.amount + ' via UPI</a>' : '') +
    '</div>';
}});
document.getElementById('bulk').addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  const fd = Object.fromEntries(new FormData(ev.target).entries());
  fd.listingId = {json.dumps(lid)};
  fd.quantity = parseInt(fd.quantity || '0', 10);
  const btn = ev.target.querySelector('button');
  btn.disabled = true; btn.textContent = 'Sending…';
  const r = await fetch('/v1/enquiries', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(fd)}});
  const j = await r.json();
  if (!r.ok) {{ btn.disabled=false; btn.textContent='Try again';
                alert(j.detail||'failed'); return; }}
  ev.target.style.display='none';
  document.getElementById('bulkdone').innerHTML =
    '<div class="ok"><b>Enquiry ' + j.id + ' sent.</b><br>' +
    'The maker will see it in their app and reply to you directly.</div>';
}});
</script></body></html>"""
    finally:
        s.close()


class OrderIn(BaseModel):
    listingId: str
    buyerName: str
    buyerPhone: str
    buyerEmail: str = ""
    address: str
    quantity: int = 1


@app.post("/v1/orders")
def create_order(body: OrderIn) -> dict[str, Any]:
    """
    A real order row, created by a real buyer action on the storefront page.

    Payment: if Razorpay keys are present, a real Razorpay order is created and its id
    returned. Otherwise a UPI intent link is generated - which is a genuine payment
    instrument requiring no API at all, only the artisan's VPA. Neither path invents a
    confirmation: `paymentStatus` stays `pending` until the webhook says otherwise.
    """
    s = db.session()
    try:
        lst = s.get(db.Listing, body.listingId)
        if not lst:
            raise HTTPException(404, "listing not found")
        if lst.status not in ("active", "published"):
            raise HTTPException(400, f"listing is {lst.status}, not purchasable")
        if lst.quantity < body.quantity:
            raise HTTPException(400, "not enough stock")

        amount = float(lst.price) * body.quantity
        order = db.Order(id=db.nid("ord"), listing_id=lst.id, channel="storefront",
                         buyer_name=body.buyerName, buyer_phone=body.buyerPhone,
                         buyer_email=body.buyerEmail, address=body.address,
                         quantity=body.quantity, amount=amount, status="created",
                         payment_status="pending")
        s.add(order)
        db.log_event(s, "order", order.id, "status", "", "created",
                     f"{body.quantity} x {lst.id}")

        pay_link = ""
        rp_key, rp_secret = os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")
        if rp_key and rp_secret:
            try:
                import requests

                r = requests.post("https://api.razorpay.com/v1/orders",
                                  auth=(rp_key, rp_secret), timeout=30,
                                  json={"amount": int(amount * 100), "currency": "INR",
                                        "receipt": order.id})
                if r.status_code < 300:
                    rb = r.json()
                    order.payment_provider = "razorpay"
                    order.payment_ref = rb.get("id", "")
                    order.status = "payment_pending"
                    db.log_event(s, "order", order.id, "status", "created",
                                 "payment_pending", f"razorpay {order.payment_ref}")
                else:
                    db.log_event(s, "order", order.id, "error",
                                 detail=f"razorpay HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                db.log_event(s, "order", order.id, "error", detail=f"razorpay: {e}")
        vpa = os.getenv("ARTISAN_UPI_VPA")
        if not order.payment_ref and vpa:
            # A real UPI intent link - opens any UPI app with the amount pre-filled.
            pay_link = (f"upi://pay?pa={vpa}&pn={os.getenv('ARTISAN_UPI_NAME','Artisan')}"
                        f"&am={amount:.2f}&cu=INR&tn={order.id}")
            order.payment_provider = "upi_intent"
            order.status = "payment_pending"
            db.log_event(s, "order", order.id, "status", "created", "payment_pending",
                         "UPI intent issued")

        lst.quantity = max(lst.quantity - body.quantity, 0)
        if lst.quantity == 0:
            db.log_event(s, "listing", lst.id, "note", detail="stock exhausted")
        s.commit()
        return {**order.public(), "payLink": pay_link,
                "razorpayOrderId": order.payment_ref,
                "razorpayKeyId": rp_key or ""}
    finally:
        s.close()


@app.get("/v1/orders")
def list_orders(limit: int = 100,
                authorization: str | None = Header(None)) -> dict[str, Any]:
    """Orders on the caller's own listings. Buyers' details are seller data."""
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        if not me:
            return {"orders": []}
        rows = (s.query(db.Order)
                .join(db.Listing, db.Order.listing_id == db.Listing.id)
                .filter(db.Listing.artisan_id == me.id)
                .order_by(db.Order.created_at.desc()).limit(limit).all())
        return {"orders": [o.public() for o in rows]}
    finally:
        s.close()


class OrderPatch(BaseModel):
    status: str | None = None
    paymentStatus: str | None = None
    courier: str | None = None
    trackingId: str | None = None
    trackingUrl: str | None = None


@app.patch("/v1/orders/{oid}")
def patch_order(oid: str, body: OrderPatch) -> dict[str, Any]:
    """Seller-side fulfilment. Transitions are validated against the real flow."""
    s = db.session()
    try:
        o = s.get(db.Order, oid)
        if not o:
            raise HTTPException(404, "order not found")
        if body.status:
            if not db.can_transition(db.ORDER_FLOW, o.status, body.status):
                raise HTTPException(400,
                                    f"cannot go {o.status} -> {body.status}")
            db.log_event(s, "order", oid, "status", o.status, body.status, "seller")
            o.status = body.status
        for k, col in (("paymentStatus", "payment_status"), ("courier", "courier"),
                       ("trackingId", "tracking_id"), ("trackingUrl", "tracking_url")):
            v = getattr(body, k)
            if v is not None:
                setattr(o, col, v)
        if o.status == "delivered" and o.payment_status == "paid":
            db.log_event(s, "order", oid, "status", "delivered", "completed", "auto")
            o.status = "completed"
            lst = s.get(db.Listing, o.listing_id)
            if lst and lst.quantity == 0:
                lst.status = "sold"
                db.log_event(s, "listing", lst.id, "status", "active", "sold", "all stock sold")
        s.commit()
        return o.public()
    finally:
        s.close()


# ═════════════════════════════════════════════════════════════ 6. webhooks

@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request) -> dict[str, Any]:
    """
    Real Razorpay webhook with HMAC-SHA256 signature verification.

    Unsigned or badly-signed calls are rejected - a payment is only ever marked paid
    because Razorpay said so, never because the app assumed it.
    """
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    raw = await request.body()
    sig = request.headers.get("x-razorpay-signature", "")
    if not secret:
        raise HTTPException(503, "RAZORPAY_WEBHOOK_SECRET not configured")
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(400, "bad signature")

    body = json.loads(raw or b"{}")
    event = body.get("event", "")
    entity = (body.get("payload", {}).get("payment", {}).get("entity", {})
              or body.get("payload", {}).get("order", {}).get("entity", {}))
    order_ref = entity.get("order_id") or entity.get("id", "")
    receipt = entity.get("receipt", "")

    s = db.session()
    try:
        o = None
        if receipt:
            o = s.get(db.Order, receipt)
        if o is None and order_ref:
            o = s.query(db.Order).filter(db.Order.payment_ref == order_ref).first()
        if o is None:
            return {"ok": True, "ignored": "no matching order"}

        db.log_event(s, "order", o.id, "webhook", detail=event, payload=body)
        if event in ("payment.captured", "order.paid"):
            o.payment_status = "paid"
            if db.can_transition(db.ORDER_FLOW, o.status, "paid"):
                db.log_event(s, "order", o.id, "status", o.status, "paid", "razorpay")
                o.status = "paid"
            s.commit()
            # Payment confirmed is the real trigger for logistics, not order creation.
            if os.getenv("AUTO_SHIP_ON_PAYMENT", "true").lower() != "false":
                logistics.create_shipment(s, o)
        elif event == "payment.failed":
            o.payment_status = "failed"
            db.log_event(s, "order", o.id, "status", o.status, "failed", "razorpay")
            o.status = "failed"
            lst = s.get(db.Listing, o.listing_id)
            if lst:
                lst.quantity += o.quantity        # release the reserved stock
        elif event.startswith("refund."):
            o.payment_status = "refunded"
            o.status = "refunded"
            db.log_event(s, "order", o.id, "status", o.status, "refunded", "razorpay")
        s.commit()
        return {"ok": True, "order": o.id, "status": o.status}
    finally:
        s.close()


@app.post("/webhooks/{channel}")
async def channel_webhook(channel: str, request: Request) -> dict[str, Any]:
    """
    Marketplace status callbacks (ONDC on_status, Amazon notifications, Shopify orders).
    Every payload is recorded; recognised shapes advance the publication's real status.
    """
    body = await request.json() if request.headers.get("content-type", "").startswith(
        "application/json") else {}
    s = db.session()
    try:
        ext = str(body.get("listingId") or body.get("id")
                  or body.get("message", {}).get("order", {}).get("id", ""))
        pub = (s.query(db.Publication)
               .filter(db.Publication.channel == channel,
                       db.Publication.external_id == ext).first()) if ext else None
        db.log_event(s, "publication", pub.id if pub else channel, "webhook",
                     detail=f"{channel} callback", payload=body)
        if pub:
            state = str(body.get("status") or body.get("state") or "").lower()
            if state in ("published", "active", "live"):
                pub.status = "published"
                if body.get("url"):
                    pub.url = body["url"]
            elif state in ("rejected", "failed"):
                pub.status = "failed"
                pub.error = str(body.get("error") or body.get("reason") or "")[:500]
        s.commit()
        return {"ok": True, "recorded": True, "matched": bool(pub)}
    finally:
        s.close()


# ═══════════════════════════════════════════════════ 7. passport + trends

class PassportIn(BaseModel):
    rawHash: str
    ops: list[str] = []
    artisanId: str
    giTag: str | None = "GI-99 Banaras Brocades & Sarees"
    geo: str | None = "25.3176 N, 82.9739 E - Varanasi cluster"


_KEY_PATH = os.getenv("PASSPORT_KEY_PATH", "passport_key.pem")


def _signing_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    if os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    key = ed25519.Ed25519PrivateKey.generate()
    with open(_KEY_PATH, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()))
    return key


@app.post("/v1/passport")
def passport(body: PassportIn) -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization

    key = _signing_key()
    claims = {"artisanId": body.artisanId, "giTag": body.giTag, "geo": body.geo,
              "rawHash": body.rawHash, "enhanceOps": body.ops,
              "capturedAt": datetime.now(timezone.utc).isoformat()}
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    sig = key.sign(payload)
    pub = key.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                        format=serialization.PublicFormat.Raw)
    return {**claims,
            "id": f"KK-BNS-{datetime.now().year}-"
                  f"{hashlib.sha256(payload).hexdigest()[:10].upper()}",
            "signature": "ed25519:" + base64.b64encode(sig).decode(),
            "publicKey": "ed25519:" + base64.b64encode(pub).decode()}


# The /v1/trends endpoint was removed here.
#
# It asked Nemotron to produce "three trends with a realistic rupee band" for a craft
# cluster and the app rendered them with a confidence percentage. A language model
# guessing what handloom sells for is not market data, and an artisan who priced
# against it would have been priced against nothing. /v1/insights replaces it with
# aggregates computed from listings that were actually published on this network,
# withheld entirely when too few sellers contribute to anonymise safely.

# ═══════════════════════════════════════════ 7. enquiries, summary, insights

class EnquiryIn(BaseModel):
    listingId: str
    buyerName: str
    buyerPhone: str
    buyerEmail: str = ""
    organisation: str = ""
    quantity: int = 0
    targetPrice: float = 0
    neededBy: str = ""
    message: str = ""


@app.post("/v1/enquiries")
def create_enquiry(body: EnquiryIn) -> dict[str, Any]:
    """
    A buyer asking for a bigger or custom order, from the real form on the listing
    page. This is what replaced the old consortium screen: that showed an invented
    group of artisans filling an invented 500-piece order. An enquiry is a message
    somebody actually sent, or there is no enquiry.
    """
    s = db.session()
    try:
        lst = s.get(db.Listing, body.listingId)
        if not lst:
            raise HTTPException(404, "listing not found")
        if body.quantity < 2:
            raise HTTPException(400, "a bulk enquiry needs at least 2 pieces")
        if not body.buyerName.strip() or not body.buyerPhone.strip():
            raise HTTPException(400, "name and phone are required")
        e = db.Enquiry(
            id=db.nid("enq"), listing_id=lst.id, artisan_id=lst.artisan_id,
            channel="storefront", buyer_name=body.buyerName.strip(),
            buyer_phone=body.buyerPhone.strip(), buyer_email=body.buyerEmail.strip(),
            organisation=body.organisation.strip(), quantity=body.quantity,
            target_price=body.targetPrice, needed_by=body.neededBy.strip(),
            message=body.message.strip())
        s.add(e)
        db.log_event(s, "enquiry", e.id, "created", "", "new",
                     f"{body.quantity} pieces via storefront")
        s.commit()
        return e.public()
    finally:
        s.close()


@app.get("/v1/enquiries")
def list_enquiries(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Enquiries on the caller's own products. Nobody else's."""
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        if not me:
            return {"enquiries": []}
        rows = (s.query(db.Enquiry).filter(db.Enquiry.artisan_id == me.id)
                .order_by(db.Enquiry.created_at.desc()).limit(200).all())
        titles = {l.id: (l.title_hi or l.title_en or "")
                  for l in s.query(db.Listing)
                  .filter(db.Listing.artisan_id == me.id).all()}
        return {"enquiries": [{**e.public(), "productTitle": titles.get(e.listing_id, "")}
                              for e in rows]}
    finally:
        s.close()


class EnquiryPatch(BaseModel):
    status: str | None = None
    reply: str | None = None


@app.patch("/v1/enquiries/{eid}")
def patch_enquiry(eid: str, body: EnquiryPatch,
                  authorization: str | None = Header(None)) -> dict[str, Any]:
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        if not me:
            raise HTTPException(401, "login required")
        e = s.get(db.Enquiry, eid)
        if not e or e.artisan_id != me.id:
            raise HTTPException(404, "enquiry not found")
        if body.reply is not None:
            e.reply = body.reply
            if e.status == "new":
                e.status = "replied"
        if body.status:
            if (body.status not in db.ENQUIRY_FLOW
                    and body.status not in db.ENQUIRY_TERMINAL):
                raise HTTPException(400, f"unknown status {body.status}")
            db.log_event(s, "enquiry", eid, "status", e.status, body.status, "seller")
            e.status = body.status
        s.commit()
        return e.public()
    finally:
        s.close()


@app.get("/v1/summary")
def summary(authorization: str | None = Header(None),
            x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    Home. Counts of the artisan's own rows, plus what needs doing.

    A guest has no account, so there is nothing to total beyond the drafts on this
    device; the app shows those and invites them to sign in rather than displaying an
    empty dashboard.
    """
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        if not me:
            drafts = []
            if x_guest_token:
                drafts = (s.query(db.Listing)
                          .filter(db.Listing.guest_token == x_guest_token,
                                  db.Listing.artisan_id.is_(None))
                          .order_by(db.Listing.updated_at.desc()).limit(20).all())
            return {"authenticated": False,
                    "drafts": [analytics.listing_card(s, d) for d in drafts]}
        out = analytics.summary(s, me.id)
        return {"authenticated": True, **out}
    finally:
        s.close()


@app.get("/v1/insights")
def insights(authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    What other sellers on this network are doing, aggregated and anonymised.

    Only published listings are counted, no seller is named, and a category is
    dropped entirely unless several different artisans contribute to it - which is
    what prevents an "average price" from being one person's price. When there is not
    enough data the response says so; it does not manufacture a trend.
    """
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        return analytics.insights(s, me.id if me else None)
    finally:
        s.close()


@app.get("/v1/marketplaces/support")
def marketplace_support() -> dict[str, Any]:
    """Which figures each platform can ever give us, so a blank can be explained."""
    return {"support": channels.metric_support(), "metrics": channels.METRICS}
