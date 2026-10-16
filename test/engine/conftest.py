"""Bootstrap for the engine tests (``test/engine``).

Puts ``backend/`` and the vendored TRELLIS.2 checkout on ``sys.path`` (the embedded interpreter runs isolated, so
nothing is on the path by default) and keeps pytest's temporary directories under ``test/engine/.pytest_tmp``.

Run from anywhere::

    E:\\14-OCT-2026\\Img3D\\backend\\python\\python.exe -m pytest E:\\14-OCT-2026\\Img3D\\test\\engine -p no:cacheprovider
"""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path

# The embedded interpreter keeps the per-user site-packages enabled; drop it so tests see only backend\python.
_USER_SITE = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _USER_SITE]

import pytest  # noqa: E402

ENGINE_TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ENGINE_TEST_DIR.parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
TRELLIS_SOURCE_DIR = BACKEND_DIR / "third_party" / "TRELLIS.2"
MODEL_DIR = BACKEND_DIR / ".model" / "TRELLIS.2-4B"
AUX_ROOT = BACKEND_DIR / ".model"
SAMPLE_IMAGE = PROJECT_ROOT / "test" / "images" / "ALICE" / "ALICE_full_front_view.png"

for _path in (BACKEND_DIR, TRELLIS_SOURCE_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    if not config.option.basetemp:
        config.option.basetemp = str(ENGINE_TEST_DIR / ".pytest_tmp")


@pytest.fixture(autouse=True)
def _restore_process_state():
    """``prepare_runtime`` sets process-wide env vars (HF_HOME, ATTN_BACKEND, ...); keep other tests isolated."""
    environ = dict(os.environ)
    path = list(sys.path)
    yield
    os.environ.clear()
    os.environ.update(environ)
    sys.path[:] = path


@pytest.fixture(scope="session")
def cuda_available() -> bool:
    try:
        import torch
    except Exception:  # noqa: BLE001
        return False
    return bool(torch.cuda.is_available())
