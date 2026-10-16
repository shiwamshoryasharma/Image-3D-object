"""GLB reader and writer of the part asset layer (part-aware DESIGN.md sections 3.12 and 10.2, slice S3a).

``read_base_glb``: raw accessors equal the written values, the sha256 is checked, and multi-primitive, sparse, Draco,
transformed, non-indexed and extra-attribute inputs are rejected with ``unsupported_base_mesh``.
``write_parts_glb``: image bufferViews are copied byte for byte, ``POSITION`` has ``min``/``max``, ``_FEATURE_ID_0``
is UNSIGNED_SHORT in its own bufferView with ``byteStride`` 4, every vertex-attribute element is 4-byte aligned, every
``part:<id>`` node carries ``extras.img3d.id``, trimesh loads the file and ``compute_glb_stats`` sums node faces.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from parts_fixtures import base_document, texture_bytes, uv_sphere, write_base_glb, write_glb

from parts.gltf_io import (
    COMPONENT_UNSIGNED_SHORT,
    BaseMeshChangedError,
    NodeSpec,
    UnsupportedBaseMeshError,
    face_base_color_lab,
    read_base_glb,
    read_glb,
    read_parts_glb,
    srgb_to_lab,
    write_parts_glb,
)
from parts.partition import build_mesh_nodes
from parts.types import REGION_ATTRIBUTE
from processing.mesh_stats import compute_glb_stats


@pytest.fixture
def sphere():
    return uv_sphere(3000)


@pytest.fixture
def base_path(tmp_runtime: Path, sphere) -> Path:
    return write_base_glb(tmp_runtime / "model.glb", sphere)


def _bits(array: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(array).view(np.uint32)


def _two_part_specs(base, labels: np.ndarray) -> list[NodeSpec]:
    owner = (base.positions[base.faces].mean(axis=1)[:, 1] > 0.3).astype(np.int64)
    nodes = build_mesh_nodes(base, owner, labels, part_ids=["body", "hair"])
    extras = [{"kind": "base", "parent": None}, {"kind": "physical", "parent": "body", "seams": ["seam_0"]}]
    return [NodeSpec.from_mesh_node(base, node, extra) for node, extra in zip(nodes, extras, strict=True)]


def _labels(base) -> np.ndarray:
    return np.where(base.positions[base.faces].mean(axis=1)[:, 0] > 0.0, 3, 5).astype(np.uint16)


def test_read_base_glb_returns_the_raw_accessors_and_the_file_hash(base_path: Path, sphere):
    base = read_base_glb(base_path)
    assert base.positions.dtype == np.float32 and base.positions.shape == sphere.positions.shape
    assert np.array_equal(_bits(base.positions), _bits(sphere.positions))
    assert np.array_equal(_bits(base.normals), _bits(sphere.normals))
    assert np.array_equal(_bits(base.uv), _bits(sphere.uv))
    assert base.faces.dtype == np.uint32 and np.array_equal(base.faces, sphere.faces)
    assert base.sha256 == hashlib.sha256(base_path.read_bytes()).hexdigest()
    assert base.byte_length == base_path.stat().st_size
    assert (base.material_count, base.texture_count, base.image_count) == (1, 2, 2)
    assert base.base_color_image_index() == 0
    assert read_base_glb(base_path, expected_sha256=base.sha256.upper()).face_count == sphere.face_count
    with pytest.raises(BaseMeshChangedError):
        read_base_glb(base_path, expected_sha256="0" * 64)


def test_short_index_accessors_are_widened_to_uint32(tmp_runtime: Path):
    mesh = uv_sphere(200)
    base = read_base_glb(write_base_glb(tmp_runtime / "u16.glb", mesh, index_dtype="<u2"))
    assert base.faces.dtype == np.uint32 and np.array_equal(base.faces, mesh.faces)


def _mutated(tmp_runtime: Path, name: str, mutate) -> Path:
    document, binary = base_document(uv_sphere(200))
    mutate(document)
    return write_glb(tmp_runtime / name, document, binary)


def _second_primitive(doc: dict[str, Any]) -> None:
    doc["meshes"][0]["primitives"].append(dict(doc["meshes"][0]["primitives"][0]))


def _second_mesh(doc: dict[str, Any]) -> None:
    doc["meshes"].append(json.loads(json.dumps(doc["meshes"][0])))


def _sparse(doc: dict[str, Any]) -> None:
    doc["accessors"][1]["sparse"] = {"count": 1, "indices": {"bufferView": 0, "componentType": 5125},
                                     "values": {"bufferView": 1}}


def _draco(doc: dict[str, Any]) -> None:
    doc["extensionsUsed"].append("KHR_draco_mesh_compression")
    doc["extensionsRequired"].append("KHR_draco_mesh_compression")
    doc["meshes"][0]["primitives"][0]["extensions"] = {
        "KHR_draco_mesh_compression": {"bufferView": 0, "attributes": {"POSITION": 0}}}


def _meshopt(doc: dict[str, Any]) -> None:
    doc["extensionsUsed"].append("EXT_meshopt_compression")


def _translated(doc: dict[str, Any]) -> None:
    doc["nodes"][0]["translation"] = [0.0, 1.0, 0.0]


def _parent_scaled(doc: dict[str, Any]) -> None:
    doc["nodes"].append({"name": "root", "children": [0], "scale": [2.0, 2.0, 2.0]})


def _no_indices(doc: dict[str, Any]) -> None:
    del doc["meshes"][0]["primitives"][0]["indices"]


def _extra_attribute(doc: dict[str, Any]) -> None:
    doc["meshes"][0]["primitives"][0]["attributes"]["COLOR_0"] = 1


def _lines(doc: dict[str, Any]) -> None:
    doc["meshes"][0]["primitives"][0]["mode"] = 1


def _morph_targets(doc: dict[str, Any]) -> None:
    doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 1}]


def _external_buffer(doc: dict[str, Any]) -> None:
    doc["buffers"][0]["uri"] = "model.bin"


def _quantized_positions(doc: dict[str, Any]) -> None:
    doc["accessors"][1]["componentType"] = 5123


@pytest.mark.parametrize(
    "mutate",
    [_second_primitive, _second_mesh, _sparse, _draco, _meshopt, _translated, _parent_scaled, _no_indices,
     _extra_attribute, _lines, _morph_targets, _external_buffer, _quantized_positions],
)
def test_unsupported_base_meshes_are_rejected(tmp_runtime: Path, mutate):
    path = _mutated(tmp_runtime, f"{mutate.__name__.strip('_')}.glb", mutate)
    with pytest.raises(UnsupportedBaseMeshError) as info:
        read_base_glb(path)
    assert info.value.code == "unsupported_base_mesh"


def test_corrupt_and_missing_files_are_rejected(tmp_runtime: Path, base_path: Path):
    truncated = tmp_runtime / "truncated.glb"
    truncated.write_bytes(base_path.read_bytes()[:1000])
    not_glb = tmp_runtime / "plain.glb"
    not_glb.write_bytes(b"not a glb file at all, just text")
    for path in (truncated, not_glb, tmp_runtime / "missing.glb"):
        with pytest.raises(UnsupportedBaseMeshError):
            read_base_glb(path)


def test_writer_layout_alignment_and_feature_ids(tmp_runtime: Path, base_path: Path):
    base = read_base_glb(base_path)
    specs = _two_part_specs(base, _labels(base))
    out = tmp_runtime / "model_parts.glb"
    report = write_parts_glb(out, base, specs, scene_extras={"schema": "img3d.parts/1", "analysis_id": "pa_test"})
    data = out.read_bytes()
    assert report.bytes == len(data) and report.nodes == 2
    assert report.sha256 == hashlib.sha256(data).hexdigest()
    assert not list(tmp_runtime.glob(".*.tmp"))

    gltf = read_glb(out).document
    assert gltf["scenes"][0]["name"] == "img3d_parts"
    assert gltf["scenes"][0]["extras"]["img3d"]["analysis_id"] == "pa_test"
    asset = gltf["nodes"][0]
    assert asset["name"] == "img3d_asset" and asset["children"] == [1, 2]
    assert "matrix" not in asset and "translation" not in asset
    views = gltf["bufferViews"]
    assert all(view["byteOffset"] % 4 == 0 for view in views)
    for node, spec in zip(gltf["nodes"][1:], specs, strict=True):
        assert node["name"] == f"part:{spec.part_id}"
        assert node["extras"]["img3d"]["id"] == spec.part_id and "kind" in node["extras"]["img3d"]
        primitive = gltf["meshes"][node["mesh"]]["primitives"][0]
        assert primitive["material"] == 0 and primitive["mode"] == 4
        assert set(primitive["attributes"]) == {"POSITION", "NORMAL", "TEXCOORD_0", REGION_ATTRIBUTE}
        assert gltf["accessors"][primitive["indices"]]["componentType"] == 5125
        for name, accessor_index in primitive["attributes"].items():
            accessor = gltf["accessors"][accessor_index]
            view = views[accessor["bufferView"]]
            assert (view["byteOffset"] + accessor.get("byteOffset", 0)) % 4 == 0, name
            assert view.get("byteStride", 4) % 4 == 0
        feature = gltf["accessors"][primitive["attributes"][REGION_ATTRIBUTE]]
        feature_view = views[feature["bufferView"]]
        assert feature["componentType"] == COMPONENT_UNSIGNED_SHORT and feature["type"] == "SCALAR"
        assert feature_view["byteStride"] == 4 and feature.get("byteOffset", 0) % 4 == 0
        assert sum(1 for a in gltf["accessors"] if a["bufferView"] == feature["bufferView"]) == 1
        position = gltf["accessors"][primitive["attributes"]["POSITION"]]
        assert position["min"] == [float(v) for v in spec.positions.min(axis=0)]
        assert position["max"] == [float(v) for v in spec.positions.max(axis=0)]


def test_writer_copies_materials_and_image_bytes_verbatim(tmp_runtime: Path, base_path: Path):
    base = read_base_glb(base_path)
    out = tmp_runtime / "model_parts.glb"
    write_parts_glb(out, base, _two_part_specs(base, _labels(base)), scene_extras={})
    written = read_parts_glb(out)
    gltf = written.glb.document
    for key in ("materials", "textures", "extensionsUsed", "extensionsRequired"):
        assert gltf[key] == base.document[key]
    assert "samplers" not in gltf
    assert len(gltf["images"]) == base.image_count
    for index in range(base.image_count):
        assert written.image_bytes(index) == base.image_bytes(index)
        assert gltf["images"][index]["mimeType"] == base.document["images"][index]["mimeType"]


def test_read_parts_glb_round_trips_every_accessor(tmp_runtime: Path, base_path: Path):
    base = read_base_glb(base_path)
    specs = _two_part_specs(base, _labels(base))
    out = tmp_runtime / "model_parts.glb"
    write_parts_glb(out, base, specs, scene_extras={})
    written = read_parts_glb(out)
    assert [node.part_id for node in written.nodes] == ["body", "hair"]
    for spec in specs:
        node = written.node(spec.part_id)
        assert np.array_equal(_bits(node.positions), _bits(spec.positions))
        assert np.array_equal(_bits(node.normals), _bits(spec.normals))
        assert np.array_equal(_bits(node.uv), _bits(spec.uv))
        assert np.array_equal(node.faces, spec.faces)
        assert node.feature_ids.dtype == np.uint16 and np.array_equal(node.feature_ids, spec.feature_ids)
        assert node.material == 0


def test_trimesh_loads_the_output_and_stats_sum_node_faces(tmp_runtime: Path, base_path: Path):
    import trimesh

    base = read_base_glb(base_path)
    out = tmp_runtime / "model_parts.glb"
    write_parts_glb(out, base, _two_part_specs(base, _labels(base)), scene_extras={})
    scene = trimesh.load(str(out), force="scene")
    assert sum(len(geometry.faces) for geometry in scene.dump()) == base.face_count
    stats = compute_glb_stats(out)
    base_stats = compute_glb_stats(base_path)
    assert stats.faces == base.face_count == base_stats.faces
    assert stats.materials == 1
    assert stats.textures == base_stats.textures == base.texture_count


def test_single_node_glb_and_writer_validation(tmp_runtime: Path, base_path: Path):
    base = read_base_glb(base_path)
    specs = _two_part_specs(base, _labels(base))
    single = tmp_runtime / "hair.glb"
    assert write_parts_glb(single, base, specs[1:], scene_extras={}).nodes == 1
    assert compute_glb_stats(single).faces == specs[1].faces.shape[0]

    hair = specs[1]
    cases = [
        [],
        [hair, hair],
        [NodeSpec(hair.part_id, hair.positions, hair.faces, hair.feature_ids, {"id": "other", "kind": "physical"},
                  hair.normals, hair.uv)],
        [NodeSpec(hair.part_id, hair.positions, hair.faces, hair.feature_ids, {"parent": "body"}, hair.normals,
                  hair.uv)],
        [NodeSpec(hair.part_id, hair.positions.astype(np.float64), hair.faces, hair.feature_ids, {"kind": "physical"},
                  hair.normals, hair.uv)],
        [NodeSpec(hair.part_id, hair.positions, hair.faces, hair.feature_ids.astype(np.uint32), {"kind": "physical"},
                  hair.normals, hair.uv)],
        [NodeSpec(hair.part_id, hair.positions, hair.faces, hair.feature_ids, {"kind": "physical"}, None, hair.uv)],
        [NodeSpec(hair.part_id, hair.positions, hair.faces, hair.feature_ids, {"kind": "physical",
                                                                                "bad": float("nan")},
                  hair.normals, hair.uv)],
    ]
    for case in cases:
        with pytest.raises(ValueError):
            write_parts_glb(tmp_runtime / "bad.glb", base, case, scene_extras={})
    assert not (tmp_runtime / "bad.glb").exists()


def test_face_base_color_lab_samples_the_base_color_atlas(tmp_runtime: Path):
    mesh = uv_sphere(500)
    images = (texture_bytes(color=(255, 0, 0), fmt="PNG"), texture_bytes(color=(0, 0, 0), fmt="PNG"))
    base = read_base_glb(write_base_glb(tmp_runtime / "red.glb", mesh, images=images))
    lab = face_base_color_lab(base)
    assert lab.shape == (mesh.face_count, 3) and lab.dtype == np.float32
    assert np.allclose(lab, [53.2408, 80.0925, 67.2032], atol=0.05)
    assert np.allclose(srgb_to_lab(np.array([1.0, 1.0, 1.0])), [100.0, 0.0, 0.0], atol=0.01)
    no_uv = read_base_glb(write_base_glb(tmp_runtime / "no_uv.glb", mesh, with_uv=False))
    assert face_base_color_lab(no_uv) is None
