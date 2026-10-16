"""Test helpers: in-memory images, polling, timed WebSocket receive."""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from io import BytesIO
from typing import Any, TypeVar

import anyio
import httpx
import uvicorn
from PIL import Image
from starlette.testclient import TestClient, WebSocketTestSession

T = TypeVar("T")


# --------------------------------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------------------------------


def image_bytes(
    fmt: str = "PNG",
    size: tuple[int, int] = (128, 96),
    mode: str = "RGB",
    color: Any = (10, 120, 200),
    exif_orientation: int | None = None,
) -> bytes:
    img = Image.new(mode, size, color)
    out = BytesIO()
    kwargs: dict[str, Any] = {}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif
    img.save(out, format=fmt, **kwargs)
    return out.getvalue()


def png_bytes(size: tuple[int, int] = (128, 96), mode: str = "RGB", **kwargs: Any) -> bytes:
    color = (10, 120, 200, 128) if mode == "RGBA" else kwargs.pop("color", (10, 120, 200))
    return image_bytes("PNG", size, mode, color, **kwargs)


# --------------------------------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------------------------------


def assert_error(response: Any, status_code: int, code: str) -> dict[str, Any]:
    """Assert the uniform error body and return its ``error`` object."""
    assert response.status_code == status_code, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == {"code", "message", "details", "request_id"}, error
    assert error["code"] == code, error
    assert isinstance(error["message"], str) and error["message"]
    assert error["request_id"], error
    assert response.headers.get("x-request-id") == error["request_id"]
    return error


def create_set(client: TestClient, name: str | None = "test set", headers: dict[str, str] | None = None) -> dict:
    response = client.post("/api/v1/reference-sets", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def upload(
    client: TestClient,
    set_id: str,
    data: bytes | None = None,
    *,
    slot: str = "front",
    filename: str = "image.png",
    content_type: str = "image/png",
    label: str | None = None,
    make_primary: bool | None = None,
    headers: dict[str, str] | None = None,
):
    form: dict[str, str] = {"slot": slot}
    if label is not None:
        form["label"] = label
    if make_primary is not None:
        form["make_primary"] = "true" if make_primary else "false"
    return client.post(
        f"/api/v1/reference-sets/{set_id}/images",
        data=form,
        files={"file": (filename, data if data is not None else png_bytes(), content_type)},
        headers=headers,
    )


def create_set_with_image(
    client: TestClient, headers: dict[str, str] | None = None, **upload_kwargs: Any
) -> tuple[dict, dict]:
    ref_set = create_set(client, headers=headers)
    response = upload(client, ref_set["id"], headers=headers, **upload_kwargs)
    assert response.status_code == 201, response.text
    body = response.json()
    return body["set"], body["image"]


def create_job(client: TestClient, set_id: str, settings: dict | None = None, **extra: Any):
    payload: dict[str, Any] = {"reference_set_id": set_id, "settings": settings or {}}
    payload.update(extra)
    return client.post("/api/v1/jobs", json=payload)


# --------------------------------------------------------------------------------------------------
# Waiting
# --------------------------------------------------------------------------------------------------


def wait_for(predicate: Callable[[], T], *, timeout: float = 10.0, interval: float = 0.02, message: str = "") -> T:
    deadline = time.monotonic() + timeout
    last: T | None = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"Timed out after {timeout}s waiting for condition {message}; last={last!r}")


def wait_for_job(client: TestClient, job_id: str, statuses: set[str], *, timeout: float = 15.0) -> dict:
    def check() -> dict | None:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        return job if job["status"] in statuses else None

    return wait_for(check, timeout=timeout, message=f"job {job_id} in {statuses}")


# --------------------------------------------------------------------------------------------------
# Live server (real uvicorn, real sockets)
# --------------------------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def live_uvicorn(app: Any, port: int, *, startup_timeout: float = 15.0) -> Iterator[str]:
    """Serve ``app`` with uvicorn on 127.0.0.1:``port`` in a thread; yields the base URL, stops it on exit."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False))
    thread = threading.Thread(target=server.run, name="uvicorn-test", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + startup_timeout
    while True:
        try:
            if httpx.get(f"{base_url}/api/v1/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline or not thread.is_alive():
            server.should_exit = True
            raise AssertionError("live server did not start")
        time.sleep(0.05)
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        assert not thread.is_alive(), "live server did not stop"


# --------------------------------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------------------------------


def ws_receive(ws: WebSocketTestSession, timeout: float = 5.0) -> dict:
    """``receive_json`` with a timeout (Starlette's TestClient would block forever)."""

    async def _receive() -> Any:
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()  # noqa: SLF001 - test-only access

    message = ws.portal.call(_receive)
    ws._raise_on_close(message)  # noqa: SLF001
    return json.loads(message["text"])


def ws_collect_until(
    ws: WebSocketTestSession, stop: Callable[[dict], bool], *, timeout: float = 15.0
) -> list[dict]:
    """Receive messages until ``stop(message)`` is true; returns every message received (including the last)."""
    deadline = time.monotonic() + timeout
    messages: list[dict] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"Timed out waiting for WebSocket message; got {[m['type'] for m in messages]}")
        message = ws_receive(ws, timeout=remaining)
        messages.append(message)
        if stop(message):
            return messages
