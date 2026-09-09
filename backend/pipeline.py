"""
The image pipeline, in one place.

    enhance -> OCR -> vision detect -> matte -> background -> draft fields

This was inline in the /v1/analyze route. It moved here when server-side processing
was added, because there are now two callers - the direct request, and the background
job worker for slow connections - and two copies of a hundred-line pipeline would
drift within a week.

`on_stage` lets the job worker report progress without this module knowing anything
about jobs.
"""
from __future__ import annotations

import base64
import hashlib
import io
from datetime import datetime, timezone
from typing import Any, Callable

import bg
import db
import imaging
import vision

Stage = Callable[[str, int], None]


def _noop(stage: str, pct: int) -> None:
    pass


def analyse_into(s, listing, data: bytes, *, transcript: str = "",
                 background: str = "studio",
                 save_media: Callable[[Any, str, str], str],
                 skip_models: bool = False,
                 on_stage: Stage = _noop) -> dict[str, Any]:
    """
    Run the pipeline and fill `listing` in place. The caller owns the transaction.

    Fields the artisan has already edited are never overwritten - the pipeline only
    fills blanks. That rule is what makes it safe to re-run analysis on a listing
    somebody has been working on.

    `skip_models=True` is manual mode: store the photograph, run nothing. No OCR, no
    vision model, no background generation. The image is kept because the per-field
    AI buttons need something to look at when she asks for them, but until she asks,
    nothing looks at her product. That is the promise manual mode makes, and it has to
    be true on the server, not just in the interface.
    """
    if skip_models:
        on_stage("storing", 60)
        img = imaging.Image.open(io.BytesIO(data))
        url = save_media(img, listing.id, "final")
        thumb = img.copy()
        thumb.thumbnail((320, 320))
        thumb_url = save_media(thumb, listing.id, "thumb")
        listing.raw_hash = "sha256:" + hashlib.sha256(data).hexdigest()
        listing.image_url = url
        listing.enhance_ops = ["manual: stored as taken, no models run"]
        listing.vision = {"thumbUrl": thumb_url, "notes": "manual mode: not analysed"}
        listing.transcript = transcript or listing.transcript
        listing.status = "draft"
        db.log_event(s, "listing", listing.id, "status", "processing", "draft",
                     "photo stored; manual mode, no models run")
        on_stage("done", 100)
        return {"listingId": listing.id, "imageUrl": url, "thumbUrl": thumb_url,
                "rawHash": listing.raw_hash, "ops": listing.enhance_ops, "ms": 0,
                "ocr": {"ok": False, "boxes": [], "text": ""}, "detected": {},
                "suggestions": {}, "confidence": {},
                "notes": "Manual mode: nothing was analysed.",
                "ocrUsed": [], "source": "manual"}

    t0 = datetime.now(timezone.utc)
    raw_hash = "sha256:" + hashlib.sha256(data).hexdigest()
    lid = listing.id
    ops: list[str] = []

    # -- OCR (local, deterministic) ----------------------------------------
    on_stage("reading", 20)
    ocr = vision.run_ocr(data)
    ops.append(f"ocr:rapidocr {len(ocr.get('boxes', []))} text boxes")

    # -- detection + image-grounded extraction ------------------------------
    on_stage("detecting", 40)
    analysis = vision.analyse(data, ocr=ocr, transcript=transcript)
    mapped = (vision.apply_confident(analysis["fields"])
              if analysis.get("ok") else
              {"fields": {}, "suggestions": {}, "confidence": {}, "ocr_used": [],
               "notes": analysis.get("error", "")})
    ops.append("detect:" + (vision.VLM_MODEL if analysis.get("ok")
                            else f"failed ({analysis.get('error', '')[:60]})"))

    # -- matting + background ----------------------------------------------
    on_stage("picture", 62)
    cut, mops = imaging.matte(data)
    ops += mops
    if cut is not None and background != "none":
        hint = mapped["fields"].get("object") or mapped["suggestions"].get("object") or ""
        final, bops = bg.compose(cut, provider=background, product_hint=str(hint))
        ops += bops
    else:
        uri, eops = imaging.enhance(data)
        ops += eops
        final = imaging.Image.open(io.BytesIO(base64.b64decode(uri.split(",")[1])))

    on_stage("storing", 78)
    image_url = save_media(final, lid, "final")
    if cut is not None:
        save_media(cut, lid, "cut")

    # A small copy, so an app on a slow connection can show something for 15 KB
    # instead of 200. Only worth making when the pipeline already has the image open.
    thumb_url = ""
    try:
        thumb = final.copy()
        thumb.thumbnail((320, 320))
        thumb_url = save_media(thumb, lid, "thumb")
    except Exception:
        thumb_url = image_url

    f = mapped["fields"]
    listing.raw_hash = raw_hash
    listing.image_url = image_url
    listing.enhance_ops = ops
    listing.vision = {"confidence": mapped["confidence"],
                      "suggestions": mapped["suggestions"],
                      "notes": mapped["notes"], "ocrUsed": mapped["ocr_used"],
                      "model": analysis.get("model", ""), "thumbUrl": thumb_url}
    listing.ocr = ocr
    listing.transcript = transcript or listing.transcript

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

    on_stage("done", 100)
    return {
        "listingId": lid,
        "imageUrl": image_url,
        "thumbUrl": thumb_url,
        "rawHash": raw_hash,
        "ops": ops,
        "ms": int((datetime.now(timezone.utc) - t0).total_seconds() * 1000),
        "ocr": ocr,
        "detected": f,
        "suggestions": mapped["suggestions"],
        "confidence": mapped["confidence"],
        "notes": mapped["notes"],
        "ocrUsed": mapped["ocr_used"],
        "source": vision.VLM_MODEL if analysis.get("ok") else "failed",
    }
