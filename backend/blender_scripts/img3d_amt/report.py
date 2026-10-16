"""report.json (DESIGN section 3 schema) + the ``Metric`` list shared by the report and the ``result`` event.

Metric: ``{key, label, unit|null, before|null, after|null, better: "lower"|"higher"|"neutral"}``.
``before`` comes from the ``analyze`` step, ``after`` from the ``validation`` step; steps add values the analysis
does not compute with ``ctx.set_metric(key, before=..., after=...)``.
"""

from __future__ import annotations

import json
import os
import platform
from datetime import datetime, timezone
from typing import Any

import bpy
import numpy as np

from . import PROTOCOL_VERSION, TOOL_NAME, __version__
from .context import TunerContext
from .protocol import to_jsonable

REPORT_SCHEMA = 1

# key -> (label, unit, better)
METRIC_DEFS: dict[str, tuple[str, str | None, str]] = {
    "vertices": ("Vertices", None, "neutral"),
    "faces": ("Faces", None, "neutral"),
    "triangles": ("Triangles", None, "neutral"),
    "quads": ("Quads", None, "neutral"),
    "ngons": ("N-gons", None, "lower"),
    "non_manifold_edges": ("Non-manifold edges", None, "lower"),
    "boundary_edges": ("Boundary edges", None, "lower"),
    "holes": ("Holes", None, "lower"),
    "largest_hole_edges": ("Largest hole", "edges", "lower"),
    "components": ("Separate parts", None, "neutral"),
    "small_components": ("Small floating parts", None, "lower"),
    "degenerate_faces": ("Degenerate faces", None, "lower"),
    "duplicate_vertices": ("Duplicate vertices", None, "lower"),
    "inconsistent_normal_edges": ("Flipped-normal edges", None, "lower"),
    "hidden_faces": ("Hidden faces", None, "lower"),
    "inner_shell_faces": ("Inner-shell faces", None, "lower"),
    "symmetry_error_pct": ("Symmetry error", "% diag", "neutral"),
    "surface_area": ("Surface area", "units²", "neutral"),
    "mean_edge_length": ("Mean edge length", "units", "neutral"),
    "deviation_mean_pct": ("Mean deviation", "% diag", "lower"),
    "deviation_p95_pct": ("Deviation (95th pct)", "% diag", "lower"),
    "deviation_max_pct": ("Max deviation", "% diag", "lower"),
    "uv_layers": ("UV layers", None, "higher"),
    "materials": ("Materials", None, "higher"),
    "textures": ("Textures", None, "higher"),
    "armatures": ("Armatures", None, "neutral"),
    "max_bone_influences": ("Max bone influences", None, "lower"),
    "unnormalized_weight_verts": ("Unnormalized weights", "vertices", "lower"),
    "pose_max_stretch": ("Max pose stretch", "%", "lower"),
}


def _clean(value: Any) -> Any:
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return round(value, 6) if np.isfinite(value) else None
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    return value


def build_metrics(before: dict[str, Any], after: dict[str, Any],
                  overrides: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Merge analysis values and step overrides into the ordered Metric list (unknown keys are appended)."""
    overrides = overrides or {}
    keys = [k for k in METRIC_DEFS if k in before or k in after or k in overrides]
    keys += sorted(k for k in set(before) | set(after) | set(overrides) if k not in METRIC_DEFS)
    out: list[dict[str, Any]] = []
    for key in keys:
        label, unit, better = METRIC_DEFS.get(key, (key.replace("_", " ").capitalize(), None, "neutral"))
        b = overrides.get(key, {}).get("before", before.get(key))
        a = overrides.get(key, {}).get("after", after.get(key))
        if b is None and a is None:
            continue
        out.append({"key": key, "label": label, "unit": unit, "before": _clean(b), "after": _clean(a),
                    "better": better})
    return out


def environment(ctx: TunerContext) -> dict[str, Any]:
    gpu = ctx.gpu or {}
    return {"gpu": gpu.get("available"), "gpu_renderer": gpu.get("renderer"), "gpu_backend": gpu.get("backend"),
            "python": platform.python_version(), "numpy": np.__version__,
            "blender": bpy.app.version_string, "platform": platform.platform(),
            "peak_memory_mb": ctx.peak_memory}


def build_report(ctx: TunerContext, *, steps: list[dict[str, Any]], status: str, duration_s: float,
                 metrics: list[dict[str, Any]], outputs: dict[str, Any],
                 error: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble the report.json document (DESIGN schema + ``status``, ``context``, ``timings``, ``analysis``)."""
    analysis = {label: entry.get("details") for label, entry in (ctx.store.get("analysis") or {}).items()}
    report = {
        "schema": REPORT_SCHEMA,
        "tool": TOOL_NAME,
        "script_version": __version__,
        "protocol": PROTOCOL_VERSION,
        "blender_version": bpy.app.version_string,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(float(duration_s), 3),
        "status": status,
        "input": dict(ctx.input_info) if ctx.input_info else {"path": ctx.config.input_path,
                                                             "format": ctx.config.input_format,
                                                             "size_bytes": None},
        "options": ctx.options.to_dict(),
        "context": dict(ctx.config.context),
        "steps": steps,
        "metrics": metrics,
        "warnings": list(ctx.warnings),
        "validation": ctx.validation or {"passed": False, "checks": []},
        "outputs": outputs,
        "environment": environment(ctx),
        "timings": dict(ctx.timings),
        "analysis": analysis,
    }
    if error is not None:
        report["error"] = error
    return to_jsonable(report)


def write_json_atomic(path: str, data: dict[str, Any]) -> None:
    """Write JSON (UTF-8, indented) via temp file + ``os.replace``."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False, allow_nan=False)
    os.replace(tmp, path)
