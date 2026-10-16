"""WebSocket hub: hello, ping, job filter, client messages, shutdown close code."""

from __future__ import annotations

import asyncio
import functools
import time

import pytest
from helpers import create_job, create_set_with_image, wait_for_job, ws_collect_until, ws_receive
from starlette.websockets import WebSocketDisconnect, WebSocketState

TERMINAL = {"completed", "failed", "cancelled"}


def test_hello_contains_system_status(client):
    with client.websocket_connect("/api/v1/ws") as ws:
        hello = ws_receive(ws)
    assert hello["type"] == "hello"
    assert hello["ts"].endswith("Z")
    system = hello["data"]["system"]
    assert system["engine"]["name"] == "fake-engine"
    assert system["queue"]["max_size"] == 16


def test_ping_is_sent_periodically_and_pong_is_ignored(client):
    with client.websocket_connect("/api/v1/ws") as ws:
        ws_receive(ws)
        ws.send_json({"type": "pong"})
        ws.send_text("not json at all")
        message = ws_collect_until(ws, lambda m: m["type"] == "ping", timeout=3)[-1]
    assert message["data"] == {}
    assert message["ts"].endswith("Z")


def test_job_id_filter(harness):
    client = harness.client
    ref_set, _ = create_set_with_image(client)
    harness.engine.hold()
    first = create_job(client, ref_set["id"]).json()
    second = create_job(client, ref_set["id"]).json()
    with client.websocket_connect(f"/api/v1/ws?job_id={second['id']}") as ws:
        ws_receive(ws)
        harness.engine.release()
        messages = ws_collect_until(
            ws, lambda m: m["type"] == "job.updated" and m["data"]["status"] in TERMINAL
        )
    job_ids = {m["data"]["id"] for m in messages if m["type"] == "job.updated"}
    assert job_ids == {second["id"]}
    wait_for_job(client, first["id"], TERMINAL)


def test_shutdown_closes_connections_with_1001(harness):
    client = harness.client
    with client.websocket_connect("/api/v1/ws") as ws:
        ws_receive(ws)
        client.portal.call(functools.partial(harness.container.ws_hub.close_all, timeout=0.1))
        with pytest.raises(WebSocketDisconnect) as exc_info:
            while True:
                ws_receive(ws, timeout=3)
    assert exc_info.value.code == 1001
    # New connections are refused with 1001 while shutting down.
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws") as ws:
            ws_receive(ws, timeout=3)
    assert exc_info.value.code == 1001


def test_unknown_websocket_path_is_rejected(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/nope") as ws:
            ws_receive(ws, timeout=2)


class StalledWebSocket:
    """WebSocket double for a client that stops reading: after ``sends_before_stall`` successful sends every send
    (and close) blocks forever, as when the peer's TCP window is full. ``receive`` never returns."""

    def __init__(self, sends_before_stall: int = 0) -> None:
        self.application_state = WebSocketState.CONNECTING
        self.client_state = WebSocketState.CONNECTING
        self.sends_before_stall = sends_before_stall
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.application_state = self.client_state = WebSocketState.CONNECTED

    async def send_text(self, text: str) -> None:
        if len(self.sent) >= self.sends_before_stall:
            await asyncio.Event().wait()
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        await asyncio.Event().wait()

    async def receive(self) -> dict:
        await asyncio.Event().wait()
        return {}  # pragma: no cover


@pytest.mark.parametrize("sends_before_stall", [0, 1])
def test_unresponsive_client_is_dropped(harness, sends_before_stall):
    """A client that stops reading (hello stalls, or a later ping stalls) is dropped after the send timeout."""
    hub = harness.container.ws_hub
    hub.send_timeout = 0.3

    async def scenario() -> tuple[float, int, int]:
        ws = StalledWebSocket(sends_before_stall)
        started = time.monotonic()
        await asyncio.wait_for(hub.serve(ws, job_id=None, token=None), timeout=10)
        return time.monotonic() - started, hub.connection_count, len(ws.sent)

    elapsed, connections, sent = harness.client.portal.call(scenario)
    assert connections == 0
    assert sent == sends_before_stall
    assert elapsed < 5  # ping interval 0.5 s + send timeout 0.3 s


def test_close_all_is_bounded_with_a_stalled_client(harness):
    """Graceful shutdown must not hang on a client whose socket send is blocked."""
    hub = harness.container.ws_hub
    hub.send_timeout = 60  # the stalled ping alone would not drop the client during this test

    async def scenario() -> float:
        ws = StalledWebSocket(sends_before_stall=1)  # hello goes out, the first ping blocks
        serve_task = asyncio.create_task(hub.serve(ws, job_id=None, token=None))
        await asyncio.sleep(1.0)
        assert not serve_task.done() and hub.connection_count == 1
        started = time.monotonic()
        await asyncio.wait_for(hub.close_all(timeout=0.5), timeout=15)
        elapsed = time.monotonic() - started
        await asyncio.wait_for(serve_task, timeout=5)
        return elapsed

    elapsed = harness.client.portal.call(scenario)
    assert elapsed < 5
    assert hub.connection_count == 0
