"""``backend/main.py`` as a process: exit behaviour when the server cannot start while the model is loading."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = TEST_ROOT.parent / "backend"

_CHILD = textwrap.dedent(
    """
    import functools, os, site, sys
    user_site = os.path.normcase(os.path.abspath(site.getusersitepackages()))
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != user_site]
    sys.path[:0] = [{backend!r}, {test_backend!r}]

    import main
    import core.container
    from fakes import FakeEngine

    # A model load that takes far longer than the test: the GPU thread is busy when the server gives up.
    core.container.create_engine = lambda settings: FakeEngine(ready=False, load_delay=120)
    main.Settings = functools.partial(main.Settings, _env_file=None)  # never read the real backend/.env
    raise SystemExit(main.main(["--port", sys.argv[1]]))
    """
).format(backend=str(BACKEND_DIR), test_backend=str(TEST_ROOT / "backend"))


def test_port_in_use_exits_promptly_even_while_the_model_loads(runtime_dir):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]

        env = dict(os.environ)
        env.update(
            IMG3D_RUNTIME_DIR=str(runtime_dir / "runtime"),
            IMG3D_LOG_DIR=str(runtime_dir / "logs"),
            IMG3D_PRELOAD_MODEL="true",
            IMG3D_AUTH_ENABLED="false",
            IMG3D_SHUTDOWN_TIMEOUT_S="2",
        )
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD, str(port)],
            env=env,
            cwd=str(runtime_dir),
            capture_output=True,
            text=True,
            timeout=90,
        )
        elapsed = time.monotonic() - started

    output = proc.stdout + proc.stderr
    assert proc.returncode == 3, output  # uvicorn's STARTUP_FAILURE
    assert elapsed < 60, f"process hung on the busy GPU thread ({elapsed:.0f}s)\n{output}"
    assert "GPU worker still busy at exit" in output, output
