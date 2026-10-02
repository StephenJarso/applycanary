#!/usr/bin/env python3
"""Generate ApplyCanary brand assets for the Capacitor shells (Android + iOS).

Draws the canary mark and splash lockup with PIL only — no network, no binary
assets tracked in git. Every output file is (re)created at the exact path AND
pixel size of the placeholder that `cap add` produced: the script walks the
Android res/ folders and the iOS asset catalog, reads each existing image's
size, and overwrites it in place. Run it again any time the platforms are
re-added or resized:

    cd frontend && python3 scripts/generate_assets.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FRONTEND = Path(__file__).resolve().parent.parent

# DESIGN.md palette.
INDIGO = (80, 0, 225)        # --ac-primary
INDIGO_DEEP = (30, 0, 97)    # on-primary-fixed
INDIGO_SOFT = (105, 51, 255) # --ac-primary-hover
CANARY = (252, 212, 0)       # --ac-canary
CANARY_FIXED = (255, 225, 109)  # --ac-canary-fixed (lighter, for wing layers)
CANARY_DEEP = (232, 196, 0)
WHITE = (255, 255, 255)
SURFACE = (248, 249, 250)    # --ac-surface
INK = (25, 28, 29)           # --ac-on-surface

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


# ---------------------------------------------------------------- widgets

def rounded_rect(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float],
                 radius: float, fill: tuple[int, int, int, int] | tuple[int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def draw_bird(draw: ImageDraw.ImageDraw, cx: float, cy: float, s: float,
              body: tuple[int, int, int], wing: tuple[int, int, int],
              beak: tuple[int, int, int]) -> None:
    """The flat canary mark: circle head, triangle beak, tail, wing.

    (cx, cy) is the visual centre of the bird; s scales it (head radius ≈ s).
    """
    # Tail — a single folded parallelogram off the body's rear.
    x0, y0 = cx - 1.1 * s, cy + 0.7 * s
    draw.polygon(
        [(x0, y0), (x0 - 2.0 * s, y0 + 1.15 * s), (x0 - 1.55 * s, y0 + 1.75 * s), (x0 + 0.1 * s, y0 + 0.9 * s)],
        fill=body,
    )
    # Body — a plump ellipse leaning forward.
    draw.ellipse((cx - 1.5 * s, cy - 0.9 * s, cx + 1.35 * s, cy + 1.45 * s), fill=body)
    # Head.
    head_r = 0.95 * s
    hx, hy = cx + 0.85 * s, cy - 0.75 * s
    draw.ellipse((hx - head_r, hy - head_r, hx + head_r, hy + head_r), fill=body)
    # Crest — small notch of feathers on top of the head.
    draw.polygon(
        [(hx - 0.1 * s, hy - 0.9 * s), (hx + 0.25 * s, hy - 1.6 * s), (hx + 0.55 * s, hy - 0.75 * s)],
        fill=body,
    )
    # Beak — small triangle pointing right.
    draw.polygon(
        [(hx + 0.8 * s, hy - 0.2 * s), (hx + 1.75 * s, hy + 0.12 * s), (hx + 0.8 * s, hy + 0.45 * s)],
        fill=beak,
    )
    # Eye.
    er = 0.15 * s
    draw.ellipse((hx + 0.18 * s - er, hy - 0.18 * s - er, hx + 0.18 * s + er, hy - 0.18 * s + er), fill=INDIGO_DEEP)
    # Wing — a soft folded-wing shape hugging the body's lower half (drawn
    # after the body so it reads as a layer ON the bird, not a hole in it).
    draw.polygon(
        [
            (cx - 1.05 * s, cy + 0.15 * s),
            (cx + 0.45 * s, cy - 0.05 * s),
            (cx + 0.6 * s, cy + 0.55 * s),
            (cx - 0.2 * s, cy + 1.3 * s),
            (cx - 0.95 * s, cy + 0.8 * s),
        ],
        fill=wing,
    )


def draw_wave_mark(draw: ImageDraw.ImageDraw, cx: float, cy: float, s: float,
                   color: tuple[int, int, int]) -> None:
    """Five voice-bars — the AI Interview Studio motif, used on the splash."""
    heights = (0.5, 0.9, 1.4, 0.9, 0.5)
    w = 0.34 * s
    gap = 0.3 * s
    total = 5 * w + 4 * gap
    x = cx - total / 2
    for h in heights:
        draw.rounded_rectangle(
            (x, cy - h * s, x + w, cy + h * s),
            radius=w / 2,
            fill=color,
        )
        x += w + gap


# ------------------------------------------------------------ icon pieces

def icon_canvas(size: int, bg: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (size, size), bg + (255,))
    return img


def draw_launcher_icon(size: int) -> Image.Image:
    """Full-bleed launcher icon: indigo field, canary bird, canary dot ring."""
    img = icon_canvas(size, INDIGO)
    draw = ImageDraw.Draw(img)
    u = size / 100  # design unit

    # Faint orbit ring + canary satellite, echoing the landing hero art.
    r = 40 * u
    draw.ellipse((50 * u - r, 52 * u - r, 50 * u + r, 52 * u + r),
                 outline=(255, 255, 255, 46), width=max(1, int(1.6 * u)))
    sat = 4.4 * u
    sx = 50 * u + r * math.cos(math.radians(215))
    sy = 52 * u + r * math.sin(math.radians(215))
    draw.ellipse((sx - sat, sy - sat, sx + sat, sy + sat), fill=CANARY)

    # Bird, centred slightly high to optically balance the tail.
    draw_bird(draw, 47 * u, 44 * u, 9.2 * u, body=CANARY, wing=WHITE, beak=WHITE)
    return img


def draw_foreground(size: int) -> Image.Image:
    """Adaptive-icon foreground: bird inside the safe zone, transparent elsewhere.

    The canvas is 108dp where only the middle ~66dp is guaranteed visible, so
    the mark is drawn small and centred; the background layer is solid indigo.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    u = size / 108  # adaptive grid: 108dp canvas
    draw_bird(draw, 54 * u, 54 * u, 10.5 * u, body=CANARY, wing=WHITE, beak=WHITE)
    return img


def draw_splash(w: int, h: int, dark: bool) -> Image.Image:
    """Centered splash: wordmark + bird + voice-bars, generous whitespace.

    Content is sized off min(w, h) so portrait and landscape both balance.
    """
    bg = INDIGO if not dark else INDIGO_DEEP
    ink = WHITE
    accent = CANARY
    img = Image.new("RGBA", (w, h), bg + (255,))
    draw = ImageDraw.Draw(img)
    m = min(w, h)

    cx, cy = w / 2, h / 2
    scale = m / 480  # design was laid out against a 480px short side

    # Bird above the wordmark, sized and placed so the tail clears the text.
    bird_cy = cy - 128 * scale
    draw_bird(draw, cx - 16 * scale, bird_cy, 30 * scale, body=accent, wing=CANARY_FIXED, beak=ink)

    # Wordmark.
    try:
        font = ImageFont.truetype(FONT_BOLD, int(44 * scale))
    except OSError:
        font = ImageFont.load_default()
    label = "ApplyCanary"
    tw = draw.textlength(label, font=font)
    ty = bird_cy + 84 * scale
    draw.text((cx - tw / 2, ty), label, font=font, fill=ink)

    # Tagline.
    try:
        sub = ImageFont.truetype(FONT_BOLD, int(15 * scale))
    except OSError:
        sub = font
    tag = "YOUR AI CAREER AGENT"
    sw = draw.textlength(tag, font=sub)
    draw.text((cx - sw / 2, ty + 62 * scale), tag, font=sub, fill=(255, 255, 255, 150))

    # Voice-bars motif under the lockup.
    draw_wave_mark(draw, cx, ty + 118 * scale, 26 * scale, color=accent)
    return img


# ------------------------------------------------------------- generation

def generate_android() -> None:
    res = FRONTEND / "android" / "app" / "src" / "main" / "res"
    if not res.exists():
        print("skip android (no res/)")
        return

    # Solid adaptive-icon background colour.
    (res / "values" / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<resources>\n"
        "    <color name=\"ic_launcher_background\">#5000E1</color>\n"
        "</resources>\n"
    )

    for path in sorted(res.rglob("*.png")):
        rel = path.relative_to(res).as_posix()
        name = path.name
        with Image.open(path) as probe:
            size = (probe.width, probe.height)
        if name == "ic_launcher_foreground.png":
            img = draw_foreground(max(size))
        elif name in ("ic_launcher.png", "ic_launcher_round.png"):
            img = draw_launcher_icon(max(size))
            if name == "ic_launcher_round.png":
                # Round launchers get a circular mask.
                mask = Image.new("L", (img.width, img.height), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, img.width, img.height), fill=255)
                img.putalpha(mask)
        elif name == "splash.png":
            img = draw_splash(size[0], size[1], dark=False)
        else:
            continue
        img.save(path)
        print(f"android: {rel} {img.width}x{img.height}")


def generate_ios() -> None:
    catalog = FRONTEND / "ios" / "App" / "App" / "Assets.xcassets"
    if not catalog.exists():
        print("skip ios (no asset catalog)")
        return

    icons = catalog / "AppIcon.appiconset"
    for path in sorted(icons.glob("*.png")):
        with Image.open(path) as probe:
            size = (probe.width, probe.height)
        img = draw_launcher_icon(max(size))
        img.save(path)
        print(f"ios: icon {path.name} {img.width}x{img.height}")

    splashes = catalog / "Splash.imageset"
    names = {p.name for p in splashes.glob("*.png")}
    # All three Contents.json entries are the same 2732x2732 canvas.
    for name in sorted(names):
        img = draw_splash(2732, 2732, dark=False)
        img.save(splashes / name)
        print(f"ios: splash {name} 2732x2732")


def main() -> None:
    generate_android()
    generate_ios()
    print("done.")


if __name__ == "__main__":
    main()
