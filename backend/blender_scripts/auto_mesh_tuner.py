"""Img3D Auto Mesh Tuner - Blender entry script.

Run (Blender >= 5.1)::

    blender.exe --background --factory-startup --python-exit-code 86 \
        --python backend/blender_scripts/auto_mesh_tuner.py -- --config <cfg.json>
    # or: -- --input <mesh> --output-dir <dir> [--options '<json>'] [--log-level debug]

Protocol: ``@@AMT@@{json}`` lines on stdout (see ``img3d_amt/protocol.py``). Exit codes: 0 ok, 2 bad arguments /
config / Blender too old, 3 input unreadable or no mesh, 4 processing/export failure, 86 uncaught exception.
This file must stay importable by old Blender Pythons so the version check can report a clean error.
"""

import os
import sys


def _main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError, OSError):
            pass
    here = os.path.dirname(os.path.abspath(__file__))
    norm = os.path.normcase(here)
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != norm]
    sys.path.insert(0, here)

    import platform

    import bpy
    from img3d_amt import MIN_BLENDER_VERSION, PROTOCOL_VERSION, __version__, protocol

    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if tuple(bpy.app.version) < tuple(MIN_BLENDER_VERSION):
        required = ".".join(str(v) for v in MIN_BLENDER_VERSION[:2])
        protocol.hello(protocol=PROTOCOL_VERSION, script_version=__version__,
                       blender_version=bpy.app.version_string, python=platform.python_version(), gpu=None)
        protocol.error("blender_incompatible",
                       "Auto Mesh Tuner requires Blender {} or newer (this is Blender {})".format(
                           required, bpy.app.version_string))
        sys.exit(2)

    from img3d_amt import cli

    sys.exit(cli.main(argv))


_main()
