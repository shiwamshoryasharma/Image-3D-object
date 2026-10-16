"""In-process async pub/sub used to push job/system updates to WebSocket clients.

* :meth:`EventBus.publish` must be called on the event loop thread.
* :meth:`EventBus.publish_threadsafe` may be called from any thread (e.g. the GPU worker); it hops onto the
  loop with ``loop.call_soon_threadsafe`` so ordering with loop-side publishes is preserved.
* Every subscriber has a bounded queue. On overflow the oldest *droppable* event (progress) is discarded;
  non-droppable events (status changes) are only discarded if the queue holds nothing else.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

DEFAULT_SUBSCRIBER_QUEUE_SIZE = 256


@dataclass(frozen=True, slots=True)
class Event:
    type: str
    data: Any
    ts: datetime = field(default_factory=utcnow)
    job_id: str | None = None
    """Set for job events; used by subscribers filtering on a single job."""
    droppable: bool = False
    """``True`` for high-frequency progress events that may be dropped under back-pressure."""


EventPredicate = Callable[[Event], bool]


class Subscription:
    """A subscriber's bounded event queue. Iterate with ``await sub.get()`` (``None`` once closed)."""

    def __init__(self, bus: EventBus, *, maxsize: int, predicate: EventPredicate | None) -> None:
        self._bus = bus
        self._maxsize = max(1, maxsize)
        self._predicate = predicate
        self._items: deque[Event] = deque()
        self._ready = asyncio.Event()
        self._closed = False
        self.dropped = 0

    @property
    def closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        return len(self._items)

    def offer(self, event: Event) -> None:
        """Enqueue ``event`` (loop thread only)."""
        if self._closed:
            return
        if self._predicate is not None and not self._predicate(event):
            return
        if len(self._items) >= self._maxsize:
            self._drop_one()
        self._items.append(event)
        self._ready.set()

    def _drop_one(self) -> None:
        for index, queued in enumerate(self._items):
            if queued.droppable:
                del self._items[index]
                break
        else:
            self._items.popleft()
        self.dropped += 1

    async def get(self) -> Event | None:
        """Wait for the next event. Returns ``None`` when the subscription is closed and drained."""
        while not self._items:
            if self._closed:
                return None
            self._ready.clear()
            await self._ready.wait()
        return self._items.popleft()

    def close(self) -> None:
        self._closed = True
        self._ready.set()
        self._bus._discard(self)


class EventBus:
    def __init__(self, *, queue_size: int = DEFAULT_SUBSCRIBER_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subscribers: set[Subscription] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None
        self._closed = False

    # ------------------------------------------------------------------ lifecycle
    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Bind to the running event loop (call from the loop thread during startup)."""
        self._loop = loop or asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._closed = False

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def close(self) -> None:
        """Close every subscription (subscribers' ``get()`` returns ``None``)."""
        self._closed = True
        for sub in list(self._subscribers):
            sub.close()
        self._subscribers.clear()

    # ------------------------------------------------------------------ subscriptions
    def subscribe(self, predicate: EventPredicate | None = None, *, maxsize: int | None = None) -> Subscription:
        sub = Subscription(self, maxsize=maxsize or self._queue_size, predicate=predicate)
        if self._closed:
            sub.close()
        else:
            self._subscribers.add(sub)
        return sub

    def _discard(self, sub: Subscription) -> None:
        self._subscribers.discard(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------------ publishing
    def publish(self, event: Event) -> None:
        """Deliver ``event`` to every subscriber (event loop thread only)."""
        if self._closed:
            return
        for sub in list(self._subscribers):
            try:
                sub.offer(event)
            except Exception:  # noqa: BLE001 - a broken subscriber must not affect others
                logger.exception("Event subscriber failed; dropping it")
                sub.close()

    def publish_threadsafe(self, event: Event) -> None:
        """Deliver ``event`` from any thread.

        Always hops through ``call_soon_threadsafe`` (even on the loop thread) so events published from the GPU
        worker thread and from the loop are delivered in one FIFO order.
        """
        loop = self._loop
        if loop is None or self._closed:
            return
        try:
            loop.call_soon_threadsafe(self.publish, event)
        except RuntimeError:
            # Loop already closed (shutdown in progress) - nothing to deliver to.
            pass

    def call_soon_threadsafe(self, callback: Callable[..., Any], *args: Any) -> None:
        """Run ``callback(*args)`` on the bound loop from any thread (no-op if the loop is gone)."""
        loop = self._loop
        if loop is None:
            return
        if threading.get_ident() == self._loop_thread_id:
            callback(*args)
            return
        try:
            loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            pass

    def call_later_threadsafe(self, delay: float, callback: Callable[..., Any], *args: Any) -> None:
        """Schedule ``callback(*args)`` after ``delay`` seconds on the bound loop, from any thread."""
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.call_later, delay, callback, *args)
        except RuntimeError:
            pass
