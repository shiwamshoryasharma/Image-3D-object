"""Real-Blender tests of the ``rig_optimization`` step (``backend/blender_scripts/img3d_amt/rig.py``).

A synthetic skinned arm with deliberately bad weights (6 influences, unnormalized sums, 1e-5 weights, an unweighted
block, an empty extra group, a vertex weighted to the tip bone -> spike) goes through the FULL CLI pipeline as .blend,
.glb and .fbx; the outputs are re-imported and inspected independently of img3d_amt. Also: clean rig left alone,
option off, a real TRELLIS job (unrigged -> skipped) and before/after posed renders.
Artifacts: test/outputs/mesh_tuner/rig/ (runs/, renders/, logs/).

Run:
  backend\\python\\python.exe -s -m pytest -c test\\pytest.ini test\\mesh_tuner\\rig -q -p no:cacheprovider
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from rig_testlib import (COLOR_BONES, EXCLUDED_JOBS, HELPERS_DIR, JOBS_DIR, MINIMAL_OPTIONS, OUTPUT_ROOT,
                         PYTHON_311, RIG_MODULE, TRELLIS_JOB, BlenderRun, check_map, fresh_dir, inspect_rig,
                         load_report, metric_map, rig_step, run_blender, run_tuner)

SKIP_MESSAGE = "No rig present (TRELLIS output is unrigged)"
RIG_CHECKS = {"rig_weights", "pose_stretch", "pose_collapse"}
STEP_IDS = ["load", "analyze", "geometry_repair", "face_refinement", "topology_tuning", "rig_optimization",
            "validation", "export"]


# ================================================================================================================
# shared runs
# ================================================================================================================


def _run(input_path: Path, name: str, options: dict[str, Any]) -> tuple[BlenderRun, Path, dict[str, Any]]:
    out = fresh_dir(name)
    run = run_tuner(input_path, out, options, tag=name)
    assert run.returncode == 0, run.tail()
    assert [s["id"] for s in run.of("plan")[0]["steps"]] == STEP_IDS
    assert not run.of("error"), run.of("error")
    return run, out, load_report(out)


@pytest.fixture(scope="module")
def blend_run(rig_assets: dict[str, Any]) -> tuple[BlenderRun, Path, dict[str, Any]]:
    return _run(rig_assets["blend"], "rig_bad_blend", dict(MINIMAL_OPTIONS, save_blend=True))


@pytest.fixture(scope="module")
def glb_run(rig_assets: dict[str, Any]) -> tuple[BlenderRun, Path, dict[str, Any]]:
    return _run(rig_assets["glb"], "rig_bad_glb", MINIMAL_OPTIONS)


def _mesh_details(report: dict[str, Any]) -> dict[str, Any]:
    step = rig_step(report)
    assert step["status"] == "done", step
    meshes = step["details"]["meshes"]
    assert len(meshes) == 1
    return meshes[0]


def _assert_clean_weights(mesh_info: dict[str, Any]) -> None:
    assert mesh_info["max_influences"] <= 4, mesh_info
    assert mesh_info["unnormalized"] == 0 and mesh_info["unweighted"] == 0 and mesh_info["tiny"] == 0, mesh_info
    assert mesh_info["max_sum_error"] < 1e-3


def _assert_rig_checks_pass(report: dict[str, Any]) -> None:
    checks = check_map(report)
    assert RIG_CHECKS <= set(checks), sorted(checks)
    for cid in RIG_CHECKS:
        assert checks[cid]["status"] == "pass", checks[cid]
        assert set(checks[cid]) == {"id", "label", "status", "value", "threshold", "message"}


def _skinned(info: dict[str, Any]) -> dict[str, Any]:
    meshes = [m for m in info["meshes"] if m["armature"]]
    assert len(meshes) == 1, info["meshes"]
    return meshes[0]


# ================================================================================================================
# static + unit
# ================================================================================================================


def test_rig_module_is_python311_syntax() -> None:
    """Blender 5.1 may bundle Python 3.11: the module must compile there (read-only use of the 4.2 install)."""
    if not PYTHON_311.is_file():
        pytest.skip("Blender 4.2's Python 3.11 is not installed")
    code = f"compile(open(r'{RIG_MODULE}', encoding='utf-8').read(), 'rig.py', 'exec')"
    proc = subprocess.run([str(PYTHON_311), "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.blender
def test_selftest_inside_blender(blender52: Path) -> None:
    run = run_blender(HELPERS_DIR / "selftest_rig.py", [], tag="selftest_rig", timeout=300)
    assert run.returncode == 0, run.tail()
    (result,) = run.tagged("SELFTEST")
    assert not result["failed"], "\n".join(f"{k}:\n{v}" for k, v in result["failed"].items())
    assert len(result["passed"]) >= 15, result["passed"]


# ================================================================================================================
# synthetic rig through the full CLI
# ================================================================================================================


@pytest.mark.blender
def test_blend_input_weights_cleaned(blend_run, rig_assets: dict[str, Any]) -> None:
    run, out, report = blend_run
    m = _mesh_details(report)
    before, after, fixes = m["before"], m["after"], m["fixes"]
    assert before["max_influences"] == 6 and before["over_limit_verts"] == rig_assets["six_influence_verts"]
    assert before["unnormalized_verts"] == rig_assets["unnormalized_verts"]
    assert before["unweighted_verts"] == rig_assets["unweighted_verts"]
    assert before["tiny_weights"] == rig_assets["tiny_weight_verts"]
    assert after == {**after, "max_influences": 4, "over_limit_verts": 0, "unnormalized_verts": 0,
                     "unweighted_verts": 0, "tiny_weights": 0}
    assert after["min_weight"] >= 1e-3 and after["max_sum_error"] < 1e-6
    assert fixes["tiny_weights_removed"] == rig_assets["tiny_weight_verts"]
    # the 6-influence band loses its far hand/finger strays first, so the 4-limit rarely has to cut anything
    assert fixes["over_limit_verts"] <= rig_assets["six_influence_verts"]
    assert fixes["outlier_influences"] >= 1 and rig_assets["spike_vertex"] in fixes["outlier_vertices"]
    assert fixes["stray_influences"] >= rig_assets["six_influence_verts"]   # 4-10 % on far hand/finger bones
    assert fixes["unweighted_filled"] == rig_assets["unweighted_verts"] + 1  # + the cleared spike vertex
    assert fixes["unfillable_verts"] == 0 and fixes["smoothed_verts"] >= fixes["unweighted_filled"]
    assert sorted(fixes["groups_removed"]) == ["extra_empty", "ik_target"]
    metrics = metric_map(report)
    assert (metrics["max_bone_influences"]["before"], metrics["max_bone_influences"]["after"]) == (6, 4)
    assert metrics["unnormalized_weight_verts"]["before"] == rig_assets["unnormalized_verts"]
    assert metrics["unnormalized_weight_verts"]["after"] == 0
    assert metrics["armatures"]["before"] == 1 and metrics["armatures"]["after"] == 1
    assert report["validation"]["passed"] is True
    assert run.step("rig_optimization")["status"] == "done"


@pytest.mark.blender
def test_blend_input_pose_checks_flag_spike_then_pass(blend_run) -> None:
    _, _, report = blend_run
    m = _mesh_details(report)
    pb, pa = m["pose_before"], m["pose_after"]
    assert pb["bones_tested"] == pa["bones_tested"] == 6 and pb["rotations_per_bone"] == 4
    assert pb["max_stretch"] > 5.0, pb                     # the spike + the unweighted block explode
    assert pb["worst_bones"][0]["bone"] == "finger"         # the spike follows the tip bone
    assert pa["max_stretch"] < 2.5 and pa["stretched_edges"] == 0 and pa["collapsed_tris"] == 0, pa
    assert pb["restored"] is True and pa["restored"] is True and pa["restore_error"] == 0.0
    metrics = metric_map(report)
    stretch = metrics["pose_max_stretch"]
    assert stretch["unit"] == "%" and stretch["better"] == "lower"
    assert stretch["before"] > 400.0 and stretch["after"] < 150.0, stretch
    _assert_rig_checks_pass(report)
    msg = check_map(report)["pose_stretch"]["message"]
    assert "before cleanup" in msg and "worst bones" in msg


@pytest.mark.blender
def test_blend_input_export_and_saved_pose(blend_run, rig_assets: dict[str, Any]) -> None:
    """The exported GLB keeps a clean skin; the saved .blend keeps the non-rest pose, non-deform groups and the
    modifier-referenced group; the spike vertex no longer follows the tip bone."""
    _, out, _ = blend_run
    spike = ",".join(str(c) for c in rig_assets["spike_co"])
    src = inspect_rig(rig_assets["blend"], tag="inspect_blend_src")
    saved = inspect_rig(out / "tuned.blend", tag="inspect_blend_saved", extra=[f"--probe={spike}"])
    glb = inspect_rig(out / "tuned.glb", tag="inspect_blend_glb", extra=[f"--probe={spike}", "--pose-bone", "finger"])
    # pose / rest pose unchanged
    assert saved["pose"] == src["pose"]
    hand = saved["pose"]["hand"]
    assert hand["rotation_mode"] == "XYZ" and math.isclose(hand["rotation_euler"][2], math.radians(20), abs_tol=1e-6)
    assert saved["pose_position"] == src["pose_position"] == "POSE"
    for b_src, b_out in zip(src["bones"], saved["bones"]):
        assert b_src["name"] == b_out["name"] and np.allclose(b_src["head"], b_out["head"], atol=1e-6)
        assert np.allclose(b_src["tail"], b_out["tail"], atol=1e-6)
    # weights in the saved scene
    mesh = _skinned(saved)
    _assert_clean_weights(mesh)
    groups = mesh["groups"]
    assert "extra_empty" not in groups and "ik_target" not in groups
    assert groups["displace_ref"][0] == 0                   # referenced by the (disabled) Displace modifier
    assert groups["paint_mask"][0] == rig_assets["paint_mask_count"]
    assert groups["paint_mask"] == _skinned(src)["groups"]["paint_mask"]  # non-deform group untouched
    weights = mesh["probes"][0]["weights"]
    assert mesh["probes"][0]["dist"] < 1e-6 and weights.get("finger", 0.0) < 0.01, weights
    assert weights.get("upper_arm", 0.0) > 0.9, weights
    # exported GLB: skin survives, still clean, spike gone
    assert glb["armatures"] == 1
    gmesh = _skinned(glb)
    _assert_clean_weights(gmesh)
    assert gmesh["faces"] == rig_assets["triangles"]       # glTF stores triangles
    assert all(t["max_stretch"] < 2.0 and t["collapsed"] == 0 for t in gmesh["pose_test"]), gmesh["pose_test"]
    assert gmesh["probes"][0]["weights"].get("finger", 0.0) < 0.01


@pytest.mark.blender
def test_glb_input(glb_run, rig_assets: dict[str, Any]) -> None:
    """GLB round trip: Blender's exporter normalized the weights and moved the unweighted block onto its
    ``neutral_bone`` - the step treats those as unweighted and re-fills them."""
    _, out, report = glb_run
    m = _mesh_details(report)
    assert m["before"]["max_influences"] == 6 and m["after"]["max_influences"] <= 4
    assert m["fixes"]["neutral_bone_verts"] == rig_assets["unweighted_verts"]
    assert m["fixes"]["unweighted_filled"] >= rig_assets["unweighted_verts"]
    assert m["fixes"]["outlier_influences"] >= 1
    assert m["after"]["unweighted_verts"] == 0 and m["after"]["unnormalized_verts"] == 0
    assert m["pose_before"]["max_stretch"] > 5.0 and m["pose_after"]["max_stretch"] < 2.5
    _assert_rig_checks_pass(report)
    src = inspect_rig(rig_assets["glb"], tag="inspect_glb_src")
    spike = ",".join(str(c) for c in rig_assets["spike_co"])
    res = inspect_rig(out / "tuned.glb", tag="inspect_glb_out", extra=[f"--probe={spike}", "--pose-bone", "finger"])
    assert res["armatures"] == 1
    src_bones = {b["name"]: b for b in src["bones"]}
    out_bones = {b["name"]: b for b in res["bones"]}
    assert set(src_bones) == set(out_bones)                  # (the exporter may reorder the joints)
    for name, b_src in src_bones.items():
        b_out = out_bones[name]
        assert b_src["parent"] == b_out["parent"], name
        assert np.allclose(b_src["head"], b_out["head"], atol=1e-5), name
        assert np.allclose(b_src["tail"], b_out["tail"], atol=1e-5), name
    mesh = _skinned(res)
    _assert_clean_weights(mesh)
    assert mesh["faces"] == _skinned(src)["faces"]
    assert mesh["groups"]["neutral_bone"][0] == 0
    assert mesh["probes"][0]["weights"].get("finger", 0.0) < 0.01
    assert all(t["max_stretch"] < 2.0 for t in mesh["pose_test"]), mesh["pose_test"]


@pytest.mark.blender
def test_fbx_input(rig_assets: dict[str, Any]) -> None:
    _, out, report = _run(rig_assets["fbx"], "rig_bad_fbx", MINIMAL_OPTIONS)
    assert report["input"]["format"] == "fbx"
    m = _mesh_details(report)
    assert m["before"]["max_influences"] == 6 and m["before"]["tiny_weights"] == rig_assets["tiny_weight_verts"]
    assert m["before"]["unweighted_verts"] == rig_assets["unweighted_verts"]
    assert m["before"]["unnormalized_verts"] == rig_assets["unnormalized_verts"]
    assert m["after"]["max_influences"] <= 4 and m["after"]["unweighted_verts"] == 0
    assert m["after"]["unnormalized_verts"] == 0 and m["after"]["tiny_weights"] == 0
    assert m["fixes"]["outlier_influences"] >= 1
    assert m["pose_before"]["max_stretch"] > 5.0 and m["pose_after"]["max_stretch"] < 2.5
    _assert_rig_checks_pass(report)
    res = inspect_rig(out / "tuned.glb", tag="inspect_fbx_out", extra=["--pose-bone", "finger"])
    assert res["armatures"] == 1
    _assert_clean_weights(_skinned(res))


@pytest.mark.blender
def test_clean_rig_is_left_alone(rig_assets: dict[str, Any]) -> None:
    _, _, report = _run(rig_assets["clean_blend"], "rig_clean_blend", MINIMAL_OPTIONS)
    step = rig_step(report)
    m = _mesh_details(report)
    assert "weights already clean" in step["message"], step["message"]
    assert m["write"] == {"entries_removed": 0, "entries_written": 0}
    assert m["fixes"]["groups_removed"] == [] and m["fixes"]["unweighted_filled"] == 0
    assert m["fixes"]["outlier_influences"] == 0 and m["fixes"]["spike_vertices"] == 0
    assert m["fixes"]["stray_influences"] == 0
    assert math.isclose(m["pose_before"]["max_stretch"], m["pose_after"]["max_stretch"], rel_tol=1e-9)
    assert m["pose_after"]["max_stretch"] < 2.0
    _assert_rig_checks_pass(report)


@pytest.mark.blender
def test_option_off_skips_rig_step(rig_assets: dict[str, Any]) -> None:
    run, _, report = _run(rig_assets["blend"], "rig_disabled", dict(MINIMAL_OPTIONS, optimize_rig=False))
    step = run.step("rig_optimization")
    assert step["status"] == "skipped" and step["message"] == "Rig optimization is disabled"
    assert not RIG_CHECKS & set(check_map(report))
    assert "pose_max_stretch" not in metric_map(report)


@pytest.mark.blender
def test_default_options_pipeline_keeps_clean_skin(rig_assets: dict[str, Any]) -> None:
    """All other steps at their defaults (geometry steps may change the mesh): the exported skin is still clean."""
    run, out, report = _run(rig_assets["glb"], "rig_bad_glb_defaults", {"render_previews": False})
    assert run.step("rig_optimization")["status"] in ("done", "warning")
    res = inspect_rig(out / "tuned.glb", tag="inspect_defaults_out")
    assert res["armatures"] == 1
    _assert_clean_weights(_skinned(res))
    assert {c["id"] for c in report["validation"]["checks"]} >= RIG_CHECKS


# ================================================================================================================
# real TRELLIS job: unrigged -> skipped
# ================================================================================================================


@pytest.mark.blender
def test_trellis_job_rig_step_skipped(blender52: Path) -> None:
    assert TRELLIS_JOB not in EXCLUDED_JOBS
    model = JOBS_DIR / TRELLIS_JOB / "model.glb"
    if not model.is_file():
        pytest.skip(f"{TRELLIS_JOB} output is not available")
    run, out, report = _run(model, "trellis_046e", MINIMAL_OPTIONS)
    step = run.step("rig_optimization")
    assert step["status"] == "skipped" and step["message"] == SKIP_MESSAGE
    assert step["details"] == {"armatures": 0}
    assert rig_step(report)["status"] == "skipped"
    assert not RIG_CHECKS & set(check_map(report))
    metrics = metric_map(report)
    assert metrics["armatures"]["before"] == 0 and "pose_max_stretch" not in metrics
    assert metrics["max_bone_influences"]["before"] == 0
    assert (out / "tuned.glb").stat().st_size > 1_000_000


# ================================================================================================================
# visual verification artifacts
# ================================================================================================================


@pytest.mark.blender
def test_posed_renders_before_after(blend_run, rig_assets: dict[str, Any]) -> None:
    """Workbench renders (matcap + weight colours) of the bad input, the tuned output and the clean reference in
    rest and three test poses -> test/outputs/mesh_tuner/rig/renders/. The posed 'after' must look like the clean
    reference and unlike the 'before'."""
    from PIL import Image

    _, out, _ = blend_run
    render_dir = OUTPUT_ROOT / "renders"
    poses = ["finger:X:45", "upper_arm:X:45", "hand:Z:45"]
    sources = {"before": rig_assets["blend"], "after": out / "tuned.glb", "clean": rig_assets["clean_blend"]}
    for tag, path in sources.items():
        extra = ["--render-dir", render_dir, "--render-tag", tag, "--color-bones", COLOR_BONES, "--res", 768]
        for pose in poses:
            extra += ["--render-pose", pose]
        info = inspect_rig(path, tag=f"render_{tag}", extra=extra)
        assert len(info["renders"]) == 2 * (1 + len(poses)), info["renders"]

    def img(name: str) -> np.ndarray:
        path = render_dir / name
        assert path.is_file(), path
        with Image.open(path) as im:
            assert im.size == (768, 768)
            return np.asarray(im.convert("RGB"), dtype=np.float32)

    for label in ("finger_X45", "upper_arm_X45"):
        before = img(f"before_{label}_matcap.png")
        after = img(f"after_{label}_matcap.png")
        clean = img(f"clean_{label}_matcap.png")
        d_after_clean = float(np.abs(after - clean).mean())
        d_before_clean = float(np.abs(before - clean).mean())
        assert d_after_clean < 0.5 * d_before_clean, (label, d_after_clean, d_before_clean)
