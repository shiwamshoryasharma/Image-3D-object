"""WebSocket hub (contract section 8).

Per connection: ``hello`` with the system status, then ``job.updated`` / ``system.updated`` / ``blender.updated`` /
``tuning.updated`` events from the :class:`~core.events.EventBus` (``?job_id=`` restricts the job-scoped
``job.updated`` / ``tuning.updated`` events to that job) and a ``ping`` every ``ws_ping_interval_s``.
Auth failure closes with 4401; server shutdown closes with 1001.

The connection's own task waits for either the client to disconnect (a receiver task drains client messages) or
the connection to be declared *dead*. A sender task (bus events) and a pinger task write to the socket under a
per-connection lock.

Slow consumers: the event queue per connection is bounded (progress events are dropped first, see
:mod:`core.events`), and every send has a timeout. A client that stops reading long enough for a send to time out
is dropped (the handler returns and the server closes the transport), so it can never block other clients or a
graceful shutdown. Cleanup never awaits, so a connection torn down by cancellation always releases its subscription.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Final

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from config.settings import Settings
from core.events import Event, EventBus, EventPredicate, Subscription
from core.exceptions import AuthenticationError
from schemas.events import EventType, ws_message
from services.auth_service import AuthService
from services.system_service import SystemService
from utils.timeutil import utcnow_iso

logger = logging.getLogger(__name__)

CLOSE_GOING_AWAY: Final = 1001
CLOSE_INTERNAL_ERROR: Final = 1011
CLOSE_UNAUTHORIZED: Final = 4401

SEND_TIMEOUT_S: Final = 10.0
"""A send blocked this long (client not reading, TCP buffers full) marks the connection dead."""
CLOSE_TIMEOUT_S: Final = 2.0
"""Maximum time spent sending a close frame to one client."""

_EXPECTED_SOCKET_ERRORS = (WebSocketDisconnect, ConnectionError, RuntimeError, TimeoutError)


@dataclass(eq=False)
class _Connection:
    websocket: WebSocket
    subscription: Subscription
    send_timeout: float = SEND_TIMEOUT_S
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    dead: asyncio.Event = field(default_factory=asyncio.Event)
    """Set when the client stopped consuming or a writer failed; ``serve`` then drops the connection."""

    async def send(self, payload: dict[str, Any]) -> None:
        """Send one JSON message. Raises ``TimeoutError`` (and marks the connection dead) if the client stalls."""
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        try:
            async with asyncio.timeout(self.send_timeout):
                async with self.send_lock:
                    await self.websocket.send_text(text)
        except TimeoutError:
            self.dead.set()
            raise

    async def close(self, code: int, reason: str = "", *, timeout: float = CLOSE_TIMEOUT_S) -> None:
        """Send a close frame once (no-op if the socket is already closing/closed). Never raises."""
        try:
            async with asyncio.timeout(timeout):
                async with self.send_lock:
                    if self.websocket.application_state != WebSocketState.CONNECTED:
                        return
                    if self.websocket.client_state == WebSocketState.DISCONNECTED:
                        return
                    await self.websocket.close(code=code, reason=reason)
        except TimeoutError:
            self.dead.set()
        except Exception:  # noqa: BLE001 - the peer may already be gone
            pass


def _retrieve_exception(task: asyncio.Task[Any]) -> None:
    """Done-callback: consume background task exceptions (sends after a disconnect are expected)."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None and not isinstance(exc, _EXPECTED_SOCKET_ERRORS):
        logger.warning("WebSocket background task failed: %r", exc)


class WebSocketHub:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        system: SystemService,
        auth: AuthService,
        *,
        send_timeout: float = SEND_TIMEOUT_S,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._system = system
        self._auth = auth
        self.send_timeout = send_timeout
        self._connections: set[_Connection] = set()
        self._closing = False

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def serve(self, websocket: WebSocket, *, job_id: str | None, token: str | None) -> None:
        await websocket.accept()
        if self._closing:
            await websocket.close(code=CLOSE_GOING_AWAY, reason="Server shutting down")
            return
        try:
            self._auth.authorize(token)
        except AuthenticationError:
            await websocket.close(code=CLOSE_UNAUTHORIZED, reason="Unauthorized")
            return

        conn = _Connection(
            websocket=websocket,
            subscription=self._bus.subscribe(_job_filter(job_id)),
            send_timeout=self.send_timeout,
        )
        self._connections.add(conn)
        logger.info("WebSocket connected (job filter: %s, clients: %d)", job_id or "-", len(self._connections))
        tasks: list[asyncio.Task[Any]] = []
        try:
            status = await self._system.status()
            await conn.send(
                {"type": str(EventType.HELLO), "ts": utcnow_iso(), "data": {"system": status.model_dump(mode="json")}}
            )
            receiver = asyncio.create_task(self._receive_until_disconnect(conn), name="ws-receiver")
            watchdog = asyncio.create_task(conn.dead.wait(), name="ws-watchdog")
            tasks = [
                receiver,
                watchdog,
                asyncio.create_task(self._sender(conn), name="ws-sender"),
                asyncio.create_task(self._pinger(conn), name="ws-pinger"),
            ]
            for task in tasks:
                task.add_done_callback(_retrieve_exception)
            await asyncio.wait({receiver, watchdog}, return_when=asyncio.FIRST_COMPLETED)
            if receiver.done():
                receiver.result()  # re-raise disconnects / socket errors (handled below)
            else:
                logger.warning("Dropping unresponsive WebSocket client")
        except (WebSocketDisconnect, ConnectionError):
            pass
        except TimeoutError:
            logger.warning("Dropping unresponsive WebSocket client (send timed out)")
        except RuntimeError as exc:  # receive/send on an already closed socket
            logger.debug("WebSocket closed: %s", exc)
        except Exception:  # noqa: BLE001 - never let one client break the hub
            logger.exception("WebSocket connection failed")
            await conn.close(CLOSE_INTERNAL_ERROR)
        finally:
            for task in tasks:
                task.cancel()
            conn.subscription.close()
            self._connections.discard(conn)
            conn.finished.set()
            logger.info("WebSocket disconnected (clients: %d)", len(self._connections))

    async def close_all(self, code: int = CLOSE_GOING_AWAY, *, timeout: float = 3.0) -> None:
        """Close every connection with ``code`` (shutdown) and refuse new connections.

        Bounded in time: close frames are sent concurrently (each with its own timeout) and connections that have
        not finished after ``timeout`` seconds are dropped.
        """
        self._closing = True
        connections = list(self._connections)
        if not connections:
            return
        logger.info("Closing %d WebSocket connection(s) with code %d", len(connections), code)
        for conn in connections:
            conn.subscription.close()
        await asyncio.gather(*(conn.close(code, "Server shutting down") for conn in connections))
        waiters = [asyncio.create_task(conn.finished.wait()) for conn in connections]
        _, pending = await asyncio.wait(waiters, timeout=timeout)
        for waiter in pending:
            waiter.cancel()
        for conn in connections:
            if not conn.finished.is_set():
                conn.dead.set()  # the handler returns and the server closes the transport

    # ------------------------------------------------------------------ connection tasks
    @staticmethod
    async def _receive_until_disconnect(conn: _Connection) -> None:
        while True:
            message = await conn.websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            # Client messages ({"type": "pong"} or anything else) are accepted and ignored.

    @staticmethod
    async def _sender(conn: _Connection) -> None:
        try:
            while True:
                event = await conn.subscription.get()
                if event is None:
                    return
                await conn.send(ws_message(event))
        except _EXPECTED_SOCKET_ERRORS:
            return  # client gone (the receiver sees the disconnect) or send timed out (connection marked dead)
        except Exception:  # noqa: BLE001 - e.g. an unserializable payload: drop the client, it will reconnect
            logger.warning("WebSocket sender failed; dropping the connection", exc_info=True)
            conn.dead.set()

    async def _pinger(self, conn: _Connection) -> None:
        interval = self._settings.ws_ping_interval_s
        try:
            while True:
                await asyncio.sleep(interval)
                await conn.send({"type": str(EventType.PING), "ts": utcnow_iso(), "data": {}})
        except _EXPECTED_SOCKET_ERRORS:
            return  # client gone or send timed out (connection marked dead)


_JOB_SCOPED_EVENTS: Final = frozenset({str(EventType.JOB_UPDATED), str(EventType.TUNING_UPDATED)})
"""Event types that belong to one job; a ``?job_id=`` connection only receives those of its job."""


def _job_filter(job_id: str | None) -> EventPredicate | None:
    if not job_id:
        return None

    def predicate(event: Event) -> bool:
        if event.type in _JOB_SCOPED_EVENTS:
            return event.job_id == job_id
        return True

    return predicate
