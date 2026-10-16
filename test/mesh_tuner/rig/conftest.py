"""Fixtures for the rig_optimization step tests (marker ``blender``; helpers in rig_testlib.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rig_testlib import BLENDER_52, HELPERS_DIR, OUTPUT_ROOT, run_blender


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "blender: runs a real Blender subprocess")


@pytest.fixture(scope="session")
def blender52() -> Path:
    if not BLENDER_52.is_file():
        pytest.skip("Blender 5.2 is not installed")
    return BLENDER_52


@pytest.fixture(scope="session")
def rig_assets(blender52: Path) -> dict[str, Any]:
    """The synthetic skinned arm (bad + clean variants as .blend/.glb/.fbx), built once per session."""
    out_dir = OUTPUT_ROOT / "synthetic"
    run = run_blender(HELPERS_DIR / "make_rig.py", ["--out-dir", out_dir], tag="make_rig", timeout=300)
    assert run.returncode == 0, run.tail()
    manifest = json.loads((out_dir / "rig_manifest.json").read_text(encoding="utf-8"))
    for key in ("glb", "fbx", "blend", "clean_blend", "clean_glb"):
        manifest[key] = Path(manifest[key])
        assert manifest[key].is_file(), manifest[key]
    return manifest
