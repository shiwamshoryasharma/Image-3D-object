"""Validation driver (embedded backend Python): run blender_helpers/face_debug.py on real jobs (2 in parallel) and
build before/after side-by-side images.

python -s run_face_debug.py <job,job,...|all> [--no-render]
Outputs: test/outputs/mesh_tuner/refine/facedebug/<job>/ ; summary JSON printed + facedebug/summary.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refine_testlib import HELPERS_DIR, OUTPUT_ROOT, SCRIPTS_DIR, VALIDATION_JOBS, job_glb, run_blender  # noqa: E402


def side_by_side(folder: Path) -> None:
    from PIL import Image

    for before in sorted(folder.glob("*_before.png")):
        after = before.with_name(before.name.replace("_before.png", "_after.png"))
        if not after.is_file():
            continue
        a = Image.open(before).convert("RGB")
        b = Image.open(after).convert("RGB")
        c = Image.new("RGB", (a.width + b.width + 6, max(a.height, b.height)), (255, 0, 0))
        c.paste(a, (0, 0))
        c.paste(b, (a.width + 6, 0))
        c.save(before.with_name(before.name.replace("_before.png", "_cmp.png")))


def one(job: str, render: bool) -> dict:
    out = OUTPUT_ROOT / "facedebug" / job
    out.mkdir(parents=True, exist_ok=True)
    args = ["--scripts", SCRIPTS_DIR, "--input", job_glb(job), "--out", out]
    if not render:
        args.append("--no-render")
    run = run_blender(HELPERS_DIR / "face_debug.py", args, tag=f"facedebug_{job}", timeout=900)
    rows = [json.loads(line[line.index("@@FACEDEBUG@@") + 13:]) for line in run.stdout.splitlines()
            if "@@FACEDEBUG@@" in line]
    res = rows[-1] if rows else {"error": run.tail(20)}
    res["rc"] = run.returncode
    res["wall_s"] = round(run.duration_s, 1)
    if render:
        side_by_side(out)
    return {job: res}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    jobs = VALIDATION_JOBS if a.jobs == "all" else tuple(a.jobs.split(","))
    summary: dict = {}
    with ThreadPoolExecutor(2) as pool:
        for res in pool.map(lambda j: one(j, not a.no_render), jobs):
            summary.update(res)
            print(json.dumps(res, indent=1)[:6000])
    (OUTPUT_ROOT / "facedebug").mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / "facedebug" / "summary.json").write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
