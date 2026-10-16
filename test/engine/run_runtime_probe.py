"""Diagnostic: run ``inference.trellis.environment.prepare_runtime`` and print the report (no weights loaded).

Usage::

    E:\\14-OCT-2026\\Img3D\\backend\\python\\python.exe E:\\14-OCT-2026\\Img3D\\test\\engine\\run_runtime_probe.py [--attention auto]

Exit code 0 if the runtime is ready (torch/CUDA/extensions/attention/trellis2 import), 1 otherwise.
"""

from __future__ import annotations

import os
import site
import sys

_USER_SITE = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _USER_SITE]

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
from pathlib import Path  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from inference.trellis.environment import prepare_runtime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attention", default=None, choices=("auto", "flash_attn", "xformers", "sdpa"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

    from config.settings import Settings

    overrides = {"runtime_dir": PROJECT_ROOT / "test" / "outputs" / "engine_probe" / "runtime"}
    if args.attention:
        overrides["attention_backend"] = args.attention
    report = prepare_runtime(Settings(**overrides))
    print(report.summary())
    print(json.dumps(report.to_details(), indent=2))
    if report.patches:
        print("patches:", report.patches)
    for error in report.errors:
        print("ERROR:", error)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
