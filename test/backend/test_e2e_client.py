"""Runs test/e2e/e2e_generate.py against a real uvicorn server (fake engine) over HTTP + WebSocket."""

from __future__ import annotations

import contextlib
import importlib.util
import re
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeEngine
from helpers import free_port, live_uvicorn, png_bytes

from config.settings import Settings
from core.logging import flush_logging

E2E_SCRIPT = Path(__file__).resolve().parents[1] / "e2e" / "e2e_generate.py"
CREDENTIALS = ("e2e", "e2e-password-1")


def _load_e2e_module():
    spec = importlib.util.spec_from_file_location("e2e_generate", E2E_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["e2e_generate"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def start_live_server(make_settings) -> Iterator[Callable[..., tuple[str, Settings]]]:
    """Factory: start a live server (auth on, fake engine) and return ``(base_url, settings)``."""
    from main import create_app

    stack = contextlib.ExitStack()

    def factory(engine: FakeEngine | None = None, **overrides: Any) -> tuple[str, Settings]:
        port = free_port()
        settings = make_settings(
            port=port, ws_ping_interval_s=20, ui_username=CREDENTIALS[0], ui_password=CREDENTIALS[1], **overrides
        )
        settings.auth_enabled = None  # auto: enabled because credentials are set
        app = create_app(settings, engine=engine or FakeEngine(steps=5, step_delay=0.01))
        return stack.enter_context(live_uvicorn(app, port)), settings

    with stack:
        yield factory


def _e2e_args(base_url: str, image: Path, out_dir: Path, *extra: str, timeout: str = "60") -> list[str]:
    return [
        "--base-url", base_url,
        "--image", str(image),
        "--username", CREDENTIALS[0],
        "--password", CREDENTIALS[1],
        "--output-dir", str(out_dir),
        "--timeout", timeout,
        *extra,
    ]  # fmt: skip


def test_e2e_client_against_live_server(start_live_server, runtime_dir):
    base_url, _ = start_live_server()
    image = runtime_dir / "input.png"
    image.write_bytes(png_bytes((256, 256), mode="RGBA"))
    back = runtime_dir / "back.png"
    back.write_bytes(png_bytes((200, 200)))
    out_dir = runtime_dir / "e2e-out"
    e2e = _load_e2e_module()

    extra = ["--ref", f"back={back}", "--resolution", "512", "--texture-size", "1024", "--polygon-target", "100000"]
    assert e2e.main(_e2e_args(base_url, image, out_dir, *extra, "--seed", "7")) == 0
    glbs = list(out_dir.glob("img3d_job_*.glb"))
    assert len(glbs) == 1
    assert glbs[0].read_bytes()[:4] == b"glTF"
    assert list(out_dir.glob("img3d_job_*_preview.png"))
    assert list(out_dir.glob("img3d_job_*_result.json"))


def test_e2e_client_requires_credentials(start_live_server, runtime_dir):
    base_url, _ = start_live_server()
    e2e = _load_e2e_module()
    assert e2e.main(["--base-url", base_url, "--output-dir", str(runtime_dir / "x"), "--timeout", "5"]) == 1


def test_e2e_client_sees_job_that_failed_before_the_websocket_connected(start_live_server, runtime_dir):
    """A job that fails immediately (model cannot load) must be reported at once, not after --timeout."""
    engine = FakeEngine(ready=False, load_error="weights missing")
    base_url, _ = start_live_server(engine=engine)
    image = runtime_dir / "input.png"
    image.write_bytes(png_bytes((128, 128)))
    e2e = _load_e2e_module()

    started = time.monotonic()
    assert e2e.main(_e2e_args(base_url, image, runtime_dir / "out", timeout="40")) == 1
    assert time.monotonic() - started < 15


def test_websocket_token_never_reaches_the_logs(start_live_server, runtime_dir):
    """Uvicorn logs the WebSocket handshake path WITH its query string; ``?token=`` values must be redacted."""
    log_dir = runtime_dir / "logs"
    base_url, _ = start_live_server(log_dir=log_dir)
    image = runtime_dir / "input.png"
    image.write_bytes(png_bytes((128, 128)))
    e2e = _load_e2e_module()
    assert e2e.main(_e2e_args(base_url, image, runtime_dir / "out")) == 0  # connects with ?job_id=...&token=...
    flush_logging()

    text = "\n".join(path.read_text("utf-8", errors="replace") for path in log_dir.glob("*.log"))
    assert '"WebSocket /api/v1/ws?' in text.replace('\\"', '"'), "uvicorn handshake line not captured"
    leaked = [value for value in re.findall(r"token=([^&\s\"'\\]+)", text) if value != "***"]
    assert leaked == []
