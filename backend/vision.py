"""
Vision: object detection, OCR, and image-grounded attribute extraction.

Why this module exists
----------------------
Before it, /v1/catalog took only a voice transcript. The image was uploaded, matted
and thrown away - no model ever looked at it. When the transcript was empty or the
LLM errored, the endpoint returned a hardcoded Banarasi dupatta regardless of what
had been photographed. That is the actual cause of "the name/description/price are
completely wrong", and no amount of patching the displayed output would fix it.

Two independent signals now read the image:

  1. RapidOCR (Apache-2.0, ONNX, fully local, no network) does deterministic text
     detection + recognition. It returns literal strings with per-box confidence.
     It never guesses: if there is no text in the photo, it returns nothing.

  2. A vision-language model (llama-3.2-11b-vision-instruct on NVIDIA's endpoint)
     identifies the object, materials, colours and craft, and interprets whatever
     OCR found - deciding that "₹450" is a price and "Khurja Pottery" is a brand.

Field mapping is the part that was asked for explicitly, so it is deliberate rather
than incidental: the VLM must return each field with a `source` and a `confidence`,
and `apply_confident()` only writes a field when that confidence clears a threshold.
A low-confidence price never lands in the price box, and OCR noise from one field
cannot leak into another, because every field is claimed independently or not at all.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from typing import Any

from PIL import Image

import llm

VLM_MODEL = os.getenv("NVIDIA_VLM_MODEL", "meta/llama-3.2-11b-vision-instruct")

# Below this, a field is offered as a suggestion but never auto-filled.
CONFIDENCE_FLOOR = float(os.getenv("VISION_CONFIDENCE_FLOOR", "0.55"))

_ocr = None


# --------------------------------------------------------------------- OCR

def _ocr_engine():
    """Lazy - the first call unpacks ~15 MB of ONNX models bundled with the wheel."""
    global _ocr
    # The same host-level switch imaging.py reads. Off on a container too small to
    # hold the ONNX models; see the note beside LOCAL_VISION there.
    from imaging import LOCAL_VISION

    if not LOCAL_VISION:
        raise RuntimeError(
            "LOCAL_VISION=off on this host, so local OCR is not available. The "
            "vision model still reads the photograph; only offline OCR is absent.")
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr = RapidOCR()
    return _ocr


def run_ocr(data: bytes, min_conf: float = 0.5) -> dict[str, Any]:
    """
    Deterministic local OCR. Returns literal text boxes, never an interpretation.

    Each box is {text, confidence, box:[x0,y0,x1,y1]}. Boxes are ordered top-to-bottom
    then left-to-right, which is what makes a label's reading order recoverable.
    """
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:
        return {"ok": False, "error": f"decode: {e}", "boxes": [], "text": ""}

    # RapidOCR wants a numpy array; upscale small images, detection degrades badly below ~640px.
    import numpy as np

    if max(img.size) < 640:
        f = 640 / max(img.size)
        img = img.resize((int(img.width * f), int(img.height * f)), Image.LANCZOS)

    try:
        result, _ = _ocr_engine()(np.array(img))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "boxes": [], "text": ""}

    boxes: list[dict[str, Any]] = []
    for item in (result or []):
        try:
            poly, text, conf = item[0], item[1], float(item[2])
        except Exception:
            continue
        if conf < min_conf or not str(text).strip():
            continue
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        boxes.append({
            "text": str(text).strip(),
            "confidence": round(conf, 3),
            "box": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))],
        })

    boxes.sort(key=lambda b: (b["box"][1], b["box"][0]))
    return {
        "ok": True,
        "boxes": boxes,
        "text": "\n".join(b["text"] for b in boxes),
        "engine": "rapidocr-onnxruntime (local)",
    }


# ------------------------------------------------------- price parsing helper

_PRICE_RE = re.compile(
    r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]{1,9})(?:\.\d{1,2})?|([0-9][0-9,]{2,9})\s*(?:/-|rupees|रु)",
    re.IGNORECASE,
)


def parse_price(text: str) -> float | None:
    """
    Pull a rupee amount out of OCR text.

    Requires an explicit currency marker. A bare number on a label is far more often
    a size, a thread count or a year than a price, and silently treating it as a price
    is exactly the field-mapping error being fixed here.
    """
    for m in _PRICE_RE.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        if not raw:
            continue
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        if 1 <= v <= 10_000_000:
            return v
    return None


# --------------------------------------------------------------------- VLM

# Stage A. Small vision models are excellent at perception and poor at emitting a
# complex JSON schema - llama-3.2-11b-vision reliably describes this pot correctly and
# then ignores the schema entirely. So the VLM is asked only for prose, and Nemotron
# (which is strong at structured output) does the structuring in stage B. Each model
# is used for the thing it is actually good at.
LOOK_PROMPT = """Describe this product photograph for a marketplace listing.

Cover, in plain prose:
- what the object is (be specific: "terracotta water pot", not "pottery")
- what it appears to be made of
- its colours
- any decoration, technique or pattern you can see
- its approximate shape and proportions
- any text, label, tag or price visible in the image, quoted exactly
- anything else in frame that is not the product

Describe only what you can actually see. Do not guess a region, a price or a material
you cannot observe. If something is unclear, say it is unclear."""


VISION_SYSTEM = """You identify handmade Indian craft products from a photograph, for an
artisan's marketplace listing.

You are given the photo and, separately, any text an OCR engine literally read from it.

Rules:
- Describe ONLY what is visible. Never infer a material, technique, region or dimension
  that you cannot see and that the OCR text does not state. A wrong claim causes a
  customer return that the artisan personally pays for.
- OCR text may be noisy, rotated or partial. Use it when it is coherent, ignore it when
  it is garbage. Never copy an OCR fragment into a field it does not belong to.
- Treat a number as a price ONLY if it carries a currency marker or an explicit price
  word. Sizes, counts, years and phone numbers are not prices.
- If you cannot determine a field, set its value to null and its confidence to 0.
  A null is far more useful to this app than a confident guess.

Return ONE JSON object, no prose, no markdown fence:

{
  "object":      {"value": "<short noun phrase, e.g. 'terracotta oil lamp'>", "confidence": 0-1},
  "title":       {"value": "<marketplace title, <=140 chars>", "confidence": 0-1},
  "description": {"value": "<60-90 words, factual, no invented claims>", "confidence": 0-1},
  "category":    {"value": "<'A > B > C' path>", "confidence": 0-1},
  "price":       {"value": <number or null>, "confidence": 0-1, "source": "ocr|estimate|none"},
  "materials":   [{"value": "<material>", "confidence": 0-1}],
  "colors":      ["<colour name>"],
  "craft":       {"value": "<craft/technique or region, e.g. 'Khurja pottery'>", "confidence": 0-1},
  "dimensions":  {"value": "<e.g. '20 x 12 cm' or null>", "confidence": 0-1},
  "hsn":         {"value": "<4-digit HSN heading>", "confidence": 0-1},
  "ocr_used":    ["<the OCR strings you actually relied on>"],
  "notes":       "<one line on anything ambiguous>"
}"""


def _downscale(data: bytes, max_side: int = 1120) -> str:
    """VLM input. Large photos cost latency and add nothing above ~1120px."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if max(img.size) > max_side:
        f = max_side / max(img.size)
        img = img.resize((int(img.width * f), int(img.height * f)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def analyse(data: bytes, ocr: dict[str, Any] | None = None,
            transcript: str = "") -> dict[str, Any]:
    """
    Look at the image. Returns the structured field bundle described above, or
    {"ok": False, ...} - never a hardcoded product.
    """
    if not llm.available():
        return {"ok": False, "error": "no NVIDIA_API_KEY", "fields": {}}

    ocr_text = (ocr or {}).get("text", "")

    # ---- stage A: perception. VLM, prose only. -------------------------------
    try:
        r = llm.vision_client().chat.completions.create(
            model=VLM_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": LOOK_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + _downscale(data)}},
            ]}],
            max_tokens=700,
            temperature=0.15,
        )
        seen = (r.choices[0].message.content or "").strip()
    except Exception as e:
        # Say which of the two it was, because the answer is different. A timeout means
        # the endpoint is queueing and the listing goes ahead without detection, which
        # the caller already handles. Anything else is a real fault worth reading.
        if type(e).__name__ in ("APITimeoutError", "APIConnectionError"):
            msg = (f"the vision model did not answer within {llm.VLM_TIMEOUT:.0f}s. "
                   f"The photograph is kept and the listing goes on without automatic "
                   f"detection; tap re-analyse to try it again.")
        else:
            msg = f"VLM {type(e).__name__}: {e}"
        return {"ok": False, "error": msg, "fields": {}}

    if not seen:
        return {"ok": False, "error": "VLM returned nothing", "fields": {}}

    # ---- stage B: structuring. Nemotron, JSON only. --------------------------
    parts = [f"What a vision model observed in the photograph:\n---\n{seen}\n---"]
    if ocr_text:
        parts.append(f"Text an OCR engine literally read from the image:\n---\n"
                     f"{ocr_text[:1500]}\n---")
    else:
        parts.append("OCR found no legible text in the image.")
    if transcript:
        parts.append(f'The artisan also said this out loud (trust it for facts not '
                     f'visible, such as time taken):\n"{transcript[:800]}"')

    try:
        # chat_json repairs malformed JSON and, failing that, hands the model its own
        # broken output to fix. Before this, one stray double quote inside a
        # description dropped the whole analysis and left every field blank.
        fields = llm.chat_json(VISION_SYSTEM, "\n\n".join(parts), max_tokens=1800)
    except Exception as e:
        return {"ok": False, "error": f"structuring {type(e).__name__}: {e}",
                "fields": {}, "observed": seen}

    # Cross-check the price against OCR independently. If OCR shows an explicit rupee
    # amount, that is harder evidence than the model's reading of it.
    ocr_price = parse_price(ocr_text)
    if ocr_price is not None:
        fields["price"] = {"value": ocr_price, "confidence": 0.9, "source": "ocr"}

    return {"ok": True, "fields": fields, "model": VLM_MODEL,
            "structurer": llm.MODEL, "observed": seen}


# ------------------------------------------------------------ field mapping

def _unwrap(v: Any) -> tuple[Any, float]:
    """Fields arrive as {value, confidence}; tolerate a bare value too."""
    if isinstance(v, dict):
        return v.get("value"), float(v.get("confidence") or 0)
    return v, 0.0 if v in (None, "") else 0.6


def apply_confident(fields: dict[str, Any],
                    floor: float = CONFIDENCE_FLOOR) -> dict[str, Any]:
    """
    Turn the analysis bundle into listing fields, keeping only what cleared the floor.

    Anything below the floor is returned under `suggestions` instead - shown to the
    artisan as "we think this might be…" rather than written into the form. This is
    what stops a misread label from silently becoming the product's price.
    """
    out: dict[str, Any] = {}
    suggestions: dict[str, Any] = {}
    conf: dict[str, float] = {}

    simple = ["title", "description", "category", "craft", "dimensions", "hsn", "object"]
    for key in simple:
        val, c = _unwrap(fields.get(key))
        if val in (None, "", []):
            continue
        conf[key] = c
        (out if c >= floor else suggestions)[key] = val

    price, c = _unwrap(fields.get("price"))
    if isinstance(price, (int, float)) and price > 0:
        conf["price"] = c
        # A price is the one field where being wrong costs money, so it needs more
        # than the general floor unless OCR read it literally off the product.
        src = (fields.get("price") or {}).get("source") if isinstance(fields.get("price"), dict) else None
        need = floor if src == "ocr" else max(floor, 0.75)
        (out if c >= need else suggestions)["price"] = float(price)

    mats = [m for m in (fields.get("materials") or [])
            if _unwrap(m)[1] >= floor and _unwrap(m)[0]]
    if mats:
        out["materials"] = [_unwrap(m)[0] for m in mats]

    colors = [c_ for c_ in (fields.get("colors") or []) if isinstance(c_, str) and c_.strip()]
    if colors:
        out["colors"] = colors[:5]

    return {
        "fields": out,
        "suggestions": suggestions,
        "confidence": conf,
        "ocr_used": fields.get("ocr_used") or [],
        "notes": fields.get("notes") or "",
    }
