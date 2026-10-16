"""Fixtures for the Blender runtime launcher tests (``backend/launcher``).

Markers (registered here; ``test/pytest.ini`` uses ``--strict-markers``):

* ``blender`` - needs the real Blender 5.2 install (skipped automatically when it is missing).
* ``network`` - talks to download.blender.org; skipped unless ``-m network`` is given or
  ``IMG3D_RUN_NETWORK_TESTS=1`` is set.

Artifacts go to ``test/outputs/mesh_tuner/launcher/<module>/<test>`` (wiped at the start of each test).
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest
from lt_helpers import BLENDER_52, HERE, OUT_ROOT


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "blender: needs a real Blender >= 5.1 installation (Windows)")
    config.addinivalue_line("markers", "network: needs internet access to download.blender.org")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_network = os.environ.get("IMG3D_RUN_NETWORK_TESTS") == "1" or "network" in (config.getoption("-m") or "")
    skip_network = pytest.mark.skip(reason="network test: pass -m network or set IMG3D_RUN_NETWORK_TESTS=1")
    skip_blender = pytest.mark.skip(reason=f"Blender 5.2 not installed at {BLENDER_52}")
    for item in items:
        if HERE not in Path(item.path).resolve().parents:
            continue
        if "network" in item.keywords and not run_network:
            item.add_marker(skip_network)
        if "blender" in item.keywords and not BLENDER_52.is_file():
            item.add_marker(skip_blender)


def _clean(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=False)


@pytest.fixture
def out_dir(request: pytest.FixtureRequest) -> Path:
    """Fresh per-test artifact directory (kept after the run for inspection)."""
    module = request.module.__name__.rsplit(".", 1)[-1].removeprefix("test_launcher_")[:14]
    name = re.sub(r"[^A-Za-z0-9_]+", "_", request.node.name).strip("_")[:40]
    path = OUT_ROOT / module / name
    _clean(path)
    path.mkdir(parents=True)
    return path


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    path = OUT_ROOT / "_fixtures"
    path.mkdir(parents=True, exist_ok=True)
    return path
