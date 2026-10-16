"""Serves the built frontend (``frontend/dist``) at ``/`` with SPA fallback - never shadowing ``/api``."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)

_API_PREFIX = "api"
_NO_CACHE = "no-cache"
_IMMUTABLE = "public, max-age=31536000, immutable"


class SPAStaticFiles(StaticFiles):
    """Static files with ``index.html`` fallback for client-side routes; ``/api/*`` is never served here."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # e.g. a WebSocket to an unknown path
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": "Not found"})
            return
        await super().__call__(scope, receive, send)

    async def get_response(self, path: str, scope: Scope) -> Response:
        # ``get_path`` uses os.path.normpath -> backslashes on Windows and "." for the root.
        normalized = path.replace("\\", "/").strip("/")
        if normalized == ".":
            normalized = ""
        if normalized == _API_PREFIX or normalized.startswith(_API_PREFIX + "/"):
            raise HTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or not _looks_like_route(normalized):
                raise
            response = await super().get_response("index.html", scope)
            response.headers["Cache-Control"] = _NO_CACHE
            return response
        if normalized.startswith("assets/"):
            response.headers.setdefault("Cache-Control", _IMMUTABLE)
        elif normalized in ("", "index.html"):
            response.headers["Cache-Control"] = _NO_CACHE
        return response


def _looks_like_route(path: str) -> bool:
    """Client-side routes have no file extension in their last segment (``/jobs/123`` but not ``/x.js``)."""
    last = path.rsplit("/", 1)[-1]
    return "." not in last


def mount_frontend(app: FastAPI, dist_dir: Path) -> bool:
    """Mount ``dist_dir`` at ``/`` if it contains ``index.html``. Returns ``True`` when mounted."""
    if not (dist_dir / "index.html").is_file():
        logger.info("Frontend build not found at %s; serving the API only", dist_dir)
        return False
    app.mount("/", SPAStaticFiles(directory=str(dist_dir), html=True), name="frontend")
    logger.info("Serving frontend from %s", dist_dir)
    return True
