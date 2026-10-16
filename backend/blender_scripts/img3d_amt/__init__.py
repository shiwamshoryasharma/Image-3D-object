"""Img3D Auto Mesh Tuner - Blender-side package (runs INSIDE Blender's bundled Python, never imported by the backend).

Layout (see ``.claude/skills/img3d-auto-mesh-tuner/DESIGN.md`` sections 3-5):

* ``protocol``   - ``@@AMT@@`` JSON-lines events on stdout (hello/plan/step/progress/log/result/error).
* ``config``     - ``TunerConfig`` / ``TuningOptions`` parsing (cfg.json or CLI flags), presets, validation.
* ``context``    - ``TunerContext`` shared by all steps, ``StepOutcome``, ``StepError``.
* ``meshdata``   - vectorized numpy helpers over ``bpy.types.Mesh`` (arrays, topology, smoothing, attributes).
* ``io_mesh``    - import (glb/gltf/obj/fbx/ply/stl/blend), weld, GLB export with fidelity verification.
* ``analysis``   - mesh metrics (DESIGN section 3 metric keys).
* ``pipeline``   - the 8 fixed steps, progress weights, rollback of optional steps, exit codes.
* ``report`` / ``previews`` / ``validation`` / ``cli``.
* step modules ``repair`` / ``face`` / ``topology`` / ``rig`` expose ``run(ctx) -> StepOutcome``.

The package must stay importable on Python 3.11 (Blender 5.1) and only relies on bpy/bmesh/mathutils/numpy.
``__init__``, ``protocol`` and ``config`` do not import bpy so they can be unit-tested outside Blender.
"""

from __future__ import annotations

__version__ = "1.0.0"
PROTOCOL_VERSION = 1
MIN_BLENDER_VERSION = (5, 1, 0)
TOOL_NAME = "Img3D Auto Mesh Tuner"

__all__ = ["MIN_BLENDER_VERSION", "PROTOCOL_VERSION", "TOOL_NAME", "__version__"]
