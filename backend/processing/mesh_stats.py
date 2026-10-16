"""Statistics of an exported GLB, computed with trimesh plus the GLB's own JSON chunk.

trimesh provides geometry facts (vertex/face counts, world-space bounds). Material/texture/UV counts are read
from the glTF JSON chunk because trimesh may merge or drop materials on import.
"""

from __future__ import annotations

import json
import logging
import struct
from pathlib import Path
from typing import Any

from models.job import MeshStats
from utils.jsonsafe import finite_or

logger = logging.getLogger(__name__)

_GLB_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A


def read_glb_json(path: Path) -> dict[str, Any]:
    """Return the glTF JSON chunk of a binary GLB file."""
    with open(path, "rb") as fh:
        header = fh.read(12)
        if len(header) < 12 or header[:4] != _GLB_MAGIC:
            raise ValueError("Not a binary glTF (GLB) file")
        chunk_header = fh.read(8)
        if len(chunk_header) < 8:
            raise ValueError("Truncated GLB file")
        chunk_length, chunk_type = struct.unpack("<II", chunk_header)
        if chunk_type != _CHUNK_JSON:
            raise ValueError("GLB file does not start with a JSON chunk")
        return json.loads(fh.read(chunk_length).decode("utf-8"))


def compute_glb_stats(path: Path, *, fallback_texture_size: int | None = None) -> MeshStats:
    """Compute :class:`MeshStats` for ``path`` (raises ``ValueError``/``OSError`` if the file is unusable)."""
    gltf = read_glb_json(path)
    materials = len(gltf.get("materials", []) or [])
    textures = len(gltf.get("textures", []) or [])
    has_uv = any(
        "TEXCOORD_0" in (primitive.get("attributes") or {})
        for mesh in gltf.get("meshes", []) or []
        for primitive in mesh.get("primitives", []) or []
    )

    vertices, faces, bbox_min, bbox_max, texture_size = _trimesh_stats(path)
    if vertices == 0 and faces == 0:
        vertices, faces = _accessor_counts(gltf)

    # Degenerate geometry can yield NaN/Inf bounds; those are not valid JSON and would break every API response.
    bbox_min = [round(finite_or(v), 6) for v in bbox_min]
    bbox_max = [round(finite_or(v), 6) for v in bbox_max]
    dimensions = [round(hi - lo, 6) for lo, hi in zip(bbox_min, bbox_max, strict=True)]
    return MeshStats(
        vertices=vertices,
        faces=faces,
        materials=materials,
        textures=textures,
        texture_size=texture_size or fallback_texture_size,
        has_uv=has_uv,
        bbox_min=bbox_min,
        bbox_max=bbox_max,
        dimensions=dimensions,
        file_size_bytes=path.stat().st_size,
    )


def _trimesh_stats(path: Path) -> tuple[int, int, list[float], list[float], int | None]:
    import trimesh  # local import: heavy module, only needed in the worker

    scene = trimesh.load(str(path), file_type="glb", force="scene", process=False)
    vertices = 0
    faces = 0
    largest_texture = 0
    for node_name in scene.graph.nodes_geometry:
        _, geometry_name = scene.graph[node_name]
        geometry = scene.geometry.get(geometry_name)
        if geometry is None:
            continue
        geometry_vertices = getattr(geometry, "vertices", None)
        geometry_faces = getattr(geometry, "faces", None)
        vertices += 0 if geometry_vertices is None else len(geometry_vertices)
        faces += 0 if geometry_faces is None else len(geometry_faces)
        largest_texture = max(largest_texture, _material_texture_size(geometry))
    texture_size = largest_texture or None

    bounds = scene.bounds
    if bounds is None:
        bbox_min, bbox_max = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    else:
        bbox_min = [float(v) for v in bounds[0]]
        bbox_max = [float(v) for v in bounds[1]]
    return vertices, faces, bbox_min, bbox_max, texture_size


def _material_texture_size(geometry: Any) -> int:
    """Largest texture side used by a geometry's material (0 if unknown)."""
    material = getattr(getattr(geometry, "visual", None), "material", None)
    if material is None:
        return 0
    best = 0
    for attr in ("baseColorTexture", "metallicRoughnessTexture", "normalTexture", "emissiveTexture", "image"):
        image = getattr(material, attr, None)
        size = getattr(image, "size", None)
        if isinstance(size, tuple) and len(size) == 2:
            best = max(best, int(size[0]), int(size[1]))
    return best


def _accessor_counts(gltf: dict[str, Any]) -> tuple[int, int]:
    """Vertex/face counts from accessors (used only if trimesh found no geometry)."""
    accessors = gltf.get("accessors", []) or []
    vertices = 0
    faces = 0
    for mesh in gltf.get("meshes", []) or []:
        for primitive in mesh.get("primitives", []) or []:
            position = (primitive.get("attributes") or {}).get("POSITION")
            if position is not None and position < len(accessors):
                vertices += int(accessors[position].get("count", 0))
            indices = primitive.get("indices")
            if indices is not None and indices < len(accessors):
                faces += int(accessors[indices].get("count", 0)) // 3
    return vertices, faces
