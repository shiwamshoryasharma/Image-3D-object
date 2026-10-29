# Generation quality checkpoint — 2026-10-29

The user requested a fix for complex-image generation defects, especially anime
eyes/faces and holes, followed by animal and house validation. That request
authorized the targeted generation/export changes and paired protected hashes.
It did not approve the part-aware milestone or resume the Auto Mesh Tuner.

## Implemented

Production changes are confined to:
- backend/inference/trellis/export.py
- backend/inference/trellis/mesh_quality.py

The exporter uses a two-voxel remeshing band instead of one voxel. It closes
small reconstruction punctures with approximately one extra voxel of smoothing.
The decoded sampling, model weights, dependency pins, progress stages, reference
selection, API contracts and existing job artifacts were not changed.

Postprocessing removes true zero-area faces and tiny opposite-winding duplicate
membranes. Combined removals are rejected if they would turn a shared edge into
a new boundary. Compact volumetric details and normal open sheets/whiskers are
retained; generic small-part deletion is not used.

Authored vertex/face normals must survive face pruning. Recomputing them on
UV-split vertices caused a visible faceting regression during development; it
was caught by rendering and now has three dedicated regression cases.

Isolated, consistently directed triangular boundaries are handled conservatively:
- A real triangular cap may span at most three voxels. It uses new chart-specific
  UV vertices with one adjacent-face texture sample, without changing old UVs.
- An EXACT collinear T-junction up to four voxels is stitched by splitting the
  adjacent face. UVs and normals are interpolated; existing positions are kept.
- Larger, branched, inconsistently oriented or merely near-collinear cases are
  not guessed at.

## Evidence

Real GPU generation used test/images/ANIME_CHARACTER_01/Alice.png,
test/images/ALICE/ALICE_full_front_view.png, and the cat/house fixtures in
test/images/generation_quality. Provenance is in that fixture folder's
sources.json. These were SFW inputs; no NSFW-specific validation was performed.

Captures under test/outputs/generation_quality retain decoded tensors so the
actual production exporter can be replayed without resampling a new shape.
No production backend/runtime/jobs were used.

Final delivered models are in test/outputs/generation_quality/final:
anime.glb, anime_full_front.glb, cat.glb and house.glb.
Every delivered GLB has NORMAL and TEXCOORD_0 attributes, zero geometric open
boundary edges, zero degenerate faces and no inconsistent manifold-edge winding
in the exact-position/UV-seam-aware probe. Some coincident/nonmanifold geometry
is retained when deletion would be unsafe; do not call the meshes universally
manifold, anatomically perfect or suitable for every downstream use.

The observed anime eye puncture and numerical artifacts improved. Eye shape and
unseen anatomy already come from the learned reconstruction before export.
Microsoft's topology capability is not a guarantee of correct facial anatomy
for every image. See the saved face/geometry comparisons before making claims.

Final focused CPU checks: 261 passed, 1 skipped, 4 GPU tests deselected.
Final native GPU checks: 4 passed. An earlier broader run had 932 passes,
3 skips and one Blender download-cancellation timing failure; that unrelated
case passed on its targeted rerun. Raw CuMesh intermediate diagnostics can
contain collapsed facets; gate the COMPLETE production cleanup path and keep
the raw metrics as evidence.

## Reproduction and artifacts

The linked generated reports, models and renders are gitignored local artifacts.
Use the test runners below to recreate them on another checkout.

- [Detailed report](../../test/outputs/generation_quality/final/validation.md)
- [Measurements and model hashes](../../test/outputs/generation_quality/final/validation.json)
- [Before/after comparison](../../test/outputs/generation_quality/final/comparison.png)
- New tests: test/engine/test_export_cleanup.py, test_export_holes.py,
  test_export_surface.py, test_mesh_export_quality.py and test_remesh_geometry.py.
- Real-image runner: test/engine/run_quality_batch.py (one engine load).
- Capture/replay: run_quality_capture.py and replay_quality_export.py.
- CPU diagnostics: mesh_quality_probe.py and analyze_sliver_stages.py.

The capture, replay and batch runners force caches, logs, temporary directories
and runtime state under test and disable model downloads. Always acquire the
shared GPU lock. Use a new batch label rather than overwriting a finished run.
Restart an already-running backend to load the changed modules.

Both export.py and the new mesh_quality.py are pinned in
test/backend/protected_files.sha256. Future unrelated changes do not inherit
authorization to re-pin them.
