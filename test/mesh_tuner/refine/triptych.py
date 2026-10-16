"""Validation helper (embedded backend Python + PIL): original | run A | run B triptychs of compare renders.

python -s triptych.py <compare_dir_A> <compare_dir_B> <out_dir> [view ...]
Column 1 = A/<view>_before.png (original), 2 = A/<view>_after.png, 3 = B/<view>_after.png.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

a, b, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
views = sys.argv[4:] or sorted(p.name[:-len("_before.png")] for p in a.glob("*_before.png"))
for v in views:
    paths = [a / f"{v}_before.png", a / f"{v}_after.png", b / f"{v}_after.png"]
    if not all(p.is_file() for p in paths):
        continue
    ims = [Image.open(p).convert("RGB").resize((600, 600)) for p in paths]
    c = Image.new("RGB", (1812, 630), (30, 30, 30))
    d = ImageDraw.Draw(c)
    for i, (im, label) in enumerate(zip(ims, ("original", a.name, b.name))):
        c.paste(im, (i * 606, 30))
        d.text((i * 606 + 8, 8), label, fill=(255, 255, 0))
    c.save(out / f"{v}_tri.png")
print("ok", len(views))
