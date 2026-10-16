"""FIFO queue of pending job ids (event-loop thread only)."""

from __future__ import annotations

import asyncio
from collections import deque

from core.exceptions import EngineUnavailableError, QueueFullError


class JobQueue:
    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._items: deque[str] = deque()
        self._available = asyncio.Event()
        self._closed = False

    # ------------------------------------------------------------------ state
    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def pending(self) -> int:
        return len(self._items)

    @property
    def closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, job_id: object) -> bool:
        return job_id in self._items

    def snapshot(self) -> list[str]:
        return list(self._items)

    def has_capacity(self) -> bool:
        return len(self._items) < self._max_size

    def position(self, job_id: str) -> int | None:
        """1-based position of a queued job, ``None`` if not queued."""
        for index, queued in enumerate(self._items):
            if queued == job_id:
                return index + 1
        return None

    # ------------------------------------------------------------------ mutations
    def ensure_capacity(self) -> None:
        if self._closed:
            raise EngineUnavailableError("The server is shutting down")
        if not self.has_capacity():
            raise QueueFullError(
                f"The job queue is full ({self._max_size} pending jobs)", details={"max_size": self._max_size}
            )

    def put(self, job_id: str) -> int:
        """Append a job id. Returns its 1-based position. Raises ``QueueFullError`` (429) when full."""
        self.ensure_capacity()
        self._items.append(job_id)
        self._available.set()
        return len(self._items)

    def remove(self, job_id: str) -> bool:
        try:
            self._items.remove(job_id)
        except ValueError:
            return False
        return True

    async def get(self) -> str | None:
        """Wait for the next job id. Returns ``None`` once the queue is closed."""
        while True:
            if self._closed:
                return None
            if self._items:
                return self._items.popleft()
            self._available.clear()
            await self._available.wait()

    def close(self) -> list[str]:
        """Stop handing out jobs. Returns the ids that were still pending."""
        self._closed = True
        remaining = list(self._items)
        self._items.clear()
        self._available.set()
        return remaining
