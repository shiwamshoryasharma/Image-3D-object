"""Run the geometry-repair validation on real TRELLIS jobs and write a summary table.

  backend\\python\\python.exe -s test\\mesh_tuner\\repair\\run_real_jobs.py [job ...] [--no-renders] [--full]

Jobs default to all real jobs (never job_269a864ac597) + the engine_mechanics smoke mesh ("smoke").
Per job: test/outputs/mesh_tuner/repair/runs/<job>/ (tuned.glb, report.json, events.json, compare/*.png|json).
Summary: test/outputs/mesh_tuner/repair/summary.json + summary.md.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from repair_testlib import (EXCLUDED_JOBS, OUTPUT_ROOT, REAL_JOBS, compare, job_glb, repair_details,  # noqa: E402
                            report_metrics, run_repair)

METRIC_KEYS = ("faces", "vertices", "holes", "boundary_edges", "non_manifold_edges", "components",
               "small_components", "inconsistent_normal_edges", "inner_shell_faces", "hidden_faces",
               "deviation_p95_pct")


def process(job: str, *, renders: bool = True, full: bool = False) -> dict[str, Any]:
    if job in EXCLUDED_JOBS:
        raise ValueError(f"{job} is excluded")
    out = OUTPUT_ROOT / "runs" / job
    t0 = time.perf_counter()
    run = run_repair(job_glb(job), out, full=full, tag=f"repair_{job}")
    row: dict[str, Any] = {"job": job, "rc": run.returncode, "pipeline_s": round(run.duration_s, 1)}
    if run.returncode != 0:
        row["error"] = run.tail(30)
        return row
    det = repair_details(run)
    step = run.step("geometry_repair") or {}
    row.update({"repair_status": step.get("status"), "repair_message": step.get("message"),
                "repair_s": step.get("duration_s"), "timings_s": det.get("timings_s"),
                "memory_mb": det.get("memory_mb"), "visibility": det.get("visibility"),
                "inner_shell": {k: v for k, v in (det.get("inner_shell") or {}).items() if k != "safety"},
                "safety": (det.get("inner_shell") or {}).get("safety"),
                "holes_detail": {k: v for k, v in (det.get("holes") or {}).items()
                                 if k not in ("largest_filled", "largest_skipped")},
                "islands": det.get("islands"), "non_manifold": det.get("non_manifold"), "normals": det.get("normals")})
    metrics = report_metrics(out)
    row["metrics"] = {k: {"before": metrics[k].get("before"), "after": metrics[k].get("after")}
                      for k in METRIC_KEYS if k in metrics}
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    row["validation"] = {c["id"]: c["status"] for c in report["validation"]["checks"]}
    filled = (det.get("holes") or {}).get("largest_filled") or []
    cmp = compare(job_glb(job), out / "tuned.glb", out / "compare", tag=f"compare_{job}", holes=filled,
                  renders=renders)
    row["compare"] = {k: cmp.get(k) for k in ("double_wall_fraction_before", "double_wall_fraction_after", "slices",
                                              "humanoid", "faces_before", "faces_after")}
    row["depth"] = cmp["depth"]["summary"]
    row["total_s"] = round(time.perf_counter() - t0, 1)
    return row


def markdown(rows: list[dict[str, Any]]) -> str:
    head = ("| job | faces before -> after | ratio | holes b -> a | non-manifold b -> a | parts b -> a | "
            "inner shell | hidden b -> a | depth mean/p99/max % diag | receded / lost px | repair s | peak MB |")
    lines = [head, "|" + "---|" * 12]
    for r in rows:
        if r.get("rc") != 0:
            lines.append(f"| {r['job']} | FAILED rc={r.get('rc')} |" + " |" * 10)
            continue
        m = r["metrics"]

        def ba(key: str) -> str:
            v = m.get(key, {})
            return f"{v.get('before')} -> {v.get('after')}"

        fb, fa = m["faces"]["before"], m["faces"]["after"]
        d = r["depth"]
        lines.append(
            f"| {r['job']} | {fb:,} -> {fa:,} | {fa / fb:.2f} | {ba('holes')} | {ba('non_manifold_edges')} | "
            f"{ba('components')} | {r['inner_shell'].get('inner_faces', 0):,} | {ba('hidden_faces')} | "
            f"{d['mean_abs_pct']}/{d['p99_abs_pct']}/{d['max_abs_pct']} | {d['receded_frac']}/{d['silhouette_lost_frac']} | "
            f"{r.get('repair_s')} | {(r.get('memory_mb') or {}).get('peak_private')} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    renders = "--no-renders" not in argv
    full = "--full" in argv
    jobs = [a for a in argv if not a.startswith("--")] or [*REAL_JOBS, "smoke"]
    rows = []
    summary_path = OUTPUT_ROOT / ("summary_full.json" if full else "summary.json")
    for job in jobs:
        print(f"[repair] {job} ...", flush=True)
        row = process(job, renders=renders, full=full)
        rows.append(row)
        print(f"[repair] {job}: rc={row['rc']} {row.get('repair_message')} ({row.get('total_s')} s)", flush=True)
        summary_path.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    summary_path.with_suffix(".md").write_text(markdown(rows), encoding="utf-8")
    print(markdown(rows))
    return 0 if all(r.get("rc") == 0 for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
