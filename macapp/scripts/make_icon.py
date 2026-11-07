"""Draws Reeper's app icon and writes the AppIcon set (macOS sizes) into Assets.xcassets.

    python macapp/scripts/make_icon.py
"""
import json
import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "Reeper", "Resources", "Assets.xcassets")
S = 1024


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_master():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # macOS icon grid: an 824-pt rounded square centred on the 1024 canvas.
    body = (100, 100, 924, 924)
    radius = 185

    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((100, 116, 924, 940), radius, fill=(0, 0, 0, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))

    top, bottom = (43, 16, 85), (213, 58, 157)   # indigo -> magenta
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=lerp(top, bottom, t) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(body, radius, fill=255)
    img.alpha_composite(Image.composite(grad, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))

    # Soft highlight at the top.
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((180, 40, 844, 520), fill=(255, 255, 255, 46))
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    glow.putalpha(ImageChops.multiply(glow.getchannel("A"), mask))
    img.alpha_composite(glow)

    # Waveform: rounded white bars, tallest in the middle.
    d = ImageDraw.Draw(img)
    heights = [150, 270, 390, 520, 390, 300, 190]
    bar_w, gap = 62, 34
    total = len(heights) * bar_w + (len(heights) - 1) * gap
    x = (S - total) // 2
    cy = 540
    for h in heights:
        d.rounded_rectangle((x, cy - h // 2, x + bar_w, cy + h // 2), bar_w // 2, fill=(255, 255, 255, 245))
        x += bar_w + gap

    # Four-point sparkle, top right: "mood".
    cx, cy, r = 742, 262, 92
    pts = []
    for i in range(8):
        ang = math.pi / 4 * i - math.pi / 2
        rad = r if i % 2 == 0 else r * 0.28
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    d.polygon(pts, fill=(255, 236, 160, 255))
    return img


def main():
    master = draw_master()
    iconset = os.path.join(ASSETS, "AppIcon.appiconset")
    os.makedirs(iconset, exist_ok=True)
    images = []
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            master.resize((px, px), Image.LANCZOS).save(os.path.join(iconset, name))
            images.append({"idiom": "mac", "size": f"{size}x{size}", "scale": f"{scale}x", "filename": name})
    with open(os.path.join(iconset, "Contents.json"), "w") as f:
        json.dump({"images": images, "info": {"author": "xcode", "version": 1}}, f, indent=2)

    accent = os.path.join(ASSETS, "AccentColor.colorset")
    os.makedirs(accent, exist_ok=True)
    with open(os.path.join(accent, "Contents.json"), "w") as f:
        json.dump({"colors": [{"idiom": "universal", "color": {"color-space": "srgb", "components": {
            "red": "0.835", "green": "0.227", "blue": "0.616", "alpha": "1.000"}}}],
            "info": {"author": "xcode", "version": 1}}, f, indent=2)
    with open(os.path.join(ASSETS, "Contents.json"), "w") as f:
        json.dump({"info": {"author": "xcode", "version": 1}}, f, indent=2)
    if len(sys.argv) > 1:
        master.save(sys.argv[1])


if __name__ == "__main__":
    main()
