"""Exploration (test only, embedded backend Python + PIL): side-by-side before/after PNGs.

python -s sbs.py <dir> [names...]  -> <dir>/<name>_cmp.png from <name>_before.png + <name>_after.png
"""
import sys
from pathlib import Path

from PIL import Image

d = Path(sys.argv[1])
names = sys.argv[2:] or sorted({p.name[:-len("_before.png")] for p in d.glob("*_before.png")})
for n in names:
    a = Image.open(d / f"{n}_before.png").convert("RGB")
    b = Image.open(d / f"{n}_after.png").convert("RGB")
    c = Image.new("RGB", (a.width + b.width + 6, max(a.height, b.height)), (255, 0, 0))
    c.paste(a, (0, 0))
    c.paste(b, (a.width + 6, 0))
    c.save(d / f"{n}_cmp.png")
    print("wrote", d / f"{n}_cmp.png")
