"""WebSocket message schemas (contract 8)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from core.events import Event
from schemas.common import ApiModel
from utils.timeutil import isoformat_z


class EventType(StrEnum):
    HELLO = "hello"
    JOB_UPDATED = "job.updated"
    SYSTEM_UPDATED = "system.updated"
    PING = "ping"
    BLENDER_UPDATED = "blender.updated"
    """Blender runtime status (detection, selection, download progress); data = ``BlenderRuntimeOut``."""
    TUNING_UPDATED = "tuning.updated"
    """Auto Mesh Tuner run status/progress; data = ``TuningRunOut``; ``Event.job_id`` = the run's job."""
    FEATURES_UPDATED = "features.updated"
    """Capability/flag discovery changed; data = ``FeaturesOut`` (debounced 250 ms, never dropped)."""
    IMAGE_GENERATION_UPDATED = "image_generation.updated"
    """Image generation run status/progress; data = ``ImageGenerationOut``."""
    SEGMENTATION_UPDATED = "segmentation.updated"
    """Segmentation run status/progress; data = ``SegmentationOut``; ``Event.job_id`` = the job of a job source."""
    PART_ANALYSIS_UPDATED = "part_analysis.updated"
    """Part analysis status/progress; data = ``PartAnalysisOut``; ``Event.job_id`` = the analysed job (job-scoped)."""
    WORKFLOW_UPDATED = "workflow.updated"
    """Workflow status/step progress; data = ``WorkflowOut``."""


class WsMessage(ApiModel):
    type: str
    ts: str
    data: Any


def ws_message(event: Event) -> dict[str, Any]:
    """Wire format of an event: ``{"type", "ts", "data"}``."""
    return {"type": event.type, "ts": isoformat_z(event.ts), "data": event.data}
