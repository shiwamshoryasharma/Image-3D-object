"""Validation driver (embedded backend Python): run the full tuner CLI on real jobs and summarise the
face_refinement / topology_tuning results.

python -s run_job.py <job_id|all> [--options '<json>'] [--tag suffix]
Writes test/outputs/mesh_tuner/refine/runs/<job>[_suffix]/ (tuned.glb, report.json, previews) and prints a summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refine_testlib import OUTPUT_ROOT, VALIDATION_JOBS, job_glb, run_tuner  # noqa: E402


def summarize(job: str, run, out: Path) -> dict:
    rep_path = out / "report.json"
    rep = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.is_file() else {}
    steps = {s["id"]: s for s in rep.get("steps", [])}
    metrics = {m["key"]: (m.get("before"), m.get("after")) for m in rep.get("metrics", [])}
    summary = {"job": job, "rc": run.returncode, "wall_s": round(run.duration_s, 1),
               "status": (run.result or {}).get("status"),
               "steps": {k: {"status": v.get("status"), "s": v.get("duration_s"), "msg": v.get("message")}
                         for k, v in steps.items()},
               "deviation": {k: metrics.get(k) for k in ("deviation_mean_pct", "deviation_p95_pct",
                                                         "deviation_max_pct")},
               "faces": metrics.get("faces"), "warnings": rep.get("warnings")}
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--options", default="{}")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    jobs = VALIDATION_JOBS if a.job == "all" else tuple(a.job.split(","))
    options = json.loads(a.options)
    rows = []
    for job in jobs:
        out = OUTPUT_ROOT / "runs" / (job + (f"_{a.tag}" if a.tag else ""))
        run = run_tuner(job_glb(job), out, options, tag=f"{job}{('_' + a.tag) if a.tag else ''}")
        s = summarize(job, run, out)
        rows.append(s)
        print(json.dumps(s, indent=1))
        if run.returncode != 0:
            print(run.tail(30))
    (OUTPUT_ROOT / "runs").mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / "runs" / f"summary{('_' + a.tag) if a.tag else ''}.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
