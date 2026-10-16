"""The ``validation`` step: after-metrics, surface deviation vs the ORIGINAL, pass/warn/fail checks.

Deviation (all in % of the original bbox diagonal):
* tuned -> original: distances from points of the tuned mesh (up to ``VERTEX_SAMPLES`` vertices + ``SURFACE_SAMPLES``
  area-weighted surface points) to the original surface (BVH of ``ctx.original``): mean / p95 / max.
* original -> tuned (only when the repair step stored ``ctx.store["original_visible_face_mask"]``, a (F_original,)
  bool array): distances from points on the VISIBLE original faces to the tuned surface - catches visible geometry
  that was removed or moved.

Checks (``{"id", "label", "status": "pass"|"warn"|"fail", "value", "threshold", "message"}``):
deviation (p95 <= 0.5 % pass, <= 2 % warn, else fail) · visible_surface (same thresholds, when the mask exists) ·
bbox (<= 1 % pass, <= 5 % warn, else fail) · faces (> 0) · non_manifold / holes / normals (not increased, warn) ·
uv_layers / materials / textures (not lost, fail) · pose checks delegated to the rig step
(``ctx.store["pose_checks"]``).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from . import analysis, meshdata
from .context import StepOutcome, TunerContext

log = logging.getLogger(__name__)

VERTEX_SAMPLES = 150_000
SURFACE_SAMPLES = 50_000
REVERSE_SAMPLES = 50_000
DEVIATION_PASS_PCT = 0.5
DEVIATION_FAIL_PCT = 2.0
BBOX_PASS_PCT = 1.0
BBOX_FAIL_PCT = 5.0
CHECK_STATUSES = ("pass", "warn", "fail")


def run(ctx: TunerContext) -> StepOutcome:
    """Validate the tuned primary mesh against the original snapshot (see module doc)."""
    obj = ctx.require_primary()
    after = analysis.analyze(ctx, obj, label="after", progress=(0.0, 0.45))
    ctx.metrics_after = after
    dev = measure_deviation(ctx, progress=(0.45, 0.95))
    for key in ("deviation_mean_pct", "deviation_p95_pct", "deviation_max_pct"):
        if dev.get(key) is not None:
            ctx.set_metric(key, after=dev[key])
    checks = build_checks(ctx, ctx.metrics_before, after, dev)
    checks += collect_pose_checks(ctx)
    passed = not any(c["status"] == "fail" for c in checks)
    ctx.validation = {"passed": passed, "checks": checks}
    ctx.store["deviation"] = dev
    ctx.progress(1.0, "Validation complete")
    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    details = {"deviation": dev, "checks": len(checks), "warnings": len(warns), "failures": len(fails)}
    if fails or warns:
        parts = [f"{c['label']}: {c['message']}" for c in fails + warns]
        for part in parts:
            ctx.warn(f"Validation - {part}")
        summary = (f"{len(fails)} failed, " if fails else "") + f"{len(warns)} warning(s) of {len(checks)} checks"
        return StepOutcome("warning", summary, details)
    return StepOutcome("done", f"All {len(checks)} checks passed", details)


def _pct_stats(dist: np.ndarray, diag: float, prefix: str) -> dict[str, float | None]:
    if len(dist) == 0 or diag <= 0:
        return {f"{prefix}_mean_pct": None, f"{prefix}_p95_pct": None, f"{prefix}_max_pct": None}
    scale = 100.0 / diag
    return {f"{prefix}_mean_pct": round(float(dist.mean()) * scale, 6),
            f"{prefix}_p95_pct": round(float(np.percentile(dist, 95)) * scale, 6),
            f"{prefix}_max_pct": round(float(dist.max()) * scale, 6)}


def _triangles(mesh) -> np.ndarray:
    if meshdata.is_all_triangles(mesh):
        return meshdata.get_triangles(mesh)
    tris, _, _ = meshdata.get_loop_triangles(mesh)
    return tris


def measure_deviation(ctx: TunerContext, progress: tuple[float, float] = (0.0, 1.0), seed: int = 0) -> dict[str, Any]:
    """Tuned<->original surface distances (see module doc). ``{"available": False}`` without an original."""
    p0, p1 = progress
    original = ctx.original
    obj = ctx.require_primary()
    if original is None:
        return {"available": False, "reason": "original snapshot missing"}
    co_orig, tris_orig = ctx.original_geometry()
    diag = float(np.linalg.norm(co_orig.max(0) - co_orig.min(0))) if len(co_orig) else 0.0
    co = meshdata.get_positions(obj.data)
    out: dict[str, Any] = {"available": True, "diag": diag}
    if len(co) == 0 or diag <= 0:
        out.update(_pct_stats(np.zeros(0), diag, "deviation"))
        return out
    rng = np.random.default_rng(seed)
    tris = _triangles(obj.data)
    used = np.zeros(len(co), dtype=bool)
    used[tris.reshape(-1)] = True  # loose vertices are not surface (TRELLIS has a few)
    vidx = np.nonzero(used)[0]
    if len(vidx) > VERTEX_SAMPLES:
        vidx = rng.choice(vidx, VERTEX_SAMPLES, replace=False)
    surf, _ = meshdata.sample_surface(co, tris, SURFACE_SAMPLES, seed=seed)
    pts = np.concatenate([co[vidx], surf], axis=0)
    ctx.progress(p0 + 0.1 * (p1 - p0), "Measuring deviation from the original")
    dist, _, _ = meshdata.nearest_on_surface(ctx.original_bvh(), pts, max_dist=0.02 * diag)
    out["samples"] = int(len(pts))
    out.update(_pct_stats(dist, diag, "deviation"))
    out["deviation_gt_0_5pct_frac"] = round(float((dist > 0.005 * diag).mean()), 6)

    mask = ctx.store.get("original_visible_face_mask")
    if mask is not None:
        mask = np.asarray(mask, dtype=bool).reshape(-1)
        n_faces_orig = len(original.data.polygons)
        if len(tris_orig) != n_faces_orig and len(mask) == n_faces_orig:  # face mask -> loop-triangle mask
            _, tri_poly, _ = meshdata.get_loop_triangles(original.data)
            mask = mask[tri_poly]
        if len(mask) != len(tris_orig):
            log.warning("original_visible_face_mask has %d entries but the original has %d faces - ignored",
                        len(mask), n_faces_orig)
        else:
            ctx.progress(p0 + 0.6 * (p1 - p0), "Checking the visible surface")
            pts_r, _ = meshdata.sample_surface(co_orig, tris_orig, REVERSE_SAMPLES, seed=seed + 1, face_mask=mask)
            if len(pts_r):
                bvh_tuned = meshdata.bvh_from_arrays(co, tris)
                dist_r, _, _ = meshdata.nearest_on_surface(bvh_tuned, pts_r, max_dist=0.02 * diag)
                out["reverse_samples"] = int(len(pts_r))
                out["visible_faces"] = int(mask.sum())
                out.update(_pct_stats(dist_r, diag, "visible"))
    ctx.progress(p1, "Deviation measured")
    return out


def _check(check_id: str, label: str, status: str, value: Any, threshold: Any, message: str) -> dict[str, Any]:
    return {"id": check_id, "label": label, "status": status, "value": value, "threshold": threshold,
            "message": message}


def _graded(value: float | None, pass_max: float, fail_above: float) -> str:
    if value is None:
        return "warn"
    if value <= pass_max:
        return "pass"
    return "warn" if value <= fail_above else "fail"


def build_checks(ctx: TunerContext, before: dict[str, Any], after: dict[str, Any],
                 dev: dict[str, Any]) -> list[dict[str, Any]]:
    """The standard validation checks (see module doc)."""
    checks: list[dict[str, Any]] = []
    if dev.get("available"):
        p95 = dev.get("deviation_p95_pct")
        status = _graded(p95, DEVIATION_PASS_PCT, DEVIATION_FAIL_PCT)
        checks.append(_check("deviation", "Surface deviation", status, p95, DEVIATION_PASS_PCT,
                             f"95% of the surface is within {p95:.3f}% of the size of the original"
                             if p95 is not None else "Deviation could not be measured"))
        if dev.get("visible_p95_pct") is not None:
            vp95 = dev["visible_p95_pct"]
            checks.append(_check("visible_surface", "Visible surface kept",
                                 _graded(vp95, DEVIATION_PASS_PCT, DEVIATION_FAIL_PCT), vp95, DEVIATION_PASS_PCT,
                                 f"Visible original surface within {vp95:.3f}% (p95) of the result"))
    else:
        checks.append(_check("deviation", "Surface deviation", "warn", None, DEVIATION_PASS_PCT,
                             "No original snapshot to compare with"))

    bbox_change = _bbox_change_pct(ctx)
    if bbox_change is not None:
        checks.append(_check("bbox", "Size and position", _graded(bbox_change, BBOX_PASS_PCT, BBOX_FAIL_PCT),
                             round(bbox_change, 4), BBOX_PASS_PCT,
                             f"Bounding box changed by {bbox_change:.3f}% of the diagonal"))

    faces = int(after.get("faces") or 0)
    checks.append(_check("faces", "Mesh has faces", "pass" if faces > 0 else "fail", faces, 0,
                         f"{faces:,} faces" if faces else "The result has no faces"))

    for key, check_id, label in (("non_manifold_edges", "non_manifold", "Non-manifold edges"),
                                 ("holes", "holes", "Holes"),
                                 ("inconsistent_normal_edges", "normals", "Consistent normals")):
        b, a = before.get(key), after.get(key)
        if a is None:
            continue
        if b is None:
            checks.append(_check(check_id, label, "pass", a, None, f"{a:,} after (no baseline)"))
            continue
        ok = a <= b
        checks.append(_check(check_id, label, "pass" if ok else "warn", a, b,
                             f"{b:,} -> {a:,}" + ("" if ok else " (increased)")))

    for key, label in (("uv_layers", "UV layers kept"), ("materials", "Materials kept"),
                       ("textures", "Textures kept")):
        b, a = before.get(key), after.get(key)
        if a is None or b is None:
            continue
        ok = a >= b
        checks.append(_check(key, label, "pass" if ok else "fail", a, b,
                             f"{b} -> {a}" + ("" if ok else " (lost)")))
    return checks


def _bbox_change_pct(ctx: TunerContext) -> float | None:
    if ctx.original is None or ctx.primary is None:
        return None
    co_o = meshdata.get_positions(ctx.original.data)
    co_t = meshdata.get_positions(ctx.primary.data)
    if len(co_o) == 0 or len(co_t) == 0:
        return None
    omin, omax = co_o.min(0), co_o.max(0)
    diag = float(np.linalg.norm(omax - omin))
    if diag <= 0:
        return None
    change = max(float(np.abs(co_t.min(0) - omin).max()), float(np.abs(co_t.max(0) - omax).max()))
    return 100.0 * change / diag


def collect_pose_checks(ctx: TunerContext) -> list[dict[str, Any]]:
    """Validated copies of ``ctx.store["pose_checks"]`` (rig step); malformed entries are dropped with a log line."""
    raw = ctx.store.get("pose_checks") or []
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        if not isinstance(item, dict) or item.get("status") not in CHECK_STATUSES or not item.get("id"):
            log.warning("Ignoring malformed pose check: %r", item)
            continue
        out.append(_check(str(item["id"]), str(item.get("label") or item["id"]), item["status"], item.get("value"),
                          item.get("threshold"), str(item.get("message") or "")))
    return out
