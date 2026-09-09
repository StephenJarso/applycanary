"""Generate ApplyCanary app icon as ICO and PNG files.

No external SVG renderer needed — draws the icon with Pillow directly.
The design matches the brand mark in the sidebar: indigo circle with a
white 'A' and canary yellow accent.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZES = [16, 32, 48, 64, 128, 256]
OUT_DIR = Path("assets")


def draw_icon(size: int) -> Image.Image:
    """Render one size of the icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded-rect background (approximate with a large-radius oval)
    margin = max(1, size // 16)
    r = max(2, size // 6)  # corner radius
    draw.rounded_rectangle(
        [margin, margin, size - margin - 1, size - margin - 1],
        radius=r,
        fill=(80, 0, 225),  # #5000E1
    )

    # Canary yellow accent bar at bottom
    bar_h = max(2, size // 16)
    bar_y = size - margin - bar_h - max(2, size // 10)
    bar_x1 = margin + max(4, size // 6)
    bar_x2 = size - margin - max(4, size // 6)
    draw.rounded_rectangle(
        [bar_x1, bar_y, bar_x2, bar_y + bar_h],
        radius=max(1, bar_h // 2),
        fill=(252, 212, 0),  # #FCD400
    )

    # Letter "A" — try to use a bold font, fall back to drawing
    letter = "A"
    font_size = int(size * 0.52)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), letter, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2
    ty = (size - th) // 2 - max(2, size // 16)  # shift up slightly
    draw.text((tx, ty), letter, fill=(255, 255, 255), font=font)

    # Canary yellow dot (eye) above the crossbar
    dot_r = max(2, size // 16)
    dot_cx = size // 2
    dot_cy = int(size * 0.42)
    draw.ellipse(
        [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
        fill=(252, 212, 0),
    )

    return img


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    # Generate PNGs at each size
    images = {}
    for s in SIZES:
        img = draw_icon(s)
        images[s] = img
        img.save(OUT_DIR / f"icon_{s}.png")

    # Build multi-size ICO
    ico_images = [images[s] for s in SIZES]
    ico_images[0].save(
        OUT_DIR / "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=ico_images[1:],
    )

    # Also save a 256px PNG for web favicons
    images[256].save(OUT_DIR / "icon_256.png")

    print(f"Generated {OUT_DIR / 'icon.ico'} with sizes: {SIZES}")
    print(f"Generated {OUT_DIR / 'icon_256.png'}")


if __name__ == "__main__":
    main()
