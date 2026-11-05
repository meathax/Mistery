#!/usr/bin/env python3
"""Draw the Mistery app icon (pixel art: glitched neon 'M' in an FPGA chip outline, scanlines).

Writes packaging/mistery.ico (16..256 px) and packaging/mistery.png (256 px, used by the GUI).
Needs Pillow at build time only. Usage: py -3.14 rbf2src/packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
G = 32                                     # pixel-art grid
BG, CHIP, PIN = (4, 6, 4), (14, 22, 14), (40, 90, 40)
GREEN, CYAN, MAGENTA = (57, 255, 20), (0, 220, 255), (255, 0, 170)

# 'M' on a 16x14 cell grid (1 = lit)
M = ["1100000000000011",
     "1110000000000111",
     "1111000000001111",
     "1111100000011111",
     "1101110000111011",
     "1100111001110011",
     "1100011111100011",
     "1100001111000011",
     "1100000110000011",
     "1100000000000011",
     "1100000000000011",
     "1100000000000011",
     "1100000000000011",
     "1100000000000011"]


def draw(size):
    img = Image.new("RGB", (G, G), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, 27, 27], fill=CHIP)                       # FPGA package
    for k in range(6, 27, 3):                                    # pins on all four sides
        for x0, y0, x1, y1 in ((k, 1, k, 3), (k, 28, k, 30), (1, k, 3, k), (28, k, 30, k)):
            d.rectangle([x0, y0, x1, y1], fill=PIN)
    d.rectangle([4, 4, 27, 27], outline=PIN)
    ox, oy = 8, 9
    for y, row in enumerate(M):                                  # glitch: magenta left, cyan right, green on top
        for x, ch in enumerate(row):
            if ch == "1":
                img.putpixel((ox + x - 1, oy + y), MAGENTA)
    for y, row in enumerate(M):
        for x, ch in enumerate(row):
            if ch == "1":
                img.putpixel((ox + x + 1, oy + y), CYAN)
    for y, row in enumerate(M):
        for x, ch in enumerate(row):
            if ch == "1":
                img.putpixel((ox + x, oy + y), GREEN)
    for y in (12, 17):                                           # horizontal glitch slices
        row = [img.getpixel((x, y)) for x in range(G)]
        for x in range(G):
            img.putpixel((x, y), row[(x - 2) % G])
    big = img.resize((size, size), Image.NEAREST)
    if size >= 64:                                               # CRT scanlines
        sd = ImageDraw.Draw(big)
        step = size // G
        for y in range(0, size, step):
            sd.line([(0, y + step - 1), (size, y + step - 1)], fill=(0, 0, 0))
    return big


def main():
    base = draw(256)
    base.save(HERE / "mistery.png")
    base.save(HERE / "mistery.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", HERE / "mistery.ico", HERE / "mistery.png")


if __name__ == "__main__":
    main()
