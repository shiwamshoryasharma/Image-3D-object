# S3b Parts asset layer — progress (2026-10-22)

## Built
Torch-free asset layer on top of the merged S3a core (DESIGN §2.4, §3.3–§3.14, §4.6.5 steps 4–7).
- Verified/completed the stopped run's files: `buffers`, `frames`, `policy`, `relations` unchanged (all exercised by
  tests); `labels` got one fix: mirror completion (§4.6.5 5.4) now queries from the target side, so every eligible
  face within `2/effective_resolution` of a mirrored source centroid is covered (was 77 % of the mirrored eye area).
- `validate.validate_staging(staging, manifest, base_path)` → list of problems (§3.14 list + `manifest.json` equals
  the model, buffer dtype/count/indexes, face_flags reserved bits, assembled-GLB hash, mesh/part consistency).
- `publish.publish(staging, target, job_exists)`: refuses existing target (`publish_conflict`), re-checks the job and
  the analysis folder (`JobDeletedError`, never recreates `parts/<pa>/`), one `retry_os(os.replace)`.
  `validate_and_publish(...)` re-hashes the base right before the rename (`StagingInvalidError`, code `export_failed`).
- `export.export_part_layer(staging, base, topology, shells, assignment, plan, ExportOptions, partners=)`: nodes,
  seams, open loops, records (stats, frames, sides, geometry, uv, needs, provenance, lineage), relations, buffers,
  `model_parts.glb` (+ optional `parts/<id>.glb`), `masks/<value>.png`, `thumbs/<id>.webp`, A4 check on the written
  files (`ReassemblyError`), `manifest.json`, then validation. `write_part_glb_from_reader()` = on-demand per-part GLB
  for S10's `part_files`. `cut_partner_consistency()` per §3.10.
- `reader.AssetReader` (`verify` none/hashes/full), `PartGeometry`, `SeamLoops` (welded ids + positions);
  `reassemble()` rebuilds the base arrays (metadata from the base via `dataclasses.replace`).
- `consumers.PartConsumerRegistry(enabled=)`: `register()` raises `FeatureDisabledError(feature="part_refinement",
  flag="IMG3D_PART_REFINEMENT_ENABLED")` while disabled; `list()` is `()`; zero implementations ship.
- `lineage`: `LineageSource`, `face_iou_matrix` (4 joint histograms: node sets for mesh-owning records, `face_part`
  sets for regions/deferred), `compute_lineage` (IoU ≥ 0.5, ≤ 3, highest first), `find_earlier_analyses()`.

## Files
New: `backend/parts/{export,validate,publish,reader,consumers,lineage}.py`; edited: `backend/parts/labels.py`.
Tests: `test/backend/test_parts_{labels,policy,export}.py`, `test/backend/test_reader_consumers.py`.
Scratch (untracked): `test/.runtime/s3b/run_real.py`; output `test/outputs/part_aware/s3b_real_alice.json`.

## Tests
- S3b: 60 passed (labels 12, policy 23, export 16, reader/consumers 9) in ~11 s; with S1/S3a parts + boundary tests
  208 passed.
- Full suite: 639 passed, 4 skipped, 3 failed. The 3 failures are S6's in-flight vendoring (submodules added →
  See-through/Sapiens2 now `installed`): `test_model_registry::test_real_on_disk_states_of_this_machine`,
  `test_features_api::{test_features_reports_the_real_states_of_this_machine,
  test_load_refuses_unusable_models_and_reports_load_errors}` expect `vendored_source_missing`. Not S3b.

## Measured
- Rim-joined hemisphere (20 k faces): partner fill covers ≥ 95 % of inner faces, the hair cut holds both walls,
  `cut_partner_consistency` = 1.00. Mirror completion 96 % (symmetric) / 0 % (half displaced by 2.5 tolerances).
- 500 k faces: 180-face brow island + 138-face lash band keep their deferred records; assign + plan 1.65 s.
- Real `alice_front` (COPY in `test/.runtime/s3b/`, source sha256 re-checked unchanged; copy deleted afterwards),
  F = 1 936 495: prepare 6.5 s; `components`: 7 parts + 19 floaters, export 2.3 s, validate+publish 0.2 s,
  `AssetReader(verify="full")` 0.56 s, reassembly exact. Painted semantic cut (hair above y = 0.3, only front/up-facing
  faces voted): partner-filled 28.8 %, unlabelled 0.0, `cut_partner_consistency` 0.9987, export 2.2 s.

## Deviations
1. `PartGeometry` has an extra `feature_ids` field (from the GLB node); `AssetReader` adds `record()`, `assembled()`,
   `verify_hashes()`, `unassigned_faces()`.
2. `geometry.open_boundary_loops` of a mesh node = base-open loops + loops of its seams (a cut part needs `closure`).
3. `label_table` = values present in `face_labels` ∪ record region values (the `components` base record keeps value 1
   with `face_count` 0, so its value must be in the table).
4. With the frozen §2.3/§2.4 rules two *deferred* instances can never share a value (instances need ≥ min_part_faces,
   which makes them physical); the shared-value `region_faces` test uses two `max_parts_exceeded` overflow regions.
5. Thumbnails only for physical/deferred records with a mask (matches §3.11: face region and base have none).

## Requests / notes
- S10: build `ExportOptions` (generator, strategy, views, conditioning, masks, `LabelSource`, expectations,
  provenance, `earlier=find_earlier_analyses(jobs/<job>/parts, model_sha256=..., exclude=pa)`); write
  `segmentation/*` and `previews/*` into staging first; publish with `validate_and_publish(..., job_exists)`.
  Error codes: `ReassemblyError`→`partition_failed`, `StagingInvalidError`/`PublishError`→`export_failed`,
  `PublishConflictError`→`publish_conflict`, `JobDeletedError`→`job_deleted`.
- S1 owner: `parts.types` has no constant for `charts_cross_boundary`; export defines it locally.

## Open issues
- None blocking. `relations.held_by` is untested (no hand fixture); `occludes` is tested with a synthetic render.
