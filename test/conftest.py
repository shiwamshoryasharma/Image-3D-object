"""Test bootstrap shared by every test track.

* Puts ``backend/`` (application code) and ``test/backend`` (test doubles/helpers) on ``sys.path`` - the
  embedded interpreter runs in isolated mode, so nothing is added implicitly.
* Redirects runtime data and logs to ``test/.runtime`` and ``test/.logs`` and disables model preloading and the
  startup Blender detection by default (tests override ``IMG3D_PRELOAD_MODEL`` /
  ``IMG3D_BLENDER_DETECT_ON_STARTUP`` / settings per test when needed).
* Removes real secrets from the environment so tests never depend on ``backend/.env``.
"""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path

# The embedded interpreter enables the per-user site-packages and searches it BEFORE its own site-packages;
# drop it so tests import exactly the pinned embedded packages (same as backend/main.py does).
_user_site = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != _user_site]
site.ENABLE_USER_SITE = False

TEST_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TEST_ROOT.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
TEST_RUNTIME_ROOT = TEST_ROOT / ".runtime"
TEST_LOG_DIR = TEST_ROOT / ".logs"
TEST_OUTPUTS = TEST_ROOT / "outputs"
TEST_IMAGES = TEST_ROOT / "images"

for _path in (BACKEND_DIR, TEST_ROOT / "backend"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

TEST_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
TEST_LOG_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("IMG3D_RUNTIME_DIR", str(TEST_RUNTIME_ROOT / "default"))
os.environ.setdefault("IMG3D_LOG_DIR", str(TEST_LOG_DIR))
os.environ.setdefault("IMG3D_PRELOAD_MODEL", "false")
# Never execute the real Blender installs at every app startup (tests opt in explicitly).
os.environ.setdefault("IMG3D_BLENDER_DETECT_ON_STARTUP", "false")
os.environ.setdefault("IMG3D_FRONTEND_DIST_DIR", str(TEST_RUNTIME_ROOT / "no-frontend"))
os.environ.setdefault("NO_COLOR", "1")
for _secret in ("HF_TOKEN", "UI_USERNAME", "UI_PASSWORD", "IMG3D_AUTH_ENABLED", "IMG3D_AUTH_SECRET"):
    os.environ.pop(_secret, None)
