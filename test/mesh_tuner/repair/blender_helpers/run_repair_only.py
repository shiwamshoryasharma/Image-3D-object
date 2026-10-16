"""Blender helper (repair tests): run the REAL tuner CLI with only framework steps + geometry_repair active.

The face/topology/rig step functions (owned by other agents, possibly still in development) are replaced by
"skipped" doubles so the exported model shows the effect of the repair step alone.

  blender --background --factory-startup --python-exit-code 86 --python run_repair_only.py -- \
      --scripts <backend/blender_scripts> [--full] -- <tuner args...>

``--full`` keeps every real step (whole pipeline).
"""

import sys

argv = sys.argv[sys.argv.index("--") + 1:]
split = argv.index("--")
own, tuner_args = argv[:split], argv[split + 1:]
scripts = own[own.index("--scripts") + 1]
full = "--full" in own
sys.path.insert(0, scripts)

from img3d_amt import cli  # noqa: E402
from img3d_amt.context import StepOutcome  # noqa: E402

if not full:
    import importlib

    def _skip(ctx):
        return StepOutcome("skipped", "disabled for the repair test", {})

    for name in ("face", "topology", "rig"):
        try:
            mod = importlib.import_module(f"img3d_amt.{name}")
            mod.run = _skip
        except Exception as exc:  # a module under development may not import: stub it entirely
            import types

            stub = types.ModuleType(f"img3d_amt.{name}")
            stub.run = _skip
            sys.modules[f"img3d_amt.{name}"] = stub
            print(f"[run_repair_only] stubbed {name}: {exc}")

sys.exit(cli.main(tuner_args))
