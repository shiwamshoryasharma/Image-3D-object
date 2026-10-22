"""Part lineage across analyses of the same base mesh (part-aware DESIGN.md section 3.8; slice S3b).

Every analysis recomputes its part ids. When an earlier completed analysis of the same job has the same
``source.model_sha256``, each new part gets ``lineage.derived_from = [{part_id, analysis_id, face_iou}]`` for the
earlier parts whose face set overlaps it with IoU >= 0.5, at most 3 entries, highest IoU first. Both analyses share the
base face space, so the comparison is a per-face histogram.

The face set of a record is its mesh node (``face_owner == mesh_index``) for mesh-owning records (base, physical) and
its covered faces (``face_part == index``) for regions and deferred records.

Torch-free: numpy only.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
from pydantic import ValidationError

from parts.buffers import BufferError
from parts.manifest import LineageEntry
from parts.reader import AssetReader, AssetReadError
from parts.types import FACE_PART_NONE

logger = logging.getLogger(__name__)

LINEAGE_MIN_IOU: Final = 0.5
LINEAGE_MAX_ENTRIES: Final = 3
ASSET_DIR_NAME: Final = "asset"


@dataclass(frozen=True, slots=True, eq=False)
class LineageSource:
    """The per-face record sets of one analysis.

    ``mesh_indices[i]`` is the mesh index of record ``i`` (``None`` for regions and deferred records); ``face_owner``
    and ``face_part`` are the analysis' uint16 buffers in base face space.
    """

    analysis_id: str
    part_ids: tuple[str, ...]
    mesh_indices: tuple[int | None, ...]
    face_owner: np.ndarray
    face_part: np.ndarray
    created_at: str = ""

    def __post_init__(self) -> None:
        if len(self.part_ids) != len(self.mesh_indices):
            raise ValueError("part_ids and mesh_indices must have the same length")
        if np.shape(self.face_owner) != np.shape(self.face_part) or np.ndim(self.face_owner) != 1:
            raise ValueError("face_owner and face_part must be per-face arrays of the same length")

    @property
    def face_count(self) -> int:
        return int(np.shape(self.face_owner)[0])

    @classmethod
    def from_reader(cls, reader: AssetReader) -> LineageSource:
        manifest = reader.manifest
        return cls(
            analysis_id=manifest.analysis_id,
            part_ids=tuple(part.id for part in manifest.parts),
            mesh_indices=tuple(part.mesh_index if part.owns_mesh else None for part in manifest.parts),
            face_owner=reader.buffer("face_owner"),
            face_part=reader.buffer("face_part"),
            created_at=manifest.created_at,
        )

    def record_levels(self) -> tuple[np.ndarray, np.ndarray]:
        """``(coarse, fine)`` int64 ``[F]``: the mesh-owning record of each face's node, and the region/deferred
        record covering it (``-1`` = none)."""
        record_count = len(self.part_ids)
        node_count = max((index for index in self.mesh_indices if index is not None), default=-1) + 1
        node_record = np.full(max(node_count, 1), -1, dtype=np.int64)
        owns = np.zeros(record_count + 1, dtype=bool)
        for record, mesh_index in enumerate(self.mesh_indices):
            if mesh_index is not None:
                node_record[mesh_index] = record
                owns[record] = True
        owner = np.asarray(self.face_owner, dtype=np.int64)
        coarse = np.full(owner.shape[0], -1, dtype=np.int64)
        inside = owner < node_record.shape[0]
        coarse[inside] = node_record[owner[inside]]
        fine = np.asarray(self.face_part, dtype=np.int64).copy()
        fine[(fine == FACE_PART_NONE) | (fine >= record_count)] = -1
        fine[(fine >= 0) & owns[np.where(fine >= 0, fine, record_count)]] = -1
        return coarse, fine


def _joint(x: np.ndarray, y: np.ndarray, rows: int, cols: int) -> np.ndarray:
    keep = (x >= 0) & (y >= 0)
    keys = x[keep] * cols + y[keep]
    return np.bincount(keys, minlength=rows * cols).reshape(rows, cols).astype(np.int64)


def _set_sizes(coarse: np.ndarray, fine: np.ndarray, mesh_indices: Sequence[int | None]) -> np.ndarray:
    count = len(mesh_indices)
    by_coarse = np.bincount(coarse[coarse >= 0], minlength=count)
    by_fine = np.bincount(fine[fine >= 0], minlength=count)
    owns = np.asarray([index is not None for index in mesh_indices], dtype=bool)
    return np.where(owns, by_coarse, by_fine).astype(np.int64)


def face_iou_matrix(current: LineageSource, earlier: LineageSource) -> np.ndarray:
    """``iou[i, j]`` between record ``i`` of ``current`` and record ``j`` of ``earlier`` (float64)."""
    if current.face_count != earlier.face_count:
        raise ValueError("the two analyses have different face counts")
    rows, cols = len(current.part_ids), len(earlier.part_ids)
    if rows == 0 or cols == 0:
        return np.zeros((rows, cols), dtype=np.float64)
    new_coarse, new_fine = current.record_levels()
    old_coarse, old_fine = earlier.record_levels()
    # A mesh-owning record only appears in ``coarse`` and any other record only in ``fine``, so the four joint
    # histograms have disjoint non-zero entries and their sum is the intersection of the two face sets.
    intersection = (
        _joint(new_coarse, old_coarse, rows, cols)
        + _joint(new_coarse, old_fine, rows, cols)
        + _joint(new_fine, old_coarse, rows, cols)
        + _joint(new_fine, old_fine, rows, cols)
    )
    sizes_new = _set_sizes(new_coarse, new_fine, current.mesh_indices)
    sizes_old = _set_sizes(old_coarse, old_fine, earlier.mesh_indices)
    union = sizes_new[:, None] + sizes_old[None, :] - intersection
    return np.divide(intersection, union, out=np.zeros((rows, cols), dtype=np.float64), where=union > 0)


def compute_lineage(
    current: LineageSource, earlier: Sequence[LineageSource]
) -> dict[str, list[LineageEntry]]:
    """``lineage.derived_from`` of every record of ``current`` (records without a match get ``[]``)."""
    candidates: dict[str, list[tuple[float, str, str]]] = {part_id: [] for part_id in current.part_ids}
    for previous in earlier:
        if previous.analysis_id == current.analysis_id or previous.face_count != current.face_count:
            continue
        iou = face_iou_matrix(current, previous)
        rows, cols = np.nonzero(iou >= LINEAGE_MIN_IOU)
        for row, col in zip(rows.tolist(), cols.tolist(), strict=True):
            candidates[current.part_ids[row]].append(
                (float(iou[row, col]), previous.analysis_id, previous.part_ids[col])
            )
    result: dict[str, list[LineageEntry]] = {}
    for part_id, entries in candidates.items():
        entries.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
        result[part_id] = [
            LineageEntry(part_id=earlier_part, analysis_id=analysis_id, face_iou=round(min(iou, 1.0), 6))
            for iou, analysis_id, earlier_part in entries[:LINEAGE_MAX_ENTRIES]
        ]
    return result


def find_earlier_analyses(
    parts_root: Path,
    *,
    model_sha256: str,
    exclude: str | None = None,
) -> list[LineageSource]:
    """Published analyses under ``jobs/<job>/parts/`` whose ``source.model_sha256`` matches, oldest first.

    Only published layers (``<pa>/asset/manifest.json``) count as completed; an unreadable or tampered layer is
    skipped with a warning, never fatal.
    """
    root = Path(parts_root)
    if not root.is_dir():
        return []
    sources: list[LineageSource] = []
    for analysis_dir in sorted(root.iterdir()):
        if not analysis_dir.is_dir() or analysis_dir.name == exclude:
            continue
        asset_dir = analysis_dir / ASSET_DIR_NAME
        if not (asset_dir / "manifest.json").is_file():
            continue
        try:
            reader = AssetReader(asset_dir, verify="hashes")
            if reader.manifest.source.model_sha256 != model_sha256:
                continue
            sources.append(LineageSource.from_reader(reader))
        except (AssetReadError, BufferError, ValidationError, KeyError, OSError, ValueError) as exc:
            logger.warning("Skipping part layer %s for lineage: %s", analysis_dir.name, exc)
    sources.sort(key=lambda source: (source.created_at, source.analysis_id))
    return sources


__all__ = [
    "LINEAGE_MAX_ENTRIES",
    "LINEAGE_MIN_IOU",
    "LineageSource",
    "compute_lineage",
    "face_iou_matrix",
    "find_earlier_analyses",
]
