"""Draw the YakuPatch icon with Pillow (no fonts, so no font licence is involved): a speech bubble on a rounded
gradient square, with an adhesive patch across it. Writes yakupatch/gui/icon.ico (16-256 px) and icon.png (512 px).
Run: python scripts/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024                                           # drawn large, then downsampled for smooth edges
OUT = Path(__file__).resolve().parent.parent / "yakupatch" / "gui"
TOP, BOTTOM = (91, 76, 230), (236, 72, 153)        # indigo to pink


def gradient() -> Image.Image:
    g = Image.new("RGB", (1, S))
    for y in range(S):
        t = y / (S - 1)
        g.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    return g.resize((S, S))


def icon() -> Image.Image:
    img = Image.new("RGBA", (S, S))
    mask = Image.new("L", (S, S))
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=220, fill=255)
    img.paste(gradient(), mask=mask)

    shadow = Image.new("RGBA", (S, S))
    sd = ImageDraw.Draw(shadow)
    bubble = (190, 220, 834, 700)
    tail = [(330, 680), (300, 860), (500, 690)]
    sd.rounded_rectangle([v + 18 for v in bubble], radius=150, fill=(30, 20, 80, 90))
    sd.polygon([(x + 18, y + 18) for x, y in tail], fill=(30, 20, 80, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(24)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle(bubble, radius=150, fill="white")
    d.polygon(tail, fill="white")

    # The patch: a tilted bandage over the bubble, pad in the middle with holes.
    patch = Image.new("RGBA", (S, S))
    pd = ImageDraw.Draw(patch)
    pd.rounded_rectangle((220, 395, 804, 525), radius=65, fill=(255, 196, 92))
    pd.rounded_rectangle((420, 395, 604, 525), radius=10, fill=(255, 226, 160))
    for cx in (462, 512, 562):
        for cy in (432, 488):
            pd.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), fill=(232, 160, 60))
    img.alpha_composite(patch.rotate(-28, center=(512, 460), resample=Image.BICUBIC))
    return img


def main():
    big = icon()
    big.resize((512, 512), Image.LANCZOS).save(OUT / "icon.png")
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote", OUT / "icon.ico", OUT / "icon.png")


if __name__ == "__main__":
    main()
