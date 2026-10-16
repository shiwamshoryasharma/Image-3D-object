"""GLB reader and writer of the part asset layer (part-aware DESIGN.md sections 3.1, 3.4 and 3.12; slice S3a).

* :func:`read_base_glb` reads the raw accessors of a job's ``model.glb`` (1 mesh, 1 triangle primitive) together with
  its sha256. Nothing is welded, recomputed or reordered: the arrays are the base vertex and face spaces of section 3.1.
  Inputs this layer cannot reassemble losslessly (several meshes or primitives, sparse accessors, Draco or meshopt
  compression, extra vertex attributes, morph targets, transformed mesh nodes, external buffers) raise
  :class:`UnsupportedBaseMeshError` (run error ``unsupported_base_mesh``).
* :func:`write_parts_glb` writes ``model_parts.glb`` (and per-part GLBs): one flat node per mesh node under an identity
  ``img3d_asset`` node, the base material/texture/image/sampler tables copied verbatim and the image bufferViews
  copied byte for byte (asset invariant A5), ``POSITION`` with ``min``/``max``, ``UNSIGNED_INT`` indices and the
  per-vertex region attribute ``_FEATURE_ID_0`` as ``UNSIGNED_SHORT`` in its own bufferView with ``byteStride`` 4
  (glTF 2.0 section 3.6.2.4 alignment). The atlas is never decoded or re-encoded.
* :func:`read_parts_glb` reads such a file back accessor by accessor; the reassembly check (``parts/reassembly.py``)
  runs on it (asset invariant A4).
* :func:`base_color_texture` / :func:`face_base_color_lab` decode the baseColor atlas once and sample it at every face's
  UV centroid in CIE Lab (section 4.6.5 step 1).

Torch-free: json, struct, hashlib, numpy and Pillow only.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np

from parts.types import (
    NODE_NAME_PREFIX,
    PARTS_SCHEMA,
    REGION_ATTRIBUTE,
    MeshNode,
    node_name,
)

# --------------------------------------------------------------------------------------------------
# glTF constants
# --------------------------------------------------------------------------------------------------

GLB_MAGIC: Final = 0x46546C67  # b"glTF"
GLB_VERSION: Final = 2
CHUNK_JSON: Final = 0x4E4F534A  # b"JSON"
CHUNK_BIN: Final = 0x004E4942  # b"BIN\0"
GLB_HEADER_BYTES: Final = 12
CHUNK_HEADER_BYTES: Final = 8
GLB_MAX_BYTES: Final = 0xFFFF_FFFF

MODE_TRIANGLES: Final = 4
TARGET_ARRAY_BUFFER: Final = 34962
TARGET_ELEMENT_ARRAY_BUFFER: Final = 34963

COMPONENT_BYTE: Final = 5120
COMPONENT_UNSIGNED_BYTE: Final = 5121
COMPONENT_SHORT: Final = 5122
COMPONENT_UNSIGNED_SHORT: Final = 5123
COMPONENT_UNSIGNED_INT: Final = 5125
COMPONENT_FLOAT: Final = 5126

_COMPONENT_DTYPES: Final[Mapping[int, np.dtype]] = {
    COMPONENT_BYTE: np.dtype("<i1"),
    COMPONENT_UNSIGNED_BYTE: np.dtype("<u1"),
    COMPONENT_SHORT: np.dtype("<i2"),
    COMPONENT_UNSIGNED_SHORT: np.dtype("<u2"),
    COMPONENT_UNSIGNED_INT: np.dtype("<u4"),
    COMPONENT_FLOAT: np.dtype("<f4"),
}
_TYPE_COMPONENTS: Final[Mapping[str, int]] = {
    "SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16,
}
_INDEX_COMPONENT_TYPES: Final = frozenset({COMPONENT_UNSIGNED_BYTE, COMPONENT_UNSIGNED_SHORT, COMPONENT_UNSIGNED_INT})

BASE_ATTRIBUTES: Final = ("POSITION", "NORMAL", "TEXCOORD_0")
"""The vertex attributes a base primitive may carry. Anything else could not be reassembled losslessly (A4)."""

COMPRESSION_EXTENSIONS: Final = frozenset(
    {"KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_meshopt_compression"}
)

SCENE_NAME: Final = "img3d_parts"
ASSET_NODE_NAME: Final = "img3d_asset"
WRITER_GENERATOR: Final = f"Img3D part asset layer ({PARTS_SCHEMA})"
FEATURE_ID_STRIDE: Final = 4
"""``_FEATURE_ID_0`` is UNSIGNED_SHORT (2 bytes) padded to a 4-byte stride, so every vertex element stays aligned."""

_IDENTITY_MATRIX: Final = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
_WRITE_CHUNK_BYTES: Final = 1 << 24


# --------------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------------


class UnsupportedBaseMeshError(ValueError):
    """The base GLB is unreadable or not a single, uncompressed triangle primitive (run error code below)."""

    code: Final = "unsupported_base_mesh"


class BaseMeshChangedError(ValueError):
    """The base GLB no longer has the sha256 the caller expected (the job's ``model.glb`` changed underneath)."""


class GlbFormatError(ValueError):
    """A GLB written by this layer could not be read back (truncated or not produced by :func:`write_parts_glb`)."""


# --------------------------------------------------------------------------------------------------
# Low-level GLB container
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class GlbFile:
    """A parsed GLB container: the glTF JSON document and the BIN chunk (zero-copy view over the file bytes)."""

    document: dict[str, Any]
    binary: memoryview
    sha256: str
    byte_length: int


def read_glb(path: Path | str, *, error: type[ValueError] = GlbFormatError) -> GlbFile:
    """Parse a binary glTF 2.0 container. Raises ``error`` for anything that is not a well-formed GLB."""
    data = Path(path).read_bytes()
    return parse_glb(data, error=error)


def parse_glb(data: bytes, *, error: type[ValueError] = GlbFormatError) -> GlbFile:
    """Parse GLB bytes (see :func:`read_glb`)."""
    if len(data) < GLB_HEADER_BYTES + CHUNK_HEADER_BYTES:
        raise error("not a GLB file: too short")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise error("not a GLB file: bad magic")
    if version != GLB_VERSION:
        raise error(f"unsupported GLB container version {version} (expected 2)")
    if length > len(data):
        raise error(f"truncated GLB: header says {length} bytes, file has {len(data)}")
    view = memoryview(data)
    offset = GLB_HEADER_BYTES
    document: dict[str, Any] | None = None
    binary = memoryview(b"")
    while offset + CHUNK_HEADER_BYTES <= length:
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        start = offset + CHUNK_HEADER_BYTES
        end = start + chunk_length
        if end > length:
            raise error("truncated GLB chunk")
        if document is None:
            if chunk_type != CHUNK_JSON:
                raise error("the first GLB chunk is not JSON")
            try:
                parsed = json.loads(bytes(view[start:end]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise error(f"invalid glTF JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise error("invalid glTF JSON: the document is not an object")
            document = parsed
        elif chunk_type == CHUNK_BIN and len(binary) == 0:
            binary = view[start:end]
        offset = end + (-chunk_length % 4)
    if document is None:
        raise error("GLB has no JSON chunk")
    return GlbFile(document=document, binary=binary, sha256=hashlib.sha256(data).hexdigest(), byte_length=len(data))


def _list(document: Mapping[str, Any], key: str) -> list[Any]:
    value = document.get(key) or []
    return value if isinstance(value, list) else []


def read_accessor(
    glb: GlbFile,
    index: int,
    *,
    error: type[ValueError] = GlbFormatError,
    copy_data: bool = False,
) -> np.ndarray:
    """Read accessor ``index`` as a ``(count, components)`` little-endian array (``(count,)`` for SCALAR).

    Tightly packed data is returned as a read-only zero-copy view over the BIN chunk unless ``copy_data`` is set;
    strided data (``byteStride`` larger than the element) is always copied into a contiguous array. Sparse accessors,
    accessors without a bufferView and bufferViews outside the GLB BIN chunk raise ``error``.
    """
    accessors = _list(glb.document, "accessors")
    if not 0 <= index < len(accessors):
        raise error(f"accessor {index} does not exist")
    accessor = accessors[index]
    if "sparse" in accessor:
        raise error(f"accessor {index} is sparse")
    if "bufferView" not in accessor:
        raise error(f"accessor {index} has no bufferView")
    try:
        dtype = _COMPONENT_DTYPES[int(accessor["componentType"])]
        components = _TYPE_COMPONENTS[str(accessor["type"])]
        count = int(accessor["count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise error(f"accessor {index} has an invalid componentType/type/count") from exc
    views = _list(glb.document, "bufferViews")
    view_index = int(accessor["bufferView"])
    if not 0 <= view_index < len(views):
        raise error(f"accessor {index} references missing bufferView {view_index}")
    buffer_view = views[view_index]
    if int(buffer_view.get("buffer", 0)) != 0:
        raise error(f"bufferView {view_index} is not in the GLB BIN chunk")
    if buffer_view.get("extensions"):
        raise error(f"bufferView {view_index} uses extensions {sorted(buffer_view['extensions'])}")
    element = dtype.itemsize * components
    stride = int(buffer_view.get("byteStride") or element)
    view_offset = int(buffer_view.get("byteOffset", 0))
    view_length = int(buffer_view["byteLength"])
    offset = view_offset + int(accessor.get("byteOffset", 0))
    if count < 0 or stride < element:
        raise error(f"accessor {index} has an invalid count or byteStride")
    needed = 0 if count == 0 else stride * (count - 1) + element
    if offset - view_offset + needed > view_length or view_offset + view_length > len(glb.binary):
        raise error(f"accessor {index} reads past its bufferView or the BIN chunk")
    shape: tuple[int, ...] = (count,) if components == 1 else (count, components)
    if count == 0:
        return np.zeros(shape, dtype=dtype)
    if stride == element:
        array = np.frombuffer(glb.binary, dtype=dtype, count=count * components, offset=offset).reshape(shape)
        return array.copy() if copy_data else array
    strides = (stride,) if components == 1 else (stride, dtype.itemsize)
    strided = np.ndarray(shape, dtype=dtype, buffer=glb.binary, offset=offset, strides=strides)
    return np.ascontiguousarray(strided)


def _image_bytes(glb: GlbFile, image: Mapping[str, Any], index: int, error: type[ValueError]) -> bytes:
    if "bufferView" in image:
        views = _list(glb.document, "bufferViews")
        view_index = int(image["bufferView"])
        if not 0 <= view_index < len(views):
            raise error(f"image {index} references missing bufferView {view_index}")
        buffer_view = views[view_index]
        if int(buffer_view.get("buffer", 0)) != 0:
            raise error(f"image {index} is not stored in the GLB BIN chunk")
        start = int(buffer_view.get("byteOffset", 0))
        end = start + int(buffer_view["byteLength"])
        if end > len(glb.binary):
            raise error(f"image {index} reads past the BIN chunk")
        return bytes(glb.binary[start:end])
    uri = str(image.get("uri", ""))
    if uri.startswith("data:") and ";base64," in uri:
        return base64.b64decode(uri.split(";base64,", 1)[1])
    raise error(f"image {index} references an external file")


# --------------------------------------------------------------------------------------------------
# Base mesh
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class BaseMesh:
    """Raw accessors of the single base primitive of ``model.glb`` (section 3.1), plus what the writer copies.

    * ``positions`` float32 ``[V, 3]``, ``normals`` float32 ``[V, 3]`` or ``None``, ``uv`` float32 ``[V, 2]`` or
      ``None``: base vertex space, accessor order, UV-seam duplicates included.
    * ``faces`` uint32 ``[F, 3]``: base face space (index-accessor order).
    * ``document`` / ``binary``: the glTF JSON and BIN chunk; materials, textures, images and samplers are copied from
      them verbatim by :func:`write_parts_glb`.

    The arrays may be read-only views over the file bytes; callers never modify them.
    """

    positions: np.ndarray
    normals: np.ndarray | None
    uv: np.ndarray | None
    faces: np.ndarray
    sha256: str
    byte_length: int
    document: dict[str, Any]
    binary: memoryview
    material: int | None
    index_component_type: int
    path: Path | None = None

    @property
    def vertex_count(self) -> int:
        return int(self.positions.shape[0])

    @property
    def face_count(self) -> int:
        return int(self.faces.shape[0])

    @property
    def material_count(self) -> int:
        return len(_list(self.document, "materials"))

    @property
    def texture_count(self) -> int:
        return len(_list(self.document, "textures"))

    @property
    def image_count(self) -> int:
        return len(_list(self.document, "images"))

    def image_bytes(self, index: int) -> bytes:
        """Encoded bytes of image ``index`` exactly as stored in the GLB (never decoded)."""
        images = _list(self.document, "images")
        if not 0 <= index < len(images):
            raise IndexError(f"image {index} does not exist")
        glb = GlbFile(self.document, self.binary, self.sha256, self.byte_length)
        return _image_bytes(glb, images[index], index, UnsupportedBaseMeshError)

    def base_color_image_index(self) -> int | None:
        """Image index of the primitive material's baseColor texture (``EXT_texture_webp`` aware), or ``None``."""
        if self.material is None:
            return None
        materials = _list(self.document, "materials")
        textures = _list(self.document, "textures")
        try:
            texture_index = int(materials[self.material]["pbrMetallicRoughness"]["baseColorTexture"]["index"])
            texture = textures[texture_index]
        except (IndexError, KeyError, TypeError, ValueError):
            return None
        if "source" in texture:
            return int(texture["source"])
        for extension in (texture.get("extensions") or {}).values():
            if isinstance(extension, Mapping) and "source" in extension:
                return int(extension["source"])
        return None

    def base_color_texcoord(self) -> int:
        """``texCoord`` set of the baseColor texture (0 unless the material says otherwise)."""
        if self.material is None:
            return 0
        try:
            info = _list(self.document, "materials")[self.material]["pbrMetallicRoughness"]["baseColorTexture"]
        except (IndexError, KeyError, TypeError):
            return 0
        return int(info.get("texCoord", 0))


def read_base_glb(path: Path | str, *, expected_sha256: str | None = None) -> BaseMesh:
    """Read ``model.glb`` (section 3.12): raw accessors of its single triangle primitive plus the file's sha256.

    Raises :class:`UnsupportedBaseMeshError` for anything that cannot be reassembled losslessly and
    :class:`BaseMeshChangedError` when ``expected_sha256`` is given and differs.
    """
    path = Path(path)
    try:
        glb = read_glb(path, error=UnsupportedBaseMeshError)
    except OSError as exc:
        raise UnsupportedBaseMeshError(f"cannot read {path.name}: {exc}") from exc
    if expected_sha256 is not None and glb.sha256 != expected_sha256.lower():
        raise BaseMeshChangedError(f"{path.name} has sha256 {glb.sha256}, expected {expected_sha256}")
    return _base_from_glb(glb, path)


def _base_from_glb(glb: GlbFile, path: Path | None) -> BaseMesh:
    error = UnsupportedBaseMeshError
    document = glb.document
    version = str((document.get("asset") or {}).get("version", ""))
    if not version.startswith("2."):
        raise error(f"unsupported glTF version {version!r}")
    for key in ("extensionsUsed", "extensionsRequired"):
        compressed = COMPRESSION_EXTENSIONS.intersection(_list(document, key))
        if compressed:
            raise error(f"compressed geometry is not supported ({', '.join(sorted(compressed))})")
    buffers = _list(document, "buffers")
    if len(buffers) != 1 or "uri" in buffers[0]:
        raise error("the GLB must store all data in its own BIN chunk (exactly one buffer, no uri)")
    if int(buffers[0].get("byteLength", 0)) > len(glb.binary):
        raise error("the BIN chunk is shorter than the declared buffer")
    meshes = _list(document, "meshes")
    if len(meshes) != 1:
        raise error(f"expected exactly one mesh, found {len(meshes)}")
    primitives = _list(meshes[0], "primitives")
    if len(primitives) != 1:
        raise error(f"expected exactly one primitive, found {len(primitives)}")
    primitive = primitives[0]
    if int(primitive.get("mode", MODE_TRIANGLES)) != MODE_TRIANGLES:
        raise error("the primitive is not a triangle list (mode 4)")
    if primitive.get("extensions"):
        raise error(f"primitive extensions are not supported ({', '.join(sorted(primitive['extensions']))})")
    if primitive.get("targets") or meshes[0].get("weights"):
        raise error("morph targets are not supported")
    attributes = primitive.get("attributes") or {}
    extra = sorted(set(attributes) - set(BASE_ATTRIBUTES))
    if extra:
        raise error(f"vertex attributes other than POSITION/NORMAL/TEXCOORD_0 are not supported ({', '.join(extra)})")
    if "POSITION" not in attributes:
        raise error("the primitive has no POSITION attribute")
    if "indices" not in primitive:
        raise error("the primitive has no indices")
    _check_mesh_nodes_untransformed(document)

    accessors = _list(document, "accessors")
    positions = _float_attribute(glb, attributes, "POSITION", "VEC3")
    vertex_count = int(positions.shape[0])
    normals = _float_attribute(glb, attributes, "NORMAL", "VEC3") if "NORMAL" in attributes else None
    uv = _float_attribute(glb, attributes, "TEXCOORD_0", "VEC2") if "TEXCOORD_0" in attributes else None
    for name, array in (("NORMAL", normals), ("TEXCOORD_0", uv)):
        if array is not None and array.shape[0] != vertex_count:
            raise error(f"{name} has {array.shape[0]} elements, POSITION has {vertex_count}")

    index_accessor = int(primitive["indices"])
    accessor = accessors[index_accessor] if 0 <= index_accessor < len(accessors) else {}
    component_type = int(accessor.get("componentType", 0))
    if component_type not in _INDEX_COMPONENT_TYPES or accessor.get("type") != "SCALAR":
        raise error("indices must be an unsigned SCALAR accessor")
    indices = read_accessor(glb, index_accessor, error=error)
    if indices.shape[0] % 3:
        raise error(f"index count {indices.shape[0]} is not a multiple of 3")
    if indices.shape[0] and int(indices.max()) >= vertex_count:
        raise error("an index points past the vertex arrays")
    faces = (indices if component_type == COMPONENT_UNSIGNED_INT else indices.astype(np.uint32)).reshape(-1, 3)
    for image_index, image in enumerate(_list(document, "images")):
        if "bufferView" not in image and not str(image.get("uri", "")).startswith("data:"):
            raise error(f"image {image_index} references an external file")
    material = primitive.get("material")
    return BaseMesh(
        positions=positions,
        normals=normals,
        uv=uv,
        faces=faces,
        sha256=glb.sha256,
        byte_length=glb.byte_length,
        document=document,
        binary=glb.binary,
        material=None if material is None else int(material),
        index_component_type=component_type,
        path=path,
    )


def _float_attribute(glb: GlbFile, attributes: Mapping[str, Any], name: str, type_name: str) -> np.ndarray:
    accessors = _list(glb.document, "accessors")
    index = int(attributes[name])
    accessor = accessors[index] if 0 <= index < len(accessors) else {}
    if int(accessor.get("componentType", 0)) != COMPONENT_FLOAT or accessor.get("type") != type_name:
        raise UnsupportedBaseMeshError(f"{name} must be a FLOAT {type_name} accessor")
    if accessor.get("normalized"):
        raise UnsupportedBaseMeshError(f"{name} must not be normalized")
    return read_accessor(glb, index, error=UnsupportedBaseMeshError)


def _check_mesh_nodes_untransformed(document: Mapping[str, Any]) -> None:
    nodes = _list(document, "nodes")
    parent: dict[int, int] = {}
    for index, node in enumerate(nodes):
        for child in node.get("children") or []:
            parent[int(child)] = index
    for index, node in enumerate(nodes):
        if node.get("mesh") is None:
            continue
        current: int | None = index
        visited: set[int] = set()
        while current is not None and current not in visited:
            visited.add(current)
            if not _is_identity_node(nodes[current]):
                raise UnsupportedBaseMeshError("the mesh node (or a parent) has a transform")
            current = parent.get(current)


def _is_identity_node(node: Mapping[str, Any]) -> bool:
    if "matrix" in node and tuple(float(v) for v in node["matrix"]) != _IDENTITY_MATRIX:
        return False
    if "translation" in node and any(float(v) != 0.0 for v in node["translation"]):
        return False
    if "rotation" in node and tuple(float(v) for v in node["rotation"]) != (0.0, 0.0, 0.0, 1.0):
        return False
    return not ("scale" in node and any(float(v) != 1.0 for v in node["scale"]))


# --------------------------------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class NodeSpec:
    """One mesh node to write: node-local arrays plus the value of the glTF node's ``extras.img3d``.

    ``positions``/``normals``/``uv`` are float32, ``faces`` uint32 ``[n, 3]`` (node-local corners), ``feature_ids``
    uint16 ``[m]`` (``_FEATURE_ID_0``). ``extras`` must carry ``kind``; ``id`` is set to ``part_id``.
    """

    part_id: str
    positions: np.ndarray
    faces: np.ndarray
    feature_ids: np.ndarray
    extras: Mapping[str, Any]
    normals: np.ndarray | None = None
    uv: np.ndarray | None = None

    @classmethod
    def from_mesh_node(cls, base: BaseMesh, node: MeshNode, extras: Mapping[str, Any]) -> NodeSpec:
        """Gather the base accessors by ``node.vmap`` (bit-exact by construction, A4)."""
        vmap = node.vmap
        return cls(
            part_id=node.part_id,
            positions=base.positions[vmap],
            normals=None if base.normals is None else base.normals[vmap],
            uv=None if base.uv is None else base.uv[vmap],
            faces=node.local_faces,
            feature_ids=node.feature_ids,
            extras=extras,
        )


@dataclass(frozen=True, slots=True)
class GlbWriteReport:
    """What :func:`write_parts_glb` wrote: file size, sha256 and number of mesh nodes."""

    bytes: int
    sha256: str
    nodes: int


@dataclass(slots=True)
class _BinLayout:
    pieces: list[memoryview]
    views: list[dict[str, Any]]
    offset: int = 0

    def add(self, data: np.ndarray | bytes, *, target: int | None = None, stride: int | None = None) -> int:
        raw = memoryview(data).cast("B") if isinstance(data, np.ndarray) else memoryview(data)
        pad = -self.offset % 4
        if pad:
            self.pieces.append(memoryview(bytes(pad)))
            self.offset += pad
        view: dict[str, Any] = {"buffer": 0, "byteOffset": self.offset, "byteLength": len(raw)}
        if stride is not None:
            view["byteStride"] = stride
        if target is not None:
            view["target"] = target
        self.pieces.append(raw)
        self.offset += len(raw)
        self.views.append(view)
        return len(self.views) - 1


def write_parts_glb(
    path: Path | str,
    base: BaseMesh,
    nodes: Sequence[NodeSpec],
    *,
    scene_extras: Mapping[str, Any],
) -> GlbWriteReport:
    """Write a multi-node GLB (section 3.12). ``scene_extras`` becomes the scene's ``extras.img3d``.

    The file is written to a temporary sibling and renamed into place, so a crash never leaves a half-written GLB.
    """
    if not nodes:
        raise ValueError("write_parts_glb needs at least one node")
    ids = [spec.part_id for spec in nodes]
    if len(set(ids)) != len(ids):
        raise ValueError("node part ids must be unique")

    layout = _BinLayout(pieces=[], views=[])
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    gltf_nodes: list[dict[str, Any]] = [{"name": ASSET_NODE_NAME, "children": list(range(1, len(nodes) + 1))}]
    for mesh_index, spec in enumerate(nodes):
        attributes = _write_node_arrays(layout, accessors, base, spec)
        primitive: dict[str, Any] = {"attributes": attributes["attributes"], "indices": attributes["indices"],
                                     "mode": MODE_TRIANGLES}
        if base.material is not None:
            primitive["material"] = base.material
        meshes.append({"name": spec.part_id, "primitives": [primitive]})
        extras = {"img3d": _node_extras(spec)}
        gltf_nodes.append({"name": node_name(spec.part_id), "mesh": mesh_index, "extras": extras})

    document = base.document
    images = copy.deepcopy(_list(document, "images"))
    base_glb = GlbFile(document, base.binary, base.sha256, base.byte_length)
    for image_index, image in enumerate(images):
        if "bufferView" in image:
            image["bufferView"] = layout.add(_image_bytes(base_glb, image, image_index, UnsupportedBaseMeshError))

    gltf: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": WRITER_GENERATOR},
        "scene": 0,
        "scenes": [{"name": SCENE_NAME, "nodes": [0], "extras": {"img3d": _jsonable(dict(scene_extras))}}],
        "nodes": gltf_nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": layout.views,
        "buffers": [{"byteLength": layout.offset}],
    }
    for key in ("materials", "textures", "samplers"):
        if _list(document, key):
            gltf[key] = copy.deepcopy(_list(document, key))
    if images:
        gltf["images"] = images
    for key in ("extensionsUsed", "extensionsRequired"):
        if _list(document, key):
            gltf[key] = list(_list(document, key))

    json_bytes = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    bin_padding = -layout.offset % 4
    bin_length = layout.offset + bin_padding
    total = GLB_HEADER_BYTES + CHUNK_HEADER_BYTES + len(json_bytes) + CHUNK_HEADER_BYTES + bin_length
    if total > GLB_MAX_BYTES:
        raise ValueError(f"the GLB would be {total} bytes; the container limit is 4 GiB")

    target = Path(path)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as handle:
            head = struct.pack("<III", GLB_MAGIC, GLB_VERSION, total)
            head += struct.pack("<II", len(json_bytes), CHUNK_JSON) + json_bytes
            head += struct.pack("<II", bin_length, CHUNK_BIN)
            for piece in _iter_chunks([memoryview(head), *layout.pieces, memoryview(bytes(bin_padding))]):
                handle.write(piece)
                digest.update(piece)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return GlbWriteReport(bytes=total, sha256=digest.hexdigest(), nodes=len(nodes))


def _iter_chunks(pieces: Iterable[memoryview]) -> Iterable[memoryview]:
    for piece in pieces:
        for start in range(0, len(piece), _WRITE_CHUNK_BYTES):
            yield piece[start:start + _WRITE_CHUNK_BYTES]


def _node_extras(spec: NodeSpec) -> dict[str, Any]:
    extras = _jsonable(dict(spec.extras))
    if extras.setdefault("id", spec.part_id) != spec.part_id:
        raise ValueError(f"extras.img3d.id {extras['id']!r} differs from part id {spec.part_id!r}")
    if "kind" not in extras:
        raise ValueError(f"extras.img3d of part {spec.part_id!r} has no 'kind'")
    return extras


def _jsonable(value: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"extras are not JSON-serialisable: {exc}") from exc


def _write_node_arrays(
    layout: _BinLayout, accessors: list[dict[str, Any]], base: BaseMesh, spec: NodeSpec
) -> dict[str, Any]:
    positions = _require_array(spec.positions, np.float32, 3, f"{spec.part_id}: positions")
    count = int(positions.shape[0])
    if count == 0 or spec.faces.size == 0:
        raise ValueError(f"part {spec.part_id!r} has no vertices or no faces")
    faces = _require_array(spec.faces, np.uint32, 3, f"{spec.part_id}: faces")
    if int(faces.max()) >= count:
        raise ValueError(f"part {spec.part_id!r}: a face index points past its vertices")
    feature_ids = np.asarray(spec.feature_ids)
    if feature_ids.dtype != np.uint16 or feature_ids.shape != (count,):
        raise ValueError(f"part {spec.part_id!r}: feature_ids must be uint16 with one entry per vertex")
    optional: list[tuple[str, np.ndarray, int, str]] = []
    for name, array, base_array, width, type_name in (
        ("NORMAL", spec.normals, base.normals, 3, "VEC3"),
        ("TEXCOORD_0", spec.uv, base.uv, 2, "VEC2"),
    ):
        if (array is None) != (base_array is None):
            raise ValueError(f"part {spec.part_id!r}: {name} must be present exactly when the base has it")
        if array is not None:
            checked = _require_array(array, np.float32, width, f"{spec.part_id}: {name}")
            if checked.shape[0] != count:
                raise ValueError(f"part {spec.part_id!r}: {name} has {checked.shape[0]} elements, expected {count}")
            optional.append((name, checked, width, type_name))

    indices_view = layout.add(faces.astype("<u4", copy=False).reshape(-1), target=TARGET_ELEMENT_ARRAY_BUFFER)
    accessors.append({"bufferView": indices_view, "componentType": COMPONENT_UNSIGNED_INT, "count": int(faces.size),
                      "type": "SCALAR"})
    indices_accessor = len(accessors) - 1

    attributes: dict[str, int] = {}
    position_view = layout.add(positions.astype("<f4", copy=False), target=TARGET_ARRAY_BUFFER)
    accessors.append({
        "bufferView": position_view,
        "componentType": COMPONENT_FLOAT,
        "count": count,
        "type": "VEC3",
        "min": [float(v) for v in positions.min(axis=0)],
        "max": [float(v) for v in positions.max(axis=0)],
    })
    attributes["POSITION"] = len(accessors) - 1
    for name, array, _width, type_name in optional:
        view = layout.add(array.astype("<f4", copy=False), target=TARGET_ARRAY_BUFFER)
        accessors.append({"bufferView": view, "componentType": COMPONENT_FLOAT, "count": count, "type": type_name})
        attributes[name] = len(accessors) - 1

    padded = np.zeros((count, FEATURE_ID_STRIDE // 2), dtype="<u2")
    padded[:, 0] = feature_ids
    feature_view = layout.add(padded, target=TARGET_ARRAY_BUFFER, stride=FEATURE_ID_STRIDE)
    accessors.append({"bufferView": feature_view, "byteOffset": 0, "componentType": COMPONENT_UNSIGNED_SHORT,
                      "count": count, "type": "SCALAR"})
    attributes[REGION_ATTRIBUTE] = len(accessors) - 1
    return {"attributes": attributes, "indices": indices_accessor}


def _require_array(array: np.ndarray, dtype: type[np.generic], width: int, what: str) -> np.ndarray:
    value = np.asarray(array)
    if value.dtype != dtype:
        raise ValueError(f"{what} must be {np.dtype(dtype).name}, got {value.dtype.name}")
    if value.ndim != 2 or value.shape[1] != width:
        raise ValueError(f"{what} must have shape (n, {width}), got {value.shape}")
    return np.ascontiguousarray(value)


# --------------------------------------------------------------------------------------------------
# Reading a written parts GLB back
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class GlbMeshNode:
    """One mesh node of a parts GLB as stored in the file (accessor values, never recomputed)."""

    part_id: str
    name: str
    extras: dict[str, Any]
    positions: np.ndarray
    normals: np.ndarray | None
    uv: np.ndarray | None
    faces: np.ndarray
    feature_ids: np.ndarray | None
    material: int | None


@dataclass(frozen=True, slots=True, eq=False)
class PartsGlb:
    """A parsed ``model_parts.glb`` (or per-part GLB)."""

    glb: GlbFile
    scene_extras: dict[str, Any]
    nodes: tuple[GlbMeshNode, ...]

    def node(self, part_id: str) -> GlbMeshNode:
        for node in self.nodes:
            if node.part_id == part_id:
                return node
        raise KeyError(part_id)

    def image_bytes(self, index: int) -> bytes:
        images = _list(self.glb.document, "images")
        return _image_bytes(self.glb, images[index], index, GlbFormatError)


def read_parts_glb(path: Path | str) -> PartsGlb:
    """Read every mesh node of a GLB written by :func:`write_parts_glb`.

    Nodes are matched by ``extras.img3d.id`` first and by the ``part:<id>`` node name otherwise.
    """
    glb = read_glb(path)
    document = glb.document
    meshes = _list(document, "meshes")
    scenes = _list(document, "scenes")
    scene_index = int(document.get("scene", 0))
    scene = scenes[scene_index] if 0 <= scene_index < len(scenes) else {}
    scene_extras = dict((scene.get("extras") or {}).get("img3d") or {})
    result: list[GlbMeshNode] = []
    for node in _list(document, "nodes"):
        if node.get("mesh") is None:
            continue
        extras = dict((node.get("extras") or {}).get("img3d") or {})
        name = str(node.get("name", ""))
        part_id = str(extras.get("id") or name.removeprefix(NODE_NAME_PREFIX))
        mesh_index = int(node["mesh"])
        if not 0 <= mesh_index < len(meshes):
            raise GlbFormatError(f"node {name!r} references missing mesh {mesh_index}")
        primitives = _list(meshes[mesh_index], "primitives")
        if len(primitives) != 1:
            raise GlbFormatError(f"mesh of node {name!r} must have exactly one primitive")
        primitive = primitives[0]
        attributes = primitive.get("attributes") or {}
        if "POSITION" not in attributes or "indices" not in primitive:
            raise GlbFormatError(f"mesh of node {name!r} has no POSITION or no indices")
        indices = read_accessor(glb, int(primitive["indices"]))
        if indices.shape[0] % 3:
            raise GlbFormatError(f"mesh of node {name!r} has an index count that is not a multiple of 3")

        def attribute(key: str, *, _attributes: Mapping[str, Any] = attributes) -> np.ndarray | None:
            return read_accessor(glb, int(_attributes[key])) if key in _attributes else None

        material = primitive.get("material")
        result.append(
            GlbMeshNode(
                part_id=part_id,
                name=name,
                extras=extras,
                positions=read_accessor(glb, int(attributes["POSITION"])),
                normals=attribute("NORMAL"),
                uv=attribute("TEXCOORD_0"),
                faces=indices.astype(np.uint32, copy=False).reshape(-1, 3),
                feature_ids=attribute(REGION_ATTRIBUTE),
                material=None if material is None else int(material),
            )
        )
    return PartsGlb(glb=glb, scene_extras=scene_extras, nodes=tuple(result))


# --------------------------------------------------------------------------------------------------
# Per-face base colour (section 4.6.5 step 1)
# --------------------------------------------------------------------------------------------------


def base_color_texture(base: BaseMesh) -> np.ndarray | None:
    """Decode the baseColor atlas once (Pillow) as uint8 RGB ``[H, W, 3]``; ``None`` without a baseColor texture."""
    image_index = base.base_color_image_index()
    if image_index is None or not 0 <= image_index < base.image_count:
        return None
    from PIL import Image  # local import: only the colour step decodes the atlas

    with Image.open(io.BytesIO(base.image_bytes(image_index))) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def face_base_color_lab(base: BaseMesh, *, texture: np.ndarray | None = None) -> np.ndarray | None:
    """CIE Lab (D65) colour of the baseColor atlas at every face's UV centroid, float32 ``[F, 3]``.

    glTF UVs have their origin at the image's top-left corner and the default sampler wraps (REPEAT), so
    ``pixel = floor(frac(uv) * size)``. Returns ``None`` without UVs or a baseColor texture. The material's
    ``baseColorFactor`` multiplies the texel, as the glTF metallic-roughness model defines.
    """
    if base.uv is None or base.base_color_texcoord() != 0:
        return None
    rgb = base_color_texture(base) if texture is None else texture
    if rgb is None:
        return None
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    uv_centroid = base.uv[base.faces].astype(np.float64).mean(axis=1)
    frac = uv_centroid - np.floor(uv_centroid)
    x = np.minimum((frac[:, 0] * width).astype(np.int64), width - 1)
    y = np.minimum((frac[:, 1] * height).astype(np.int64), height - 1)
    srgb = rgb[y, x].astype(np.float64) / 255.0
    factor = _base_color_factor(base)
    if factor is not None:
        srgb = _linear_to_srgb(_srgb_to_linear(srgb) * factor)
    return srgb_to_lab(srgb).astype(np.float32)


def _base_color_factor(base: BaseMesh) -> np.ndarray | None:
    if base.material is None:
        return None
    try:
        factor = _list(base.document, "materials")[base.material]["pbrMetallicRoughness"]["baseColorFactor"]
    except (IndexError, KeyError, TypeError):
        return None
    rgb = np.asarray([float(v) for v in factor[:3]], dtype=np.float64)
    return None if np.all(rgb == 1.0) else rgb


def _srgb_to_linear(srgb: np.ndarray) -> np.ndarray:
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    linear = np.clip(linear, 0.0, 1.0)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1.0 / 2.4) - 0.055)


_SRGB_TO_XYZ: Final = np.array(
    [[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]]
)
_D65_WHITE: Final = np.array([0.95047, 1.0, 1.08883])


def srgb_to_lab(srgb: np.ndarray) -> np.ndarray:
    """sRGB in [0, 1] (``[..., 3]``) to CIE Lab with the D65 white point."""
    xyz = _srgb_to_linear(np.asarray(srgb, dtype=np.float64)) @ _SRGB_TO_XYZ.T / _D65_WHITE
    delta = 6.0 / 29.0
    f = np.where(xyz > delta**3, np.cbrt(xyz), xyz / (3.0 * delta**2) + 4.0 / 29.0)
    lightness = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([lightness, a, b], axis=-1)
