"""Fixtures for the refine (face_refinement / topology_tuning) tests (marker ``blender``; helpers in
refine_testlib.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from refine_testlib import BLENDER_52, HELPERS_DIR, OUTPUT_ROOT, SCRIPTS_DIR, run_blender  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "blender: runs a real Blender subprocess")


@pytest.fixture(scope="session")
def blender52() -> Path:
    if not BLENDER_52.is_file():
        pytest.skip("Blender 5.2 is not installed")
    return BLENDER_52


@pytest.fixture(scope="session")
def synth(blender52: Path) -> dict[str, Any]:
    """Run blender_helpers/synthetic_refine.py once; returns its result dict (one entry per check)."""
    out = OUTPUT_ROOT / "synthetic"
    run = run_blender(HELPERS_DIR / "synthetic_refine.py", ["--scripts", SCRIPTS_DIR, "--out", out],
                      tag="synthetic_refine", timeout=900)
    assert run.returncode == 0, run.tail()
    line = next((x for x in run.stdout.splitlines() if "@@SYNTH@@" in x), None)
    assert line is not None, run.tail()
    data = json.loads(line[line.index("@@SYNTH@@") + len("@@SYNTH@@"):])
    (out / "synthetic_results.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data
