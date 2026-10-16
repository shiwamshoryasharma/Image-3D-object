"""Img3D backend - the single entry point.

Run with the embedded interpreter::

    backend\\python\\python.exe backend\\main.py [--host 127.0.0.1] [--port 8000] [--log-level INFO] [--no-preload]

The embedded Python runs in isolated mode (the script directory is NOT on ``sys.path``), so the backend
directory is put on ``sys.path`` before any application import.

The embedded interpreter still enables the per-user site-packages
(``%APPDATA%\\Python\\Python314\\site-packages``), which is searched BEFORE ``backend\\python\\Lib\\site-packages``
and may hold other versions of fastapi/pydantic/numpy/huggingface_hub. It is removed from ``sys.path`` here,
before any third-party import, so only the pinned embedded environment is used.
"""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent


def _bootstrap_sys_path() -> None:
    """Use only the embedded environment: drop the user site-packages and put ``backend/`` first."""
    user_site = site.getusersitepackages() if hasattr(site, "getusersitepackages") else None
    if user_site:
        user_site_norm = os.path.normcase(os.path.abspath(user_site))
        sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != user_site_norm]
    site.ENABLE_USER_SITE = False
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))


_bootstrap_sys_path()

import argparse  # noqa: E402
import logging  # noqa: E402
from collections.abc import Sequence  # noqa: E402
from typing import Any  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from api.router import build_api_router  # noqa: E402
from api.static import mount_frontend  # noqa: E402
from config.settings import Settings, get_settings  # noqa: E402
from core.container import ServiceContainer, build_container  # noqa: E402
from core.error_handlers import register_exception_handlers  # noqa: E402
from core.lifecycle import build_lifespan  # noqa: E402
from core.logging import configure_logging, flush_logging  # noqa: E402
from core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware  # noqa: E402
from core.part_aware_container import PartProviderOverrides  # noqa: E402
from inference.base import InferenceEngine  # noqa: E402
from launcher import BlenderProcessRunner  # noqa: E402
from services.blender_runtime_service import RuntimeManagerFactory  # noqa: E402

logger = logging.getLogger("img3d")

LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def create_app(
    settings: Settings | None = None,
    *,
    engine: InferenceEngine | None = None,
    blender_runtime_factory: RuntimeManagerFactory | None = None,
    blender_runner: BlenderProcessRunner | None = None,
    part_providers: PartProviderOverrides | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: configuration (defaults to :func:`config.settings.get_settings`).
        engine: inference engine to use (tests inject a double); defaults to
            :func:`inference.registry.create_engine`.
        blender_runtime_factory: ``factory(launcher_settings, on_change) -> BlenderRuntimeManager`` (tests inject
            managers with fake detectors/downloaders); defaults to the real manager.
        blender_runner: process runner for Auto Mesh Tuner runs (tests inject a double); defaults to the
            launcher's :class:`~launcher.BlenderProcessRunner`.
        part_providers: part-aware provider/registry/executor doubles (tests); defaults to the lazily resolved
            real ones (:mod:`core.part_aware_container`).
    """
    settings = settings or get_settings()
    configure_logging(settings)
    container = build_container(
        settings,
        engine=engine,
        blender_runtime_factory=blender_runtime_factory,
        blender_runner=blender_runner,
        part_providers=part_providers,
    )

    docs_enabled = not settings.is_production
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.app_version,
        description="Local image-to-3D generation (TRELLIS.2) - REST + WebSocket API.",
        lifespan=build_lifespan(container),
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
    )
    app.state.container = container

    register_exception_handlers(app)
    # Middleware: the last added is the outermost. CORS wraps the request-context middleware so even the
    # last-resort 500 responses carry CORS headers.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "Content-Disposition"],
    )

    app.include_router(build_api_router())
    mount_frontend(app, settings.frontend_dist_dir)
    return app


def _get_container(app: FastAPI) -> ServiceContainer:
    return app.state.container


def _build_server(app: FastAPI, settings: Settings) -> Any:
    """``uvicorn.Server`` that closes WebSockets with 1001 *before* uvicorn tears connections down (1012)."""
    import uvicorn

    container = _get_container(app)

    class Img3DServer(uvicorn.Server):
        async def shutdown(self, sockets: Any = None) -> None:
            try:
                await container.ws_hub.close_all()
            except Exception:  # noqa: BLE001
                logger.exception("Error while closing WebSocket connections")
            await super().shutdown(sockets=sockets)

    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,  # our logging configuration (uvicorn loggers propagate into it)
        access_log=False,  # RequestContextMiddleware writes the access log (without query strings)
        server_header=False,
        proxy_headers=False,
        timeout_graceful_shutdown=max(1, int(settings.shutdown_timeout_s)),
        lifespan="on",
    )
    return Img3DServer(config)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="img3d", description="Img3D backend server (FastAPI + TRELLIS.2)")
    parser.add_argument("--host", help="bind address (default: IMG3D_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="port (default: IMG3D_PORT or 8000)")
    parser.add_argument("--log-level", type=str.upper, choices=LOG_LEVELS, help="log level (default: IMG3D_LOG_LEVEL)")
    parser.add_argument(
        "--no-preload", action="store_true", help="do not load the model at startup (loads on first job)"
    )
    return parser.parse_args(argv)


def _install_windows_break_handler() -> None:
    """Treat Ctrl+Break like Ctrl+C on Windows.

    uvicorn shuts down gracefully on SIGBREAK and then re-raises the signal; with Python's default SIGBREAK
    action that would kill the process (exit code 3) after the clean shutdown. Raising KeyboardInterrupt instead
    lets ``main()`` finish normally.
    """
    import signal

    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is None:
        return

    def _raise_interrupt(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(sigbreak, _raise_interrupt)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    _install_windows_break_handler()
    overrides: dict[str, Any] = {}
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    if args.log_level:
        overrides["log_level"] = args.log_level
    if args.no_preload:
        overrides["preload_model"] = False
    settings = Settings(**overrides)

    app = create_app(settings)
    container = _get_container(app)
    server = _build_server(app, settings)
    logger.info("Starting server on http://%s:%d (API: /api/v1, docs: /api/docs)", settings.host, settings.port)
    exit_code = 1
    try:
        server.run()
        exit_code = 0 if server.started else 1
    except KeyboardInterrupt:
        exit_code = 0
    except SystemExit as exc:  # uvicorn exits like this when e.g. the port is already in use
        exit_code = exc.code if isinstance(exc.code, int) else 1
        if exit_code:
            logger.error("Server failed to start (exit code %d)", exit_code)
    except Exception:
        logger.exception("Server crashed")
        exit_code = 1
    finally:
        flush_logging()
        if container.worker.is_busy:
            # The GPU thread is stuck in a non-interruptible call (e.g. model loading). Its executor thread is
            # joined at interpreter exit, so exit immediately instead of hanging the shell until it finishes.
            logger.warning("GPU worker still busy at exit; terminating the process")
            flush_logging()
            os._exit(exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
