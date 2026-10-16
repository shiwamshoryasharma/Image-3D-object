"""Real-Blender tests of the Auto Mesh Tuner framework (entry script, protocol, pipeline, I/O, analysis, validation,
previews, report). The four processing steps are stubbed (``tuner`` fixture -> run_with_injected_steps.py --mode
stubs) so this suite stays independent of the real steps. Marker ``blender``; artifacts in
test/outputs/mesh_tuner/framework/.

Run:
  backend\\python\\python.exe -s -m pytest -c test\\pytest.ini test\\mesh_tuner\\framework -q -p no:cacheprovider
"""

from __future__ import annotations

import glob
import json
import struct
import subprocess
from pathlib import Path
from typing import Any

import pytest

from amt_testlib import (EXCLUDED_JOBS, HELPERS_DIR, JOBS_DIR, OUTPUT_ROOT, PYTHON_311, SCRIPTS_DIR, BlenderRun,
                         fresh_dir, read_glb_json, run_blender, write_config, write_glb_with_json)

STEP_IDS = ["load", "analyze", "geometry_repair", "face_refinement", "topology_tuning", "rig_optimization",
            "validation", "export"]
STUB_STEPS = {"geometry_repair", "face_refinement", "topology_tuning", "rig_optimization"}
METRIC_BETTER = {"lower", "higher", "neutral"}
CHECK_STATUSES = {"pass", "warn", "fail"}
REAL_JOBS = ["job_04535ae38930", "job_046e19f0ca62"]


# ================================================================================================================
# helpers
# ================================================================================================================


def png_size(path: Path) -> tuple[int, int]:
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", head[16:24])


def assert_protocol_sequence(run: BlenderRun) -> dict[str, dict[str, Any]]:
    """hello, plan, (running, terminal) per step in order, monotonic progress, result last; returns final step events."""
    ev = run.events
    assert ev[0]["event"] == "hello", ev[:2]
    assert ev[0]["protocol"] == 1 and ev[0]["blender_version"].startswith("5.") and isinstance(ev[0]["gpu"], bool)
    assert ev[1]["event"] == "plan" and [s["id"] for s in ev[1]["steps"]] == STEP_IDS
    assert all(s["label"] for s in ev[1]["steps"])
    steps = run.of("step")
    assert [s["id"] for s in steps] == [i for i in STEP_IDS for _ in range(2)], [(s["id"], s["status"]) for s in steps]
    finals: dict[str, dict[str, Any]] = {}
    for k, sid in enumerate(STEP_IDS):
        running, final = steps[2 * k], steps[2 * k + 1]
        assert running["status"] == "running"
        assert final["status"] in ("done", "warning", "skipped"), final
        assert isinstance(final["duration_s"], (int, float)) and final["duration_s"] >= 0
        assert isinstance(final["details"], dict)
        finals[sid] = final
    overall = [e["progress"] for e in ev if e["event"] in ("step", "progress")]
    assert all(b >= a - 1e-9 for a, b in zip(overall, overall[1:])), "overall progress must not go backwards"
    assert overall[-1] == pytest.approx(1.0)
    for p in run.of("progress"):
        assert 0.0 <= p["fraction"] <= 1.0 and p["id"] in STEP_IDS
    assert ev[-1]["event"] == "result"
    assert not run.errors, run.errors
    return finals


def assert_report_schema(report: dict[str, Any]) -> None:
    """DESIGN section 3 report.json schema."""
    for key, typ in {"schema": int, "tool": str, "script_version": str, "protocol": int, "blender_version": str,
                     "created_at": str, "duration_s": (int, float), "input": dict, "options": dict, "steps": list,
                     "metrics": list, "warnings": list, "validation": dict, "outputs": dict,
                     "environment": dict}.items():
        assert isinstance(report.get(key), typ), (key, report.get(key))
    assert report["schema"] == 1 and report["protocol"] == 1 and report["tool"] == "Img3D Auto Mesh Tuner"
    assert set(report["input"]) >= {"path", "format", "size_bytes"}
    assert [s["id"] for s in report["steps"]] == STEP_IDS
    for step in report["steps"]:
        assert set(step) >= {"id", "label", "status", "message", "duration_s", "details"}
        assert step["status"] in ("done", "warning", "skipped", "failed")
    for metric in report["metrics"]:
        assert set(metric) == {"key", "label", "unit", "before", "after", "better"}, metric
        assert metric["better"] in METRIC_BETTER
    val = report["validation"]
    assert isinstance(val["passed"], bool) and isinstance(val["checks"], list)
    for check in val["checks"]:
        assert set(check) >= {"id", "label", "status", "value", "threshold", "message"}
        assert check["status"] in CHECK_STATUSES
    assert set(report["outputs"]) >= {"model", "blend", "previews"}
    for p in report["outputs"]["previews"]:
        assert set(p) >= {"name", "view", "variant"}
    assert set(report["environment"]) >= {"gpu", "gpu_renderer", "python", "numpy"}


def metric_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["key"]: m for m in report["metrics"]}


def norm_material(m: dict[str, Any]) -> tuple:
    mode = m.get("alphaMode", "OPAQUE")
    return mode, bool(m.get("doubleSided", False)), (m.get("alphaCutoff", 0.5) if mode == "MASK" else None)


# ================================================================================================================
# syntax (Python 3.11 = Blender 4.2 / 5.1 interpreters)
# ================================================================================================================


def test_python311_syntax() -> None:
    if not PYTHON_311.is_file():
        pytest.skip("Blender 4.2 bundled Python 3.11 not available")
    files = [str(SCRIPTS_DIR / "auto_mesh_tuner.py")] + sorted(glob.glob(str(SCRIPTS_DIR / "img3d_amt" / "*.py")))
    code = ("import sys\nfor p in sys.argv[1:]:\n"
            "    compile(open(p, encoding='utf-8').read(), p, 'exec')\nprint('OK', len(sys.argv) - 1)")
    proc = subprocess.run([str(PYTHON_311), "-I", "-c", code, *files], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"OK {len(files)}"


# ================================================================================================================
# meshdata self-test inside Blender
# ================================================================================================================


@pytest.mark.blender
def test_meshdata_selftest(blender52: Path, synthetic: dict[str, Any]) -> None:
    run = run_blender(blender52, HELPERS_DIR / "selftest_meshdata.py",
                      ["--scripts", str(SCRIPTS_DIR), "--glb", str(synthetic["synthetic"]["path"])],
                      tag="selftest_meshdata", timeout=300)
    assert run.returncode == 0, run.tail()
    line = next(x for x in run.stdout.splitlines() if "@@SELFTEST@@" in x)
    data = json.loads(line[line.index("@@SELFTEST@@") + len("@@SELFTEST@@"):])
    failed = {k: v for k, v in data["results"].items() if not v["ok"]}
    assert not failed, (failed, data["errors"])
    assert len(data["results"]) >= 15


# ================================================================================================================
# synthetic end-to-end
# ================================================================================================================


@pytest.mark.blender
def test_synthetic_end_to_end(tuner, synthetic: dict[str, Any], inspect_models) -> None:
    out = fresh_dir("synthetic_cfg")
    src = synthetic["synthetic"]["path"]
    result_dir = out / "result"
    cfg = write_config(out / "cfg.json", {
        "schema": 1, "input": str(src), "output_dir": str(result_dir),
        "options": {"preset": "balanced", "island_min_ratio": 0.002, "save_blend": True, "bogus_option": 1},
        "outputs": {"model": "tuned.glb", "report": "report.json", "blend": None, "previews_dir": "previews"},
        "context": {"job_id": "job_synthetic", "tuning_id": "tn_test", "source": "synthetic"}})
    run = tuner(["--config", str(cfg)], tag="synthetic_cfg")
    assert run.returncode == 0, run.tail()
    finals = assert_protocol_sequence(run)
    for sid in STUB_STEPS:
        assert finals[sid]["status"] == "skipped" and finals[sid]["message"] == "stubbed for the framework test"
    for sid in ("load", "analyze", "validation", "export"):
        assert finals[sid]["status"] == "done", finals[sid]
    assert any(e["level"] == "warning" and "bogus_option" in e["message"] for e in run.of("log"))

    res = run.result
    assert res["status"] == "completed_with_warnings"  # the unknown option
    assert res["model"] == "tuned.glb" and res["report"] == "report.json" and res["blend"] == "tuned.blend"
    assert res["validation"]["passed"] is True
    assert any("bogus_option" in w for w in res["warnings"])

    report = json.loads((result_dir / "report.json").read_text(encoding="utf-8"))
    assert_report_schema(report)
    assert report["status"] == "completed_with_warnings"
    assert report["input"]["format"] == "glb" and report["input"]["size_bytes"] == src.stat().st_size
    assert report["options"]["island_min_ratio"] == 0.002 and report["context"]["job_id"] == "job_synthetic"
    m = metric_map(report)
    man = synthetic["synthetic"]
    assert m["holes"]["before"] == man["holes"] == 3
    assert m["largest_hole_edges"]["before"] >= 5
    assert m["degenerate_faces"]["before"] == man["degenerate_faces"]
    assert m["components"]["before"] == 1 + man["islands"] + man["degenerate_faces"]
    assert m["small_components"]["before"] == man["islands"] + man["degenerate_faces"]
    assert m["duplicate_vertices"]["before"] >= man["split_duplicate_vertices"]
    assert m["duplicate_vertices"]["after"] == 0
    assert m["inconsistent_normal_edges"]["before"] > 0
    assert m["faces"]["before"] == m["faces"]["after"] == man["faces_total"]
    assert m["uv_layers"]["before"] == 1 and m["materials"]["before"] == 1 and m["textures"]["before"] == 1
    assert m["deviation_p95_pct"]["after"] < 1e-3 and m["deviation_max_pct"]["after"] < 1e-2
    assert m["symmetry_error_pct"]["before"] is not None
    for key in ("vertices", "non_manifold_edges", "boundary_edges", "surface_area", "mean_edge_length", "armatures"):
        assert key in m
    checks = {c["id"]: c for c in report["validation"]["checks"]}
    assert {"deviation", "bbox", "faces", "non_manifold", "holes", "normals", "uv_layers", "materials",
            "textures"} <= set(checks)
    assert all(c["status"] == "pass" for c in checks.values()), checks

    # outputs: tuned GLB re-importable with the same UV/material/texture counts, previews, .blend
    tuned = result_dir / "tuned.glb"
    assert tuned.is_file() and (result_dir / "tuned.blend").is_file()
    before, after = inspect_models([src, tuned], tag="inspect_synthetic")
    for key in ("mesh_objects", "faces", "uv_layers", "materials", "images", "image_sizes",
                "material_backface_culling"):
        assert before[key] == after[key], (key, before[key], after[key])
    assert not [a for a in after["attributes"] if a.startswith("amt_")]
    previews = res["previews"]
    assert len(previews) == 8 and {p["variant"] for p in previews} == {"before", "after"}
    assert {p["view"] for p in previews} == {"front", "three_quarter", "side", "back"}  # sphere: no face view
    for p in previews:
        path = result_dir / "previews" / p["name"]
        assert path.is_file() and png_size(path) == (768, 768)
    assert report["outputs"]["previews"] == previews


@pytest.mark.blender
def test_cli_flags_multi_object_join(tuner, synthetic: dict[str, Any], inspect_models) -> None:
    out = fresh_dir("synthetic_multi_flags")
    run = tuner(["--input", str(synthetic["multi"]["path"]), "--output-dir", str(out),
                 "--options", '{"render_previews": false, "preset": "conservative"}'], tag="synthetic_multi_flags")
    assert run.returncode == 0, run.tail()
    finals = assert_protocol_sequence(run)
    load = finals["load"]["details"]
    assert load["mesh_objects"] == 4 and load["joined_objects"] == 4
    assert run.result["previews"] == [] and not (out / "previews").exists()
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert_report_schema(report)
    assert report["options"]["preset"] == "conservative" and report["options"]["max_hole_edges"] == 120
    (row,) = inspect_models([out / "tuned.glb"], tag="inspect_multi")
    assert row["mesh_objects"] == 1 and row["faces"] == synthetic["synthetic"]["faces_total"]


@pytest.mark.blender
def test_rigged_input_keeps_skin(tuner, synthetic: dict[str, Any], inspect_models) -> None:
    out = fresh_dir("synthetic_rigged")
    run = tuner(["--input", str(synthetic["rigged"]["path"]), "--output-dir", str(out)], tag="synthetic_rigged")
    assert run.returncode == 0, run.tail()
    finals = assert_protocol_sequence(run)
    assert finals["load"]["details"]["armatures"] == 1 and finals["load"]["details"]["mesh_objects"] == 1
    assert finals["export"]["details"]["model"]["skins"] is True
    m = metric_map(json.loads((out / "report.json").read_text(encoding="utf-8")))
    assert m["armatures"]["before"] == 1 and m["max_bone_influences"]["before"] == 2
    assert m["unnormalized_weight_verts"]["before"] == 0
    src_row, out_row = inspect_models([synthetic["rigged"]["path"], out / "tuned.glb"], tag="inspect_rigged")
    for key in ("armatures", "bones", "skinned_meshes", "mesh_objects", "faces", "vertex_groups", "images"):
        assert src_row[key] == out_row[key], (key, src_row[key], out_row[key])


@pytest.mark.blender
@pytest.mark.parametrize("kind", ["obj", "stl"])
def test_other_input_formats(tuner, synthetic: dict[str, Any], kind: str) -> None:
    out = fresh_dir(f"synthetic_{kind}")
    run = tuner(["--input", str(synthetic[kind]["path"]), "--output-dir", str(out),
                 "--options", '{"render_previews": false}'], tag=f"synthetic_{kind}")
    assert run.returncode == 0, run.tail()
    assert_protocol_sequence(run)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["input"]["format"] == kind
    m = metric_map(report)
    assert m["faces"]["after"] == synthetic["synthetic"]["faces_total"] and m["holes"]["before"] == 3
    assert report["validation"]["passed"] is True


@pytest.mark.blender
@pytest.mark.parametrize("material", [
    {"alphaMode": "MASK", "alphaCutoff": 0.3, "doubleSided": True},
    {"alphaMode": "BLEND", "doubleSided": True},
])
def test_material_settings_round_trip(tuner, synthetic: dict[str, Any], material: dict[str, Any]) -> None:
    """alphaMode / alphaCutoff / doubleSided of the input GLB survive import -> export (verified + patched)."""
    tag = f"material_{material['alphaMode'].lower()}"
    out = fresh_dir(tag)
    doc = read_glb_json(synthetic["synthetic"]["path"])
    for key in ("alphaMode", "alphaCutoff", "doubleSided"):
        doc["materials"][0].pop(key, None)
    doc["materials"][0].update(material)
    src = write_glb_with_json(synthetic["synthetic"]["path"], out / "input.glb", doc)
    run = tuner(["--input", str(src), "--output-dir", str(out / "result"), "--options",
                 '{"render_previews": false}'], tag=tag)
    assert run.returncode == 0, run.tail()
    out_doc = read_glb_json(out / "result" / "tuned.glb")
    assert [norm_material(m) for m in out_doc["materials"]] == [norm_material(m) for m in doc["materials"]]
    export = [s for s in run.of("step") if s["id"] == "export"][-1]["details"]["model"]
    assert export["fidelity"]["checked"] is True and export["fidelity"]["issues"] == []


# ================================================================================================================
# pipeline failure policy (step functions replaced by test doubles inside Blender)
# ================================================================================================================


def _injected(blender52: Path, mode: str, args: list[str], tag: str) -> BlenderRun:
    return run_blender(blender52, HELPERS_DIR / "run_with_injected_steps.py",
                       ["--scripts", str(SCRIPTS_DIR), "--mode", mode, "--", *args], tag=tag, timeout=300)


@pytest.mark.blender
def test_optional_step_failures_roll_back(blender52: Path, synthetic: dict[str, Any]) -> None:
    out = fresh_dir("injected_optional_failures")
    run = _injected(blender52, "optional_failures",
                    ["--input", str(synthetic["synthetic"]["path"]), "--output-dir", str(out),
                     "--options", '{"render_previews": false}'], "injected_optional_failures")
    assert run.returncode == 0, run.tail()
    steps = {s["id"]: s for s in run.of("step") if s["status"] != "running"}
    assert steps["geometry_repair"]["status"] == "done"
    assert steps["face_refinement"]["status"] == "failed"
    assert steps["face_refinement"]["details"] == {"code": "face_test_failure", "rolled_back": True}
    assert steps["topology_tuning"]["status"] == "failed"
    assert steps["topology_tuning"]["details"]["code"] == "internal_error"
    assert "ZeroDivisionError" in steps["topology_tuning"]["message"]
    assert steps["rig_optimization"]["status"] == "warning"
    assert steps["validation"]["status"] == "done" and steps["export"]["status"] == "done"
    assert not run.errors
    res = run.result
    assert res["status"] == "completed_with_warnings"
    assert any("Face refinement failed" in w and "rolled back" in w for w in res["warnings"])
    assert any("Rig optimization: Injected rig warning" in w for w in res["warnings"])
    m = {x["key"]: x for x in res["metrics"]}
    assert m["deviation_max_pct"]["after"] < 1e-2  # the +0.05 shift of the failed step was rolled back
    assert m["hidden_faces"] == {"key": "hidden_faces", "label": "Hidden faces", "unit": None, "before": 123,
                                 "after": 0, "better": "lower"}
    assert "pose_max_stretch" not in m  # metric override of the failed step rolled back
    checks = {c["id"]: c for c in res["validation"]["checks"]}
    assert checks["visible_surface"]["status"] == "pass"
    export = steps["export"]["details"]["model"]
    assert export["subdivision_kept_as_modifier"] == 0 and export["removed_attributes"] == []
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert_report_schema(report)
    assert [s["status"] for s in report["steps"]] == ["done", "done", "done", "failed", "failed", "warning", "done",
                                                      "done"]


@pytest.mark.blender
def test_required_step_failure_exit_4(blender52: Path, synthetic: dict[str, Any]) -> None:
    out = fresh_dir("injected_required_failure")
    run = _injected(blender52, "required_failure",
                    ["--input", str(synthetic["synthetic"]["path"]), "--output-dir", str(out)],
                    "injected_required_failure")
    assert run.returncode == 4, run.tail()
    assert run.events[-1]["event"] == "error"
    err = run.errors[-1]
    assert err["code"] == "repair_test_failure" and err["step"] == "geometry_repair"
    assert [(s["id"], s["status"]) for s in run.of("step")][-1] == ("geometry_repair", "failed")
    assert run.result is None and not (out / "tuned.glb").exists()
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["error"]["step"] == "geometry_repair"


@pytest.mark.blender
@pytest.mark.parametrize("mode", ["bad_return", "edit_mode_leak"])
def test_misbehaving_steps_are_contained(blender52: Path, synthetic: dict[str, Any], mode: str) -> None:
    out = fresh_dir(f"injected_{mode}")
    run = _injected(blender52, mode, ["--input", str(synthetic["synthetic"]["path"]), "--output-dir", str(out),
                                      "--options", '{"render_previews": false}'], f"injected_{mode}")
    assert run.returncode == 0, run.tail()
    face = [s for s in run.of("step") if s["id"] == "face_refinement"][-1]
    if mode == "bad_return":
        assert face["status"] == "failed" and face["details"]["code"] == "bad_step_result"
    else:
        assert face["status"] == "done"
    assert run.result["status"] in ("completed", "completed_with_warnings") and (out / "tuned.glb").is_file()


# ================================================================================================================
# error handling / exit codes
# ================================================================================================================

_BAD_CONFIGS = {
    "invalid_option_type": ({"options": {"smoothing": "abc"}}, "invalid_options"),
    "unknown_preset": ({"options": {"preset": "ultra"}}, "invalid_options"),
    "output_name_with_folder": ({"outputs": {"model": "../escape.glb"}}, "bad_config"),
    "unsupported_schema": ({"schema": 7}, "unsupported_schema"),
}


@pytest.mark.blender
@pytest.mark.parametrize("case", sorted(_BAD_CONFIGS))
def test_bad_config_exit_2(tuner, case: str) -> None:
    out = fresh_dir(f"bad_config_{case}")
    patch, code = _BAD_CONFIGS[case]
    data = {"schema": 1, "input": str(out / "in.glb"), "output_dir": str(out / "result")}
    data.update(patch)
    cfg = write_config(out / "cfg.json", data)
    run = tuner(["--config", str(cfg)], tag=f"bad_config_{case}", timeout=120)
    assert run.returncode == 2, run.tail()
    assert [e["event"] for e in run.events] == ["hello", "error"]
    assert run.errors[0]["code"] == code and run.errors[0]["message"]


@pytest.mark.blender
def test_malformed_json_and_missing_args_exit_2(blender52: Path) -> None:
    """Goes through the real entry script (the other 5.2 tests use the stubbed helper)."""
    from amt_testlib import ENTRY_SCRIPT

    out = fresh_dir("bad_json")
    cfg = out / "cfg.json"
    cfg.write_text("{not json", encoding="utf-8")
    run = run_blender(blender52, ENTRY_SCRIPT, ["--config", str(cfg)], tag="bad_json", timeout=120)
    assert run.returncode == 2 and run.errors[0]["code"] == "bad_config", run.tail()
    assert run.events[0]["event"] == "hello" and run.events[0]["blender_version"].startswith("5.")
    run = run_blender(blender52, ENTRY_SCRIPT, [], tag="missing_args", timeout=120)
    assert run.returncode == 2 and run.errors[0]["code"] == "bad_arguments", run.tail()


@pytest.mark.blender
@pytest.mark.parametrize("case", ["missing", "corrupt_glb", "unsupported_ext"])
def test_input_errors_exit_3(tuner, case: str) -> None:
    out = fresh_dir(f"input_error_{case}")
    if case == "missing":
        src, code = out / "does_not_exist.glb", "input_not_found"
    elif case == "corrupt_glb":
        src, code = out / "corrupt.glb", "input_unreadable"
        src.write_bytes(b"this is not a glb file" * 10)
    else:
        src, code = out / "mesh.txt", "unsupported_format"
        src.write_text("v 0 0 0\n", encoding="utf-8")
    run = tuner(["--input", str(src), "--output-dir", str(out / "result")], tag=f"input_error_{case}", timeout=120)
    assert run.returncode == 3, run.tail()
    assert [e["event"] for e in run.events][:2] == ["hello", "plan"]
    steps = run.of("step")
    assert [(s["id"], s["status"]) for s in steps] == [("load", "running"), ("load", "failed")]
    err = run.errors[-1]
    assert err["code"] == code and err["step"] == "load"
    report = json.loads((out / "result" / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["error"]["code"] == code


@pytest.mark.blender
def test_blender_42_is_rejected(blender42: Path, synthetic: dict[str, Any]) -> None:
    from amt_testlib import ENTRY_SCRIPT

    out = fresh_dir("blender42")
    run = run_blender(blender42, ENTRY_SCRIPT, ["--input", str(synthetic["synthetic"]["path"]), "--output-dir",
                                                str(out)], tag="blender42", timeout=120)
    assert run.returncode == 2, run.tail()
    assert [e["event"] for e in run.events] == ["hello", "error"]
    assert run.events[0]["blender_version"].startswith("4.2")
    err = run.errors[0]
    assert err["code"] == "blender_incompatible" and "requires Blender 5.1" in err["message"]
    assert not any(out.iterdir())


# ================================================================================================================
# real TRELLIS jobs (stub steps -> deviation ~ 0, export round-trip fidelity)
# ================================================================================================================


def _image_bytes(doc: dict[str, Any]) -> list[int]:
    views = doc.get("bufferViews") or []
    return sorted(views[im["bufferView"]]["byteLength"] for im in doc.get("images") or [] if "bufferView" in im)


def _record_summary(job_id: str, data: dict[str, Any]) -> None:
    path = OUTPUT_ROOT / "real_jobs_summary.json"
    summary = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    summary[job_id] = data
    path.write_text(json.dumps(summary, indent=1), encoding="utf-8")


@pytest.mark.blender
@pytest.mark.slow
@pytest.mark.parametrize("job_id", REAL_JOBS)
def test_real_job_end_to_end(tuner, job_id: str) -> None:
    assert job_id not in EXCLUDED_JOBS
    src = JOBS_DIR / job_id / "model.glb"
    if not src.is_file():
        pytest.skip(f"{src} not available")
    out = fresh_dir(f"real_{job_id}")
    cfg = write_config(out / "cfg.json", {"schema": 1, "input": str(src), "output_dir": str(out / "result"),
                                          "options": {"preset": "balanced"},
                                          "context": {"job_id": job_id, "tuning_id": "tn_framework_test",
                                                      "source": "trellis2"}})
    run = tuner(["--config", str(cfg)], tag=f"real_{job_id}", timeout=1800)
    assert run.returncode == 0, run.tail()
    finals = assert_protocol_sequence(run)
    res = run.result
    assert res["status"] == "completed" and res["validation"]["passed"] is True, res["warnings"]
    result_dir = out / "result"
    report = json.loads((result_dir / "report.json").read_text(encoding="utf-8"))
    assert_report_schema(report)
    steps = {s["id"]: s for s in report["steps"]}
    for sid in ("load", "analyze", "validation", "export"):
        assert steps[sid]["duration_s"] > 0 and steps[sid]["memory_mb"]
    load = finals["load"]["details"]
    assert load["source_frame"]["source"] == "trellis2" and load["source_frame"]["unit_cube"] is True
    assert load["merged_vertices"] > 0.3 * load["raw_vertices"]  # UV-seam duplicates welded

    # export fidelity vs the input GLB
    src_doc, out_doc = read_glb_json(src), read_glb_json(result_dir / "tuned.glb")
    assert [norm_material(m) for m in src_doc["materials"]] == [norm_material(m) for m in out_doc["materials"]]
    assert len(src_doc["images"]) == len(out_doc["images"]) and len(src_doc["textures"]) == len(out_doc["textures"])
    assert {i["mimeType"] for i in out_doc["images"]} == {i["mimeType"] for i in src_doc["images"]} == {"image/webp"}
    assert "EXT_texture_webp" in out_doc.get("extensionsRequired", [])
    assert _image_bytes(src_doc) == _image_bytes(out_doc)  # AUTO re-embeds the original WebP bytes
    fidelity = finals["export"]["details"]["model"]["fidelity"]
    assert fidelity["checked"] is True and fidelity["issues"] == []

    m = metric_map(report)
    assert m["deviation_mean_pct"]["after"] < 1e-4 and m["deviation_p95_pct"]["after"] < 1e-3
    assert m["deviation_max_pct"]["after"] < 1e-2
    assert m["holes"]["before"] > 0 and m["non_manifold_edges"]["before"] > 0
    assert m["small_components"]["before"] > 0 and 0 < m["symmetry_error_pct"]["before"] < 0.5
    analysis_s = report["analysis"]["before"]["timings_s"]["total"]
    assert analysis_s < 15.0

    names = {p["name"] for p in res["previews"]}
    assert len(names) == 12 and {"before_face.png", "after_face.png", "before_face_texture.png"} <= names
    for name in names:
        assert png_size(result_dir / "previews" / name) == (768, 768)

    _record_summary(job_id, {
        "input_mb": round(src.stat().st_size / 2**20, 1),
        "output_mb": round((result_dir / "tuned.glb").stat().st_size / 2**20, 1),
        "wall_s": round(run.duration_s, 2), "report_duration_s": report["duration_s"],
        "steps_s": {s["id"]: s["duration_s"] for s in report["steps"]},
        "timings": report["timings"], "analysis_before_s": report["analysis"]["before"]["timings_s"],
        "analysis_after_s": report["analysis"]["after"]["timings_s"],
        "peak_memory_mb": report["environment"]["peak_memory_mb"],
        "metrics_before": {k: v["before"] for k, v in m.items()},
        "deviation": {k: m[k]["after"] for k in ("deviation_mean_pct", "deviation_p95_pct", "deviation_max_pct")},
        "gpu": report["environment"]["gpu_renderer"],
    })
