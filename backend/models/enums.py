"""Domain enumerations shared by every backend layer (API, services, workers, inference)."""

from __future__ import annotations

from enum import StrEnum


class ReferenceSlot(StrEnum):
    """Category of a reference image inside a reference set."""

    FRONT = "front"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    FACE_FRONT = "face_front"
    FACE_SIDE = "face_side"
    DETAIL = "detail"


# Slots that can hold at most one image. ``DETAIL`` holds up to ``settings.max_detail_images``.
SINGLE_SLOTS: tuple[ReferenceSlot, ...] = (
    ReferenceSlot.FRONT,
    ReferenceSlot.BACK,
    ReferenceSlot.LEFT,
    ReferenceSlot.RIGHT,
    ReferenceSlot.FACE_FRONT,
    ReferenceSlot.FACE_SIDE,
)

# Order used for display and for auto-promoting a new primary image when the primary is removed.
SLOT_PRIMARY_PRIORITY: tuple[ReferenceSlot, ...] = (
    ReferenceSlot.FRONT,
    ReferenceSlot.FACE_FRONT,
    ReferenceSlot.LEFT,
    ReferenceSlot.RIGHT,
    ReferenceSlot.BACK,
    ReferenceSlot.FACE_SIDE,
    ReferenceSlot.DETAIL,
)

# Order used when listing images of a set.
SLOT_DISPLAY_ORDER: tuple[ReferenceSlot, ...] = (
    ReferenceSlot.FRONT,
    ReferenceSlot.BACK,
    ReferenceSlot.LEFT,
    ReferenceSlot.RIGHT,
    ReferenceSlot.FACE_FRONT,
    ReferenceSlot.FACE_SIDE,
    ReferenceSlot.DETAIL,
)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)


class JobStage(StrEnum):
    QUEUED = "queued"
    WAITING_FOR_MODEL = "waiting_for_model"
    PREPROCESSING = "preprocessing"
    CONDITIONING = "conditioning"
    SPARSE_STRUCTURE = "sparse_structure"
    SHAPE = "shape"
    TEXTURE = "texture"
    DECODING = "decoding"
    EXPORTING = "exporting"
    FINALIZING = "finalizing"
    DONE = "done"


class EngineState(StrEnum):
    NOT_LOADED = "not_loaded"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class ReferenceMode(StrEnum):
    """How reference images are fed to the engine.

    ``primary_only`` is what TRELLIS.2 supports today. ``multi_view`` is reserved for engines that can
    condition on several categorized views; the data model already carries every reference.
    """

    PRIMARY_ONLY = "primary_only"
    MULTI_VIEW = "multi_view"
