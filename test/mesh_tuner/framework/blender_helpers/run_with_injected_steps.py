"""Blender helper (test only): run the real tuner CLI with step functions replaced by test doubles.

Run:
  blender --background --factory-startup --python-exit-code 86 --python run_with_injected_steps.py -- \
      --scripts <backend/blender_scripts> --mode <mode> -- <tuner args...>
Modes:
  optional_failures  repair: done + visible mask + hidden_faces metric; face: mutates mesh/modifiers/store then
                     raises StepError; topology: raises ZeroDivisionError; rig: returns a warning outcome
  required_failure   repair raises StepError (run must abort with exit 4)
  bad_return         face returns an int (bad_step_result, optional -> continue)
  edit_mode_leak     face enters Edit mode and returns done (pipeline must restore Object mode before export)
"""

import sys

argv = sys.argv[sys.argv.index("--") + 1:]
split = argv.index("--")
own, tuner_args = argv[:split], argv[split + 1:]
scripts = own[own.index("--scripts") + 1]
mode = own[own.index("--mode") + 1]
sys.path.insert(0, scripts)

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from img3d_amt import cli, face, meshdata, repair, rig, topology  # noqa: E402
from img3d_amt.context import StepError, StepOutcome  # noqa: E402


def repair_with_mask(ctx):
    n = len(ctx.original.data.polygons)
    ctx.store["original_visible_face_mask"] = np.ones(n, dtype=bool)
    ctx.set_metric("hidden_faces", before=123, after=0)
    ctx.progress(0.5, "pretending")
    return StepOutcome("done", "mask stored", {"faces": n})


def face_mutate_then_fail(ctx):
    obj = ctx.require_primary()
    co = meshdata.get_positions(obj.data)
    meshdata.set_positions(obj.data, co + 0.05)
    meshdata.set_attribute(obj.data, "amt_face_region", np.ones(len(co), np.float32))
    obj.modifiers.new("AMT_test_subsurf", "SUBSURF")
    ctx.store["face_tmp"] = True
    ctx.set_metric("pose_max_stretch", after=99.0)
    raise StepError("face_test_failure", "Injected face failure after mutating the mesh")


def topology_crash(ctx):
    return 1 / 0


def rig_warning(ctx):
    return StepOutcome("warning", "Injected rig warning", {"k": 1})


def repair_fail(ctx):
    raise StepError("repair_test_failure", "Injected repair failure")


def face_bad_return(ctx):
    return 42


def face_edit_mode(ctx):
    obj = ctx.require_primary()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    return StepOutcome("done", "left in edit mode", {})


if mode == "optional_failures":
    repair.run, face.run, topology.run, rig.run = repair_with_mask, face_mutate_then_fail, topology_crash, rig_warning
elif mode == "required_failure":
    repair.run = repair_fail
elif mode == "bad_return":
    face.run = face_bad_return
elif mode == "edit_mode_leak":
    face.run = face_edit_mode
else:
    raise SystemExit(f"unknown mode {mode}")

sys.exit(cli.main(tuner_args))
