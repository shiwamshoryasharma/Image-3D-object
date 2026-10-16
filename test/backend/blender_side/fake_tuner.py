"""TEST-ONLY protocol-conformant Auto Mesh Tuner stand-in (runs INSIDE Blender's Python, DESIGN.md section 3).

Used by ``test/backend/test_mesh_tuner_api.py::test_real_blender_plumbing`` to exercise the real backend plumbing
(launcher runner + tuning worker + Job Object kill) with the real Blender executable, without depending on the
real tuner's mesh algorithms.

Invocation (by the worker): ``blender -b --factory-startup ... --python fake_tuner.py -- --config <cfg.json>``.
Behaviour: emits ``hello`` (with the Blender pid), ``plan``, the 8 fixed steps (``running`` -> ``done``), imports
the input GLB with the real glTF importer, exports ``tuned.glb``, writes PNG previews and ``report.json`` and emits
``result``. ``options.symmetry == "symmetrize"`` selects the *slow* mode: geometry_repair keeps emitting progress
for ~60 s so a test can cancel the run (the Blender process tree must be killed).
Exit codes: 0 ok, 2 bad arguments, 3 input unreadable.
"""

import json
import os
import sys
import time

import bpy

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(line_buffering=True)

SENTINEL = "@@AMT@@"
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
STEPS = (
    ("load", "Load mesh", 0.05),
    ("analyze", "Mesh analysis", 0.07),
    ("geometry_repair", "Geometry repair", 0.30),
    ("face_refinement", "Face refinement", 0.15),
    ("topology_tuning", "Topology tuning", 0.20),
    ("rig_optimization", "Rig optimization", 0.05),
    ("validation", "Validation", 0.13),
    ("export", "Save results", 0.05),
)


def emit(obj):
    sys.stdout.write(SENTINEL + json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def write_png(path, rgba):
    size = 32
    image = bpy.data.images.new(os.path.basename(path), width=size, height=size)
    image.pixels.foreach_set(list(rgba) * (size * size))
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()


def main():
    if len(ARGS) != 2 or ARGS[0] != "--config":
        emit({"event": "error", "code": "bad_arguments", "message": "usage: -- --config <cfg.json>", "step": None,
              "traceback": None})
        return 2
    with open(ARGS[1], encoding="utf-8") as fh:
        cfg = json.load(fh)
    options = cfg.get("options") or {}
    outputs = cfg["outputs"]
    out_dir = cfg["output_dir"]
    slow = options.get("symmetry") == "symmetrize"
    started = time.time()
    emit({"event": "hello", "protocol": 1, "script_version": "fake-tuner-1", "blender_version": bpy.app.version_string,
          "python": sys.version.split()[0], "gpu": None, "pid": os.getpid()})
    emit({"event": "plan", "steps": [{"id": sid, "label": label} for sid, label, _ in STEPS]})
    overall = 0.0
    details = {}
    faces_before = 0
    for sid, label, weight in STEPS:
        step_start = time.time()
        emit({"event": "step", "id": sid, "status": "running", "message": label, "duration_s": None,
              "progress": round(overall, 4), "details": {}})
        if sid == "load":
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.ops.import_scene.gltf(filepath=cfg["input"])
            meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
            if not meshes:
                emit({"event": "error", "code": "input_unreadable", "message": "No mesh in input", "step": sid,
                      "traceback": None})
                return 3
            faces_before = sum(len(o.data.polygons) for o in meshes)
            details = {"objects": len(meshes), "faces": faces_before}
        elif sid == "geometry_repair" and slow:
            for index in range(600):
                emit({"event": "progress", "id": sid, "fraction": index / 600.0,
                      "progress": round(overall + weight * index / 600.0, 4), "message": f"slow {index}"})
                time.sleep(0.1)
        elif sid == "export":
            os.makedirs(os.path.join(out_dir, outputs["previews_dir"]), exist_ok=True)
            bpy.ops.export_scene.gltf(filepath=os.path.join(out_dir, outputs["model"]), export_format="GLB")
            write_png(os.path.join(out_dir, outputs["previews_dir"], "before_front.png"), (0.8, 0.3, 0.2, 1.0))
            write_png(os.path.join(out_dir, outputs["previews_dir"], "after_front.png"), (0.2, 0.6, 0.9, 1.0))
        overall += weight
        emit({"event": "step", "id": sid, "status": "done", "message": f"{label} ok",
              "duration_s": round(time.time() - step_start, 3), "progress": round(overall, 4), "details": details})
    metrics = [{"key": "faces", "label": "Faces", "unit": None, "before": faces_before, "after": faces_before,
                "better": "neutral"}]
    previews = [{"name": "before_front.png", "view": "front", "variant": "before"},
                {"name": "after_front.png", "view": "front", "variant": "after"}]
    validation = {"passed": True, "checks": []}
    report = {"schema": 1, "tool": "Img3D Auto Mesh Tuner", "script_version": "fake-tuner-1", "protocol": 1,
              "blender_version": bpy.app.version_string, "duration_s": round(time.time() - started, 3),
              "options": options, "steps": [], "metrics": metrics, "warnings": [], "validation": validation,
              "outputs": {"model": outputs["model"], "blend": None, "previews": previews}}
    with open(os.path.join(out_dir, outputs["report"]), "w", encoding="utf-8") as fh:
        json.dump(report, fh)
    emit({"event": "result", "status": "completed", "model": outputs["model"], "report": outputs["report"],
          "blend": None, "previews": previews, "metrics": metrics, "warnings": [], "validation": validation})
    return 0


sys.exit(main())
