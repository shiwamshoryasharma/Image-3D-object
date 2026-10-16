"""Synthetic tests of the face_refinement / topology_tuning code (smoothing, symmetry, face, topology) inside a
real Blender 5.2 (marker ``blender``), plus CLI runs of the real entry script on small synthetic GLBs.

Run:
  backend/python/python.exe -s -m pytest -c test/pytest.ini test/mesh_tuner/refine -q -p no:cacheprovider
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from refine_testlib import OUTPUT_ROOT, PYTHON_311, SCRIPTS_DIR, run_tuner

OWN_FILES = ("smoothing.py", "symmetry.py", "face.py", "topology.py")


def _ok(result: dict[str, Any]) -> dict[str, Any]:
    assert "error" not in result, result.get("traceback") or result.get("error")
    return result


def test_python311_syntax() -> None:
    if not PYTHON_311.is_file():
        pytest.skip("Blender 4.2 bundled Python 3.11 not available")
    files = [str(SCRIPTS_DIR / "img3d_amt" / f) for f in OWN_FILES]
    proc = subprocess.run([str(PYTHON_311), "-I", "-m", "py_compile", *files], capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.blender
def test_noisy_sphere_denoised_with_low_volume_change(synth: dict[str, Any]) -> None:
    r = _ok(synth["noisy_sphere"])
    assert r["rms_after"] < 0.6 * r["rms_before"], r
    assert r["volume_change"] < 0.01, r
    assert r["taubin_volume_change"] < 0.01, r          # Taubin: no shrinkage ...
    assert r["laplacian_volume_change"] > r["taubin_volume_change"], r   # ... unlike plain Laplacian


@pytest.mark.blender
def test_noisy_cube_edges_preserved(synth: dict[str, Any]) -> None:
    r = _ok(synth["noisy_cube"])
    assert r["crease_edges"] >= 12 * 18, r
    assert r["flat_err_after"] < 0.6 * r["flat_err_before"], r          # faces flattened (noise removed)
    assert r["crease_deg_after"] > 80.0, r                              # 90 deg cube edges stay sharp
    assert r["crease_deg_laplacian"] < r["crease_deg_after"] - 10.0, r  # (a plain Laplacian rounds them)
    assert r["edge_err_after"] < r["edge_err_laplacian"], r


@pytest.mark.blender
def test_thin_plate_fair_removes_pit(synth: dict[str, Any]) -> None:
    r = _ok(synth["fair_pit"])
    assert r["pit_depth_after"] < 0.1 * r["pit_depth_before"], r
    assert r["fixed_moved"] == 0.0, r


@pytest.mark.blender
def test_symmetric_bust_dent_repaired_from_other_side(synth: dict[str, Any]) -> None:
    r = _ok(synth["bust_dent_auto"])
    assert r["humanoid"] is True, r
    assert r["action"] == "repair", r
    assert r["dent_vertices"] > 20, r
    assert r["dent_err_after"] < 0.35 * r["dent_err_before"], r
    assert r["good_side_moved_max"] < 1e-6, r            # the intact side is the source, never modified


@pytest.mark.blender
def test_symmetry_off_leaves_mesh(synth: dict[str, Any]) -> None:
    r = _ok(synth["bust_symmetry_off"])
    assert r["moved"] == 0.0, r
    assert r["status"] == "skipped", r


@pytest.mark.blender
def test_asymmetric_mesh_symmetry_auto_skipped(synth: dict[str, Any]) -> None:
    r = _ok(synth["asymmetric_auto_skipped"])
    assert r["action"] == "skip", r
    assert r["mean_pct"] > 0.3, r
    assert r["humanoid"] is False, r
    assert r["moved"] == 0.0, r
    assert r["face_attr"] is False, r


@pytest.mark.blender
def test_symmetrize_mode_mirrors_better_side(synth: dict[str, Any]) -> None:
    r = _ok(synth["symmetrize_mode"])
    assert r["source"] == "negative", r                   # the dent is on +X -> the -X side is the source
    assert r["mean_pct_after"] < 0.05, r


@pytest.mark.blender
def test_pn_midpoints_follow_curvature(synth: dict[str, Any]) -> None:
    r = _ok(synth["pn_midpoints"])
    assert r["pn_err"] < 0.25 * r["linear_err"], r


def _report(out: Path) -> dict[str, Any]:
    return json.loads((out / "report.json").read_text(encoding="utf-8"))


def _step(report: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(s for s in report["steps"] if s["id"] == step_id)


@pytest.mark.blender
def test_cli_topology_refine_and_subdivision(synth: dict[str, Any]) -> None:
    paths = _ok(synth["export_inputs"])
    out = OUTPUT_ROOT / "cli" / "coarse_sphere"
    run = run_tuner(Path(paths["sphere"]), out, {"render_previews": False, "save_blend": True},
                    tag="cli_coarse_sphere", timeout=600)
    assert run.returncode == 0, run.tail()
    rep = _report(out)
    topo = _step(rep, "topology_tuning")
    assert topo["status"] == "done", topo
    d = topo["details"]
    assert d["refine_density"]["split_edges"] > 0, d
    assert d["faces_after"] <= 1.3 * d["faces_before"] + 1, d
    assert d["subdivision"]["added"] is True, d          # small mesh -> auto adds the modifier ...
    metrics = {m["key"]: m for m in rep["metrics"]}
    assert metrics["faces"]["after"] == d["faces_after"], metrics["faces"]   # ... never applied to the GLB
    assert d["shading"]["applied"] is True, d
    face = _step(rep, "face_refinement")
    assert face["status"] == "skipped", face              # a sphere is not a character
    assert (out / "tuned.blend").is_file()


@pytest.mark.blender
def test_cli_options_off_skip_both_steps(synth: dict[str, Any]) -> None:
    paths = _ok(synth["export_inputs"])
    out = OUTPUT_ROOT / "cli" / "all_off"
    opts = {"render_previews": False, "smoothing": 0, "refine_density": False, "refine_face": False,
            "symmetry": "off", "subdivision": "off", "quads": False}
    run = run_tuner(Path(paths["bust"]), out, opts, tag="cli_all_off", timeout=600)
    assert run.returncode == 0, run.tail()
    rep = _report(out)
    assert _step(rep, "face_refinement")["status"] == "skipped"
    topo = _step(rep, "topology_tuning")
    assert topo["details"]["denoise"].get("skipped") and topo["details"]["refine_density"].get("skipped"), topo


@pytest.mark.blender
def test_cli_bust_face_step_and_quads(synth: dict[str, Any]) -> None:
    paths = _ok(synth["export_inputs"])
    out = OUTPUT_ROOT / "cli" / "bust_quads"
    run = run_tuner(Path(paths["bust"]), out, {"render_previews": False, "quads": True, "subdivision": "off"},
                    tag="cli_bust_quads", timeout=600)
    assert run.returncode == 0, run.tail()
    rep = _report(out)
    face = _step(rep, "face_refinement")
    assert face["status"] in ("done", "warning"), face
    assert face["details"]["symmetry"]["action"] == "repair", face["details"]["symmetry"]
    topo = _step(rep, "topology_tuning")
    assert topo["details"]["quads"]["quads"] > 0, topo["details"]["quads"]
    metrics = {m["key"]: m for m in rep["metrics"]}
    assert metrics["deviation_p95_pct"]["after"] <= 0.5, metrics["deviation_p95_pct"]
