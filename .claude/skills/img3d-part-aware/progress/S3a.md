# S3a Parts geometry core — progress (2026-10-16)

## Built
Torch-free geometry core (numpy/scipy, Pillow only lazily for the atlas colour). DESIGN §3.1-§3.5, §3.12, §4.6.5 steps 1, 2, 6, 7.
- `gltf_io`: `read_base_glb` gives the raw accessors plus the sha256. It rejects multi-mesh/multi-primitive, sparse,
  Draco/meshopt, extra attributes, morph targets, non-indexed, non-triangle, transformed mesh nodes, external buffers
  and non-float positions (`UnsupportedBaseMeshError.code="unsupported_base_mesh"`; `BaseMeshChangedError` on an
  `expected_sha256` mismatch). `write_parts_glb` writes atomically (temp + `os.replace`): flat `img3d_asset` node,
  material/texture/image tables copied verbatim, image bufferViews byte-identical, POSITION min/max, u32 indices, and
  `_FEATURE_ID_0` as u16 in its own bufferView with stride 4. It requires `extras.img3d.kind` and sets
  `extras.img3d.id`. `read_parts_glb` reads it back. `face_base_color_lab` samples the atlas at each face's UV
  centroid in CIE Lab.
- `weld`: the mandated void-view `np.unique` with `.ravel()`/`.reshape(-1)`, the welded edge table (CSR edge→faces,
  non-manifold edges kept, degenerate edges dropped) and the `F×F` face adjacency.
- `components`: face geometry, components ordered by area (0 = largest), and `build_topology()` (the prepare-step
  bundle).
- `shells`: `classify_shells` handles detached inner sheets (≥95 % of vertices within the host's `shell_eps`,
  cKDTree, AABB pruning, chained roots), major (≥25 %) components and separate shells. `split_floaters` applies §2.4
  rule 2 (manifest `Floater` records). `pair_opposite_faces` uses k=16 neighbours, dot < -0.5 and the "behind" test,
  in chunks. `global_shell_eps` and `partner_coverage` are also here.
- `partition`: `build_mesh_nodes` implements the §3.4 vmap rule, vectorised over (vertex<<16 | label) keys. It is
  checked against a literal transcription of the spec.
- `seams`: per-(a,b) records. Open chains end at degree≠2 vertices, loops are oriented by a's winding, both sheets
  share one record, plus the `seam_loops` buffer and `open_boundary_loops`.
- `reassembly`: the A4 check on the written GLB (bit compare of POSITION/NORMAL/TEXCOORD_0, `vmap[local]==faces`,
  every face exactly once), with `to_manifest()` and `raise_if_inexact()` (`ReassemblyError.code="partition_failed"`).

## Files (all new)
`backend/parts/{gltf_io,weld,components,shells,partition,seams,reassembly}.py`, `test/backend/parts_fixtures.py`,
`test/backend/test_gltf_io.py`, `test/backend/test_parts_geometry.py`

## Tests
- S3a: 42 passed (`test_gltf_io.py` + `test_parts_geometry.py`, about 15 s including the 2M and real-fixture tests).
- Full suite (`test\backend test\aux_models`): 547 passed, 3 skipped, 2 failed. Both failures are
  `test_gpu_task_seam.py::test_app_starts_with_part_aware_enabled_and_disabled[on/off]`: `GET /features` now
  answers 200 where the test expects 404. That is S2's in-flight features router, not S3a.

## Measured
- Real `alice_front`, read in place (sha256 and dir listing checked unchanged). Values: F=1 936 495, V=1 618 334,
  W=949 325, 390 components, comp 0 = 90.17 % of F. Inner sheets: 363 (56 036 faces). Separate shells: 26 (7 parts
  + 19 floaters at min_part_faces=194). eps=0.002685 (median edge 0.00179). Timing: weld+edges+adjacency+components
  2.40 s, shells 2.65 s, partners about 1.8 s, **total 7.13 s (< 60 s)**. **Component-0 partner coverage 75.48 %
  (≥ 70 %)**; it is 75.37 % at eps = 4/1536.
- 2M synthetic (1 997 568 faces): topology 2.9 s; nodes 0.35 + seams 0.13 + write 0.10 + verify 0.14 s; total 3.4 s.
- Rim-joined hemisphere (40 832 faces): 100 % of inner-wall faces paired, all with outer-wall faces.

## Deviations
1. `build_mesh_nodes(base, face_owner, face_labels, *, part_ids)`: the §3.4 signature has no part ids, but
   `MeshNode.part_id` needs one, so `part_ids` is a required keyword-only argument.
2. `uv_sphere` is a spherified cube with one UV chart per cube face (chart borders are UV duplicates), not a lat/long
   sphere. Lat/long pole fans put more than 16 same-wall centroids inside the wall gap, so pairing fell to 81-90 %.
   That is a fixture artifact; TRELLIS meshes have no poles. The signature is as specified, and the default wall gap
   is 0.6 edge lengths.
3. ALICE measurements differ from DESIGN's [M]: 363 inner sheets / 56 036 faces (DESIGN: 343 / 19 080),
   26 separate shells (DESIGN: 45), coverage 75.5 % (DESIGN: 78.5 % at 4/1536). The cause is the exact criteria (the
   "behind" test, highest-coverage host). Both acceptance thresholds hold.

## Requests / notes for other slices
- S3b: `BaseMesh` lives in `parts.gltf_io`. `NodeSpec.from_mesh_node(base, node, extras)` builds writer nodes.
  `SeamSet.manifest_records()`, `seam_ids_for()`, `loops_of()` and `loop_vertices` (the `seam_loops` buffer) are
  available. `ShellReport.split_floaters(..., candidates=<unlabelled roots>)`, `roots_with_inner_sheets()` (for the
  `inner_sheet_merged` tag), `inner_sheet_face_mask()` (step 5.2) and `open_boundary_loops()` are available.
  `ReassemblyReport.to_manifest()` is ready to use. The effective `min_part_faces` stays in S3b's policy.
- S10: call `build_topology` → `classify_shells` → `global_shell_eps` + `pair_opposite_faces`. Map
  `UnsupportedBaseMeshError` to `unsupported_base_mesh` and `ReassemblyError` to `partition_failed`.
  `BaseMeshChangedError` needs a run error code (suggest `source_missing`).

## Open issues
- `test/part_aware/fixtures.json` is not valid JSON: its "regenerate" string has unescaped backslashes. The real-fixture
  test escapes them before parsing; the catalog's owner should fix the file.
