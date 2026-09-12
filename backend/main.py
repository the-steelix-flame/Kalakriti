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
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

load_dotenv()

import analytics  # noqa: E402
import auth  # noqa: E402
import bg  # noqa: E402
import channels  # noqa: E402
import clusters  # noqa: E402
import db  # noqa: E402
import grn  # noqa: E402
import logistics  # noqa: E402
import market  # noqa: E402
import ondc  # noqa: E402
import passport  # noqa: E402
import seller  # noqa: E402
import settlement  # noqa: E402

log = logging.getLogger("main")
import imaging  # noqa: E402
import llm  # noqa: E402
import jobs  # noqa: E402
import media  # noqa: E402
import pipeline  # noqa: E402
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

# create_all adds missing tables but never alters an existing one, so a column added
# to a model after this database was created is silently absent. Checked here so it
# is a loud warning at startup naming the fix, rather than a 500 on whichever screen
# reads the new column first.
db.warn_if_schema_behind()

# A job left "running" belongs to a process that is no longer alive - a deploy, a
# crash, an OOM kill. Without this it would say "working on it" forever, and the
# artisan's upload would sit there unprocessed.
_orphans = jobs.requeue_orphans()
if _orphans:
    logging.getLogger("main").warning(
        "requeued %d job(s) left running by a previous process", _orphans)


def _db_kind() -> dict:
    """
    Which database this process is actually talking to, and whether it answers.

    Delegates to db.health(), which runs a real SELECT 1 rather than inferring from
    the URL - the interesting failure is a DATABASE_URL that is set and correct-looking
    but unreachable, and a string comparison cannot see that.

    The warning is the point of this endpoint. SQLite on a hosted container sits on an
    ephemeral disk: every artisan, listing and order is lost on the next deploy, and
    nothing announces it. Somebody discovers it when a seller cannot log in.
    """
    h = db.health()
    hosted = bool(os.getenv("RENDER") or os.getenv("FLY_APP_NAME")
                  or os.getenv("RAILWAY_ENVIRONMENT"))
    warning = ""
    if h.get("engine") == "sqlite" and hosted:
        warning = ("SQLite on a hosted container sits on an ephemeral disk. Every "
                   "account, listing and order is lost on the next deploy. Set "
                   "DATABASE_URL to a Postgres connection string.")
    elif not h.get("ok"):
        warning = f"The database did not answer: {h.get('error', 'unknown error')}"
    return {**h, "warning": warning}


def _b64(raw: str) -> bytes:
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    return base64.b64decode(raw)


def _save_media(img, listing_id: str, kind: str) -> str:
    """
    Store an image and return a URL that survives a redeploy.

    Delegates to media.py, which uses object storage when it is configured and local
    disk otherwise. It used to write to disk unconditionally, which is fine on a
    laptop and quietly broken on any host with an ephemeral filesystem - the images
    disappear on the next deploy, after the marketplace has already published the link.
    """
    return media.save(img, listing_id, kind)


@app.get("/media/{name}")
def serve_media(name: str):
    """Serve a locally stored image. Renamed from `media` because it
    shadowed the media module and broke /health."""
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
        "media": media.status(),
        "database": _db_kind(),
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
    # Manual mode: keep the photograph, run no models. See pipeline.analyse_into.
    skipModels: bool = False


@app.post("/v1/analyze")
def analyze(body: AnalyzeIn,
            authorization: str | None = Header(None),
            x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    Run the whole image pipeline now, and return when it is finished.

    This is the fast-connection path. It holds the request open for two to five
    minutes, which is fine on wifi and hopeless on 2G - a request that long will not
    survive a village connection, and the artisan loses the upload. For that case use
    POST /v1/jobs, which takes the photograph, returns an id immediately, and does the
    same work here on the server. Both call pipeline.analyse_into, so there is one
    implementation rather than two that drift.
    """
    data = _b64(body.imageBase64)

    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        listing = s.get(db.Listing, body.listingId) if body.listingId else None

        if listing is None:
            # Idempotency, by the one thing that identifies this photograph: the hash
            # of its bytes.
            #
            # The bug this fixes was ugly and hard to see. The phone uploads a draft,
            # the server creates the listing and runs the pipeline, and the reply is
            # lost - a tunnel restart, a dropped connection, a two-minute request on a
            # village signal. The phone never learns the listing's id, so the draft
            # stays unsynced and the next refresh uploads the same photograph again.
            # The artisan ends up with the same pot listed twice, both unfinished,
            # both nagging her from the home screen to finish them.
            #
            # Matching on the content hash makes the retry land on the row the first
            # attempt created. Scoped to the owner so two artisans photographing the
            # same product never collide, and only to rows still in progress or draft,
            # because a second genuine listing of a re-photographed item is a real
            # thing somebody may want.
            raw_hash = "sha256:" + hashlib.sha256(data).hexdigest()
            dup = s.query(db.Listing).filter(
                db.Listing.raw_hash == raw_hash,
                db.Listing.status.in_(("processing", "draft")))
            dup = (dup.filter(db.Listing.artisan_id == me.id) if me else
                   dup.filter(db.Listing.guest_token == (x_guest_token or ""),
                              db.Listing.artisan_id.is_(None)))
            listing = dup.order_by(db.Listing.created_at.asc()).first()
            if listing is not None:
                db.log_event(s, "listing", listing.id, "note",
                             detail="same photograph uploaded again; reused this row "
                                    "instead of creating a duplicate")

        if listing is None:
            # A draft belongs to the account when there is one, and otherwise to the
            # device, so guest work survives until it can be claimed at login.
            listing = db.Listing(id=db.nid("lst"), status="processing",
                                 artisan_id=me.id if me else None,
                                 guest_token="" if me else (x_guest_token or ""))
            s.add(listing)
            s.commit()
        elif me and listing.artisan_id is None:
            listing.artisan_id = me.id
            db.log_event(s, "listing", listing.id, "status", "", "processing",
                         "image received")

        out = pipeline.analyse_into(
            s, listing, data,
            transcript=body.transcript,
            background=body.background,
            save_media=_save_media,
            skip_models=body.skipModels,
        )
        s.commit()
        return {**out, "listing": listing.public()}
    finally:
        s.close()


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


def _price_advice(catalog: dict | None, detected: dict | None,
                  material_cost: float, days: float, wage_per_day: float) -> dict:
    """
    Costed price advice. Extracted from the route so "carry on with AI from here"
    produces the identical numbers rather than a second, subtly different pricing.
    """
    return _price_impl(PriceIn(catalog=catalog, detected=detected,
                               materialCost=material_cost, days=days,
                               wagePerDay=wage_per_day))


@app.post("/v1/price")
def price(body: PriceIn) -> dict[str, Any]:
    return _price_impl(body)


def _price_impl(body: PriceIn) -> dict[str, Any]:
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


@app.delete("/v1/listings/{lid}")
def delete_listing(lid: str, authorization: str | None = Header(None),
                   x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    Throw away a draft.

    Deliberately narrow. A draft is work in progress and hers to discard, but a
    listing that has been published or sold is a record other people depend on - a
    buyer's order points at it, a marketplace has its id, and a settlement divides
    money by it. Those are refused with the reason rather than deleted, because the
    row disappearing is how an order becomes an orphan nobody can explain.

    The event log goes with it. Keeping the history of a listing that no longer
    exists serves nobody, and an artisan who discards a photograph expects it gone.
    """
    s = db.session()
    try:
        lst, _me = _own_listing(s, lid, authorization, x_guest_token)

        if (lst.status or "") not in ("draft", "processing", "failed"):
            raise HTTPException(409, detail={
                "error": "not_a_draft",
                "why": f"This product is {lst.status}, not a draft. Published work "
                       f"cannot be deleted here - take it off sale instead."})

        n_orders = s.query(db.Order).filter(db.Order.listing_id == lid).count()
        if n_orders:
            raise HTTPException(409, detail={
                "error": "has_orders",
                "why": f"Somebody has ordered this ({n_orders} order(s)), so it "
                       f"cannot be deleted."})

        pubs = s.query(db.Publication).filter(db.Publication.listing_id == lid).all()
        live = [p for p in pubs if p.status in ("published", "processing")]
        if live:
            raise HTTPException(409, detail={
                "error": "is_published",
                "why": "This is live on " + ", ".join(p.channel for p in live)
                       + ". Take it off sale there first."})

        title = lst.title_en or lst.title_hi or lid
        s.query(db.Publication).filter(db.Publication.listing_id == lid).delete()
        s.query(db.ListingView).filter(db.ListingView.listing_id == lid).delete()
        s.query(db.Enquiry).filter(db.Enquiry.listing_id == lid).delete()
        s.query(db.Event).filter(db.Event.subject_type == "listing",
                                 db.Event.subject_id == lid).delete()
        s.delete(lst)
        s.commit()
        return {"ok": True, "id": lid, "title": title}
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

        # Every timestamp in this database is naive UTC (see db.now), and the client
        # echoes back whatever it was given, which carries an offset. Both sides go
        # through db.naive_utc so the comparison is like for like. The one second of
        # slack absorbs the sub-second difference between the value the client read
        # and the value the database rounded on write; without it every edit would
        # look stale against itself.
        _utc = db.naive_utc

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
 .test{{background:#FBF2DC;border:1px solid #EFDFB4;padding:12px 14px;border-radius:12px;
   font-size:13.5px;color:#4A4468;margin-top:14px}}
</style>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
</head><body><div class="wrap">
<p style="margin:0 0 14px"><a href="/market" style="color:#A93C12;text-decoration:none;
  font-weight:700">&#8592; All handmade pieces</a></p>
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
  {'<div class="test"><b>Test mode.</b> The checkout is genuine Razorpay, but no real money moves. Use card <b>4111 1111 1111 1111</b>, any future expiry, any CVV.</div>' if os.getenv('RAZORPAY_KEY_ID', '').startswith('rzp_test') else ''}
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
  const done = document.getElementById('done');

  // Razorpay first when it is configured. This is the real checkout, not a
  // stand-in: real order objects, real signatures, the same code path production
  // runs. In test mode the only difference is that the money does not exist.
  if (j.razorpayOrderId && j.razorpayKeyId) {{
    done.innerHTML = '<div class="ok">Opening payment…</div>';
    const rz = new Razorpay({{
      key: j.razorpayKeyId,
      order_id: j.razorpayOrderId,
      amount: Math.round(j.amount * 100),
      currency: 'INR',
      name: 'Kalakriti',
      description: {json.dumps(title)},
      prefill: {{ name: fd.buyerName, contact: fd.buyerPhone, email: fd.buyerEmail }},
      theme: {{ color: '#D4541F' }},
      handler: async (res) => {{
        // Confirm server-side before telling anybody it worked. The browser
        // saying "paid" is not evidence; the signature checked against our
        // secret, and Razorpay's own record of the amount, are.
        const v = await fetch('/v1/orders/' + j.id + '/payment', {{
          method: 'POST', headers: {{'Content-Type':'application/json'}},
          body: JSON.stringify({{
            razorpayOrderId: res.razorpay_order_id,
            razorpayPaymentId: res.razorpay_payment_id,
            signature: res.razorpay_signature }})}});
        const vj = await v.json();
        done.innerHTML = v.ok
          ? '<div class="ok"><b>Paid.</b><br>Order ' + j.id +
            '<br>Payment ' + vj.paymentId +
            '<br><br>The maker can see this order in their app now.</div>'
          : '<div class="ok" style="background:#FCEBE9;border-color:#B3261E">' +
            '<b>Payment could not be confirmed.</b><br>' +
            ((vj.detail && vj.detail.why) || 'Please contact the seller.') + '</div>';
      }},
      modal: {{ ondismiss: () => {{
        done.innerHTML = '<div class="ok"><b>Order ' + j.id +
          ' is held, unpaid.</b><br>Reopen this page to try paying again.</div>';
      }} }},
    }});
    rz.on('payment.failed', (res) => {{
      done.innerHTML = '<div class="ok" style="background:#FCEBE9;border-color:#B3261E">' +
        '<b>Payment failed.</b><br>' + ((res.error && res.error.description) || '') + '</div>';
    }});
    rz.open();
    return;
  }}

  done.innerHTML =
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
    listingId: str | None = None
    giTag: str | None = "GI-99 Banaras Brocades & Sarees"
    geo: str | None = "25.3176 N, 82.9739 E - Varanasi cluster"


@app.post("/v1/passport")
def mint_passport(body: PassportIn) -> dict[str, Any]:
    """
    Mint a Provenance Passport, and keep it.

    This used to sign a set of claims, return them once and forget them, so the QR
    code printed on a finished product referred to a record held nowhere. With
    `listingId` the passport is written to the listing and /passport/{id} opens months
    later, in any browser, with no app installed.
    """
    s = db.session()
    try:
        return passport.mint(s, raw_hash=body.rawHash, ops=body.ops,
                             artisan_id=body.artisanId, listing_id=body.listingId,
                             gi_tag=body.giTag, geo=body.geo)
    finally:
        s.close()


@app.get("/passport/{pid}/pubkey")
def passport_pubkey(pid: str) -> dict[str, Any]:
    """
    The public key, the signature and the signed bytes, published openly.

    The addendum's argument for an open standard is that a third party can check the
    claim independently. That is only true if all three are downloadable.
    """
    s = db.session()
    try:
        doc = passport.pubkey_doc(s, pid)
        if not doc:
            raise HTTPException(404, "no such passport")
        return doc
    finally:
        s.close()


@app.get("/passport/{pid}", response_class=HTMLResponse)
def passport_page(pid: str) -> str:
    """The public verification page behind every passport QR code."""
    s = db.session()
    try:
        out = passport.page(s, pid)
        if out is None:
            raise HTTPException(404, "no such passport")
        return out
    finally:
        s.close()


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


# ════════════════════════════════════════════ 8. clusters: the cooperative model
#
# A marketplace will not accept a seller without GST, PAN and a bank account, and the
# artisans this app exists for do not have them. A cluster is how they sell anyway:
# one GST-holding member becomes the seller of record for the group and is paid a
# coordination commission for carrying that liability.
#
# The rules that make this trustworthy rather than a better-dressed middleman live in
# `clusters.py`, not in these handlers. This layer only translates HTTP to that.

def _need_artisan(s, authorization: str | None) -> db.Artisan:
    me = auth.artisan_from_token(s, authorization)
    if me is None:
        raise HTTPException(401, "sign in first")
    return me


def _cluster_error(e: clusters.ClusterError) -> HTTPException:
    """
    A refusal the artisan can act on, carrying its own explanation.

    409 rather than 400 for the ones that are about state rather than input - a full
    capacity book or an already-owned cluster is not a malformed request, and the app
    renders the two differently.
    """
    state_codes = {"insufficient_capacity", "owns_active_cluster",
                   "owner_cannot_join", "not_a_member"}
    status = 409 if e.code in state_codes else 400
    if e.code in ("cluster_not_found", "bad_invite_code", "receipt_not_found",
                  "artisan_not_found"):
        status = 404
    # Not an argument about the request, which is well formed - an argument about
    # who is making it. A member asking to write their own goods receipt is refused
    # for the same reason they cannot write their own payslip.
    if e.code == "not_cluster_owner":
        status = 403
    return HTTPException(status, detail=e.payload())


class ClusterIn(BaseModel):
    name: str
    craftCategory: str = ""
    commissionPct: float = 0
    maxOrderUnits: int = 0
    district: str = ""
    state: str = ""


@app.post("/v1/clusters")
def create_cluster(body: ClusterIn,
                   authorization: str | None = Header(None)) -> dict[str, Any]:
    """Create a cluster. Requires a GSTIN, because the owner is named on the invoice."""
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        try:
            c = clusters.create_cluster(
                s, me, name=body.name, craft_category=body.craftCategory,
                commission_pct=body.commissionPct,
                max_order_units=body.maxOrderUnits,
                district=body.district, state=body.state)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        return clusters.card(s, c, me)
    finally:
        s.close()


@app.get("/v1/clusters")
def list_clusters(craftCategory: str = "", district: str = "", state: str = "",
                  q: str = "", mine: bool = False, limit: int = 50,
                  authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Browse open clusters, or list the caller's own memberships with `mine=true`.

    Readable without signing in. Somebody deciding whether this app is worth creating
    an account for should be able to see what joining would actually mean first.
    """
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        if mine:
            if me is None:
                raise HTTPException(401, "sign in first")
            return {"clusters": clusters.memberships_of(s, me),
                    "owned": [clusters.card(s, c, me) for c in
                              s.query(db.Cluster)
                              .filter(db.Cluster.owner_artisan_id == me.id).all()]}
        return {"clusters": clusters.browse(s, me, craft_category=craftCategory,
                                            district=district, state=state,
                                            query=q, limit=limit)}
    finally:
        s.close()


@app.get("/v1/clusters/{cid}")
def get_cluster(cid: str,
                authorization: str | None = Header(None)) -> dict[str, Any]:
    """One cluster's full terms. The owner additionally sees the member list."""
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        c = s.get(db.Cluster, cid)
        if c is None:
            raise HTTPException(404, "cluster not found")
        out = clusters.card(s, c, me)
        if me is not None and c.owner_artisan_id == me.id:
            out["members"] = [
                {**m.public(),
                 "name": (m.artisan.full_name or m.artisan.business_name or "")
                         if m.artisan else "",
                 "phone": m.artisan.phone if m.artisan else "",
                 "capacityUnits": m.artisan.capacity_units if m.artisan else 0,
                 "capacityAvailable": m.artisan.capacity_available if m.artisan else 0}
                for m in (c.memberships or [])]
        return out
    finally:
        s.close()


@app.get("/v1/clusters/by-code/{code}")
def cluster_by_code(code: str,
                    authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Resolve an invite code to a cluster's terms, before joining.

    Separate from join on purpose: scanning a QR code should show somebody what they
    are about to agree to, not enrol them in it.
    """
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        c = clusters.by_code(s, code)
        if c is None:
            raise HTTPException(404, detail={
                "error": "bad_invite_code",
                "why": "No cluster has that code. Check it with whoever gave it to you."})
        return clusters.card(s, c, me)
    finally:
        s.close()


class JoinIn(BaseModel):
    inviteCode: str = ""


@app.post("/v1/clusters/{cid}/join")
def join_cluster(cid: str, body: JoinIn | None = None,
                 authorization: str | None = Header(None)) -> dict[str, Any]:
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        code = (body.inviteCode if body else "") or ""
        try:
            m = clusters.join(s, me, cluster_id=cid, invite_code=code)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        c = s.get(db.Cluster, m.cluster_id)
        return {"membership": m.public(), "cluster": clusters.card(s, c, me)}
    finally:
        s.close()


@app.post("/v1/clusters/join")
def join_cluster_by_code(body: JoinIn,
                         authorization: str | None = Header(None)) -> dict[str, Any]:
    """Join with only a code, for somebody onboarded in person with no id to hand."""
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        try:
            m = clusters.join(s, me, invite_code=body.inviteCode)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        c = s.get(db.Cluster, m.cluster_id)
        return {"membership": m.public(), "cluster": clusters.card(s, c, me)}
    finally:
        s.close()


@app.post("/v1/clusters/{cid}/leave")
def leave_cluster(cid: str,
                  authorization: str | None = Header(None)) -> dict[str, Any]:
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        try:
            m = clusters.leave(s, me, cid)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        return m.public()
    finally:
        s.close()


class RoleIn(BaseModel):
    role: str


@app.patch("/v1/me/role")
def set_my_role(body: RoleIn,
                authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    The Solo Seller / Cluster Creator switch from addendum §10.2, both directions.

    Changeable later rather than fixed at signup, because somebody who starts by
    listing their own work is exactly the person who later coordinates a cluster.
    """
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        try:
            clusters.set_role(s, me, body.role)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        return me.public()
    finally:
        s.close()


class CapacityIn(BaseModel):
    units: int
    clusterId: str = ""
    reason: str = ""


@app.patch("/v1/me/capacity")
def set_my_capacity(body: CapacityIn,
                    authorization: str | None = Header(None)) -> dict[str, Any]:
    """How many units this artisan can make in a cycle. One number per person."""
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        units = int(body.units or 0)
        if units < 0:
            raise HTTPException(400, "capacity cannot be negative")
        if units < (me.capacity_committed or 0):
            raise HTTPException(409, detail={
                "error": "below_committed",
                "why": f"You have already promised {me.capacity_committed} unit(s). "
                       f"Capacity cannot be set below work already committed."})
        me.capacity_units = units
        s.commit()
        return me.public()
    finally:
        s.close()


@app.post("/v1/me/capacity/commit")
def commit_my_capacity(body: CapacityIn,
                       authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Reserve capacity against an accepted order.

    The counter lives on the artisan, not the membership, so two clusters in the same
    craft cannot both be promised the same weeks - addendum §11, cases 1 and 2.
    """
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        try:
            out = clusters.commit_capacity(s, me, body.units,
                                           cluster_id=body.clusterId,
                                           reason=body.reason)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        return out
    finally:
        s.close()


@app.post("/v1/me/capacity/release")
def release_my_capacity(body: CapacityIn,
                        authorization: str | None = Header(None)) -> dict[str, Any]:
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        try:
            out = clusters.release_capacity(s, me, body.units, body.reason)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        return out
    finally:
        s.close()


class ReviewIn(BaseModel):
    settlementId: str = ""
    paidOnTime: int = 0
    commissionFair: int = 0
    ordersRegular: int = 0
    note: str = ""


@app.get("/v1/clusters/{cid}/reviews")
def list_reviews(cid: str,
                 authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    What members say, and whether the caller may add to it.

    Readable without signing in, because the whole point of a rating is that somebody
    deciding where to send months of work can read it before committing.
    """
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        c = s.get(db.Cluster, cid)
        if c is None:
            raise HTTPException(404, "cluster not found")
        out: dict[str, Any] = {
            "reviews": clusters.reviews_for(s, cid),
            "rating": clusters.rating(s, c),
        }
        if me is not None:
            out["eligibility"] = clusters.review_eligibility(s, me, cid)
        return out
    finally:
        s.close()


@app.post("/v1/clusters/{cid}/reviews")
def add_review(cid: str, body: ReviewIn,
               authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Leave a review. Only possible once this cluster has actually paid you.

    Enforced here rather than in the app, because a rule that lives only in a screen
    is not a rule - and this particular rule is the one that makes the rating worth
    reading at all.
    """
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        try:
            r = clusters.add_review(
                s, me, cid, settlement_id=body.settlementId,
                paid_on_time=body.paidOnTime, commission_fair=body.commissionFair,
                orders_regular=body.ordersRegular, note=body.note)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        c = s.get(db.Cluster, cid)
        return {"review": r.public(), "rating": clusters.rating(s, c)}
    finally:
        s.close()


class GrnIn(BaseModel):
    artisanId: str
    quantityReceived: int
    quantityRejected: int = 0
    orderId: str = ""
    enquiryId: str = ""
    note: str = ""


@app.post("/v1/clusters/{cid}/grn")
def log_grn(cid: str, body: GrnIn,
            authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Record what an artisan physically delivered to the dispatch point.

    This is what settlement reads. Not the commitment, not the order quantity - what
    arrived and passed inspection, which is usually a different number and is the
    only one anybody should be paid against.
    """
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        try:
            g = grn.log_receipt(s, me, cluster_id=cid, artisan_id=body.artisanId,
                                quantity_received=body.quantityReceived,
                                quantity_rejected=body.quantityRejected,
                                order_id=body.orderId, enquiry_id=body.enquiryId,
                                note=body.note)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        return {"receipt": g.public(),
                "tally": grn.tally(s, cid, order_id=body.orderId,
                                   enquiry_id=body.enquiryId)}
    finally:
        s.close()


@app.get("/v1/clusters/{cid}/grn")
def list_grn(cid: str, orderId: str = "", enquiryId: str = "", artisanId: str = "",
             authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Receipts and the running tally.

    The owner sees everything. A member sees only their own rows - what another
    artisan delivered, and whether any of it was rejected, is not theirs to read.
    """
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        c = s.get(db.Cluster, cid)
        if c is None:
            raise HTTPException(404, "cluster not found")

        is_owner = c.owner_artisan_id == me.id
        if not is_owner:
            member = (s.query(db.ClusterMembership)
                      .filter(db.ClusterMembership.cluster_id == cid,
                              db.ClusterMembership.artisan_id == me.id).first())
            if member is None:
                raise HTTPException(403, "not your cluster")
            artisanId = me.id          # narrowed to themselves, whatever was asked

        rows = grn.receipts(s, cid, order_id=orderId, enquiry_id=enquiryId,
                            artisan_id=artisanId)
        out: dict[str, Any] = {"receipts": [g.public() for g in rows],
                               "isOwner": is_owner}
        if is_owner:
            out["tally"] = grn.tally(s, cid, order_id=orderId, enquiry_id=enquiryId)
        return out
    finally:
        s.close()


class VoidIn(BaseModel):
    reason: str = ""


@app.post("/v1/grn/{gid}/void")
def void_grn(gid: str, body: VoidIn,
             authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Mark a receipt void. It is kept, not deleted - a settlement dispute is resolved
    by reading the log, and a log that can be quietly rewritten resolves nothing.
    """
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        try:
            g = grn.void_receipt(s, me, gid, body.reason)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        return g.public()
    finally:
        s.close()


class SettleIn(BaseModel):
    orderId: str
    platformFee: float | None = None
    logisticsFee: float | None = None
    gstRate: float | None = None


@app.post("/v1/clusters/{cid}/settlements")
def compute_settlement(cid: str, body: SettleIn,
                       authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Work out what everyone is owed on one order.

    Only the cluster owner may run this, for the same reason only they may log a
    goods receipt: they are the seller of record, and this decides who is paid what.

    A settlement that cannot be completed comes back with status `needs_input` and a
    note naming the missing figure, rather than a number somebody guessed.
    """
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        c = s.get(db.Cluster, cid)
        if c is None:
            raise HTTPException(404, "cluster not found")
        if c.owner_artisan_id != me.id:
            raise HTTPException(403, detail={
                "error": "not_cluster_owner",
                "why": "Only the person who runs this cluster can settle its orders."})
        try:
            st = settlement.compute(s, body.orderId, cid,
                                    platform_fee_override=body.platformFee,
                                    logistics_fee_override=body.logisticsFee,
                                    gst_rate_override=body.gstRate)
        except clusters.ClusterError as e:
            raise _cluster_error(e)
        s.commit()
        return settlement.explain(s, st)
    finally:
        s.close()


@app.get("/v1/clusters/{cid}/settlements")
def list_settlements(cid: str,
                     authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    Settlements for this cluster.

    The owner sees all of them in full. A member sees only their own line on each -
    what another artisan was paid is not theirs to read, and the cluster's commission
    is between the owner and the whole group rather than a per-member disclosure.
    """
    s = db.session()
    try:
        me = _need_artisan(s, authorization)
        c = s.get(db.Cluster, cid)
        if c is None:
            raise HTTPException(404, "cluster not found")
        is_owner = c.owner_artisan_id == me.id
        if not is_owner:
            member = (s.query(db.ClusterMembership)
                      .filter(db.ClusterMembership.cluster_id == cid,
                              db.ClusterMembership.artisan_id == me.id).first())
            if member is None:
                raise HTTPException(403, "not your cluster")

        rows = (s.query(db.Settlement)
                .filter(db.Settlement.cluster_id == cid)
                .order_by(db.Settlement.created_at.desc()).all())
        out = []
        for st in rows:
            full = settlement.explain(s, st)
            if is_owner:
                out.append(full)
                continue
            mine = [p for p in full["payees"] if p["artisanId"] == me.id]
            if not mine:
                continue
            out.append({"id": full["id"], "orderId": full["orderId"],
                        "status": full["status"], "computedAt": full["computedAt"],
                        "payees": mine})
        return {"settlements": out, "isOwner": is_owner}
    finally:
        s.close()


@app.get("/v1/listings/{lid}/take-home")
def take_home(lid: str, clusterId: str = "", quantity: int = 1,
              authorization: str | None = Header(None)) -> dict[str, Any]:
    """
    What the artisan would actually keep at this listing's price.

    The Fair-Floor promise is dishonest if the number shown is the sticker price, so
    this exists to sit beside it. Deductions that cannot be known before a sale are
    reported as unknown and the estimate is marked incomplete, never folded in as
    zero.
    """
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        lst = s.get(db.Listing, lid)
        if lst is None:
            raise HTTPException(404, "listing not found")

        cluster = s.get(db.Cluster, clusterId) if clusterId else None
        if cluster is None and me is not None:
            m = (s.query(db.ClusterMembership)
                 .filter(db.ClusterMembership.artisan_id == me.id,
                         db.ClusterMembership.status == "active").first())
            cluster = s.get(db.Cluster, m.cluster_id) if m else None
        if cluster is None:
            raise HTTPException(400, detail={
                "error": "no_cluster",
                "why": "This estimate depends on a cluster's commission, and no "
                       "cluster was given or found for you."})
        return settlement.quote_for_artisan(s, lst, cluster, quantity)
    finally:
        s.close()


# ═══════════════════════════════════════════════ 8b. our own marketplace
#
# The one channel nobody has to approve. Amazon, Flipkart, GeM and ONDC all need a
# GST number before a seller account can exist, so for a demo they cannot be live
# however correct the integration is. This is the shop we own: every listing an
# artisan publishes appears here, and a buyer can actually complete a purchase.

@app.get("/market", response_class=HTMLResponse)
def market_page(q: str = "", category: str = "") -> str:
    """The shop front, server-rendered so it opens on any phone or laptop."""
    s = db.session()
    try:
        return market.page(s, q=q, category=category)
    finally:
        s.close()


@app.get("/v1/market")
def market_api(q: str = "", category: str = "", limit: int = 60) -> dict[str, Any]:
    """The same shop as JSON, for a buyer view inside the app."""
    s = db.session()
    try:
        return {"items": market.listings(s, q=q, category=category, limit=limit),
                "categories": market.categories(s),
                "testMode": os.getenv("RAZORPAY_KEY_ID", "").startswith("rzp_test")}
    finally:
        s.close()


class PaymentIn(BaseModel):
    razorpayOrderId: str
    razorpayPaymentId: str
    signature: str


@app.post("/v1/orders/{oid}/payment")
def confirm_payment(oid: str, body: PaymentIn) -> dict[str, Any]:
    """
    Confirm a payment from the checkout that just completed.

    This is what makes a demo work without a webhook. Razorpay's webhook needs a
    public URL it can reach, and it is the right long-term backstop for a buyer who
    pays and then closes the browser - but the buyer who stays on the page can be
    confirmed here and now, from the signed response their own checkout returned.

    Not trusted blindly: `market.verify_payment` checks the signature against our
    secret and then asks Razorpay what it thinks the payment was for, because a
    valid signature on a one-rupee payment is still a valid signature.
    """
    s = db.session()
    try:
        order = s.get(db.Order, oid)
        if order is None:
            raise HTTPException(404, "order not found")
        out = market.verify_payment(
            s, order,
            razorpay_order_id=body.razorpayOrderId,
            razorpay_payment_id=body.razorpayPaymentId,
            signature=body.signature)
        if not out.get("ok"):
            raise HTTPException(400, detail=out)
        return out
    finally:
        s.close()


# ═══════════════════════════════════════ 9. ONDC: the Seller App (BPP) endpoints
#
# These are not webhooks. On ONDC we are a BPP and buyer apps call us, so this is the
# public surface of the network - `/search` is a stranger's app browsing, `/confirm`
# is a stranger's app telling us somebody paid.
#
# Every one of them answers immediately with a bare ACK and does the real work in the
# background, because that is what Beckn requires: the substantive answer goes back
# as a separate POST to the buyer app's own `bap_uri`. Holding the connection open to
# reply properly is the most common way a BPP gets marked unreliable on the network.

def _ondc_dispatch(handler, body: dict) -> None:
    """
    Run one Beckn action and post the callback, on a worker thread.

    Its own session, because it outlives the request that started it. Exceptions are
    logged and swallowed: a handler that raises must not take the process with it,
    and the buyer app will re-ask with `/status` if nothing arrives.
    """
    s = db.session()
    try:
        handler(s, body)
    except Exception as e:                                  # noqa: BLE001
        log.exception("ondc handler %s failed: %s", getattr(handler, "__name__", "?"), e)
    finally:
        s.close()


async def _ondc_action(request: Request, handler, tasks: BackgroundTasks) -> dict:
    """Shared shape for every Beckn action: record it, ACK, then work."""
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        return ondc.nack("30000", "Body is not JSON.")

    if ondc.configured():
        return ondc.nack("30000", "This seller platform is not registered on ONDC yet.")

    ctx = body.get("context") or {}
    s = db.session()
    try:
        db.log_event(s, "ondc", ctx.get("transaction_id", "") or "-",
                     "inbound", detail=ctx.get("action", ""), payload=body)
        s.commit()
    finally:
        s.close()

    # A NACK has to be decided synchronously - refusing an order after ACKing it is
    # not something the protocol gives us a way to say.
    if handler in (ondc.handle_select, ondc.handle_init, ondc.handle_confirm,
                   ondc.handle_status, ondc.handle_cancel):
        s = db.session()
        try:
            out = handler(s, body)
        except Exception as e:                              # noqa: BLE001
            log.exception("ondc %s failed: %s", ctx.get("action"), e)
            s.close()
            return ondc.nack("30000", "Could not process that request.")
        s.close()
        if isinstance(out, dict) and "nack" in out:
            return out["nack"]
        return ondc.ack()

    tasks.add_task(_ondc_dispatch, handler, body)
    return ondc.ack()


@app.post("/ondc/search")
async def ondc_search(request: Request, tasks: BackgroundTasks) -> dict[str, Any]:
    """A buyer app browsing. The catalogue goes back as `on_search`."""
    return await _ondc_action(request, ondc.handle_search, tasks)


@app.post("/ondc/select")
async def ondc_select(request: Request, tasks: BackgroundTasks) -> dict[str, Any]:
    """What would this cost. Answered with a quote built from `Listing.price`."""
    return await _ondc_action(request, ondc.handle_select, tasks)


@app.post("/ondc/init")
async def ondc_init(request: Request, tasks: BackgroundTasks) -> dict[str, Any]:
    """Terms and billing. Still not a sale."""
    return await _ondc_action(request, ondc.handle_init, tasks)


@app.post("/ondc/confirm")
async def ondc_confirm(request: Request, tasks: BackgroundTasks) -> dict[str, Any]:
    """
    The sale. This writes the Order row that shows up in the artisan's Orders tab.
    """
    return await _ondc_action(request, ondc.handle_confirm, tasks)


@app.post("/ondc/status")
async def ondc_status(request: Request, tasks: BackgroundTasks) -> dict[str, Any]:
    return await _ondc_action(request, ondc.handle_status, tasks)


@app.post("/ondc/cancel")
async def ondc_cancel(request: Request, tasks: BackgroundTasks) -> dict[str, Any]:
    return await _ondc_action(request, ondc.handle_cancel, tasks)


@app.get("/ondc-site-verification.html", response_class=HTMLResponse)
def ondc_site_verification() -> str:
    """
    Proof to the ONDC registry that we control this domain.

    The registry hands out a request id at subscription time; we sign it with the
    same Ed25519 key and serve the signature here, and the registry fetches this page
    to check it. Without it the subscription is never approved and nothing else in
    this section is reachable.
    """
    req_id = os.getenv("ONDC_REQUEST_ID", "")
    if not req_id or ondc.configured():
        return ("<html><body>ONDC_REQUEST_ID is not set, so this page cannot be "
                "signed yet.</body></html>")
    signed = base64.b64encode(
        ondc._priv_key().sign(req_id.encode())).decode()
    return (f'<html><head><meta name="ondc-site-verification" '
            f'content="{signed}"/></head><body>ONDC Site Verification Page</body></html>')


# ═════════════════════════════════ 10. slow connections: jobs and field assist
#
# Two ways to make a listing, chosen by the artisan, because the right answer depends
# on a connection we cannot see from here.
#
#   auto    the photograph is uploaded once and this server does everything. The app
#           can be closed. Results are collected later, cheapest bytes first.
#   manual  she fills the form herself, and asks for AI one field at a time, or from
#           any point onwards. Nothing is done to her listing that she did not ask for.
#
# Manual is not "AI off". Every model in the app is still available; the difference is
# who starts it.


class JobIn(BaseModel):
    imageBase64: str
    transcript: str = ""
    lang: str = "hi-IN"
    background: str = "studio"
    listingId: str | None = None


@app.post("/v1/jobs")
def create_job(body: JobIn,
               authorization: str | None = Header(None),
               x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    Hand the server a photograph and stop waiting.

    Returns in well under a second: the upload is stored and a worker picks it up.
    The artisan can close the app, lose signal, or go and make another pot.
    """
    if not body.imageBase64:
        raise HTTPException(400, "no image")
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        artisan_id = me.id if me else None
    finally:
        s.close()
    return jobs.create(artisan_id, x_guest_token or "", body.imageBase64,
                       {"transcript": body.transcript, "lang": body.lang,
                        "background": body.background,
                        "listingId": body.listingId})


@app.get("/v1/jobs")
def list_jobs(authorization: str | None = Header(None),
              x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    The caller's own jobs. Small on purpose - this is polled, sometimes on a
    connection that charges by the megabyte, so results are omitted here and fetched
    per job once one is actually finished.
    """
    s = db.session()
    try:
        me = auth.artisan_from_token(s, authorization)
        q = s.query(jobs.Job)
        if me:
            q = q.filter(jobs.Job.artisan_id == me.id)
        elif x_guest_token:
            q = q.filter(jobs.Job.guest_token == x_guest_token,
                         jobs.Job.artisan_id.is_(None))
        else:
            return {"jobs": []}
        rows = q.order_by(jobs.Job.created_at.desc()).limit(20).all()
        return {"jobs": [j.public(with_result=False) for j in rows],
                "working": sum(1 for j in rows if j.status in ("queued", "running")),
                "ready": sum(1 for j in rows
                             if j.status == "done" and not j.seen)}
    finally:
        s.close()


@app.get("/v1/jobs/{jid}")
def get_job(jid: str,
            authorization: str | None = Header(None),
            x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    s = db.session()
    try:
        j = s.get(jobs.Job, jid)
        if not j:
            raise HTTPException(404, "job not found")
        me = auth.artisan_from_token(s, authorization)
        if not jobs.owns(j, me.id if me else None, x_guest_token):
            raise HTTPException(403, "that upload belongs to somebody else")
        return j.public()
    finally:
        s.close()


@app.post("/v1/jobs/{jid}/seen")
def mark_job_seen(jid: str,
                  authorization: str | None = Header(None),
                  x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """The artisan has looked at the finished result, so stop announcing it."""
    s = db.session()
    try:
        j = s.get(jobs.Job, jid)
        if not j:
            raise HTTPException(404, "job not found")
        me = auth.artisan_from_token(s, authorization)
        if not jobs.owns(j, me.id if me else None, x_guest_token):
            raise HTTPException(403, "that upload belongs to somebody else")
        j.seen = 1
        s.commit()
        return {"ok": True}
    finally:
        s.close()


ASSIST_FIELDS = {
    "title": ("Write one product title, 8 to 14 words, plain and specific. Say what the "
              "object is, its material and its most visible feature. No marketing "
              "adjectives, no exclamation marks.",
              "titleEn"),
    "titleHi": ("Write one product title in Hindi, 8 to 14 words, plain and specific.",
                "titleHi"),
    "description": ("Write 3 to 5 sentences describing this handmade product for a "
                    "buyer: what it is, what it is made of, how it was made, what it is "
                    "for. Concrete and honest. Never invent a measurement, a material "
                    "or a certification you were not given.",
                    "descEn"),
    "descriptionHi": ("Write 3 to 5 sentences in Hindi describing this handmade product "
                      "for a buyer. Never invent details you were not given.",
                      "descHi"),
    "category": ("Give one retail category path, like "
                 "'Home & Kitchen > Kitchenware > Water Pitchers'. Nothing else.",
                 "category"),
    "hsn": ("Give the most likely 4-digit or 6-digit Indian HSN code for this product, "
            "as digits only. If you are not reasonably sure, return an empty string "
            "rather than guessing.",
            "hsn"),
    "tags": ("Give 6 to 8 short search keywords a buyer would actually type. "
             "Return them as a JSON array of strings.",
             "tags"),
}

ASSIST_SYSTEM = """You help an Indian artisan write one field of a product listing.

Ground every word in what you are given: the photograph analysis, the text read off
the product, and what the artisan said. If the information is not there, say less -
never invent a material, a measurement, a place of origin or a certification.

Return JSON: {"value": <the field>, "note": "<one short sentence on what you used>"}"""


class AssistIn(BaseModel):
    listingId: str
    field: str
    instruction: str = ""       # optional steer, e.g. "make it shorter"


@app.post("/v1/assist")
def assist(body: AssistIn,
           authorization: str | None = Header(None),
           x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    Fill one field with AI, on request.

    This is what makes manual mode a choice rather than a downgrade. Every model the
    automatic path uses is still here; the artisan decides which field, and when. The
    result is returned rather than written, so nothing changes on her listing until
    she accepts it.
    """
    if body.field not in ASSIST_FIELDS:
        raise HTTPException(400,
                            f"unknown field {body.field}; "
                            f"expected one of {', '.join(ASSIST_FIELDS)}")
    if not llm.available():
        raise HTTPException(503, {"error": "llm_not_configured",
                                  "message": "Set NVIDIA_API_KEY to use AI help."})
    s = db.session()
    try:
        lst, _ = _own_listing(s, body.listingId, authorization, x_guest_token)
        task, _col = ASSIST_FIELDS[body.field]

        # Everything known about this product, and nothing about any other.
        ctx = {
            "whatTheModelSawInThePhoto": (lst.vision or {}).get("suggestions", {}),
            "textReadOffTheProduct": (lst.ocr or {}).get("text", "")[:900],
            "whatTheArtisanSaid": lst.transcript or "",
            "currentTitle": lst.title_en or lst.title_hi or "",
            "currentDescription": (lst.desc_en or "")[:600],
            "category": lst.category, "attributes": lst.attributes or {},
        }
        prompt = (task
                  + ("\n\nThe artisan also asks: " + body.instruction[:300]
                     if body.instruction.strip() else "")
                  + "\n\nWhat is known about this product:\n"
                  + json.dumps(ctx, ensure_ascii=False)[:3000])
        out = llm.chat_json(ASSIST_SYSTEM, prompt, max_tokens=1200)
        return {"field": body.field, "value": out.get("value", ""),
                "note": out.get("note", ""), "source": "nemotron"}
    finally:
        s.close()


class ContinueIn(BaseModel):
    listingId: str
    from_step: str = "catalog"      # catalog | price | picture | all


@app.post("/v1/assist/continue")
def assist_continue(body: ContinueIn,
                    authorization: str | None = Header(None),
                    x_guest_token: str | None = Header(None)) -> dict[str, Any]:
    """
    "Carry on with AI from here."

    Runs the remaining steps in one go and returns them as suggestions, leaving every
    field the artisan has already filled exactly as she typed it. Whatever she wrote
    is the ground truth the model works from, not something to be improved.
    """
    if not llm.available():
        raise HTTPException(503, {"error": "llm_not_configured",
                                  "message": "Set NVIDIA_API_KEY to use AI help."})
    s = db.session()
    try:
        lst, _ = _own_listing(s, body.listingId, authorization, x_guest_token)
        done: dict[str, Any] = {}

        if body.from_step in ("catalog", "all"):
            parts = []
            if lst.vision:
                parts.append("Vision model saw:\n"
                             + json.dumps((lst.vision or {}).get("suggestions", {}),
                                          ensure_ascii=False)[:1500])
            if (lst.ocr or {}).get("text"):
                parts.append("OCR read on the product:\n" + lst.ocr["text"][:900])
            if lst.transcript:
                parts.append(f'The artisan said:\n"{lst.transcript[:800]}"')
            # Anything she has already written is context, not something to replace.
            typed = {k: v for k, v in {
                "title": lst.title_en or lst.title_hi,
                "description": lst.desc_en or lst.desc_hi,
                "category": lst.category}.items() if v}
            if typed:
                parts.append("The artisan has already written these, keep them "
                             "consistent and do not contradict them:\n"
                             + json.dumps(typed, ensure_ascii=False))
            if parts:
                done["catalog"] = llm.chat_json(CATALOG_SYSTEM, "\n\n".join(parts))

        if body.from_step in ("price", "catalog", "all"):
            cat = done.get("catalog") or {"titleEn": lst.title_en,
                                          "category": lst.category}
            try:
                done["price"] = _price_advice(cat, (lst.vision or {}), 1400, 3, 650)
            except Exception as e:
                done["price"] = {"error": f"{type(e).__name__}: {e}"}

        return {"listingId": lst.id, "suggestions": done,
                "note": "Nothing was saved. Accept the parts you want."}
    finally:
        s.close()
