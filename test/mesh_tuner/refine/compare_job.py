"""Validation driver (embedded backend Python): render original vs tuned close-ups of a finished run.

python -s compare_job.py <job_id> <run_dir_name> [--extra '<json views>']
Views are derived from the run's report (head / eyes / symmetry clusters) plus fixed body views; output in
test/outputs/mesh_tuner/refine/compare/<run_dir_name>/ with *_cmp.png side-by-sides.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refine_testlib import HELPERS_DIR, OUTPUT_ROOT, job_glb, run_blender  # noqa: E402
from run_face_debug import side_by_side  # noqa: E402

FRONT = [0, -1, 0]
TQ = [0.55, -0.8, 0.12]


def views_from_report(report: dict) -> dict:
    steps = {s["id"]: s for s in report.get("steps", [])}
    face = steps.get("face_refinement", {}).get("details", {})
    views: dict = {"body_front": [[0, 0, 0], 1.08, FRONT, "matcap"],
                   "body_back": [[0, 0, 0], 1.08, [0, 1, 0], "matcap"],
                   "legs_front": [[0.0, -0.05, -0.22], 0.36, FRONT, "matcap"],
                   "skirt_34": [[0.0, -0.05, 0.05], 0.45, TQ, "matcap"],
                   "back_hair": [[0.0, 0.05, 0.1], 0.6, [0, 1, 0], "matcap"]}
    head = face.get("head") or {}
    if head.get("found") and face.get("humanoid"):
        h = head["z_top"] - head["z_neck"]
        tgt = [head["center_x"], head["center_y"] - 0.3 * h, head["z_neck"] + 0.45 * h]
        sc = max(1.0 * h, 2.2 * head["half_width"])
        views.update({"face_front": [tgt, sc, FRONT, "matcap"], "face_34": [tgt, sc, TQ, "matcap"],
                      "face_tex": [tgt, sc, FRONT, "texture"], "face_tex_34": [tgt, sc, TQ, "texture"],
                      "face_flat": [tgt, sc, FRONT, "flat_matcap"]})
        for i, e in enumerate((face.get("eyes") or {}).get("eyes") or []):
            c = e["center"]
            views[f"eye{i}"] = [[c[0], c[1] - 0.05, c[2]], 0.3 * sc, FRONT, "matcap"]
    rep = ((face.get("symmetry") or {}).get("repair") or {}).get("repaired") or []
    for i, cl in enumerate(rep[:6]):
        c = cl["centroid"]
        ext = max(0.04, 2.5 * cl["extent_pct"] / 100.0 * 1.33)
        d = [0, -1, 0] if c[1] < 0 else [0, 1, 0]
        if abs(c[0]) > 0.15:
            d = [1 if c[0] > 0 else -1, 0, 0]
        views[f"sym{i}"] = [c, ext, d, "matcap"]
    return views


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("run")
    ap.add_argument("--extra", default="{}")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    run_dir = OUTPUT_ROOT / "runs" / a.run
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    views = views_from_report(report)
    views.update(json.loads(a.extra))
    if a.only:
        keep = set(a.only.split(","))
        views = {k: v for k, v in views.items() if k in keep}
    out = OUTPUT_ROOT / "compare" / a.run
    out.mkdir(parents=True, exist_ok=True)
    run = run_blender(HELPERS_DIR / "compare_render.py",
                      ["--original", job_glb(a.job), "--tuned", run_dir / "tuned.glb", "--out", out,
                       "--views", json.dumps(views)], tag=f"compare_{a.run}", timeout=900)
    if run.returncode != 0:
        print(run.tail(30))
        return 1
    side_by_side(out)
    print("views:", ", ".join(views))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
