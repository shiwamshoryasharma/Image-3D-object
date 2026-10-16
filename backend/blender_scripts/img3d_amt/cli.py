"""Command-line entry inside Blender: ``hello`` -> config -> pipeline -> exit code.

Exit codes (DESIGN section 3): 0 ok · 2 bad arguments/config · 3 input unreadable / no mesh · 4 processing/export
failure (after an ``error`` event) · 86 uncaught exception (``--python-exit-code 86``; an ``error`` event with
code ``internal_error`` is emitted first).

Environment: ``IMG3D_AMT_DISABLE_GPU=1`` skips the GPU probe (``hello.gpu`` = false, CPU fallbacks everywhere).
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import traceback

import bpy

from . import PROTOCOL_VERSION, __version__, protocol
from .config import ConfigError, load_config
from .context import probe_gpu

log = logging.getLogger(__name__)


def script_argv(argv: list[str] | None = None) -> list[str]:
    """Arguments after Blender's ``--`` separator (``argv`` defaults to ``sys.argv``)."""
    argv = sys.argv if argv is None else argv
    return list(argv[argv.index("--") + 1:]) if "--" in argv else []


def _gpu_info() -> dict:
    if os.environ.get("IMG3D_AMT_DISABLE_GPU", "").strip().lower() in ("1", "true", "yes", "on"):
        from . import context

        context._GPU_INFO = {"available": False, "renderer": None, "backend": None,
                             "error": "disabled by IMG3D_AMT_DISABLE_GPU"}
        return context._GPU_INFO
    return probe_gpu()


def main(argv: list[str] | None = None) -> int:
    """Run the tuner with ``argv`` (script arguments, i.e. what follows ``--``). Returns the exit code."""
    protocol.configure_stdout()
    protocol.install_logging("info")
    args = script_argv() if argv is None else list(argv)
    gpu = _gpu_info()
    protocol.hello(protocol=PROTOCOL_VERSION, script_version=__version__, blender_version=bpy.app.version_string,
                   python=platform.python_version(), gpu=bool(gpu.get("available")),
                   gpu_renderer=gpu.get("renderer"))
    try:
        config = load_config(args)
    except ConfigError as exc:
        protocol.error(exc.code, exc.message)
        return exc.exit_code
    protocol.install_logging(config.log_level)

    from .pipeline import Pipeline  # imported late: keeps argument errors fast and import failures reportable

    try:
        return Pipeline(config).run()
    except Exception as exc:
        protocol.error("internal_error", f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        raise
