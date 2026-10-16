"""Frozen part-aware interfaces of slice S1 (part-aware DESIGN.md §2.3, §3, §4.4.1-§4.7, §5.1-§5.2, §6, §7.2).

Main subject (§10.2 row): the part taxonomy v1 (``parts/taxonomy.py``) and the ``img3d.parts/1`` manifest
(``parts/manifest.py``): unique ids and values, mutual symmetric pairs, group coverage, part-id rules with Windows
reserved names, alias matching, a round trip of the §3.11 example, forward compatibility (unknown fields ignored),
schema-major rejection and path/id safety.

The other S1a interfaces are pinned here too, because later slices code against them: the request rules of the wire
schemas (``schemas/*``), the static workflow catalog, the ``WorkflowStore`` stub API and the torch-free provider ABCs.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from parts import taxonomy
from parts.manifest import PartsManifest, safe_relative_path_problem
from parts.types import (
    BUFFER_SPECS,
    FACE_PART_NONE,
    PARTS_SCHEMA,
    REGION_ATTRIBUTE,
    vmap_buffer_spec,
)

# The §3.11 example, verbatim (abridged by the design; hashes shortened).
EXAMPLE_MANIFEST_JSON = """\
{
  "schema": "img3d.parts/1", "analysis_id": "pa_3b1f0c9a2d7e", "job_id": "job_9f3c2a1b4d5e",
  "created_at": "2026-10-20T10:04:31Z", "taxonomy_version": 1,
  "generator": {"app_version": "1.0.0", "engine": "trellis2", "model": "TRELLIS.2-4B", "pipeline_type": "1536_cascade",
                "seed": 123456, "resolution": 1536, "effective_resolution": 1536, "texture_size": 4096,
                "polygon_target": 2000000},
  "source": {"model_file": "model.glb", "model_sha256": "4b1d0c…", "model_bytes": 82113340, "vertices": 1697772,
             "faces": 1895691, "materials": 1, "images": 2, "face_order": "gltf_indices",
             "vertex_order": "gltf_accessor", "primary_image_id": "img_7c1e0d9a2b3f",
             "origin": {"kind": "prompt", "image_generation_id": "ig_0123456789ab", "model_id": "z_image_turbo",
                        "model_revision": "f332072aa78be7aecdf3ee76d5c247082da564a6", "license_id": "apache-2.0",
                        "seed": 987654, "final_prompt_sha256": "c0ffee…"}},
  "frames": {
    "asset": {"convention": "gltf", "up": "+Y", "front": "+Z", "units": "normalized",
              "aabb": [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]]},
    "trellis": {"convention": "blender", "up": "+Z", "front": "-Y",
                "to_asset": [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1]},
    "conditioning": {"file": "segmentation/source.png", "width": 1000, "height": 1000,
                     "source_image_id": "img_7c1e0d9a2b3f", "source_size": [896, 1152], "scale": 0.8888889,
                     "crop_box": null, "alpha_threshold": 0.8, "mode": "background_removed",
                     "method": "identity", "verified": true, "residual": 0.0}},
  "views": [{"id": "input", "image_frame": "conditioning",
             "camera": {"model": "perspective", "yaw_deg": 0.0, "pitch_deg": 2.0, "fov_deg": 30.0, "distance": 1.93,
                        "world_to_camera": [1, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 1.93, 0, 0, 0, 1],
                        "intrinsics": {"fx": 1.866, "fy": 1.866, "cx": 0.5, "cy": 0.5, "normalized": true},
                        "convention": "opencv"},
             "fit": {"method": "silhouette_grid_refine", "iou": 0.91, "accepted": true, "candidates_tested": 1361},
             "weight": 1.0}],
  "strategy": {"id": "semantic_parts",
               "segmentation": {"source": "internal", "segmentation_id": null, "profile": "baseline",
                                "subject": "anime_character"},
               "separate": ["eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable"],
               "min_part_faces": 190, "max_parts": 64, "split_shells": true, "views": 8,
               "models": [{"id": "grounding_dino_base", "revision": "12bdfa3120f3e7ec7b434d90674b3396eccf88eb",
                           "license_id": "apache-2.0"},
                          {"id": "sam2_1_hiera_large", "revision": "665f8e2ad61cf5f53d65644ff27c8ee525124610",
                           "license_id": "apache-2.0"},
                          {"id": "raster_lifter", "revision": null, "license_id": "nvidia-source-code-nc"}]},
  "region_attribute": "_FEATURE_ID_0",
  "label_table": [{"value": 0, "category": "unknown"}, {"value": 1, "category": "body"},
                  {"value": 3, "category": "body.head.face"}, {"value": 20, "category": "eye.left"},
                  {"value": 22, "category": "eyebrow.left"}, {"value": 30, "category": "hair"}],
  "meshes": [
    {"index": 0, "part_id": "body", "node_name": "part:body", "faces": 1402233, "vertices": 1283310,
     "unique_vertices": 1271110, "vertex_map": "vmap_body"},
    {"index": 1, "part_id": "hair", "node_name": "part:hair", "faces": 410112, "vertices": 377020,
     "unique_vertices": 377020, "vertex_map": "vmap_hair"},
    {"index": 2, "part_id": "eye.left", "node_name": "part:eye.left", "faces": 2210, "vertices": 1301,
     "unique_vertices": 1301, "vertex_map": "vmap_eye.left"}],
  "parts": [
    {"id": "body", "category": "body", "group": "body", "label": "Body", "kind": "base", "side": "center",
     "image_side": "center", "parent": null, "attach_to": null, "host": null, "symmetric_with": null,
     "node_name": "part:body", "mesh_index": 0,
     "region": {"attribute": "_FEATURE_ID_0", "value": 1, "face_count": 1201733},
     "files": {"glb": null, "mask": null, "thumbnail": null},
     "stats": {"faces": 1402233, "vertices": 1283310, "area": 1.912, "bbox_min": [-0.39, -0.5, -0.19],
               "bbox_max": [0.39, 0.5, 0.2], "centroid": [0.0, -0.04, 0.0]},
     "frame": {"pivot": [0.0, -0.04, 0.0], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "area_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "open_surface", "open_boundary_loops": 6, "watertight": false,
                  "occluded_geometry_missing": true, "components": 2, "source": "component",
                  "tags": ["inner_sheet_merged"],
                  "floaters": [{"component": 41, "faces": 37, "centroid": [0.12, -0.31, 0.02]}]},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.0, 0.0, 1.0, 1.0], "charts_cross_boundary": true},
     "seams": ["seam_0", "seam_1"], "annotations": {}, "confidence": 1.0, "expected": false,
     "provenance": {"sources": [{"method": "components", "provider": "img3d", "model": null, "score": 1.0}],
                    "votes": 0},
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "eligible": true, "needs": ["occluded_geometry"],
                    "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}},
    {"id": "hair", "category": "hair", "group": "hair", "label": "Hair", "kind": "physical", "side": "center",
     "image_side": "center", "parent": "body", "attach_to": "body.head.face", "host": null, "symmetric_with": null,
     "node_name": "part:hair", "mesh_index": 1,
     "region": {"attribute": "_FEATURE_ID_0", "value": 30, "face_count": 410112},
     "files": {"glb": null, "mask": "masks/30.png", "thumbnail": "thumbs/hair.webp"},
     "stats": {"faces": 410112, "vertices": 377020, "area": 0.611, "bbox_min": [-0.31, 0.12, -0.2],
               "bbox_max": [0.31, 0.5, 0.17], "centroid": [0.0, 0.33, -0.02]},
     "frame": {"pivot": [0.0, 0.29, 0.01], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "seam_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "open_surface", "open_boundary_loops": 4, "watertight": false,
                  "occluded_geometry_missing": false, "components": 2, "source": "lifted", "tags": [],
                  "floaters": []},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.02, 0.41, 0.97, 0.99], "charts_cross_boundary": true},
     "seams": ["seam_0"], "annotations": {}, "confidence": 0.88, "expected": false,
     "provenance": {"sources": [{"method": "lifted", "provider": "baseline", "model": "sam2_1_hiera_large",
                                 "score": 0.88}], "votes": 51234},
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "eligible": true, "needs": ["closure"],
                    "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}},
    {"id": "eyebrow.left", "category": "eyebrow.left", "group": "eyebrows", "label": "Left eyebrow",
     "kind": "physical_deferred", "side": "left", "image_side": "right", "parent": "body",
     "attach_to": "body.head.face", "host": "body", "symmetric_with": "eyebrow.right", "node_name": null,
     "mesh_index": null, "region": {"attribute": "_FEATURE_ID_0", "value": 22, "face_count": 1890},
     "files": {"glb": null, "mask": "masks/22.png", "thumbnail": "thumbs/eyebrow.left.webp"},
     "stats": {"faces": 1890, "vertices": 1012, "area": 0.0012, "bbox_min": [0.03, 0.27, 0.13],
               "bbox_max": [0.09, 0.29, 0.16], "centroid": [0.06, 0.28, 0.15]},
     "frame": {"pivot": [0.06, 0.28, 0.15], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "area_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "texture_only", "open_boundary_loops": 0, "watertight": false,
                  "occluded_geometry_missing": false, "components": 1, "source": "lifted", "tags": [],
                  "floaters": []},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.61, 0.70, 0.64, 0.71], "charts_cross_boundary": false},
     "seams": [], "annotations": {}, "confidence": 0.61, "expected": false,
     "provenance": {"sources": [{"method": "lifted", "provider": "baseline", "model": "sam2_1_hiera_large",
                                 "score": 0.61}], "votes": 2210},
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "eligible": true, "needs": ["geometry"],
                    "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}},
    {"id": "body.head.face", "category": "body.head.face", "group": "face", "label": "Face", "kind": "region",
     "side": "center", "image_side": "center", "parent": "body", "attach_to": null, "host": "body",
     "symmetric_with": null, "node_name": null, "mesh_index": null,
     "region": {"attribute": "_FEATURE_ID_0", "value": 3, "face_count": 51230},
     "files": {"glb": null, "mask": "masks/3.png", "thumbnail": null},
     "stats": {"faces": 51230, "vertices": 26101, "area": 0.041, "bbox_min": [-0.11, 0.17, 0.05],
               "bbox_max": [0.11, 0.36, 0.19], "centroid": [0.0, 0.26, 0.15]},
     "frame": {"pivot": [0.0, 0.26, 0.15], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "area_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "surface", "open_boundary_loops": 0, "watertight": false,
                  "occluded_geometry_missing": false, "components": 1, "source": "lifted", "tags": [],
                  "floaters": []},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.55, 0.62, 0.71, 0.80], "charts_cross_boundary": false},
     "seams": [], "annotations": {}, "confidence": 0.9, "expected": false,
     "provenance": {"sources": [{"method": "lifted", "provider": "baseline", "model": "sam2_1_hiera_large",
                                 "score": 0.9}], "votes": 40112},
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "eligible": true, "needs": [],
                    "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}}
  ],
  "seams": [{"id": "seam_0", "a": "hair", "b": "body", "host_opening": true, "boundary_edges": 3122, "length": 1.84,
             "loops": [{"start": 0, "count": 1561, "closed": true, "length": 0.93},
                       {"start": 1561, "count": 1561, "closed": true, "length": 0.91}]}],
  "relations": [
    {"id": "rel_0", "type": "attached_to", "a": "hair", "b": "body.head.face", "via": "seam_0",
     "metrics": {"contact": "seam", "shared_length": 1.84, "distance": 0.0}, "source": "geometry", "confidence": 0.88},
    {"id": "rel_1", "type": "region_of", "a": "eyebrow.left", "b": "body", "via": null, "metrics": {"faces": 1890},
     "source": "geometry", "confidence": 1.0},
    {"id": "rel_2", "type": "symmetric_with", "a": "eyebrow.left", "b": "eyebrow.right", "via": null,
     "metrics": {"plane": "x=0", "centroid_mirror_error": 0.004, "area_ratio": 0.97}, "source": "geometry",
     "confidence": 0.92},
    {"id": "rel_3", "type": "expected_missing", "a": "asset", "b": "object.held", "via": null, "metrics": {},
     "source": "prompt", "confidence": 1.0}],
  "buffers": [
    {"id": "face_labels", "file": "buffers/face_labels.u16", "dtype": "uint16", "components": 1, "count": 1895691,
     "byte_length": 3791382, "sha256": "a1c9…", "indexes": "base_faces", "description": "taxonomy value per base face"},
    {"id": "face_owner", "file": "buffers/face_owner.u16", "dtype": "uint16", "components": 1, "count": 1895691,
     "byte_length": 3791382, "sha256": "77e0…", "indexes": "base_faces", "description": "mesh index per base face"},
    {"id": "vmap_hair", "file": "buffers/vmap_hair.u32", "dtype": "uint32", "components": 1, "count": 377020,
     "byte_length": 1508080, "sha256": "19be…", "indexes": "node_vertices:hair",
     "description": "base vertex id per vertex of part:hair"}],
  "artifacts": {"assembled_glb": {"file": "model_parts.glb", "sha256": "9b7a…", "bytes": 84522108, "nodes": 4},
                "part_glbs": {},
                "segmentation": {"labels": "segmentation/labels.png", "overlay": "segmentation/overlay.png",
                                 "frame": "segmentation/frame.json", "source": "segmentation/source.png"},
                "masks_dir": "masks", "previews": ["previews/front.png", "previews/back.png"], "log": null},
  "expectations": {"source": "prompt", "expected_classes": ["object.held"], "missing": ["object.held"]},
  "provenance": {"steps": [{"id": "segmentation_2d", "models": [{"id": "grounding_dino_base", "revision": "12bdfa31…",
                                                                  "license_id": "apache-2.0"}], "duration_s": 2.4},
                           {"id": "label_lift", "models": [{"id": "raster_lifter", "revision": null,
                                                            "license_id": "nvidia-source-code-nc"}], "duration_s": 6.8}],
                 "licences": ["mit", "dinov3-license", "nvidia-source-code-nc", "apache-2.0"]},
  "refinement": {"enabled": false, "policy": "locked_until_approved", "consumers": []},
  "reassembly": {"method": "vmap_gather", "verified": true, "positions_exact": true, "uvs_exact": true,
                 "normals_exact": true, "faces_exact": true},
  "summary": {"part_count": 9, "physical_count": 5, "deferred_count": 4, "region_count": 12,
              "unlabelled_face_ratio": 0.04, "partner_filled_ratio": 0.43, "cut_partner_consistency": 0.97,
              "camera_fit_iou": 0.91},
  "warnings": [{"code": "charts_cross_boundary", "message": "UV charts of cut parts may cross part borders.",
                "part_id": null},
               {"code": "floater_candidates", "message": "1 small unlabelled shell stays on the base.",
                "part_id": "body"}]
}
"""


def example() -> dict[str, Any]:
    return json.loads(EXAMPLE_MANIFEST_JSON)


def validation_types(exc: ValidationError) -> list[str]:
    return [error["type"] for error in exc.errors()]


# ==================================================================================================
# Taxonomy v1
# ==================================================================================================

FROZEN_VALUES: dict[int, str] = {
    0: "unknown",
    1: "body",
    2: "body.head",
    3: "body.head.face",
    4: "body.head.ear.left",
    5: "body.head.ear.right",
    6: "body.head.nose",
    7: "body.head.mouth",
    8: "body.neck",
    9: "body.torso",
    10: "body.arm.left",
    11: "body.arm.right",
    12: "body.hand.left",
    13: "body.hand.right",
    14: "body.leg.left",
    15: "body.leg.right",
    16: "body.foot.left",
    17: "body.foot.right",
    18: "body.skin_other",
    20: "eye.left",
    21: "eye.right",
    22: "eyebrow.left",
    23: "eyebrow.right",
    24: "eyelash.left",
    25: "eyelash.right",
    26: "body.head.eyelid.left",
    27: "body.head.eyelid.right",
    30: "hair",
    31: "hair.facial",
    40: "clothing.top",
    41: "clothing.outer",
    42: "clothing.bottom",
    43: "clothing.dress",
    44: "clothing.legwear",
    45: "clothing.handwear",
    46: "clothing.footwear",
    47: "clothing.neckwear",
    50: "accessory.headwear",
    51: "accessory.eyewear",
    52: "accessory.earwear",
    53: "accessory.jewelry",
    54: "accessory.belt",
    55: "accessory.bag",
    56: "appendage.tail",
    57: "appendage.wings",
    58: "appendage.animal_ears",
    60: "object.held",
    61: "object.detached",
}
"""§2.3: values are written to face_labels, _FEATURE_ID_0 and labels.png and must never change."""


def test_taxonomy_values_are_frozen_and_unique() -> None:
    assert taxonomy.TAXONOMY_VERSION == 1
    values = [cls.value for cls in taxonomy.CLASSES]
    ids = [cls.id for cls in taxonomy.CLASSES]
    assert len(values) == len(set(values))
    assert len(ids) == len(set(ids))
    assert values == sorted(values)
    assert {cls.value: cls.id for cls in taxonomy.CLASSES} == FROZEN_VALUES
    assert dict(taxonomy.BY_VALUE) == {value: taxonomy.TAXONOMY[class_id] for value, class_id in FROZEN_VALUES.items()}
    assert all(0 <= value < FACE_PART_NONE for value in values)


def test_class_ids_are_valid_part_ids_and_references_resolve() -> None:
    for cls in taxonomy.CLASSES:
        assert taxonomy.is_valid_part_id(cls.id), cls.id
        for candidate in cls.attach_to:
            assert candidate in taxonomy.TAXONOMY, (cls.id, candidate)
            assert candidate != cls.id
        assert cls.group in taxonomy.GROUPS_BY_ID
        assert cls.label and cls.label[0].isupper()


def test_symmetric_pairs_are_mutual() -> None:
    sided = [cls for cls in taxonomy.CLASSES if cls.sided]
    assert sided
    for cls in taxonomy.CLASSES:
        if cls.sided:
            partner = taxonomy.TAXONOMY[cls.symmetric_with]  # type: ignore[index]
            assert partner.symmetric_with == cls.id
            assert partner.sided and partner.group == cls.group and partner.default_kind == cls.default_kind
            assert {cls.side, partner.side} == {"left", "right"}
            assert cls.id.endswith(f".{cls.side}")
            assert abs(cls.value - partner.value) == 1
        else:
            assert cls.symmetric_with is None
            assert cls.side is None
    # Side-specific attachment is resolved per class.
    assert taxonomy.TAXONOMY["body.hand.left"].attach_to == ("body.arm.left",)
    assert taxonomy.TAXONOMY["body.foot.right"].attach_to == ("body.leg.right",)
    assert taxonomy.TAXONOMY["eye.right"].attach_to == ("body.head.eyelid.right", "body.head.face")
    assert taxonomy.TAXONOMY["eyelash.left"].attach_to == ("body.head.eyelid.left", "eye.left")


def test_groups_cover_the_separable_groups_plus_face_and_body() -> None:
    assert taxonomy.SEPARABLE_GROUP_IDS == (
        "eyes",
        "eyebrows",
        "eyelashes",
        "hair",
        "clothes",
        "accessories",
        "detachable",
    )
    assert taxonomy.DEFAULT_SEPARATE == taxonomy.SEPARABLE_GROUP_IDS
    assert [group.id for group in taxonomy.GROUPS] == [
        "body",
        "face",
        "eyes",
        "eyebrows",
        "eyelashes",
        "hair",
        "clothes",
        "accessories",
        "detachable",
        "other",
    ]
    used = {cls.group for cls in taxonomy.CLASSES}
    assert used == set(taxonomy.GROUPS_BY_ID)
    assert {"face", "body"} <= used and set(taxonomy.SEPARABLE_GROUP_IDS) <= used
    for group in taxonomy.GROUPS:
        assert group.separable == (group.id in taxonomy.SEPARABLE_GROUP_IDS)
        if not group.separable:
            assert group.kind_selected == "region"
    assert taxonomy.GROUPS_BY_ID["eyebrows"].kind_selected == "physical_deferred"
    assert taxonomy.GROUPS_BY_ID["eyes"].note and "eyeball" in taxonomy.GROUPS_BY_ID["eyes"].note
    assert [cls.id for cls in taxonomy.classes_for_groups(["eyes"])] == ["eye.left", "eye.right"]
    with pytest.raises(ValueError, match="nose_group"):
        taxonomy.classes_for_groups(["eyes", "nose_group"])


def test_default_kinds_instance_split_and_z_priority() -> None:
    by_id = taxonomy.TAXONOMY
    assert by_id["body"].default_kind == "base"
    assert by_id["unknown"].default_kind == "region" and by_id["unknown"].group == "other"
    for class_id in ("eye.left", "hair", "hair.facial", "clothing.dress", "accessory.bag", "object.held"):
        assert by_id[class_id].default_kind == "physical", class_id
    for class_id in ("eyebrow.left", "eyebrow.right", "eyelash.left", "eyelash.right"):
        assert by_id[class_id].default_kind == "physical_deferred", class_id
    for cls in taxonomy.CLASSES:
        if cls.group in ("body", "face") and cls.id != "body":
            assert cls.default_kind == "region", cls.id
        expected_split = cls.id.split(".")[0] in ("accessory", "appendage", "object") or cls.id in (
            "clothing.handwear",
            "clothing.footwear",
        )
        assert cls.instance_split == expected_split, cls.id
    priorities = {cls.id: cls.z_priority for cls in taxonomy.CLASSES}
    assert (
        priorities["object.held"]
        > priorities["accessory.headwear"]
        > priorities["accessory.eyewear"]
        > priorities["hair"]
        > priorities["eyelash.left"]
        > priorities["eyebrow.left"]
        > priorities["eye.left"]
        > priorities["clothing.outer"]
        > priorities["clothing.top"]
        > priorities["appendage.tail"]
        > priorities["body.torso"]
    )
    assert taxonomy.TAXONOMY["object.detached"].attach_to == ()


def test_aliases_are_normalised_and_owned_by_one_class_family() -> None:
    owner: dict[str, set[str]] = {}
    for cls in taxonomy.CLASSES:
        for alias in cls.aliases:
            assert alias == alias.lower().strip() and "  " not in alias, alias
            family = cls.id.rsplit(".", 1)[0] if cls.sided else cls.id
            owner.setdefault(alias, set()).add(family)
    shared = {alias: families for alias, families in owner.items() if len(families) > 1}
    assert shared == {}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("katana", ["object.held"]),
        ("twin tails", ["hair"]),
        ("cat ears", ["appendage.animal_ears"]),
        ("A girl with TWIN-TAILS and cat ears", ["hair", "appendage.animal_ears"]),
        ("a knight with a red cape and a sword", ["object.held", "clothing.outer"]),
        ("brown boots and a T-shirt", ["clothing.top", "clothing.footwear"]),
        ("bright eyes", ["eye.left", "eye.right"]),
        ("capes and caps", ["accessory.headwear", "clothing.outer"]),
        ("an elbow, a laptop, an escape", []),
        ("", []),
    ],
)
def test_match_terms(text: str, expected: list[str]) -> None:
    assert taxonomy.match_terms(text) == expected


def test_classes_for_term_falls_back_to_held_object() -> None:
    assert taxonomy.classes_for_term("glasses") == ["accessory.eyewear"]
    assert taxonomy.classes_for_term("unicorn") == ["object.held"]


@pytest.mark.parametrize("reserved", ["con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"])
def test_windows_reserved_first_segment_is_rejected(reserved: str) -> None:
    assert not taxonomy.is_valid_part_id(reserved)
    assert not taxonomy.is_valid_part_id(f"{reserved}.left")
    with pytest.raises(ValueError, match="Windows reserved"):
        taxonomy.validate_part_id(f"{reserved}.left")
    # Only the exact first segment is reserved.
    assert taxonomy.is_valid_part_id(f"{reserved}_2")
    assert taxonomy.is_valid_part_id(f"body.{reserved}")


@pytest.mark.parametrize(
    "bad",
    ["", "Hair", "1hair", "hair.", ".hair", "hair..left", "hair-left", "hair/left", "a.b.c.d.e.f.g", "x" * 81],
)
def test_malformed_part_ids_are_rejected(bad: str) -> None:
    assert not taxonomy.is_valid_part_id(bad)


def test_instance_and_component_ids() -> None:
    assert taxonomy.is_valid_part_id("a.b.c.d.e.f")
    assert taxonomy.is_valid_part_id("x" * 80)
    assert taxonomy.instance_part_id("accessory.jewelry", 1) == "accessory.jewelry"
    assert taxonomy.instance_part_id("accessory.jewelry", 2) == "accessory.jewelry_2"
    assert taxonomy.instance_part_id("clothing.footwear", 3) == "clothing.footwear_3"
    assert taxonomy.component_part_id(1) == "component_001"
    assert taxonomy.component_part_id(12) == "component_012"
    with pytest.raises(ValueError):
        taxonomy.instance_part_id("hair", 0)
    with pytest.raises(ValueError):
        taxonomy.component_part_id(0)


def test_to_out_payload() -> None:
    out = taxonomy.to_out()
    assert out["version"] == 1
    assert out["groups"] == [group.id for group in taxonomy.GROUPS]
    hair = next(entry for entry in out["classes"] if entry["id"] == "hair")
    assert hair == {
        "value": 30,
        "id": "hair",
        "group": "hair",
        "label": "Hair",
        "default_kind": "physical",
        "attach_to": ["body.head"],
        "symmetric_with": None,
        "sided": False,
    }
    json.dumps(out)


# ==================================================================================================
# Manifest img3d.parts/1
# ==================================================================================================


def test_buffer_specs_match_the_design_table() -> None:
    assert [(spec.id, spec.file, spec.dtype) for spec in BUFFER_SPECS] == [
        ("face_labels", "buffers/face_labels.u16", "uint16"),
        ("face_owner", "buffers/face_owner.u16", "uint16"),
        ("face_confidence", "buffers/face_confidence.u8", "uint8"),
        ("face_component", "buffers/face_component.u32", "uint32"),
        ("face_part", "buffers/face_part.u16", "uint16"),
        ("face_flags", "buffers/face_flags.u8", "uint8"),
        ("weld_map", "buffers/weld_map.u32", "uint32"),
        ("seam_loops", "buffers/seam_loops.u32", "uint32"),
    ]
    vmap = vmap_buffer_spec("eye.left")
    assert (vmap.id, vmap.file, vmap.dtype, vmap.indexes) == (
        "vmap_eye.left",
        "buffers/vmap_eye.left.u32",
        "uint32",
        "node_vertices:eye.left",
    )


def test_example_manifest_round_trips() -> None:
    data = example()
    manifest = PartsManifest.model_validate(data)
    assert manifest.schema_id == PARTS_SCHEMA
    assert manifest.region_attribute == REGION_ATTRIBUTE
    dumped = manifest.to_json_dict()
    assert dumped == data
    assert list(dumped)[0] == "schema"
    # JSON text round trip as well.
    assert PartsManifest.model_validate_json(manifest.model_dump_json()).to_json_dict() == data
    assert manifest.part_index("eyebrow.left") == 2
    assert manifest.part("hair") is not None and manifest.part("hair").owns_mesh  # type: ignore[union-attr]
    assert manifest.buffer("vmap_hair").count == 377020  # type: ignore[union-attr]
    assert manifest.mesh("eye.left").index == 2  # type: ignore[union-attr]
    assert {r.id for r in manifest.relations_of("eyebrow.left")} == {"rel_1", "rel_2"}
    assert manifest.reassembly.all_exact
    for part in manifest.parts:
        assert part.refinement.locked and part.refinement.status == "untouched"
        assert "floaters" in part.geometry.model_dump()


def test_unknown_fields_are_ignored_everywhere() -> None:
    data = example()
    data["future_top_level"] = {"x": 1}
    data["parts"][0]["future_part_field"] = [1, 2]
    data["parts"][1]["geometry"]["future_geometry_field"] = True
    data["buffers"][0]["future_buffer_field"] = "y"
    data["frames"]["asset"]["future"] = 0
    manifest = PartsManifest.model_validate(data)
    assert manifest.to_json_dict() == example()


@pytest.mark.parametrize("schema", ["img3d.parts/2", "img3d.parts/0", "img3d.parts", "other/1", "img3d.parts/1.1"])
def test_wrong_schema_is_rejected(schema: str) -> None:
    data = example()
    data["schema"] = schema
    with pytest.raises(ValidationError, match="schema"):
        PartsManifest.model_validate(data)


@pytest.mark.parametrize(
    "path",
    [
        "/abs/masks/30.png",
        "C:/masks/30.png",
        "C:masks.png",
        "..",
        "../secret.png",
        "masks/../../x.png",
        "masks\\30.png",
        "./masks/30.png",
        "masks//30.png",
        "masks/30.png\n",
    ],
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    assert safe_relative_path_problem(path) is not None
    for mutate in (
        lambda d: d["parts"][1]["files"].__setitem__("mask", path),
        lambda d: d["buffers"][0].__setitem__("file", path),
        lambda d: d["artifacts"]["assembled_glb"].__setitem__("file", path),
        lambda d: d["artifacts"].__setitem__("previews", [path]),
        lambda d: d["frames"]["conditioning"].__setitem__("file", path),
    ):
        data = example()
        mutate(data)
        with pytest.raises(ValidationError, match="Unsafe manifest path"):
            PartsManifest.model_validate(data)


def test_model_file_is_the_literal_job_model() -> None:
    data = example()
    data["source"]["model_file"] = "../../../model.glb"
    with pytest.raises(ValidationError):
        PartsManifest.model_validate(data)


@pytest.mark.parametrize(
    ("collection", "key"),
    [("parts", "id"), ("buffers", "id"), ("seams", None), ("relations", "id"), ("meshes", "index")],
)
def test_duplicate_ids_are_rejected(collection: str, key: str | None) -> None:
    data = example()
    items = data[collection]
    if collection == "seams":
        items.append(copy.deepcopy(items[0]))
    else:
        duplicate = copy.deepcopy(items[3] if collection == "parts" else items[1])
        duplicate[key] = items[0][key]  # type: ignore[index]
        if collection == "meshes":
            duplicate["part_id"] = "accessory.bag"
            duplicate["node_name"] = "part:accessory.bag"
        if collection == "buffers":
            duplicate["file"] = "buffers/other.u16"
        items.append(duplicate)
    with pytest.raises(ValidationError, match="duplicate"):
        PartsManifest.model_validate(data)


def test_invalid_part_ids_and_node_names_are_rejected() -> None:
    for mutate in (
        lambda d: d["parts"][0].__setitem__("id", "con"),
        lambda d: d["parts"][1].__setitem__("node_name", "part:body"),
        lambda d: d["parts"][1].__setitem__("parent", "Body"),
        lambda d: d["meshes"][1].__setitem__("node_name", "hair"),
        lambda d: d["relations"][0].__setitem__("a", "asset"),
        lambda d: d["relations"][3].__setitem__("a", "body"),
        lambda d: d["relations"][0].__setitem__("id", "relation_0"),
        lambda d: d["seams"][0].__setitem__("id", "seam-0"),
        lambda d: d["artifacts"].__setitem__("part_glbs", {"../x": "parts/x.glb"}),
        lambda d: d.__setitem__("analysis_id", "pa_123"),
        lambda d: d.__setitem__("job_id", "job_123"),
        lambda d: d.__setitem__("created_at", "2026-10-20T10:04:31"),
    ):
        data = example()
        mutate(data)
        with pytest.raises(ValidationError):
            PartsManifest.model_validate(data)


def test_enumerations_are_strict() -> None:
    for mutate in (
        lambda d: d["parts"][0].__setitem__("kind", "physical_maybe"),
        lambda d: d["parts"][0]["refinement"].__setitem__("status", "tuned"),
        lambda d: d["parts"][0]["geometry"].__setitem__("tags", ["inner_sheet"]),
        lambda d: d["relations"][0].__setitem__("type", "glued_to"),
        lambda d: d["strategy"]["segmentation"].__setitem__("source", "magic"),
        lambda d: d["strategy"].__setitem__("separate", ["eyes", "nose"]),
        lambda d: d.__setitem__("region_attribute", "_FEATURE_ID_1"),
        lambda d: d["buffers"][0].__setitem__("dtype", "float64"),
        lambda d: d["buffers"][0].__setitem__("indexes", "faces"),
        lambda d: d["parts"][0]["lineage"].__setitem__(
            "derived_from", [{"part_id": "body", "analysis_id": "pa_1", "face_iou": 0.9}] * 4
        ),
    ):
        data = example()
        mutate(data)
        with pytest.raises(ValidationError):
            PartsManifest.model_validate(data)


def test_expected_missing_relation_may_start_at_the_asset() -> None:
    data = example()
    relation = next(r for r in data["relations"] if r["type"] == "expected_missing")
    assert relation["a"] == "asset"
    PartsManifest.model_validate(data)


def test_buffer_size_must_match_count_and_dtype() -> None:
    data = example()
    data["buffers"][0]["byte_length"] += 1
    with pytest.raises(ValidationError, match="byte_length"):
        PartsManifest.model_validate(data)


def test_minimal_manifest_defaults_are_locked_and_honest() -> None:
    data = example()
    for key in ("frames", "views", "label_table", "seams", "relations", "expectations", "provenance", "refinement"):
        data.pop(key)
    data["parts"] = [data["parts"][0]]
    for key in ("files", "placement", "refinement", "lineage", "annotations", "seams"):
        data["parts"][0].pop(key)
    data["created_at"] = datetime(2026, 10, 20, 10, 4, 31, tzinfo=UTC)
    manifest = PartsManifest.model_validate(data)
    assert manifest.created_at == "2026-10-20T10:04:31.000Z"
    assert manifest.frames.asset.up == "+Y" and manifest.frames.conditioning is None
    assert manifest.frames.trellis.to_asset[4:8] == [0.0, 0.0, 1.0, 0.0]
    assert manifest.refinement.enabled is False and manifest.refinement.policy == "locked_until_approved"
    body = manifest.parts[0]
    assert body.refinement.locked and body.refinement.lock_reason == "part_refinement_disabled"
    assert body.placement.method == "identity" and len(body.placement.transform) == 16
    assert body.files.glb is None and body.lineage.derived_from == []
    assert manifest.views == []


# ==================================================================================================
# Wire schemas (requests: extra="forbid", partial nested objects; responses: extra="ignore")
# ==================================================================================================


def test_image_generation_request_hygiene() -> None:
    from schemas.image_generation import ImageGenerationRequest, WorkflowImageGenerationRequest

    request = ImageGenerationRequest.model_validate({"prompt": "  a knight\twith a sword\n "})
    assert request.prompt == "a knight\twith a sword"
    assert (request.preset, request.style, request.part_hint, request.num_images) == ("character", None, True, 1)
    assert (request.seed, request.randomize_seed) == (0, True)
    for body, error_type in (
        ({"prompt": "   "}, "string_too_short"),
        ({"prompt": ""}, "string_too_short"),
        ({"prompt": "bad\x07bell"}, "invalid_characters"),
        ({"prompt": "x", "negative_prompt": "a\x00"}, "invalid_characters"),
        ({"prompt": "x" * 8001}, "string_too_long"),
        ({"prompt": "x", "num_images": 5}, "less_than_equal"),
        ({"prompt": "x", "seed": 2_147_483_648}, "less_than_equal"),
        ({"prompt": "x", "preset": "portrait"}, "literal_error"),
        ({"prompt": "x", "unknown": 1}, "extra_forbidden"),
    ):
        with pytest.raises(ValidationError) as excinfo:
            ImageGenerationRequest.model_validate(body)
        assert error_type in validation_types(excinfo.value), body
    with pytest.raises(ValidationError) as excinfo:
        WorkflowImageGenerationRequest.model_validate({"prompt": "x", "num_images": 2})
    assert validation_types(excinfo.value) == ["less_than_equal"]


def test_segmentation_options_are_partial_and_validated() -> None:
    from schemas.segmentation import SegmentationOptions, SegmentationRequest

    assert SegmentationOptions.model_validate({}).model_dump() == {
        "profile": "auto",
        "subject": "auto",
        "classes": None,
        "extra_terms": [],
        "use_prompt_terms": True,
        "head_pass": True,
        "min_confidence": 0.35,
    }
    options = SegmentationOptions.model_validate({"classes": ["hair", "hair"], "extra_terms": [" katana ", "katana"]})
    assert options.classes == ["hair"] and options.extra_terms == ["katana"]
    for body, error_type in (
        ({"classes": ["hair", "tentacle"]}, "unknown_class"),
        ({"extra_terms": ["x"] * 17}, "too_long"),
        ({"extra_terms": ["x" * 41]}, "string_too_long"),
        ({"min_confidence": 0.99}, "less_than_equal"),
        ({"profile": "manga"}, "literal_error"),
        ({"bogus": True}, "extra_forbidden"),
    ):
        with pytest.raises(ValidationError) as excinfo:
            SegmentationOptions.model_validate(body)
        assert error_type in validation_types(excinfo.value), body
    request = SegmentationRequest.model_validate({"source": {"kind": "job", "job_id": "job_0123456789ab"}})
    assert request.source.kind == "job" and request.options.head_pass is True
    with pytest.raises(ValidationError):
        SegmentationRequest.model_validate({"source": {"kind": "job", "image_id": "img_0123456789ab"}})


def test_part_analysis_options_defaults_and_export() -> None:
    from schemas.parts import PartAnalysisOptions, PartExtractionOptions

    options = PartAnalysisOptions.model_validate({})
    assert options.separate == list(taxonomy.DEFAULT_SEPARATE)
    assert (options.views, options.min_part_faces, options.max_parts, options.split_shells) == (8, None, 64, True)
    assert options.export.model_dump() == {"per_part_glbs": False, "previews": True}
    assert PartAnalysisOptions.model_validate({"export": {}}).export.previews is True
    for body, error_type in (
        ({"export": {"assembled_glb": False}}, "extra_forbidden"),
        ({"separate": ["eyes", "nose"]}, "literal_error"),
        ({"views": 25}, "less_than_equal"),
        ({"max_parts": 0}, "greater_than_equal"),
        ({"min_part_faces": 100_001}, "less_than_equal"),
        ({"strategy": "magic"}, "literal_error"),
    ):
        with pytest.raises(ValidationError) as excinfo:
            PartAnalysisOptions.model_validate(body)
        assert error_type in validation_types(excinfo.value), body
    assert "segmentation_id" not in PartExtractionOptions.model_fields
    assert "export" not in PartExtractionOptions.model_fields


def _workflow_errors(body: dict[str, Any]) -> list[tuple[tuple[Any, ...], str]]:
    from schemas.workflows import WorkflowRequest

    try:
        WorkflowRequest.model_validate(body)
    except ValidationError as exc:
        return [(tuple(error["loc"]), error["type"]) for error in exc.errors()]
    return []


IMAGE_SOURCE = {"kind": "image", "reference_set_id": "rs_0123456789ab"}
JOB_SOURCE = {"kind": "job", "job_id": "job_0123456789ab"}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"source": IMAGE_SOURCE, "reconstruction": {}}, []),
        ({"source": IMAGE_SOURCE, "reconstruction": {}, "parts": {}, "segmentation": {}}, []),
        ({"source": JOB_SOURCE, "parts": {}}, []),
        ({"graph": {"nodes": [], "edges": []}, "layout": {"zoom": 1}}, []),
        ({"source": IMAGE_SOURCE}, [(("reconstruction",), "missing")]),
        ({"source": JOB_SOURCE, "reconstruction": {}}, [(("reconstruction",), "conflicting_fields")]),
        (
            {"source": IMAGE_SOURCE, "reconstruction": {}, "segmentation": {}},
            [(("segmentation",), "conflicting_fields")],
        ),
        (
            {"source": IMAGE_SOURCE, "reconstruction": {}, "parts": {"segmentation_id": "sg_0123456789ab"}},
            [(("parts", "segmentation_id"), "conflicting_fields")],
        ),
        ({"graph": {}, "parts": None}, [((), "conflicting_fields")]),
        ({}, [((), "missing")]),
        ({"source": IMAGE_SOURCE, "reconstruction": {}, "export": {}}, [(("export",), "extra_forbidden")]),
        (
            {"source": {"kind": "prompt", "generation": {"prompt": "x", "num_images": 2}}, "reconstruction": {}},
            [(("source", "prompt", "generation", "num_images"), "less_than_equal")],
        ),
        (
            {"graph": {"nodes": [{"id": "Bad", "type": "export"}]}},
            [(("graph", "nodes", 0, "id"), "string_pattern_mismatch")],
        ),
    ],
)
def test_workflow_request_rules(body: dict[str, Any], expected: list[tuple[tuple[Any, ...], str]]) -> None:
    assert _workflow_errors(body) == expected


def test_workflow_fields_revalidate_plain_option_instances() -> None:
    """Server code (compiler, services) may pass the plain request models; the workflow rules still apply."""
    from schemas.image_generation import ImageGenerationRequest
    from schemas.parts import PartAnalysisOptions
    from schemas.workflows import PromptWorkflowSource, WorkflowRequest

    request = WorkflowRequest(
        source=PromptWorkflowSource(kind="prompt", generation=ImageGenerationRequest(prompt="a knight")),
        reconstruction={},
        parts=PartAnalysisOptions(views=0),
    )
    assert request.parts is not None and request.parts.views == 0
    assert request.source.generation.num_images == 1  # type: ignore[union-attr]
    with pytest.raises(ValidationError) as excinfo:
        PromptWorkflowSource(kind="prompt", generation=ImageGenerationRequest(prompt="x", num_images=2))
    assert [(e["loc"], e["type"]) for e in excinfo.value.errors()] == [(("generation", "num_images"), "less_than_equal")]
    assert _workflow_errors(
        {"source": IMAGE_SOURCE, "reconstruction": {}, "parts": PartAnalysisOptions(segmentation_id="sg_0123456789ab")}
    ) == [(("parts", "segmentation_id"), "conflicting_fields")]


def test_workflow_graph_aliases_round_trip() -> None:
    from schemas.workflows import WorkflowGraph, WorkflowRequest

    graph = {
        "schema": "img3d.workflow_graph",
        "version": 1,
        "nodes": [{"id": "n_input", "type": "input_image", "params": {"reference_set_id": "rs_0123456789ab"}}],
        "edges": [{"from": {"node": "n_input", "port": "images"}, "to": {"node": "n_recon", "port": "images"}}],
    }
    assert WorkflowGraph.model_validate(graph).model_dump(mode="json") == graph
    request = WorkflowRequest.model_validate({"source": IMAGE_SOURCE, "reconstruction": {"settings": {}}})
    simple = request.simple_form()
    assert "graph" not in simple and "layout" not in simple
    assert simple["source"]["kind"] == "image" and simple["reconstruction"]["settings"]["polygon_target"] == 500_000


def test_response_models_ignore_unknown_fields() -> None:
    from schemas.features import FeaturesOut
    from schemas.model_registry import ModelOut
    from schemas.parts import PartAnalysisOut
    from schemas.workflows import CatalogOut, WorkflowOut

    for model in (FeaturesOut, ModelOut, PartAnalysisOut, WorkflowOut, CatalogOut):
        assert model.model_config.get("extra") == "ignore", model.__name__


# ==================================================================================================
# Workflow catalog and store stub
# ==================================================================================================


def test_catalog_nodes_ports_and_locked_refine_nodes() -> None:
    from services import workflow_catalog as catalog

    types = [node.type for node in catalog.NODE_TYPES]
    assert types == [
        "input_image",
        "prompt",
        "job_result",
        "segmentation",
        "image_to_3d",
        "part_extraction",
        "export",
        "part_refine",
        "mesh_tune",
        "eye_correction",
        "rig_correction",
        "segmentation_3d",
        "part_completion",
        "part_generation",
    ]
    assert [port.id for port in catalog.PORT_TYPES] == ["image_set", "segmentation", "mesh", "part_set"]
    for node in catalog.NODE_TYPES:
        assert node.max_instances == 1
        assert isinstance(node.params_schema().get("properties"), dict), node.type
    for node_type in catalog.REFINE_NODE_TYPES:
        node = catalog.NODE_TYPES_BY_TYPE[node_type]
        assert node.static_state == catalog.NodeState(False, True, "not_approved", "Paused until approved")
        assert node.hidden_by_default and node.compiles_to is None and node.params_model is None
    for node_type in catalog.UNSUPPORTED_NODE_TYPES:
        node = catalog.NODE_TYPES_BY_TYPE[node_type]
        assert node.static_state is not None and node.static_state.code == "unsupported"
        assert node.static_state.reason and node.hidden_by_default
    prompt_props = set(catalog.NODE_TYPES_BY_TYPE["prompt"].params_schema()["properties"])
    from schemas.image_generation import ImageGenerationRequest
    from schemas.jobs import GenerationSettings

    assert prompt_props == set(ImageGenerationRequest.model_fields)
    assert set(catalog.NODE_TYPES_BY_TYPE["image_to_3d"].params_schema()["properties"]) == set(
        GenerationSettings.model_fields
    )
    extraction = set(catalog.NODE_TYPES_BY_TYPE["part_extraction"].params_schema()["properties"])
    assert "segmentation_id" not in extraction and "export" not in extraction
    assert catalog.NODE_TYPES_BY_TYPE["segmentation"].defaults()["min_confidence"] == 0.35
    assert catalog.NODE_TYPES_BY_TYPE["export"].compiles_to == "parts.export"


def test_legacy_job_node_map_is_exact() -> None:
    from models.enums import JobStage
    from services.workflow_catalog import LEGACY_JOB_NODE_MAP

    assert dict(LEGACY_JOB_NODE_MAP) == {
        "queued": "image_to_3d",
        "waiting_for_model": "image_to_3d",
        "preprocessing": "input_image",
        "conditioning": "image_to_3d",
        "sparse_structure": "image_to_3d",
        "shape": "image_to_3d",
        "texture": "image_to_3d",
        "decoding": "image_to_3d",
        "exporting": "export",
        "finalizing": "export",
        "done": "export",
    }
    assert set(LEGACY_JOB_NODE_MAP) == {stage.value for stage in JobStage}


def test_catalog_states_templates_and_etag() -> None:
    from schemas.features import CapabilityOut
    from services import workflow_catalog as catalog

    ok = CapabilityOut(available=True, code="ok")
    capabilities = {"image_to_3d": ok, "prompt_to_3d": ok, "segmentation": ok, "part_extraction": ok}
    states = catalog.resolve_node_states(capabilities)
    assert all(states[t].available for t in ("input_image", "prompt", "image_to_3d", "part_extraction", "export"))
    assert not states["mesh_tune"].available and states["mesh_tune"].locked
    built = catalog.build_catalog(states)
    payload = built.model_dump(mode="json")
    assert payload["schema"] == "img3d.workflow_catalog/1"
    assert re.fullmatch(r'"sha256-[0-9a-f]{64}"', built.etag)
    assert [t["id"] for t in payload["templates"]] == [
        "image_to_3d",
        "image_to_3d_parts",
        "prompt_to_3d",
        "prompt_to_3d_parts",
        "rebuild_parts",
    ]
    assert payload["limits"] == {"max_nodes": 32, "max_edges": 64, "graph_max_bytes": 65536}
    for template in payload["templates"]:
        node_ids = {node["id"] for node in template["graph"]["nodes"]}
        for edge in template["graph"]["edges"]:
            assert edge["from"]["node"] in node_ids and edge["to"]["node"] in node_ids
    # Same states → same ETag; an availability change → a new ETag.
    assert catalog.build_catalog(states).etag == built.etag
    degraded = catalog.resolve_node_states(
        {**capabilities, "segmentation": CapabilityOut(available=False, code="not_approved", reason="no models")}
    )
    assert degraded["segmentation"] == catalog.NodeState(False, False, "not_approved", "no models")
    assert catalog.build_catalog(degraded).etag != built.etag
    missing = catalog.resolve_node_states({})
    assert missing["prompt"].code == "not_implemented" and missing["input_image"].available
    nodes = catalog.feature_nodes(states)
    assert [n.type for n in nodes] == [node.type for node in catalog.NODE_TYPES]
    assert all(n.hidden_by_default and n.locked for n in nodes if n.type in catalog.REFINE_NODE_TYPES)


def test_workflow_store_stub_has_the_frozen_api(tmp_path: Any) -> None:
    from storage.paths import RuntimePaths
    from storage.workflow_store import WorkflowStore

    store = WorkflowStore(RuntimePaths(tmp_path))
    assert store.list_terminal() == []
    assert store.delete("wf_0123456789ab") is False
    assert store.active_child_ids() == frozenset()
    assert list(tmp_path.iterdir()) == []
    assert str(inspect.signature(WorkflowStore.list_terminal)) == "(self) -> 'list[WorkflowRecord]'"
    assert str(inspect.signature(WorkflowStore.delete)) == "(self, wf_id: 'str') -> 'bool'"
    assert str(inspect.signature(WorkflowStore.active_child_ids)) == "(self) -> 'frozenset[str]'"


# ==================================================================================================
# Domain records and provider ABCs
# ==================================================================================================


def test_run_records_narrow_kind_and_keep_job_ids() -> None:
    from models.image_generation import ImageGenerationRun
    from models.part_analysis import PartAnalysisRun
    from models.segmentation import SegmentationRun
    from models.workflow import WorkflowRecord, WorkflowResource, WorkflowStep

    now = datetime(2026, 10, 20, tzinfo=UTC)
    assert ImageGenerationRun(id="ig_0123456789ab", created_at=now).kind == "image_generation"
    run = SegmentationRun(
        id="sg_0123456789ab", created_at=now, source={"kind": "job", "job_id": "job_0123456789ab"}
    )
    assert run.kind == "segmentation" and run.job_id == "job_0123456789ab"
    reference = SegmentationRun(
        id="sg_0123456789ac",
        created_at=now,
        source={"kind": "reference_image", "reference_set_id": "rs_0123456789ab", "image_id": "img_0123456789ab"},
    )
    assert reference.job_id is None
    with pytest.raises(ValidationError):
        SegmentationRun(id="sg_0123456789ad", created_at=now, source={"kind": "job"})
    analysis = PartAnalysisRun(id="pa_0123456789ab", created_at=now, job_id="job_0123456789ab")
    assert analysis.kind == "part_analysis" and analysis.result is None
    with pytest.raises(ValidationError):
        PartAnalysisRun(id="pa_0123456789ab", created_at=now, kind="segmentation", job_id="job_0123456789ab")
    workflow = WorkflowRecord(
        id="wf_0123456789ab",
        created_at=now,
        steps=[
            WorkflowStep(
                id="text_to_image", node="prompt", resource=WorkflowResource(kind="image_generation", id="ig_1")
            ),
            WorkflowStep(
                id="import_image",
                node="input_image",
                resource=WorkflowResource(kind="reference_image", id="img_1", reference_set_id="rs_1"),
            ),
        ],
    )
    assert workflow.is_active and workflow.child_ids() == frozenset({"ig_1"})


def test_provider_abcs_are_abstract() -> None:
    from inference.imagegen.base import TextToImageProvider
    from inference.parts.base import AmodalCompleter, CameraFit, LabelLifter, MeshSegmenter3D, PartGenerator
    from inference.providers.base import MODEL_KINDS, REASON_CODES, ModelProvider
    from inference.segmentation.base import Detector2D, Grounder2D, MaskRefiner, Parser2D

    assert "not_implemented" in REASON_CODES and "engine_unavailable" in REASON_CODES
    assert MODEL_KINDS[:3] == ("text_to_image", "segmentation", "part_extraction")
    for abc in (
        ModelProvider,
        TextToImageProvider,
        Detector2D,
        Grounder2D,
        MaskRefiner,
        Parser2D,
        LabelLifter,
        MeshSegmenter3D,
        AmodalCompleter,
        PartGenerator,
    ):
        assert inspect.isabstract(abc), abc.__name__
    assert {"upload", "release", "fit_camera", "raster", "render_textured", "render_labels"} <= set(
        LabelLifter.__abstractmethods__
    )
    fit = CameraFit(0.0, 2.0, 30.0, 1.93, (0.0, 0.0, 1.0, 1.0), 0.91, True, "silhouette_grid_refine", 1361)
    assert fit.yaw_deg == 0.0 and fit.accepted


# ==================================================================================================
# Response shapes pinned against the design examples (§4.4.6, §4.5.6, §4.6.7, §4.7.5, §6.1, §6.5)
# ==================================================================================================

NOW = datetime(2026, 10, 20, 10, 4, 31, tzinfo=UTC)

RUN_OUT_KEYS = [
    "id",
    "kind",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "workflow_id",
    "options",
    "models",
    "progress",
    "steps",
    "warnings",
    "error",
]
"""Common run shape (api-contract §11.4.2); each run response appends its own fields and ``result``."""

DESIGN_SEPARABLE_GROUPS = [
    {"id": "eyes", "label": "Eyes", "default_kind": "physical", "default_selected": True,
     "note": "Eye area is cut as an open surface; the hidden eyeball is not generated."},
    {"id": "eyebrows", "label": "Eyebrows", "default_kind": "physical_deferred", "default_selected": True,
     "note": "Eyebrows are painted on; geometry deferred (mask + surface region now)."},
    {"id": "eyelashes", "label": "Eyelashes", "default_kind": "physical_deferred", "default_selected": True,
     "note": "Eyelash geometry deferred; mask + lid curve only."},
    {"id": "hair", "label": "Hair", "default_kind": "physical", "default_selected": True,
     "note": "The scalp under the hair is not generated (opening recorded)."},
    {"id": "clothes", "label": "Clothes", "default_kind": "physical", "default_selected": True,
     "note": "The body under clothes is not generated (opening recorded)."},
    {"id": "accessories", "label": "Accessories", "default_kind": "physical", "default_selected": True, "note": None},
    {"id": "detachable", "label": "Detachable objects", "default_kind": "physical", "default_selected": True,
     "note": None},
]  # fmt: skip
"""``/features.pipeline.part_extraction.separable_groups`` of §6.1, verbatim."""

DESIGN_REGION_GROUPS = [
    {"id": "face", "label": "Face", "default_kind": "region", "default_selected": True, "note": None},
    {"id": "body", "label": "Body", "default_kind": "region", "default_selected": True, "note": None},
]


def _feature_node(
    type_: str,
    title: str,
    category: str,
    *,
    code: str = "ok",
    reason: str | None = None,
    locked: bool = False,
    hidden: bool = False,
) -> dict[str, Any]:
    return {
        "type": type_,
        "title": title,
        "category": category,
        "available": code == "ok",
        "locked": locked,
        "code": code,
        "reason": reason,
        "hidden_by_default": hidden,
    }


DESIGN_FEATURE_NODES = [
    _feature_node("input_image", "Input Image", "input"),
    _feature_node("prompt", "Prompt", "input"),
    _feature_node("job_result", "Existing Result", "input"),
    _feature_node("segmentation", "Segmentation", "analyze"),
    _feature_node("image_to_3d", "Image-to-3D", "generate"),
    _feature_node("part_extraction", "Part Extraction", "parts"),
    _feature_node("export", "Export", "output"),
    *(
        _feature_node(
            type_, title, "refine", code="not_approved", reason="Paused until approved", locked=True, hidden=True
        )
        for type_, title in (
            ("part_refine", "Part Refine"),
            ("mesh_tune", "Mesh Tune"),
            ("eye_correction", "Eye Correction"),
            ("rig_correction", "Rig Correction"),
        )
    ),
    _feature_node(
        "segmentation_3d",
        "3D Segmentation",
        "parts",
        code="unsupported",
        reason="Needs spconv/torch_scatter (no Python 3.14 / CUDA 13 wheels)",
        hidden=True,
    ),
]
"""``/features.nodes`` of §6.1 (abridged there: ``part_completion``/``part_generation`` follow ``segmentation_3d``)."""


def _all_capabilities_ok() -> dict[str, Any]:
    from schemas.features import CapabilityOut

    ok = CapabilityOut(available=True, code="ok")
    return {"image_to_3d": ok, "prompt_to_3d": ok, "segmentation": ok, "part_extraction": ok}


def test_features_building_blocks_match_the_design_example() -> None:
    from schemas.features import PartGroupOut, TaxonomyOut
    from services import workflow_catalog as catalog

    assert [group.model_dump() for group in PartGroupOut.separable_groups()] == DESIGN_SEPARABLE_GROUPS
    assert [group.model_dump() for group in PartGroupOut.region_groups()] == DESIGN_REGION_GROUPS
    nodes = [node.model_dump() for node in catalog.feature_nodes(catalog.resolve_node_states(_all_capabilities_ok()))]
    assert nodes[: len(DESIGN_FEATURE_NODES)] == DESIGN_FEATURE_NODES
    tail = nodes[len(DESIGN_FEATURE_NODES) :]
    assert [node["type"] for node in tail] == ["part_completion", "part_generation"]
    assert all(node["code"] == "unsupported" and node["hidden_by_default"] and node["reason"] for node in tail)
    taxonomy_out = TaxonomyOut.current().model_dump()
    assert taxonomy_out["version"] == 1
    assert taxonomy_out["groups"] == [group.id for group in taxonomy.GROUPS]
    assert next(entry for entry in taxonomy_out["classes"] if entry["id"] == "hair")["attach_to"] == ["body.head"]


def test_features_out_shape_and_schema_alias() -> None:
    from schemas.features import FeaturesOut, PartGroupOut, TaxonomyOut
    from services import workflow_catalog as catalog

    capability = {"available": True, "code": "ok", "reason": None}
    payload = {
        "schema": "img3d.features/1",
        "api": {"version": "1.1.0", "workflows": True, "part_analyses": True, "image_generations": True,
                "segmentations": True},
        "flags": {"part_aware": True, "image_generation": True, "segmentation": True, "part_analysis": True,
                  "workflows": True, "mesh_tuning": False, "part_refinement": False,
                  "persist_generation_intermediates": False},
        "pipeline": {
            "image_to_3d": capability,
            "prompt_to_3d": {**capability, "max_prompt_chars": 2000, "presets": ["character", "object", "raw"],
                             "styles": ["realistic", "stylized", "anime"], "supports_negative_prompt": False,
                             "default_model_id": "z_image_turbo", "default_model_state": "installed",
                             "cold_start_s": 337.0, "max_images": 4,
                             "sizes": [[1024, 1024], [896, 1152], [1152, 896]], "selection_modes": ["auto_first"]},
            "segmentation": {**capability, "subjects": ["auto", "anime_character", "realistic_human", "object"],
                             "profiles": [{**capability, "id": "baseline"},
                                          {"available": False, "code": "vendored_source_missing",
                                           "reason": "See-through code is not vendored", "id": "anime"}],
                             "default_profile": "auto", "max_extra_terms": 16, "before_reconstruction": True},
            "part_extraction": {**capability,
                                "strategies": [{**capability, "id": "components", "label": "Geometry components",
                                                "requires_segmentation": False}],
                                "default_strategy": "semantic_parts",
                                "separable_groups": DESIGN_SEPARABLE_GROUPS, "region_groups": DESIGN_REGION_GROUPS,
                                "max_views": 24, "default_views": 8, "rebuild": True, "per_part_glbs": "on_demand"},
        },
        "workflow": {**capability, "catalog_url": "/api/v1/workflows/catalog", "catalog_etag": '"sha256-3f1c"',
                     "max_nodes": 32, "max_edges": 64, "graph_max_bytes": 65536, "selection_modes": ["auto_first"],
                     "legacy_job_node_map": dict(catalog.LEGACY_JOB_NODE_MAP)},
        "nodes": DESIGN_FEATURE_NODES,
        "mesh_tuning": {"enabled": False, "reason": "Paused until the part-aware backend layer is approved"},
        "part_refinement": {"enabled": False, "reason": "Paused until the part-aware backend layer is approved"},
        "models": [],
        "taxonomy": TaxonomyOut.current().model_dump(),
        "queues": {"runs": {"pending": 0, "running_id": None, "running_kind": None, "max_size": 8}},
        "limits": {"prompt_max_chars": 2000, "negative_prompt_max_chars": 1000, "extra_terms_max": 16,
                   "views_max": 24, "workflow_graph_max_bytes": 65536, "max_parts": 256},
    }  # fmt: skip
    features = FeaturesOut.model_validate(payload)
    dumped = features.model_dump(mode="json")
    assert list(dumped) == list(payload)
    assert dumped == payload
    assert features.pipeline.part_extraction.separable_groups == PartGroupOut.separable_groups()
    broken = copy.deepcopy(payload)
    broken["pipeline"]["image_to_3d"]["code"] = "engine_down"
    with pytest.raises(ValidationError):
        FeaturesOut.model_validate(broken)


DESIGN_MODEL_OUT = {
    "id": "z_image_turbo", "kind": "text_to_image", "roles": ["generator"], "display_name": "Z-Image-Turbo",
    "state": "installed", "reason_code": "ok", "reason": None, "approved": True, "is_default": True,
    "repo_id": "Tongyi-MAI/Z-Image-Turbo", "revision": "f332072aa78be7aecdf3ee76d5c247082da564a6",
    "size_bytes": 32850000000, "vram_estimate_gb": 24.0, "ram_gb": 21.0, "cold_load_s": 337.0,
    "license": {"id": "apache-2.0", "name": "Apache License 2.0",
                "url": "https://huggingface.co/Tongyi-MAI/Z-Image-Turbo", "commercial": "allowed", "gated": False,
                "requires_acceptance": False, "accepted": True, "conditions": []},
    "dependencies": [{"distribution": "diffusers", "requirement": "==0.39.0", "installed": "0.39.0", "ok": True}],
    "vendored_source": None, "local_path": ".model/t2i/Z-Image-Turbo",
    "install": {"supported": False,
                "hint": "Weights are installed manually under backend/.model/t2i/Z-Image-Turbo (DECISIONS.md)",
                "bytes_done": None, "bytes_total": None},
    "capabilities": {"sizes": [[1024, 1024], [896, 1152], [1152, 896]], "steps": {"default": 9, "min": 1, "max": 50},
                     "guidance": {"default": 0.0, "min": 0.0, "max": 0.0}, "supports_negative_prompt": False,
                     "max_images": 4},
    "loaded_device": None, "last_used_at": None, "hidden_by_default": False,
}  # fmt: skip
"""The §6.1 ``ModelOut`` example, verbatim."""


def test_model_out_matches_the_design_example() -> None:
    from schemas.model_registry import ModelList, ModelOut

    model = ModelOut.model_validate(DESIGN_MODEL_OUT)
    dumped = model.model_dump(mode="json")
    # ``license.attribution`` is the one optional addition (``LicenseInfo.attribution``; null when unset).
    assert dumped["license"].pop("attribution") is None
    assert dumped == DESIGN_MODEL_OUT
    assert ModelList(items=[model]).model_dump(mode="json") == {"items": [model.model_dump(mode="json")]}
    sapiens = copy.deepcopy(DESIGN_MODEL_OUT)
    sapiens["license"].update(
        id="sapiens2-license",
        commercial="allowed_with_conditions",
        conditions=["No biometric identification", "No deepfakes or impersonation", "No military use"],
    )
    assert ModelOut.model_validate(sapiens).license.conditions[0] == "No biometric identification"
    for field, value in (("state", "ready"), ("reason_code", "broken"), ("kind", "upscaler")):
        with pytest.raises(ValidationError):
            ModelOut.model_validate({**DESIGN_MODEL_OUT, field: value})


def test_image_generation_out_matches_the_design_example() -> None:
    from models.image_generation import CandidateUse, ImageCandidate, ImageGenerationResult, ImageGenerationRun
    from models.runs import ModelUse
    from schemas.image_generation import ImageGenerationOut

    run = ImageGenerationRun(
        id="ig_0123456789ab",
        created_at=NOW,
        models=[
            ModelUse(id="z_image_turbo", revision="f332072aa78be7aecdf3ee76d5c247082da564a6", license_id="apache-2.0")
        ],
        result=ImageGenerationResult(
            candidates=[
                ImageCandidate(
                    index=0,
                    seed=987654,
                    width=896,
                    height=1152,
                    used_as=[CandidateUse(reference_set_id="rs_0123456789ab", image_id="img_0123456789ab")],
                )
            ],
            timings={"load_model": 0.0, "generate": 3.6, "check": 0.2, "save": 0.3, "total": 4.3},
        ),
    )
    out = ImageGenerationOut.from_run(run).model_dump(mode="json")
    assert list(out) == [*RUN_OUT_KEYS, "result"]
    assert out["kind"] == "image_generation" and out["result"]["timings"]["total"] == 4.3
    assert out["models"] == [
        {"id": "z_image_turbo", "revision": "f332072aa78be7aecdf3ee76d5c247082da564a6", "license_id": "apache-2.0"}
    ]
    assert out["result"]["candidates"] == [
        {
            "index": 0,
            "seed": 987654,
            "width": 896,
            "height": 1152,
            "url": "/api/v1/image-generations/ig_0123456789ab/candidates/0",
            "thumbnail_url": "/api/v1/image-generations/ig_0123456789ab/candidates/0/thumbnail",
            "readiness": {"ok": True, "issues": [], "method": "border_color"},
            "used_as": [{"reference_set_id": "rs_0123456789ab", "image_id": "img_0123456789ab"}],
        }
    ]
    assert run.candidate(0) is not None and run.candidate(1) is None
    queued = ImageGenerationOut.from_run(ImageGenerationRun(id="ig_0123456789ac", created_at=NOW))
    assert queued.result is None and queued.status == "queued" and queued.progress.percent == 0.0


def test_segmentation_out_matches_the_design_example() -> None:
    from models.segmentation import SegmentationFrame, SegmentationLabel, SegmentationResult, SegmentationRun
    from schemas.segmentation import SegmentationOut

    result = SegmentationResult(
        space="conditioning",
        frame=SegmentationFrame(
            mode="alpha_crop",
            method="recompute",
            scale=0.5,
            crop_box=[112, 40, 912, 840],
            size=[800, 800],
            verified=True,
            residual=0.7,
        ),
        subject="anime_character",
        profile="baseline",
        labels=[
            SegmentationLabel(
                value=30,
                category="hair",
                group="hair",
                area_px=51234,
                confidence=0.86,
                bbox=[130, 42, 870, 610],
                sources=["grounding_dino_base", "sam2_1_hiera_large"],
            )
        ],
        annotations={"eyelash.left": {"lid_curve": [[401, 300], [430, 292]], "method": "heuristic", "confidence": 0.6}},
        expected_terms_missing=["object.held"],
        timings={"parse": 0.0, "detect": 1.1, "refine": 0.6, "fuse": 0.1, "total": 2.6},
    )
    run = SegmentationRun(
        id="sg_0123456789ab", created_at=NOW, source={"kind": "job", "job_id": "job_0123456789ab"}, result=result
    )
    out = SegmentationOut.from_run(run).model_dump(mode="json")
    assert list(out) == [*RUN_OUT_KEYS, "source", "job_id", "source_url", "result"]
    assert out["job_id"] == "job_0123456789ab"
    assert out["source"] == {"kind": "job", "reference_set_id": None, "image_id": None, "job_id": "job_0123456789ab"}
    assert out["source_url"] == "/api/v1/segmentations/sg_0123456789ab/source"
    assert out["result"] == {
        "space": "conditioning",
        "frame": {"mode": "alpha_crop", "method": "recompute", "scale": 0.5, "crop_box": [112, 40, 912, 840],
                  "size": [800, 800], "verified": True, "residual": 0.7},
        "subject": "anime_character",
        "profile": "baseline",
        "labels": [{"value": 30, "category": "hair", "group": "hair", "area_px": 51234, "confidence": 0.86,
                    "bbox": [130, 42, 870, 610], "side": None,
                    "sources": ["grounding_dino_base", "sam2_1_hiera_large"],
                    "mask_url": "/api/v1/segmentations/sg_0123456789ab/masks/30"}],
        "annotations": {"eyelash.left": {"lid_curve": [[401, 300], [430, 292]], "method": "heuristic",
                                         "confidence": 0.6}},
        "expected_terms_missing": ["object.held"],
        "overlay_url": "/api/v1/segmentations/sg_0123456789ab/overlay",
        "labels_url": "/api/v1/segmentations/sg_0123456789ab/labels",
        "timings": {"parse": 0.0, "detect": 1.1, "refine": 0.6, "fuse": 0.1, "total": 2.6},
    }  # fmt: skip


def test_part_analysis_out_urls_and_nullability() -> None:
    from models.part_analysis import PartAnalysisResult, PartAnalysisRun, PartRefinementSummary, PartSummary
    from schemas.parts import PartAnalysisOut

    hair = PartSummary(
        id="hair",
        label="Hair",
        category="hair",
        group="hair",
        kind="physical",
        node_name="part:hair",
        region_value=30,
        faces=410112,
        confidence=0.88,
        side="center",
        has_mask=True,
        thumbnail="hair.webp",
        refinement=PartRefinementSummary(needs=["closure"]),
    )
    brow = PartSummary(
        id="eyebrow.left",
        label="Left eyebrow",
        category="eyebrow.left",
        group="eyebrows",
        kind="physical_deferred",
        region_value=22,
        faces=1890,
        side="left",
        has_mask=True,
    )
    semantic = PartAnalysisRun(
        id="pa_3b1f0c9a2d7e",
        created_at=NOW,
        job_id="job_9f3c2a1b4d5e",
        result=PartAnalysisResult(
            part_count=9,
            physical_count=5,
            deferred_count=4,
            region_count=12,
            camera_fit_iou=0.91,
            unlabelled_face_ratio=0.04,
            partner_filled_ratio=0.43,
            cut_partner_consistency=0.97,
            has_overlay=True,
            previews=["front"],
            parts=[hair, brow],
            timings={"total": 57.6},
        ),
    )
    out = PartAnalysisOut.from_run(semantic).model_dump(mode="json")
    assert list(out) == [*RUN_OUT_KEYS, "job_id", "result"]
    base = "/api/v1/part-analyses/pa_3b1f0c9a2d7e"
    result = out["result"]
    assert list(result) == [
        "status", "manifest_url", "assembled_url", "download_url", "zip_url", "overlay_url", "face_labels_url",
        "preview_urls", "region_attribute", "part_count", "physical_count", "deferred_count", "region_count",
        "camera_fit_iou", "unlabelled_face_ratio", "partner_filled_ratio", "cut_partner_consistency", "parts",
        "stats", "timings",
    ]  # fmt: skip
    assert (result["status"], result["region_attribute"]) == ("completed", "_FEATURE_ID_0")
    assert result["manifest_url"] == f"{base}/manifest"
    assert (result["assembled_url"], result["download_url"]) == (f"{base}/model.glb", f"{base}/export/glb")
    assert (result["zip_url"], result["face_labels_url"]) == (f"{base}/export/zip", f"{base}/buffers/face_labels")
    assert result["overlay_url"] == f"{base}/segmentation/overlay.png"
    assert result["preview_urls"] == {"front": f"{base}/previews/front.png"}
    assert result["parts"][0] == {
        "id": "hair", "label": "Hair", "category": "hair", "group": "hair", "kind": "physical",
        "node_name": "part:hair", "region_value": 30, "faces": 410112, "confidence": 0.88, "side": "center",
        "model_url": f"{base}/parts/hair/model.glb", "download_url": f"{base}/parts/hair/export/glb",
        "mask_url": f"{base}/masks/30", "thumbnail_url": f"{base}/thumbs/hair.webp",
        "refinement": {"status": "untouched", "locked": True, "eligible": True, "needs": ["closure"]},
    }  # fmt: skip
    deferred = result["parts"][1]
    assert deferred["model_url"] is None and deferred["download_url"] is None and deferred["node_name"] is None
    assert deferred["mask_url"] == f"{base}/masks/22" and deferred["thumbnail_url"] is None
    # The components strategy: no overlay, no camera fit, no cut, previews skipped; the GLB URLs stay non-null.
    components = PartAnalysisRun(
        id="pa_3b1f0c9a2d7f", created_at=NOW, job_id="job_9f3c2a1b4d5e", result=PartAnalysisResult(part_count=1)
    )
    plain = PartAnalysisOut.from_run(components).model_dump(mode="json")["result"]
    assert plain["overlay_url"] is None and plain["camera_fit_iou"] is None
    assert plain["cut_partner_consistency"] is None and plain["preview_urls"] == {}
    assert all(plain[key] for key in ("assembled_url", "download_url", "zip_url", "manifest_url", "face_labels_url"))


def test_workflow_out_matches_the_design_example() -> None:
    from models.job import JobError
    from models.workflow import WorkflowArtifact, WorkflowOutputs, WorkflowRecord, WorkflowResource, WorkflowStep
    from schemas.workflows import StepFailedDetails, WorkflowOut

    thumbnail = "/api/v1/image-generations/ig_0123456789ab/candidates/0/thumbnail"
    record = WorkflowRecord(
        id="wf_0123456789ab",
        created_at=NOW,
        started_at=NOW,
        status="running",
        request={"source": {"kind": "prompt"}},
        graph_stored=True,
        current_step="reconstruction",
        steps=[
            WorkflowStep(
                id="text_to_image",
                node="prompt",
                node_id="n_prompt",
                status="done",
                progress=100.0,
                warning_count=1,
                resource=WorkflowResource(kind="image_generation", id="ig_0123456789ab"),
                artifacts=[WorkflowArtifact(role="candidate", url=thumbnail)],
            ),
            WorkflowStep(
                id="import_image",
                node="input_image",
                status="done",
                resource=WorkflowResource(
                    kind="reference_image", id="img_0123456789ab", reference_set_id="rs_0123456789ab"
                ),
            ),
            WorkflowStep(
                id="reconstruction",
                node="image_to_3d",
                node_id="n_recon",
                status="running",
                progress=38.5,
                resource=WorkflowResource(kind="job", id="job_0123456789ab"),
            ),
            WorkflowStep(id="part_extraction", node="part_extraction", node_id="n_parts"),
        ],
        outputs=WorkflowOutputs(
            reference_set_id="rs_0123456789ab", image_id="img_0123456789ab", job_id="job_0123456789ab"
        ),
    )
    gone = {"img_0123456789ab"}
    out = WorkflowOut.from_domain(record, resource_exists=lambda r: r.id not in gone).model_dump(mode="json")
    assert list(out) == [
        "id", "name", "status", "created_at", "started_at", "finished_at", "request", "graph_stored", "current_step",
        "progress", "steps", "outputs", "error",
    ]  # fmt: skip
    first, imported, recon, pending = out["steps"]
    assert list(first) == [
        "id", "node", "node_id", "status", "message", "progress", "warning_count", "resource", "artifacts"
    ]  # fmt: skip
    assert first["resource"] == {
        "kind": "image_generation", "id": "ig_0123456789ab", "reference_set_id": None, "exists": True
    }  # fmt: skip
    assert first["warning_count"] == 1 and first["artifacts"] == [{"role": "candidate", "url": thumbnail}]
    assert imported["resource"]["exists"] is False and imported["resource"]["reference_set_id"] == "rs_0123456789ab"
    assert imported["node_id"] is None and imported["warning_count"] == 0
    assert recon["progress"] == 38.5 and recon["warning_count"] == 0
    assert pending["resource"] is None and pending["status"] == "pending" and pending["artifacts"] == []
    assert list(out["outputs"]) == [
        "reference_set_id", "image_id", "segmentation_id", "job_id", "part_analysis_id", "model_url",
        "parts_model_url", "manifest_url",
    ]  # fmt: skip
    assert out["progress"] == {"percent": 0.0, "message": None, "updated_at": None}
    assert record.child_ids() == frozenset({"ig_0123456789ab", "job_0123456789ab"})
    child_error = {"code": "partition_failed", "message": "Part extraction failed", "details": None}
    failed = record.model_copy(
        update={
            "status": "failed",
            "error": JobError(
                code="step_failed",
                message="Step part_extraction failed",
                details={
                    "step": "part_extraction",
                    "child_kind": "part_analysis",
                    "child_id": "pa_0123456789ab",
                    "child_error": child_error,
                },
            ),
        }
    )
    error = WorkflowOut.from_domain(failed, resource_exists=lambda r: True).model_dump(mode="json")["error"]
    assert error["code"] == "step_failed"
    details = StepFailedDetails.model_validate(error["details"])
    assert details.child_kind == "part_analysis" and details.child_error.model_dump() == child_error


def test_catalog_node_entry_matches_the_design_example() -> None:
    from schemas.segmentation import SegmentationOptions
    from services import workflow_catalog as catalog

    payload = catalog.build_catalog(catalog.resolve_node_states(_all_capabilities_ok())).model_dump(mode="json")
    assert list(payload) == [
        "schema", "etag", "port_types", "node_types", "templates", "legacy_job_node_map", "limits"
    ]  # fmt: skip
    assert payload["port_types"] == [
        {"id": "image_set", "label": "Image"},
        {"id": "segmentation", "label": "Masks"},
        {"id": "mesh", "label": "Mesh"},
        {"id": "part_set", "label": "Parts"},
    ]
    # The ETag is the hash of the canonical JSON of everything except itself.
    assert payload["etag"] == catalog.catalog_etag({key: value for key, value in payload.items() if key != "etag"})
    segmentation = copy.deepcopy(next(node for node in payload["node_types"] if node["type"] == "segmentation"))
    assert segmentation.pop("params_schema") == SegmentationOptions.model_json_schema()
    assert segmentation == {
        "type": "segmentation", "version": 1, "category": "analyze", "title": "Segmentation",
        "inputs": [{"id": "images", "label": "Image", "accepts": ["image_set"], "required": True, "multiple": False}],
        "outputs": [{"id": "masks", "label": "Masks", "type": "segmentation"}],
        "defaults": {"profile": "auto", "subject": "auto", "classes": None, "extra_terms": [],
                     "use_prompt_terms": True, "head_pass": True, "min_confidence": 0.35},
        "compiles_to": "segmentation", "max_instances": 1, "available": True, "locked": False,
        "code": "ok", "reason": None, "hidden_by_default": False,
    }  # fmt: skip
    rebuild = next(template for template in payload["templates"] if template["id"] == "rebuild_parts")["graph"]
    assert (rebuild["schema"], rebuild["version"]) == ("img3d.workflow_graph", 1)
    assert [node["type"] for node in rebuild["nodes"]] == ["job_result", "part_extraction", "export"]
