"""
Background generation - the step after matting.

Two real providers, chosen at request time:

  "studio"  (always available, local, instant)
      Synthesises a photographic studio scene: a vertical gradient backdrop, a
      surface plane with a horizon, a soft elliptical contact shadow anchoring the
      object to that surface, a faint reflection, and a vignette. The palette is
      chosen from the product's own dominant colour so the backdrop flatters it
      rather than fighting it. This is real image synthesis, not a stock JPEG - it
      is computed per product, and it is what marketplaces actually want, since
      Amazon and GeM both reject busy or scene-heavy primary images.

  "flux"    (only if the account can reach NVIDIA's image endpoint)
      A genuine diffusion backdrop from black-forest-labs/flux.1-schnell, prompted
      from the VLM's own reading of the product. Falls back to "studio" if the
      endpoint 404s or times out - and says so in the ops log rather than pretending.

Whichever runs, the operation is recorded in the ops list that the Provenance
Passport signs, so a generated background is always disclosed to the buyer.
"""
from __future__ import annotations

import base64
import io
import math
import os
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFilter

NVIDIA_KEY = os.getenv("NVIDIA_API_KEY", "")
FLUX_URL = os.getenv(
    "FLUX_URL", "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-schnell"
)

# Palettes keyed by the product's dominant hue. Warm goods get a cool-neutral ground
# and vice versa, so the product separates from the backdrop instead of blending in.
PALETTES = {
    "warm":    ((246, 241, 233), (223, 210, 195)),
    "cool":    ((243, 245, 247), (214, 221, 228)),
    "neutral": ((248, 246, 242), (226, 220, 211)),
    "dark":    ((238, 234, 228), (203, 195, 184)),
}


def _dominant(img: Image.Image) -> tuple[int, int, int]:
    small = img.convert("RGB").resize((48, 48), Image.LANCZOS)
    px = list(small.getdata())
    # Ignore near-white pixels: after matting most of the frame is the flat backdrop.
    px = [p for p in px if not (p[0] > 236 and p[1] > 236 and p[2] > 236)] or px
    n = len(px)
    return (sum(p[0] for p in px) // n, sum(p[1] for p in px) // n, sum(p[2] for p in px) // n)


def _family(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    if (r + g + b) / 3 < 70:
        return "dark"
    if r > b + 18:
        return "cool"      # warm product -> cool ground
    if b > r + 18:
        return "warm"      # cool product -> warm ground
    return "neutral"


def _gradient(size: int, top: tuple, bottom: tuple, horizon: float = 0.62) -> Image.Image:
    """Backdrop with a soft horizon - a seamless sweep, the way a real cyclorama reads."""
    img = Image.new("RGB", (1, size))
    d = ImageDraw.Draw(img)
    hz = int(size * horizon)
    for y in range(size):
        if y < hz:
            t = (y / max(hz, 1)) ** 1.25
            c = tuple(int(top[i] + (bottom[i] - top[i]) * t * 0.55) for i in range(3))
        else:
            t = (y - hz) / max(size - hz, 1)
            base = tuple(int(top[i] + (bottom[i] - top[i]) * 0.55) for i in range(3))
            c = tuple(int(base[i] + (bottom[i] - base[i]) * (t ** 0.7)) for i in range(3))
        d.point((0, y), fill=c)
    return img.resize((size, size), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.2))


def _vignette(size: int, strength: float = 0.16) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    pad = int(size * 0.06)
    d.ellipse([-pad, -pad, size + pad, size + pad], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(size * 0.16))
    dark = Image.new("RGB", (size, size), (0, 0, 0))
    return Image.composite(Image.new("RGB", (size, size), (255, 255, 255)), dark, m) \
        .point(lambda v: int(255 - (255 - v) * strength))


def studio(cut: Image.Image, size: int = 1400) -> tuple[Image.Image, list[str]]:
    """
    Composite the matted RGBA cut-out onto a synthesised studio scene.
    `cut` must carry a real alpha channel; its bbox anchors the shadow.
    """
    ops: list[str] = []
    fam = _family(_dominant(cut))
    top, bottom = PALETTES[fam]
    scene = _gradient(size, top, bottom)
    ops.append(f"background:generate(studio/{fam}, gradient+horizon)")

    # Fit the subject into the upper-centre, leaving room for the shadow.
    inner = int(size * 0.72)
    sub = cut.copy()
    sub.thumbnail((inner, inner), Image.LANCZOS)
    x = (size - sub.width) // 2
    y = int(size * 0.5 - sub.height * 0.52)
    y = max(y, int(size * 0.08))

    alpha = sub.split()[-1]
    bbox = alpha.getbbox() or (0, 0, sub.width, sub.height)
    base_y = y + bbox[3]
    cx = x + (bbox[0] + bbox[2]) // 2
    half_w = max((bbox[2] - bbox[0]) // 2, 8)

    # Contact shadow: an ellipse under the object, blurred proportionally to its size.
    sh = Image.new("L", (size, size), 0)
    ImageDraw.Draw(sh).ellipse(
        [cx - half_w * 0.95, base_y - half_w * 0.20,
         cx + half_w * 0.95, base_y + half_w * 0.30], fill=110)
    sh = sh.filter(ImageFilter.GaussianBlur(half_w * 0.20))
    scene = Image.composite(Image.new("RGB", (size, size), (58, 48, 40)), scene, sh)
    ops.append("shadow:contact ellipse, gaussian falloff")

    # Faint reflection on the surface plane. It must fade with distance from the
    # contact point - a uniform-opacity mirror leaves the text legible upside down,
    # which reads as a compositing mistake rather than a reflection.
    refl = sub.transpose(Image.FLIP_TOP_BOTTOM)
    fade = Image.linear_gradient("L").resize((refl.width, refl.height))  # 0 at top
    fade = fade.point(lambda v: int(((255 - v) / 255) ** 2.2 * 90))      # steep falloff
    ra = Image.new("L", refl.size, 0)
    ra.paste(Image.eval(refl.split()[-1], lambda v: v), mask=None)
    ra = Image.composite(fade, Image.new("L", refl.size, 0), refl.split()[-1])
    refl.putalpha(ra)
    refl = refl.filter(ImageFilter.GaussianBlur(6))
    scene_rgba = scene.convert("RGBA")
    scene_rgba.alpha_composite(refl, (x, base_y))
    ops.append("reflection:distance-faded, blurred")

    scene_rgba.alpha_composite(sub, (x, y))
    out = Image.blend(scene_rgba.convert("RGB"), _vignette(size), 0.0)
    out = Image.composite(out, Image.new("RGB", (size, size), (0, 0, 0)),
                          _vignette(size).convert("L").point(lambda v: v))
    ops.append("vignette:0.16")
    return out, ops


def flux_backdrop(prompt: str, size: int = 1024) -> Image.Image | None:
    """A real diffusion backdrop, or None if the account cannot reach the endpoint."""
    if not NVIDIA_KEY:
        return None
    try:
        r = requests.post(
            FLUX_URL,
            headers={"Authorization": f"Bearer {NVIDIA_KEY}", "Accept": "application/json"},
            json={"prompt": prompt, "width": size, "height": size, "steps": 4, "seed": 7},
            timeout=120,
        )
        if r.status_code != 200:
            return None
        body = r.json()
        b64 = None
        if isinstance(body, dict):
            if body.get("artifacts"):
                b64 = body["artifacts"][0].get("base64")
            elif body.get("image"):
                b64 = body["image"]
            elif body.get("data"):
                b64 = body["data"][0].get("b64_json")
        if not b64:
            return None
        return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    except Exception:
        return None


def compose(cut: Image.Image, provider: str = "studio",
            product_hint: str = "", size: int = 1400) -> tuple[Image.Image, list[str]]:
    """Entry point. Returns (final image, ops performed)."""
    if provider == "flux":
        prompt = (
            f"professional e-commerce product photography backdrop for {product_hint or 'a handmade craft object'}, "
            "seamless studio sweep, soft diffused key light from upper left, "
            "clean neutral surface, shallow depth of field, no objects, no text, no people"
        )
        bd = flux_backdrop(prompt)
        if bd is not None:
            bd = bd.resize((size, size), Image.LANCZOS).convert("RGBA")
            sub = cut.copy()
            sub.thumbnail((int(size * 0.72), int(size * 0.72)), Image.LANCZOS)
            x = (size - sub.width) // 2
            y = int(size * 0.5 - sub.height * 0.52)
            alpha = sub.split()[-1]
            bbox = alpha.getbbox() or (0, 0, sub.width, sub.height)
            sh = Image.new("L", (size, size), 0)
            cxx = x + (bbox[0] + bbox[2]) // 2
            hw = max((bbox[2] - bbox[0]) // 2, 8)
            ImageDraw.Draw(sh).ellipse(
                [cxx - hw * 0.95, y + bbox[3] - hw * 0.2,
                 cxx + hw * 0.95, y + bbox[3] + hw * 0.3], fill=115)
            sh = sh.filter(ImageFilter.GaussianBlur(hw * 0.2))
            bd = Image.composite(Image.new("RGBA", (size, size), (52, 44, 38, 255)), bd, sh)
            bd.alpha_composite(sub, (x, max(y, 0)))
            return bd.convert("RGB"), [
                "background:generate(flux.1-schnell, diffusion)",
                "shadow:contact ellipse, gaussian falloff",
            ]
        # Endpoint unreachable for this account - say so rather than silently substituting.
        img, ops = studio(cut, size)
        return img, ["background:flux unavailable for this account, used studio"] + ops

    return studio(cut, size)
