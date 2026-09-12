"""
Image pipeline — fully open source, runs locally, costs nothing.

rembg (MIT) wraps a U^2-Net ONNX model for matting; Pillow does the rest.
Colour handling is deliberately classical rather than generative: a textile's
colour must be *true*, not *attractive*. A hallucinated red causes returns, and
returns are charged back to the artisan.
"""
import base64
import io
import logging
import os

from PIL import Image, ImageEnhance, ImageOps

log = logging.getLogger("imaging")

_session = None

# Which U^2-Net variant does the matting. A setting, because it changes what the
# backend costs to host by a factor of two.
#
# The full measurement is in the note beside LOCAL_VISION below. The short version:
# u2netp saves about 185 MB of resident memory and is looser around hair, fringes and
# the frayed edge of a woven textile - which is exactly where handloom lives. It is a
# hosting compromise, not an improvement, so u2net stays the default.
MODEL = (os.getenv("REMBG_MODEL") or "u2net").strip()

# Longest edge, in pixels, that the matting step works at. 0 disables the cap.
#
# See the note in `matte` for why this exists and what it measured. Raise it on a
# host with memory to spare; the only thing it costs is the crispness of the cut-out
# edge at very large sizes, and the storefront never displays these that big.
MATTE_MAX_PX = int(os.getenv("MATTE_MAX_PX") or "1600")

# Are the local vision models available on this host at all?
#
# Measured, whole app plus one real matte of a 3072x4080 phone photo:
#
#     u2net                 729 MB
#     u2netp                544 MB
#     u2netp, capped 1600   519 MB
#
# A 512 MB container cannot run any of those - and capping the resolution barely
# helped, because the cost is the ONNX weights and their arenas, not the image.
#
# So a small host can set LOCAL_VISION=off. Background removal and local OCR then
# report themselves unavailable instead of being loaded, and the process settles at
# about 110 MB. Everything that runs on NVIDIA's endpoint - the listing copy, the
# pricing, the HSN code, the translations, and the vision model that reads the
# photograph - is unaffected, because none of it is local.
#
# What is genuinely lost is the cut-out and the studio background, and the offline
# OCR. The app already records which operations ran, so a listing made on such a host
# says so rather than implying an edit that never happened.
LOCAL_VISION = (os.getenv("LOCAL_VISION") or "on").strip().lower() not in (
    "off", "0", "false", "no")


# Longest edge the whole pipeline works at, before anything looks at the photograph.
#
# This is the cap that matters, and it was missing. MATTE_MAX_PX below only protected
# the matting step; everything else - the vision call, `enhance`, and the full
# resolution copy saved as the original - still handled the photograph at whatever the
# camera produced.
#
# A current phone shoots 3072x4080. As a PIL RGB image that is 37 MB, and `enhance`
# holds several of them at once: the decode, the auto-levels result, and a copy per
# ImageEnhance pass. Saving the original re-decodes it again. On a 512 MB container
# that is an out-of-memory kill in the middle of the request, and the symptom is not
# an error - the row is already created, so the listing sits in `processing` for ever
# and the app waits on a reply that is never coming.
#
# 1600 is chosen against what the output actually is: `enhance` composes onto a
# 1400x1400 canvas with the subject at 1232px, and the vision model sees far less than
# that. So nothing downstream can tell the difference, and the resize is recorded in
# the operations log either way, because a passport that omits a resize is not a
# record.
WORK_MAX_PX = int(os.getenv("WORK_MAX_PX") or "1600")


def fit_for_pipeline(data: bytes) -> tuple[bytes, list[str]]:
    """
    Decode once, cap the longest edge, and hand back JPEG bytes plus the ops log.

    Returns the bytes unchanged when the photograph is already small enough, so a
    modest image is never re-encoded and never loses a generation to JPEG.
    """
    if not WORK_MAX_PX:
        return data, []
    try:
        src = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except Exception as e:
        # Not this function's job to decide a photograph is unusable. Hand the bytes
        # on and let the step that needs them fail with something specific.
        log.warning("could not pre-scale the photograph (%s); passing it through", e)
        return data, []

    if max(src.width, src.height) <= WORK_MAX_PX:
        return data, []

    before = f"{src.width}x{src.height}"
    src = src.convert("RGB")
    src.thumbnail((WORK_MAX_PX, WORK_MAX_PX), Image.LANCZOS)
    buf = io.BytesIO()
    src.save(buf, format="JPEG", quality=92, optimize=True)
    out = buf.getvalue()
    log.info("pre-scaled %s -> %dx%d (%d KB -> %d KB)",
             before, src.width, src.height, len(data) // 1024, len(out) // 1024)
    return out, [f"resize:{before} -> {src.width}x{src.height} "
                 f"(working resolution, longest edge {WORK_MAX_PX})"]


def _rembg_session():
    """
    Loaded lazily, on the first photograph rather than at import.

    Lazily for two reasons: a process that never mattes anything - a worker draining
    the outbox, a health check - never pays the memory, and the weights download on
    first use rather than blocking startup.
    """
    global _session
    if not LOCAL_VISION:
        raise RuntimeError(
            "LOCAL_VISION=off on this host, so background removal is not available. "
            "The photograph is kept as taken.")
    if _session is None:
        from rembg import new_session

        log.info("loading matting model %r", MODEL)
        _session = new_session(MODEL)
    return _session


def _autolevel(img: Image.Image) -> Image.Image:
    """Gentle exposure/contrast lift. ImageOps.autocontrast clips per-channel, which
    shifts hue on saturated fabrics, so cut off only a sliver and lift separately."""
    img = ImageOps.autocontrast(img, cutoff=(0.5, 0.5), preserve_tone=True)
    img = ImageEnhance.Brightness(img).enhance(1.06)
    img = ImageEnhance.Color(img).enhance(1.04)
    return img


def matte(data: bytes) -> tuple[Image.Image | None, list[str]]:
    """
    Cut the product out, returning RGBA with a real alpha channel plus the ops log.

    Kept separate from `enhance` so the background-generation step can composite the
    cut-out onto a synthesised scene instead of onto flat white.
    """
    ops: list[str] = []
    src = ImageOps.exif_transpose(Image.open(io.BytesIO(data)).convert("RGB"))
    ops.append(f"decode:{src.width}x{src.height} exif-oriented")

    # Cap the working resolution before matting.
    #
    # This was the single largest memory cost in the whole request, and it was
    # invisible: the full-resolution photograph went straight into rembg, which holds
    # several RGBA buffers the size of the input. A modern phone shoots 4000x3000, so
    # one photograph could allocate most of a small container's memory and be
    # OOM-killed - the symptom being a restart, not an error anybody could read.
    #
    # It helps less than it looks like it should - about 10 to 25 MB, because the
    # cost is dominated by the ONNX weights rather than the picture. Kept anyway: it
    # is free, it makes matting faster, and it bounds the worst case.
    #
    # 1600px on the long edge is far more than a marketplace listing needs - the
    # storefront renders these a few hundred pixels wide - and U^2-Net resizes its
    # input to 320x320 internally anyway, so the mask loses nothing that survives to
    # the output. The downscale is recorded in the ops log, because an edit log that
    # omits a resize is not an edit log.
    if MATTE_MAX_PX and max(src.width, src.height) > MATTE_MAX_PX:
        before = f"{src.width}x{src.height}"
        src.thumbnail((MATTE_MAX_PX, MATTE_MAX_PX), Image.LANCZOS)
        ops.append(f"resize:{before} -> {src.width}x{src.height} for matting")

    src = _autolevel(src)
    ops.append("exposure:auto-levels +0.06 / saturation +0.04")
    try:
        from rembg import remove

        cut = remove(src, session=_rembg_session())
        ops.append(f"segment:{MODEL}-matting (rembg, MIT)")
        bbox = cut.split()[-1].getbbox()
        if bbox:
            pad = int(0.04 * max(cut.width, cut.height))
            cut = cut.crop((max(bbox[0] - pad, 0), max(bbox[1] - pad, 0),
                            min(bbox[2] + pad, cut.width), min(bbox[3] + pad, cut.height)))
            ops.append(f"crop:subject bbox {cut.width}x{cut.height}")
        return cut, ops
    except Exception as e:
        ops.append(f"segment:failed ({type(e).__name__})")
        return None, ops


def to_data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def enhance(data: bytes, size: int = 1400, remove_bg: bool = True) -> tuple[str, list[str]]:
    """
    Returns (data-URI PNG, list of operations performed).

    The operation list is not decoration: it is the disclosure record that the
    Authenticity Passport signs and shows to the buyer.
    """
    ops: list[str] = []
    src = Image.open(io.BytesIO(data)).convert("RGB")
    src = ImageOps.exif_transpose(src)
    ops.append(f"decode:{src.width}x{src.height} exif-oriented")

    if remove_bg:
        try:
            from rembg import remove

            cut = remove(src, session=_rembg_session())  # RGBA with alpha matte
            ops.append(f"segment:{MODEL}-matting (rembg, MIT)")

            # Crop to the subject's own bounding box before flattening. Centring the
            # *image* is not centring the *subject* - a pot shot from three metres away
            # would stay a speck in a white field. Marketplaces want the product to fill
            # the frame, so the alpha channel decides the crop.
            bbox = cut.split()[-1].getbbox()
            if bbox:
                pad = int(0.04 * max(cut.width, cut.height))
                bbox = (max(bbox[0] - pad, 0), max(bbox[1] - pad, 0),
                        min(bbox[2] + pad, cut.width), min(bbox[3] + pad, cut.height))
                cut = cut.crop(bbox)
                ops.append(f"crop:subject bbox {cut.width}x{cut.height}")

            flat = Image.new("RGBA", cut.size, (255, 255, 255, 255))
            flat.alpha_composite(cut)
            src = flat.convert("RGB")
            ops.append("background:replace(#FFFFFF)")
        except Exception as e:  # model missing, no network on first run, OOM
            ops.append(f"segment:skipped ({type(e).__name__})")

    src = _autolevel(src)
    ops.append("exposure:auto-levels +0.06 / saturation +0.04")

    # Square canvas, subject centred with a 12% margin - the shape every marketplace wants.
    inner = int(size * 0.88)
    src.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    canvas.paste(src, ((size - src.width) // 2, (size - src.height) // 2))
    ops.append(f"fit:1:1 subject-centered, 12% margin -> {size}x{size} sRGB")

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return uri, ops
