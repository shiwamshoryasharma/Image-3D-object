"""Probe (repair agent): instrument holes.fill_holes during a real repair run; dump rim data + per-hole timings.

blender ... --python holes_probe.py -- --scripts <dir> --dump <npz> -- <tuner args>
"""
import pickle
import sys
import time

argv = sys.argv[sys.argv.index("--") + 1:]
split = argv.index("--")
own, tuner_args = argv[:split], argv[split + 1:]
sys.path.insert(0, own[own.index("--scripts") + 1])
dump = own[own.index("--dump") + 1]

import numpy as np  # noqa: E402
from img3d_amt import cli, holes  # noqa: E402
from img3d_amt.context import StepOutcome  # noqa: E402
import importlib  # noqa: E402

for name in ("face", "topology", "rig"):
    importlib.import_module(f"img3d_amt.{name}").run = lambda ctx: StepOutcome("skipped", "off", {})

orig_fill = holes.fill_holes
orig_build = holes.build_patch
records = []


def build_wrapper(rim, rim_uv, normal, target, outer=None):
    t0 = time.perf_counter()
    res = orig_build(rim, rim_uv, normal, target, outer)
    dt = time.perf_counter() - t0
    rec = {"n": len(rim), "dt": dt, "ok": res is not None, "rim": np.array(rim), "normal": np.array(normal),
           "target": target, "uv": None if rim_uv is None else np.array(rim_uv),
           "outer": [np.array(o) for o in outer] if outer is not None else None}
    if res is not None:
        rec["verts"] = len(res[0])
        rec["tris"] = len(res[2])
    records.append(rec)
    return res


holes.build_patch = build_wrapper
code = cli.main(tuner_args)
with open(dump, "wb") as fh:
    pickle.dump(records, fh)
dts = np.array([r["dt"] for r in records])
ns = np.array([r["n"] for r in records])
ok = np.array([r["ok"] for r in records])
print("PROBE holes", len(records), "ok", int(ok.sum()), "total build s", round(float(dts.sum()), 2))
for lo, hi in ((3, 6), (6, 12), (12, 30), (30, 80), (80, 400)):
    s = (ns >= lo) & (ns < hi)
    if s.any():
        print(f"  n in [{lo},{hi}): {int(s.sum())} holes, ok {int(ok[s].sum())}, time {dts[s].sum():.2f}s, max {dts[s].max():.3f}s")
sys.exit(code)
