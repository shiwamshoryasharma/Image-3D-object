"""Fixtures for the Blender-side Auto Mesh Tuner framework tests (marker ``blender``; helpers in amt_testlib.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from amt_testlib import BLENDER_42, BLENDER_52, HELPERS_DIR, OUTPUT_ROOT, SCRIPTS_DIR, BlenderRun, run_blender


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "blender: runs a real Blender subprocess")


@pytest.fixture(scope="session")
def blender52() -> Path:
    if not BLENDER_52.is_file():
        pytest.skip("Blender 5.2 is not installed")
    return BLENDER_52


@pytest.fixture(scope="session")
def blender42() -> Path:
    if not BLENDER_42.is_file():
        pytest.skip("Blender 4.2 is not installed")
    return BLENDER_42


@pytest.fixture(scope="session")
def synthetic(blender52: Path) -> dict[str, Any]:
    """Synthetic defect meshes built inside Blender (once per session)."""
    out_dir = OUTPUT_ROOT / "synthetic"
    run = run_blender(blender52, HELPERS_DIR / "make_synthetic.py", ["--out-dir", out_dir], tag="make_synthetic",
                      timeout=300)
    assert run.returncode == 0, run.tail()
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest.values():
        entry["path"] = Path(entry["path"])
    return manifest


@pytest.fixture(scope="session")
def tuner(blender52: Path):
    """``tuner(args, tag, timeout=900) -> BlenderRun``: the real CLI with the four processing steps stubbed.

    The framework suite tests the framework only (I/O, protocol, analysis, validation, previews, report); the real
    repair/refine/rig steps are covered by their own suites and test/mesh_tuner/test_pipeline_real.py.
    """

    def _run(args: list[str], tag: str, timeout: float = 900.0) -> BlenderRun:
        return run_blender(blender52, HELPERS_DIR / "run_with_injected_steps.py",
                           ["--scripts", str(SCRIPTS_DIR), "--mode", "stubs", "--", *args], tag=tag, timeout=timeout)

    return _run


@pytest.fixture(scope="session")
def inspect_models(blender52: Path):
    """``inspect_models([paths], tag) -> list[dict]`` - what Blender's importer sees in each model."""

    def _inspect(paths: list[Path], tag: str) -> list[dict[str, Any]]:
        run = run_blender(blender52, HELPERS_DIR / "inspect_glb.py", [str(p) for p in paths], tag=tag, timeout=600)
        assert run.returncode == 0, run.tail()
        rows = []
        for line in run.stdout.splitlines():
            i = line.find("@@INSPECT@@")
            if i >= 0:
                rows.append(json.loads(line[i + len("@@INSPECT@@"):]))
        assert len(rows) == len(paths), run.tail()
        return rows

    return _inspect
