"""
Image pipeline — fully open source, runs locally, costs nothing.

rembg (MIT) wraps a U^2-Net ONNX model for matting; Pillow does the rest.
Colour handling is deliberately classical rather than generative: a textile's
colour must be *true*, not *attractive*. A hallucinated red causes returns, and
returns are charged back to the artisan.
"""
import base64
import io

from PIL import Image, ImageEnhance, ImageOps

_session = None


def _rembg_session():
    """Loaded lazily — the first call downloads ~176 MB of u2net.onnx to ~/.u2net/."""
    global _session
    if _session is None:
        from rembg import new_session

        _session = new_session("u2net")
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
    src = _autolevel(src)
    ops.append("exposure:auto-levels +0.06 / saturation +0.04")
    try:
        from rembg import remove

        cut = remove(src, session=_rembg_session())
        ops.append("segment:u2net-matting (rembg, MIT)")
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
            ops.append("segment:u2net-matting (rembg, MIT)")

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
