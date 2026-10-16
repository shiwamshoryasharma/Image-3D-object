"""Backend test fixtures: isolated runtime dirs, settings factory and app/client factory with the fake engine."""

from __future__ import annotations

import contextlib
import secrets
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeEngine
from fastapi import FastAPI
from starlette.testclient import TestClient

from config.settings import Settings
from core.container import ServiceContainer

TEST_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = TEST_ROOT / ".runtime"
LOG_DIR = TEST_ROOT / ".logs"


def pytest_configure(config: pytest.Config) -> None:
    # test/pytest.ini uses --strict-markers; markers of this track are registered here.
    config.addinivalue_line("markers", "blender: needs a real Blender >= 5.1 installation (Windows)")


@dataclass
class AppHarness:
    app: FastAPI
    client: TestClient
    engine: Any
    settings: Settings

    @property
    def container(self) -> ServiceContainer:
        return self.app.state.container


@pytest.fixture
def runtime_dir(request: pytest.FixtureRequest) -> Iterator[Path]:
    """A fresh runtime directory under test/.runtime (removed after the test)."""
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in request.node.name)[:60]
    path = RUNTIME_ROOT / f"{safe_name}-{secrets.token_hex(3)}"
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def tmp_runtime(runtime_dir: Path) -> Path:
    """Scratch directory under test/.runtime (pytest's tmp_path would write outside test/)."""
    return runtime_dir


@pytest.fixture
def make_settings(runtime_dir: Path) -> Callable[..., Settings]:
    """Settings isolated from ``backend/.env`` (no real secrets) with test-local paths."""

    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "runtime_dir": runtime_dir,
            "log_dir": LOG_DIR,
            "preload_model": False,
            "frontend_dist_dir": RUNTIME_ROOT / "no-frontend",
            "ws_ping_interval_s": 0.5,
            "retention_interval_s": 3600,
            "auth_enabled": False,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return factory


@pytest.fixture
def make_app(make_settings: Callable[..., Settings]) -> Iterator[Callable[..., AppHarness]]:
    """Factory starting an app (lifespan included) with a :class:`FakeEngine` unless another engine is given."""
    from main import create_app

    stack = contextlib.ExitStack()

    def factory(
        *, engine: Any = None, settings: Settings | None = None, raise_server_exceptions: bool = True, **overrides: Any
    ) -> AppHarness:
        settings = settings or make_settings(**overrides)
        engine = engine if engine is not None else FakeEngine()
        app = create_app(settings, engine=engine)
        client = stack.enter_context(TestClient(app, raise_server_exceptions=raise_server_exceptions))
        return AppHarness(app=app, client=client, engine=engine, settings=settings)

    with stack:
        yield factory


@pytest.fixture
def harness(make_app: Callable[..., AppHarness]) -> AppHarness:
    return make_app()


@pytest.fixture
def client(harness: AppHarness) -> TestClient:
    return harness.client
