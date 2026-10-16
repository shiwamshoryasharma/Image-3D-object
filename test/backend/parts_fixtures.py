"""Synthetic meshes and GLB builders for the part-aware geometry tests (part-aware DESIGN.md section 10.2, slice S3a).

Test doubles only (they never reach the app). Public for the S3b tests:

* :func:`uv_sphere` ``(n_faces, *, double_walled=False, rim_joined=False, open_hemisphere=False)``: a UV-mapped
  sphere (spherified cube, one UV chart per cube face, chart borders duplicated), optionally double-walled (inner
  wall reversed), optionally an open hemisphere, optionally rim-joined (outer and inner wall welded at the rim by a
  strip: ONE component, like TRELLIS.2 output). ``face_groups`` names the faces of each wall (``outer``, ``inner``,
  ``rim``) and of each chart (``+x`` ... ``-z``).
* :func:`capped_cylinder` (UV seams plus separately unwrapped caps), :func:`touching_spheres` (two spheres sharing
  one vertex, no edge), :func:`t_junction` (a sheet and a fin welded along a line: non-manifold edges),
  :func:`floaters` (tiny detached tetrahedra), :func:`combine`.
* :func:`write_base_glb` writes a mesh in the layout of a real TRELLIS.2 ``model.glb`` (1 mesh, 1 primitive,
  ``POSITION/TEXCOORD_0/NORMAL``, ``UNSIGNED_INT`` indices, 1 material, 2 ``EXT_texture_webp`` images, no samplers);
  :func:`base_document` + :func:`write_glb` build malformed variants.
"""

from __future__ import annotations

import io
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942


@dataclass
class SyntheticMesh:
    """Base-like arrays: ``positions``/``normals`` float32 ``[V, 3]``, ``uv`` float32 ``[V, 2]``, ``faces`` uint32
    ``[F, 3]``; ``face_groups`` maps a name to the ascending face ids of that group."""

    positions: np.ndarray
    normals: np.ndarray
    uv: np.ndarray
    faces: np.ndarray
    face_groups: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def face_count(self) -> int:
        return int(self.faces.shape[0])

    @property
    def vertex_count(self) -> int:
        return int(self.positions.shape[0])

    def group_mask(self, name: str) -> np.ndarray:
        mask = np.zeros(self.face_count, dtype=bool)
        mask[self.face_groups[name]] = True
        return mask


def combine(*meshes: SyntheticMesh, names: tuple[str, ...] | None = None) -> SyntheticMesh:
    """Concatenate meshes (vertex ids offset; no welding). Groups keep their names, prefixed by ``names[i]``."""
    positions, normals, uv, faces = [], [], [], []
    groups: dict[str, list[np.ndarray]] = {}
    vertex_offset = 0
    face_offset = 0
    for index, mesh in enumerate(meshes):
        positions.append(mesh.positions)
        normals.append(mesh.normals)
        uv.append(mesh.uv)
        faces.append(mesh.faces.astype(np.int64) + vertex_offset)
        prefix = f"{names[index]}." if names else ""
        if names:
            groups.setdefault(names[index], []).append(np.arange(mesh.face_count) + face_offset)
        for name, ids in mesh.face_groups.items():
            groups.setdefault(prefix + name, []).append(ids + face_offset)
        vertex_offset += mesh.vertex_count
        face_offset += mesh.face_count
    return SyntheticMesh(
        positions=np.concatenate(positions).astype(np.float32),
        normals=np.concatenate(normals).astype(np.float32),
        uv=np.concatenate(uv).astype(np.float32),
        faces=np.concatenate(faces).astype(np.uint32),
        face_groups={name: np.concatenate(parts).astype(np.int64) for name, parts in groups.items()},
    )


def cube_sphere_size(n_faces: int) -> int:
    """Even grid size ``s`` of each cube face so that a full sphere of ``12 * s * s`` faces is close to ``n_faces``."""
    size = max(2, int(round(math.sqrt(max(n_faces, 12) / 12.0))))
    return size + size % 2


_CUBE_FACES: tuple[tuple[str, tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]], ...] = (
    ("+x", (1, 0, 0), (0, 1, 0), (0, 0, 1)),
    ("-x", (-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    ("+y", (0, 1, 0), (0, 0, 1), (1, 0, 0)),
    ("-y", (0, -1, 0), (1, 0, 0), (0, 0, 1)),
    ("+z", (0, 0, 1), (1, 0, 0), (0, 1, 0)),
    ("-z", (0, 0, -1), (0, 1, 0), (1, 0, 0)),
)
"""Cube faces as (name, outward normal n, U, V) with ``U x V = n``: the triangles (a, a+U, a+U+V) face outward."""


def _cube_sphere_wall(
    size: int, radius: float, center: np.ndarray, *, hemisphere: bool, inward: bool
) -> SyntheticMesh:
    """One wall of a spherified cube: one UV chart per cube face (3 x 2 atlas), chart borders duplicated bit for bit.

    Grid coordinates are ``k / (size / 2)`` (exact for 0 and +-1), so a border vertex has identical cube coordinates,
    and therefore identical float32 positions, in both charts that share it.
    """
    t = np.arange(-(size // 2), size // 2 + 1, dtype=np.float64) / (size // 2)
    positions, normals, uvs, faces = [], [], [], []
    groups: dict[str, np.ndarray] = {}
    vertex_offset = 0
    face_offset = 0
    for chart, (name, n, u_axis, v_axis) in enumerate(_CUBE_FACES):
        jj, ii = np.meshgrid(np.arange(size + 1), np.arange(size + 1), indexing="ij")
        cube = (np.asarray(n, dtype=np.float64) + t[ii][..., None] * np.asarray(u_axis, dtype=np.float64)
                + t[jj][..., None] * np.asarray(v_axis, dtype=np.float64)).reshape(-1, 3)
        x, y, z = cube[:, 0], cube[:, 1], cube[:, 2]
        direction = np.stack([
            x * np.sqrt(1.0 - y * y / 2.0 - z * z / 2.0 + y * y * z * z / 3.0),
            y * np.sqrt(1.0 - z * z / 2.0 - x * x / 2.0 + z * z * x * x / 3.0),
            z * np.sqrt(1.0 - x * x / 2.0 - y * y / 2.0 + x * x * y * y / 3.0),
        ], axis=1)
        col, row = chart % 3, chart // 3
        uv = np.stack([(col + 0.05 + 0.9 * ii.reshape(-1) / size) / 3.0,
                       (row + 0.05 + 0.9 * jj.reshape(-1) / size) / 2.0], axis=1)
        j, i = np.meshgrid(np.arange(size), np.arange(size), indexing="ij")
        a = (j * (size + 1) + i).reshape(-1)
        b, c, d = a + 1, a + size + 2, a + size + 1
        quad_y = (cube[a, 1] + cube[c, 1]) / 2.0
        keep = quad_y > 0.0 if hemisphere else np.ones(a.shape[0], dtype=bool)
        if not keep.any():
            continue
        triangles = np.concatenate([np.stack([a, b, c], axis=1)[keep], np.stack([a, c, d], axis=1)[keep]])
        used, local = np.unique(triangles, return_inverse=True)
        local = local.reshape(-1, 3)
        if inward:
            local = local[:, [0, 2, 1]]
        positions.append(center + radius * direction[used])
        normals.append(direction[used] * (-1.0 if inward else 1.0))
        uvs.append(uv[used])
        faces.append(local + vertex_offset)
        groups[name] = np.arange(face_offset, face_offset + local.shape[0])
        vertex_offset += used.shape[0]
        face_offset += local.shape[0]
    return SyntheticMesh(
        positions=np.concatenate(positions).astype(np.float32),
        normals=np.concatenate(normals).astype(np.float32),
        uv=np.concatenate(uvs).astype(np.float32),
        faces=np.concatenate(faces).astype(np.uint32),
        face_groups=groups,
    )


def _welded_boundary_edges(mesh: SyntheticMesh) -> np.ndarray:
    """Directed base-vertex edges ``(u, v)`` (as wound in their face) used by exactly one face after welding."""
    _keys, weld = np.unique(mesh.positions.view(np.dtype((np.void, 12))).ravel(), return_inverse=True)
    weld = weld.reshape(-1)
    faces = mesh.faces.astype(np.int64)
    starts, ends = faces.reshape(-1), np.roll(faces, -1, axis=1).reshape(-1)
    low, high = np.minimum(weld[starts], weld[ends]), np.maximum(weld[starts], weld[ends])
    keys = low * (int(weld.max()) + 1) + high
    _unique, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    single = counts[inverse.reshape(-1)] == 1
    return np.stack([starts[single], ends[single]], axis=1)


def uv_sphere(
    n_faces: int,
    *,
    double_walled: bool = False,
    rim_joined: bool = False,
    open_hemisphere: bool = False,
    radius: float = 0.5,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    thickness: float | None = None,
) -> SyntheticMesh:
    """A UV-mapped sphere of about ``n_faces`` faces in total (see the module docstring).

    Built as a spherified cube with one UV chart per cube face, so the triangles are near-isotropic (no pole fans,
    like TRELLIS.2 output) and the chart borders are UV-seam duplicates. ``face_groups``: ``outer``, ``inner``,
    ``rim`` and one group per cube chart (``+x`` ... ``-z``; both walls). ``thickness`` is the wall gap (default
    0.6 nominal edge lengths). ``open_hemisphere`` keeps the ``y > 0`` half (open rim on the equator).
    """
    if rim_joined and not (double_walled and open_hemisphere):
        raise ValueError("rim_joined needs double_walled=True and open_hemisphere=True")
    walls = 2 if double_walled else 1
    size = cube_sphere_size((n_faces // walls) * (2 if open_hemisphere else 1))
    origin = np.asarray(center, dtype=np.float64)
    gap = 0.6 * (math.pi / 2.0) * radius / size if thickness is None else thickness
    outer = _cube_sphere_wall(size, radius, origin, hemisphere=open_hemisphere, inward=False)
    outer.face_groups["outer"] = np.arange(outer.face_count)
    if not double_walled:
        return outer
    inner = _cube_sphere_wall(size, radius - gap, origin, hemisphere=open_hemisphere, inward=True)
    inner.face_groups["inner"] = np.arange(inner.face_count)
    mesh = combine(outer, inner)
    if rim_joined:
        rim = _welded_boundary_edges(outer)
        u, v = rim[:, 0], rim[:, 1]
        u_in, v_in = u + outer.vertex_count, v + outer.vertex_count
        strip = np.concatenate([np.stack([v, u, u_in], axis=1), np.stack([v, u_in, v_in], axis=1)])
        start = mesh.face_count
        mesh.faces = np.concatenate([mesh.faces.astype(np.int64), strip]).astype(np.uint32)
        mesh.face_groups["rim"] = np.arange(start, mesh.face_count)
    return mesh


def capped_cylinder(segments: int = 32, rings: int = 8, radius: float = 0.3, height: float = 0.8) -> SyntheticMesh:
    """A closed cylinder: the side has a UV seam (column ``segments`` repeats column 0) and each cap is its own UV
    island whose rim vertices repeat the side's rim positions bit for bit."""
    j = np.arange(segments + 1)
    phi = 2.0 * np.pi * (j % segments) / segments
    ring_xz = np.stack([radius * np.cos(phi), radius * np.sin(phi)], axis=1)
    heights = np.linspace(-height / 2.0, height / 2.0, rings + 1)
    side = np.array([[x, y, z] for y in heights for x, z in ring_xz], dtype=np.float32)
    side_normals = np.array([[x / radius, 0.0, z / radius] for _y in heights for x, z in ring_xz], dtype=np.float32)
    side_uv = np.array([[jj / segments, r / rings * 0.5] for r in range(rings + 1) for jj in j], dtype=np.float32)
    faces: list[list[int]] = []
    for r in range(rings):
        for jj in range(segments):
            a, b = r * (segments + 1) + jj, (r + 1) * (segments + 1) + jj
            faces += [[a, b, b + 1], [a, b + 1, a + 1]]
    positions = [side]
    normals = [side_normals]
    uvs = [side_uv]
    offset = side.shape[0]
    for cap_y, sign in ((heights[-1], 1.0), (heights[0], -1.0)):
        rim_source = side[(rings if sign > 0 else 0) * (segments + 1):][:segments]
        cap = np.concatenate([np.array([[0.0, cap_y, 0.0]], dtype=np.float32), rim_source])
        cap_uv = np.concatenate([[[0.75, 0.25 if sign > 0 else 0.75]],
                                 0.75 + 0.2 * np.stack([np.cos(phi[:segments]), np.sin(phi[:segments])], axis=1)
                                 * np.array([1.0, 0.5])]).astype(np.float32)
        positions.append(cap)
        normals.append(np.tile(np.array([[0.0, sign, 0.0]], dtype=np.float32), (segments + 1, 1)))
        uvs.append(cap_uv)
        for jj in range(segments):
            nxt = (jj + 1) % segments
            tri = [offset, offset + 1 + nxt, offset + 1 + jj] if sign > 0 else [offset, offset + 1 + jj,
                                                                                 offset + 1 + nxt]
            faces.append(tri)
        offset += segments + 1
    return SyntheticMesh(
        positions=np.concatenate(positions).astype(np.float32),
        normals=np.concatenate(normals).astype(np.float32),
        uv=np.concatenate(uvs).astype(np.float32),
        faces=np.asarray(faces, dtype=np.uint32),
    )


def touching_spheres(n_faces_each: int = 2000, radius: float = 0.25) -> SyntheticMesh:
    """Two spheres that touch at one shared (bit-identical) vertex: one welded vertex, no shared edge."""
    left = uv_sphere(n_faces_each, radius=radius, center=(-radius, 0.0, 0.0))
    right = uv_sphere(n_faces_each, radius=radius, center=(radius, 0.0, 0.0))
    contact = np.zeros(3)
    a = int(np.argmin(np.linalg.norm(left.positions - contact, axis=1)))
    b = int(np.argmin(np.linalg.norm(right.positions - contact, axis=1)))
    right.positions = right.positions.copy()
    same = np.all(right.positions == right.positions[b], axis=1)
    right.positions[same] = left.positions[a]
    return combine(left, right, names=("left", "right"))


def _grid(
    origin: np.ndarray, axis_u: np.ndarray, axis_v: np.ndarray, nu: int, nv: int, normal: np.ndarray
) -> SyntheticMesh:
    u = np.arange(nu + 1)
    v = np.arange(nv + 1)
    positions = (origin + u[None, :, None] * axis_u + v[:, None, None] * axis_v).reshape(-1, 3).astype(np.float32)
    uv = np.stack(np.meshgrid(u / nu, v / nv), axis=-1).reshape(-1, 2).astype(np.float32)
    faces = []
    for row in range(nv):
        for col in range(nu):
            a = row * (nu + 1) + col
            faces += [[a, a + 1, a + nu + 2], [a, a + nu + 2, a + nu + 1]]
    return SyntheticMesh(
        positions=positions,
        normals=np.tile(normal.astype(np.float32), (positions.shape[0], 1)),
        uv=uv,
        faces=np.asarray(faces, dtype=np.uint32),
    )


def t_junction(n: int = 8, size: float = 0.8) -> SyntheticMesh:
    """A flat sheet in the XZ plane plus a fin in the XY plane standing on the sheet's middle line ``z = 0``.

    The fin's bottom row repeats the sheet's middle-row positions bit for bit, so every welded edge on the line has
    three faces (non-manifold). Groups: ``sheet_front`` (z > 0), ``sheet_back`` (z < 0), ``fin``.
    """
    step = size / n
    half = size / 2.0
    sheet = _grid(np.array([-half, 0.0, -half]), np.array([step, 0.0, 0.0]), np.array([0.0, 0.0, step]), n, n,
                  np.array([0.0, 1.0, 0.0]))
    fin = _grid(np.array([-half, 0.0, 0.0]), np.array([step, 0.0, 0.0]), np.array([0.0, step, 0.0]), n, n // 2,
                np.array([0.0, 0.0, 1.0]))
    middle = (n // 2) * (n + 1) + np.arange(n + 1)
    fin.positions = fin.positions.copy()
    fin.positions[: n + 1] = sheet.positions[middle]
    centroid_z = sheet.positions[sheet.faces].mean(axis=1)[:, 2]
    sheet.face_groups = {"sheet_front": np.flatnonzero(centroid_z > 0), "sheet_back": np.flatnonzero(centroid_z < 0)}
    fin.face_groups = {"fin": np.arange(fin.face_count)}
    return combine(sheet, fin)


def floaters(count: int = 3, size: float = 0.01, start: tuple[float, float, float] = (2.0, 2.0, 2.0)) -> SyntheticMesh:
    """``count`` tiny detached tetrahedra (4 faces each), spaced along +X from ``start``."""
    unit = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
    meshes = []
    for index in range(count):
        positions = (np.asarray(start) + np.array([index * 10.0 * size, 0.0, 0.0]) + size * unit).astype(np.float32)
        normals = (unit - unit.mean(axis=0)).astype(np.float32)
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        meshes.append(SyntheticMesh(positions, normals, np.zeros((4, 2), dtype=np.float32),
                                    faces.astype(np.uint32), {f"floater_{index}": np.arange(4)}))
    return combine(*meshes)


# --------------------------------------------------------------------------------------------------
# GLB builders
# --------------------------------------------------------------------------------------------------


def texture_bytes(size: int = 32, *, color: tuple[int, int, int] | None = None, fmt: str = "WEBP") -> bytes:
    """A small encoded texture: a solid ``color`` or a deterministic RGB gradient."""
    if color is not None:
        pixels = np.tile(np.asarray(color, dtype=np.uint8), (size, size, 1))
    else:
        ramp = np.linspace(0, 255, size, dtype=np.uint8)
        pixels = np.stack(np.broadcast_arrays(ramp[None, :], ramp[:, None], np.uint8(128)), axis=-1).astype(np.uint8)
    buffer = io.BytesIO()
    options: dict[str, Any] = {"lossless": True} if fmt == "WEBP" else {}
    Image.fromarray(pixels, "RGB").save(buffer, format=fmt, **options)
    return buffer.getvalue()


def base_document(
    mesh: SyntheticMesh,
    *,
    index_dtype: str = "<u4",
    images: tuple[bytes, ...] | None = None,
    with_normals: bool = True,
    with_uv: bool = True,
) -> tuple[dict[str, Any], bytes]:
    """glTF JSON + BIN of a TRELLIS.2-like ``model.glb`` (accessor layout of the real fixture)."""
    images = (texture_bytes(), texture_bytes(color=(0, 200, 0))) if images is None else images
    pieces: list[bytes] = []
    views: list[dict[str, Any]] = []

    def add(data: bytes) -> int:
        offset = sum(len(p) for p in pieces)
        pad = -offset % 4
        if pad:
            pieces.append(bytes(pad))
            offset += pad
        pieces.append(data)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)})
        return len(views) - 1

    component = {"<u4": 5125, "<u2": 5123, "<u1": 5121}[index_dtype]
    indices = mesh.faces.reshape(-1).astype(index_dtype)
    accessors: list[dict[str, Any]] = [{"componentType": component, "type": "SCALAR", "bufferView": add(
        indices.tobytes()), "count": int(indices.size), "max": [int(indices.max())], "min": [int(indices.min())]}]
    attributes: dict[str, int] = {}
    positions = mesh.positions.astype("<f4")
    accessors.append({"componentType": 5126, "type": "VEC3", "byteOffset": 0, "bufferView": add(positions.tobytes()),
                      "count": int(positions.shape[0]), "max": positions.max(axis=0).tolist(),
                      "min": positions.min(axis=0).tolist()})
    attributes["POSITION"] = len(accessors) - 1
    image_views = [add(data) for data in images]
    if with_uv:
        accessors.append({"componentType": 5126, "type": "VEC2", "byteOffset": 0,
                          "bufferView": add(mesh.uv.astype("<f4").tobytes()), "count": int(mesh.uv.shape[0])})
        attributes["TEXCOORD_0"] = len(accessors) - 1
    if with_normals:
        accessors.append({"componentType": 5126, "count": int(mesh.normals.shape[0]), "type": "VEC3",
                          "byteOffset": 0, "bufferView": add(mesh.normals.astype("<f4").tobytes())})
        attributes["NORMAL"] = len(accessors) - 1
    binary = b"".join(pieces)
    document: dict[str, Any] = {
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "asset": {"version": "2.0", "generator": "Img3D test fixture"},
        "accessors": accessors,
        "meshes": [{"name": "geometry_0", "extras": {}, "primitives": [
            {"attributes": attributes, "indices": 0, "mode": 4, "material": 0}]}],
        "images": [{"bufferView": view, "mimeType": "image/webp"} for view in image_views],
        "textures": [{"extensions": {"EXT_texture_webp": {"source": i}}} for i in range(len(image_views))],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                "baseColorFactor": [1.0, 1.0, 1.0, 1.0], "roughnessFactor": 1.0,
                                                "metallicFactor": 1.0, "metallicRoughnessTexture": {"index": 1}},
                       "alphaMode": "OPAQUE", "doubleSided": False}],
        "nodes": [{"name": "geometry_0", "mesh": 0}],
        "extensionsUsed": ["EXT_texture_webp"],
        "extensionsRequired": ["EXT_texture_webp"],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
    }
    return document, binary


def write_glb(path: Path, document: dict[str, Any], binary: bytes) -> Path:
    """Write a GLB container (JSON chunk padded with spaces, BIN chunk with zeros)."""
    json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    bin_bytes = binary + bytes(-len(binary) % 4)
    total = 12 + 8 + len(json_bytes) + (8 + len(bin_bytes) if bin_bytes else 0)
    with Path(path).open("wb") as handle:
        handle.write(struct.pack("<III", GLB_MAGIC, 2, total))
        handle.write(struct.pack("<II", len(json_bytes), CHUNK_JSON) + json_bytes)
        if bin_bytes:
            handle.write(struct.pack("<II", len(bin_bytes), CHUNK_BIN) + bin_bytes)
    return Path(path)


def write_base_glb(path: Path, mesh: SyntheticMesh, **options: Any) -> Path:
    """Write ``mesh`` as a TRELLIS.2-like ``model.glb`` (see :func:`base_document` for ``options``)."""
    document, binary = base_document(mesh, **options)
    return write_glb(path, document, binary)


def band_labels(mesh: SyntheticMesh, values: tuple[int, ...], axis: int = 1) -> np.ndarray:
    """uint16 face labels in equal bands of the face centroids along ``axis`` (highest band first)."""
    centroid = mesh.positions[mesh.faces].astype(np.float64).mean(axis=1)[:, axis]
    edges = np.linspace(centroid.max(), centroid.min(), len(values) + 1)[1:-1]
    band = np.searchsorted(-edges, -centroid, side="right")
    return np.asarray(values, dtype=np.uint16)[band]
