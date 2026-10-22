"""Read API and consumer seam of a published part layer (part-aware DESIGN.md section 3.13; slice S3b)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from parts_fixtures import SyntheticMesh, combine, uv_sphere
from test_parts_export import Layer, build_layer, character_mesh

from core.feature_errors import FeatureDisabledError
from parts.buffers import BufferError
from parts.consumers import ConsumerInfo, PartConsumerRegistry
from parts.export import write_part_glb_from_reader
from parts.gltf_io import read_parts_glb
from parts.manifest import PartRecord, PartsManifest
from parts.publish import publish
from parts.reader import AssetReader, AssetReadError

JEWELRY = 53


@pytest.fixture(scope="module")
def character() -> tuple[SyntheticMesh, np.ndarray]:
    return character_mesh()


@pytest.fixture
def published(tmp_path: Path, character: tuple[SyntheticMesh, np.ndarray]) -> Layer:
    mesh, labels = character
    layer = build_layer(tmp_path, mesh, labels, per_part_glbs=True)
    publish(layer.staging, layer.target, lambda: True)
    return layer


def test_part_equals_the_per_part_glb_accessors(published: Layer) -> None:
    reader = AssetReader(published.target)
    assert reader.manifest.analysis_id == published.analysis_id
    base = reader.base()
    for mesh in reader.manifest.meshes:
        geometry = reader.part(mesh.part_id)
        record = reader.manifest.part(mesh.part_id)
        assert record is not None and record.files.glb == f"parts/{mesh.part_id}.glb"
        node = read_parts_glb(published.target / record.files.glb).node(mesh.part_id)
        assert np.array_equal(geometry.positions, node.positions)
        assert np.array_equal(geometry.normals, node.normals)
        assert np.array_equal(geometry.uv, node.uv)
        assert np.array_equal(geometry.faces, node.faces)
        assert np.array_equal(geometry.feature_ids, node.feature_ids)
        # A4: the node is the base gathered by its vertex map.
        assert np.array_equal(geometry.positions.view(np.uint32), base.positions[geometry.vertex_map].view(np.uint32))
        assert np.array_equal(geometry.vertex_map[geometry.faces], base.faces[geometry.base_faces])
        assert geometry.frame == record.frame
    hair = reader.part("hair")
    assert hair.seams and all(seam.host_opening for seam in hair.seams)
    for seam in hair.seams:
        assert seam.other == "body"
        for ids, points in zip(seam.loops, seam.points, strict=True):
            assert points.shape == (ids.shape[0], 3)
    with pytest.raises(ValueError):
        reader.part("body.head.face")
    with pytest.raises(ValueError):
        reader.part("eyebrow.left")
    with pytest.raises(KeyError):
        reader.part("nope")


def test_region_faces_and_relations(published: Layer) -> None:
    reader = AssetReader(published.target)
    face_part = reader.buffer("face_part")
    for index, part in enumerate(reader.manifest.parts):
        faces = reader.region_faces(part.id)
        assert np.array_equal(faces, np.flatnonzero(face_part == index))
        assert faces.size == part.region.face_count
    assert reader.unassigned_faces().size == int((face_part == 0xFFFF).sum())
    assert reader.relations() == reader.manifest.relations
    assert all("eye.left" in (r.a, r.b) for r in reader.relations("eye.left"))


def test_region_faces_of_records_sharing_one_class_value(tmp_path: Path) -> None:
    """Overflow records of one class keep the class value; ``face_part`` keeps their faces apart."""
    body = uv_sphere(4000, radius=0.4)
    beads = [uv_sphere(300, radius=0.04, center=(0.6 + 0.15 * i, 0.0, 0.0)) for i in range(4)]
    mesh = combine(body, *beads)
    labels = np.full(mesh.face_count, 1, dtype=np.uint16)
    labels[body.face_count:] = JEWELRY
    layer = build_layer(tmp_path, mesh, labels, max_parts=2)
    publish(layer.staging, layer.target, lambda: True)
    reader = AssetReader(layer.target)
    jewelry = [part for part in reader.manifest.parts if part.category == "accessory.jewelry"]
    assert [p.kind for p in jewelry].count("physical") == 2
    overflow = [p for p in jewelry if p.kind == "region"]
    assert len(overflow) == 2 and {p.region.value for p in overflow} == {JEWELRY}
    assert {w.code for w in reader.manifest.warnings} >= {"max_parts_exceeded"}
    first, second = (reader.region_faces(p.id) for p in overflow)
    assert first.size == second.size == overflow[0].region.face_count
    assert np.intersect1d(first, second).size == 0
    labels_written = reader.buffer("face_labels")
    assert np.all(labels_written[first] == JEWELRY) and np.all(labels_written[second] == JEWELRY)


def test_reassemble_equals_the_base(published: Layer) -> None:
    reader = AssetReader(published.target, verify="full")
    base = reader.base()
    rebuilt = reader.reassemble()
    assert np.array_equal(rebuilt.faces, base.faces)
    assert np.array_equal(rebuilt.positions.view(np.uint32), base.positions.view(np.uint32))
    assert np.array_equal(rebuilt.normals.view(np.uint32), base.normals.view(np.uint32))
    assert np.array_equal(rebuilt.uv.view(np.uint32), base.uv.view(np.uint32))


def test_on_demand_part_glb_matches_the_assembled_node(published: Layer, tmp_path: Path) -> None:
    reader = AssetReader(published.target)
    out = tmp_path / "cache" / "hair.glb"
    out.parent.mkdir()
    report = write_part_glb_from_reader(reader, "hair", out)
    assert report.nodes == 1
    node = read_parts_glb(out).node("hair")
    assembled = reader.assembled().node("hair")
    assert np.array_equal(node.positions, assembled.positions) and np.array_equal(node.faces, assembled.faces)
    assert np.array_equal(node.feature_ids, assembled.feature_ids)
    assert node.extras["id"] == "hair" and node.extras["kind"] == "physical"


def test_verify_hashes_detects_tampering(published: Layer) -> None:
    path = published.target / "buffers" / "face_labels.u16"
    data = bytearray(path.read_bytes())
    data[1] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(BufferError):
        AssetReader(published.target).buffer("face_labels")
    with pytest.raises(BufferError):
        AssetReader(published.target, verify="full")
    unchecked = AssetReader(published.target, verify="none").buffer("face_labels")
    assert unchecked.shape == (published.manifest.source.faces,)
    assert int(unchecked[0]) == int(published.plan.face_labels[0]) ^ 0x100


def test_base_change_and_missing_manifest_are_detected(published: Layer, tmp_path: Path) -> None:
    base_path = published.job_dir / "model.glb"
    data = bytearray(base_path.read_bytes())
    data[-1] ^= 0x01
    base_path.write_bytes(bytes(data))
    with pytest.raises(ValueError):
        AssetReader(published.target).base()
    with pytest.raises(AssetReadError):
        AssetReader(tmp_path / "missing")


class _TestConsumer:
    """Test-only consumer (never shipped: zero implementations exist in the app)."""

    id = "test.closure"

    def accepts(self, part: PartRecord, manifest: PartsManifest) -> bool:
        return "closure" in part.refinement.needs

    def describe(self) -> ConsumerInfo:
        return ConsumerInfo(id=self.id, label="Test closure", version="0.0.1", handles_needs=("closure",))


def test_consumer_registry_refuses_while_disabled(published: Layer) -> None:
    registry = PartConsumerRegistry(enabled=False)
    with pytest.raises(FeatureDisabledError) as refused:
        registry.register(_TestConsumer())
    assert refused.value.code == "feature_disabled"
    assert refused.value.details == {"feature": "part_refinement", "flag": "IMG3D_PART_REFINEMENT_ENABLED"}
    assert registry.list() == () and len(registry) == 0 and registry.enabled is False
    manifest = AssetReader(published.target).manifest
    hair = manifest.part("hair")
    assert hair is not None and registry.consumers_for(hair, manifest) == ()


def test_consumer_registry_registers_when_enabled(published: Layer) -> None:
    registry = PartConsumerRegistry(enabled=True)
    consumer = _TestConsumer()
    registry.register(consumer)
    assert registry.list() == (consumer.describe(),)
    assert registry.manifest_records()[0].handles_needs == ["closure"]
    with pytest.raises(ValueError):
        registry.register(_TestConsumer())
    manifest = AssetReader(published.target).manifest
    hair = manifest.part("hair")
    face = manifest.part("body.head.face")
    assert hair is not None and face is not None
    assert registry.consumers_for(hair, manifest) == (consumer,)
    assert registry.consumers_for(face, manifest) == ()
    with pytest.raises(TypeError):
        registry.register(object())  # type: ignore[arg-type]
