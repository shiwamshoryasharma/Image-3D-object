"""``GET /api/v1/ws`` WebSocket (contract section 8)."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from api.deps import bearer_token, get_container

router = APIRouter(tags=["realtime"])


@router.websocket("/ws")
async def events_websocket(websocket: WebSocket, job_id: str | None = None, token: str | None = None) -> None:
    """Push channel: ``hello``, ``job.updated``, ``system.updated``, ``ping``. ``?job_id=`` filters job events;
    ``?token=`` (or an ``Authorization`` header) is required when auth is enabled (close code 4401 otherwise)."""
    container = get_container(websocket)
    await container.ws_hub.serve(websocket, job_id=job_id, token=token or bearer_token(websocket))
