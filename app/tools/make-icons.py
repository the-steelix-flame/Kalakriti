"""
Build the app's launcher icons from the Kalakriti logo.

Why this is not just a copy of the logo file
--------------------------------------------
The logo is a lockup: the K mark, the word "Kalakriti", and the tagline "Crafted by
Hands - Powered by Technology", on a cream field. That is right for a splash screen, a
letterhead or the header inside the app, and wrong for a launcher icon, for two
reasons that both end in the logo being unusable rather than merely imperfect.

First, Android adaptive icons are masked. The system takes the foreground layer and
crops it to whatever shape the launcher uses - circle, squircle, rounded square - and
only the middle ~66% survives. Handing it the full lockup cuts the tagline off, cuts
the wordmark off, and shrinks the K to a speck in the centre.

Second, at the size a launcher actually draws (48dp, about 4mm), the tagline is a grey
smudge and the wordmark is barely legible. An icon has one job at that size, which is
to be recognised, so it gets the mark alone.

So: the mark for the icons, the full lockup left alone for the splash.

The one subtlety
----------------
The cream cannot simply be keyed out by colour. The K's left bar carries cream-coloured
floral motifs on terracotta, and a global "make cream transparent" turns every one of
them into a hole. Instead the cream is removed by flood fill from the border, which
only takes the background that is actually connected to the outside and leaves anything
enclosed by ink alone.

Run from the repository root:

    python app/tools/make-icons.py            # uses app/assets/logo-lockup.png
    LOGO="some/other/logo.png" python app/tools/make-icons.py

Then regenerate the native resources with `npx expo prebuild --platform android`
from app/, which reads these five files out of app/assets.
"""
import io
import os

from PIL import Image, ImageDraw

SRC = os.getenv("LOGO") or os.path.join("app", "assets", "logo-lockup.png")
OUT = os.path.join("app", "assets")

# Measured from the source rather than guessed. The mark occupies this box; the blank
# band at y=750..766 is what separates it from the wordmark below.
MARK_BOX = (363, 184, 958, 750)

CREAM = (252, 245, 227)          # the logo's own background, sampled from its corners
SENTINEL = (255, 0, 255)         # a colour the logo does not contain, for the fill


def mark_with_alpha() -> Image.Image:
    """The K mark, cropped, with the outer cream turned transparent."""
    src = Image.open(SRC).convert("RGB")

    # Crop with a small margin so the flood fill has a continuous border to start
    # from even where ink touches the measured box.
    pad = 12
    box = (MARK_BOX[0] - pad, MARK_BOX[1] - pad, MARK_BOX[2] + pad, MARK_BOX[3] + pad)
    mark = src.crop(box)

    # Flood fill the background from all four edges. Four corners is not enough on its
    # own - ink reaching an edge can split the background into regions that no single
    # corner touches - so seed along every edge.
    flood = mark.copy()
    w, h = flood.size
    seeds = []
    for x in range(0, w, 16):
        seeds += [(x, 0), (x, h - 1)]
    for y in range(0, h, 16):
        seeds += [(0, y), (w - 1, y)]
    for s in seeds:
        if flood.getpixel(s) != SENTINEL:
            ImageDraw.floodfill(flood, s, SENTINEL, thresh=40)

    # Anything the fill reached becomes transparent; everything else keeps its pixel,
    # interior cream motifs included.
    out = mark.convert("RGBA")
    px_flood = flood.load()
    px_out = out.load()
    cleared = 0
    for y in range(h):
        for x in range(w):
            if px_flood[x, y] == SENTINEL:
                px_out[x, y] = (0, 0, 0, 0)
                cleared += 1
    print(f"  flood fill cleared {cleared * 100 // (w * h)}% of the crop")

    # Trim back to the ink now that the background is gone, so scaling is predictable.
    bbox = out.getbbox()
    out = out.crop(bbox)
    print(f"  mark trimmed to {out.size[0]}x{out.size[1]}")
    return out


def centred(mark: Image.Image, size: int, fraction: float,
            bg: tuple | None) -> Image.Image:
    """Scale the mark to `fraction` of `size` and centre it on a `size` square."""
    m = mark.copy()
    target = int(size * fraction)
    m.thumbnail((target, target), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), bg if bg else (0, 0, 0, 0))
    canvas.alpha_composite(m, ((size - m.width) // 2, (size - m.height) // 2))
    return canvas


def save(img: Image.Image, name: str, flatten: tuple | None = None) -> None:
    path = os.path.join(OUT, name)
    if flatten:
        flat = Image.new("RGB", img.size, flatten)
        flat.paste(img, (0, 0), img)
        flat.save(path, format="PNG", optimize=True)
    else:
        img.save(path, format="PNG", optimize=True)
    kb = os.path.getsize(path) // 1024
    print(f"  wrote {name:<34} {img.size[0]}x{img.size[1]}  {kb} KB")


def main() -> None:
    print("building icons from the logo\n")
    mark = mark_with_alpha()
    print()

    # Android adaptive foreground.
    #
    # 0.48 is measured, not chosen by eye. The launcher masks the foreground to a
    # circle: the middle 66% of the canvas is what a circular mask shows, and the
    # inner 61% is the safe zone Android actually guarantees. This mark's ink reaches
    # 393px from its own centre at 597px wide, so 0.58 - the first attempt - put the
    # K's patterned bar and the tips of the green leaf outside the circle and sliced
    # them off. 0.50 touches the 66% boundary exactly; 0.48 sits just inside the safe
    # zone with a little air, which is what an icon wants anyway.
    save(centred(mark, 1024, 0.48, None), "android-icon-foreground.png")

    # The layer behind it. Flat cream, the logo's own background, so the icon reads the
    # way the logo does. The file that was here was Expo's default pale blue.
    save(Image.new("RGBA", (1024, 1024), CREAM + (255,)), "android-icon-background.png")

    # Monochrome, for themed icons on Android 13+. Silhouette of the mark in one tone;
    # the system recolours it, so only the alpha matters.
    mono = centred(mark, 1024, 0.48, None)
    solid = Image.new("RGBA", mono.size, (60, 40, 25, 255))
    solid.putalpha(mono.getchannel("A"))
    save(solid, "android-icon-monochrome.png")

    # iOS and the general icon are not masked to a circle, only slightly rounded, so
    # the mark can sit larger. Flattened onto cream because iOS rejects transparency
    # in an app icon.
    save(centred(mark, 1024, 0.76, CREAM + (255,)), "icon.png", flatten=CREAM)

    # Browser tab.
    save(centred(mark, 256, 0.80, CREAM + (255,)), "favicon.png", flatten=CREAM)

    # splash-icon.png is deliberately not touched. The splash has room for the full
    # lockup, which is what it is for.
    print("\n  left alone: splash-icon.png (the full logo, correct there)")


if __name__ == "__main__":
    main()
