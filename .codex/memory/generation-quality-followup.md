# Generation and parts follow-up — 2026-10-30

Status: code and specified hole/parts regressions validated. Eye/face fidelity is
still limited by the learned single-image reconstruction; do not call it solved.
This supersedes the lower-resolution 2026-10-29 export checkpoint.

## Confirmed causes

The reported anime used test/images/ALICE/ALICE_full_front_view.png, effective
resolution 1536, seed 1719986157, 2M polygon target and 4K textures. The original
GLB had 2,085 geometric boundary edges and long vertical rods. Its part analysis
was `components`: 100% unlabelled faces, seven disconnected geometry components.
It never ran semantic identification of hair, clothes or eyes.

CuMesh native `fill_holes` can append corrupt fan-center vertices on the installed
Windows runtime. In the legacy cat capture all 21,949 appended centers had wrong
Z coordinates (maximum error about 1.13e18); X/Y matched boundary-center averages.
The original FDG vertices were bounded and valid. Removing only the native-added
centers/fans and bypassing native fills restored a successful full-1536 export.
The anime's first decoder-fill centers were correct; bypassing all native fills
also removed its stray rods in the same-seed live generation. The exact native
compiler/kernel defect has not been established. Do not attribute it to a proven
1536 resolution limit, GPU hardware defect, or allocator pressure alone.

Proof: test/outputs/parts_quality_regression/hole_center_corruption.json.
Historical source captures and diagnostic logs remain under test; no production
job was modified. The reported production job was copied into test before analysis.

## Implementation

- inference/trellis/decoding.py preserves the raw FDG mesh and voxel attributes,
  bypassing upstream decode_latent's native hole fill. Generation uses this helper.
- inference/trellis/postprocess.py is the MIT-licensed local adaptation of the
  pinned upstream O-Voxel exporter. It bypasses native hole fills and permits an
  independent remeshing grid; original geometry/BVH and attribute grid still bake
  textures. Default is full source resolution, not a speculative density budget.
- Export retains the two-voxel UDF band, UV/authored-normal-preserving cleanup,
  bounded triangular/T-junction repairs and conservative planar micro-gap caps.
- Output-to-source support and sampled source-to-output surface-coverage gates
  reject rods and collapsed outputs before atomic publication. A failed attempt
  retries half the remeshing grid from the unchanged decoded source. Metadata
  records the actual remesh resolution. Repairs/gates use that actual grid.
- Native BVH distances can return zero on failed traversal. Coverage independently
  verifies returned triangle candidates with float64 CPU closest-point math.
- Individual-part requests explicitly select semantic_parts in all shared UI
  request paths. Availability gates check that strategy; explicit components is
  still supported and shown as geometry-only. Poor semantic coverage is visible.
- The verified frontend production build was installed into frontend/dist.

Raw non-remeshed export, coordinate scaling, occupancy pruning, cache handoff alone
and speculative lower-grid density budgets were unsuccessful diagnostics, not
adopted quality fixes. Do not resurrect them as validated alternatives.

## Final live evidence

Batch: test/outputs/generation_quality/native_fill_bypass_batch.json.
Requested settings: resolution1536, seed1719986157, polygon target2M, texture4096.

| Input | Effective/remesh grid | Faces | Open boundary edges | Unsupported vertices | Sample coverage |
|---|---:|---:|---:|---:|---:|
| ALICE_full_front_view | 1536 | 1,992,348 | 0 | 0 | 100% |
| cat | 1536 | 1,944,558 | 0 | 0 | 100% |
| house | 1152 | 1,913,274 | 0 | 0 | 100% |

The house was reduced by upstream's token limit. No export retry was needed.
Every model has UVs, authored normals and 4K textures. Source coverage samples
65,536 points per model; it is a bounded regression check, not an anatomical proof.
Nonmanifold contacts remain (anime9661, cat see report, house4087); zero boundaries
must not be described as proof of watertight manifold or CAD-solid geometry.

Real eight-view semantic extraction of the corrected anime produced 47 physical
parts and four regions, 0.004207 unlabelled ratio and 0.992237 cut consistency.
Full AssetReader verification passed, exact reassembly is true, each exported GLB
loaded with finite nonempty geometry, and original job/model/preview hashes stayed
unchanged. Result: test/outputs/parts_quality_regression/semantic/native_fill_bypass/.
The app also loaded the real assembled model and displayed colorized named parts.

- Backend/engine CPU: 1,004 passed, one skipped, four GPU tests deselected.
- Focused CPU regressions: 120 passed; stricter adapter-routing mocks: 21 passed.
- Final native GPU gap/topology regressions: four passed.
- Frontend unit tests: 548 passed; mock request contracts: 191 passed.
- TypeScript app/node checks, lint and isolated production build passed.
- Final protected pins: 39 checks passed.

Blender5.2.2 rendered front/back, textured/clay face closeups, cat and house.
Inspected renders: test/outputs/parts_quality_regression/final/native_fill_bypass/.
The long rods and punctures are absent. The anime eyes still look flattened and
blurry: this error exists in the predicted reconstruction, and topology cleanup
cannot recover the intended eye anatomy. Do not claim all deformation is fixed.
These inputs were SFW; no NSFW-specific validation was performed.

Hidden-surface completion of separated parts remains deferred by the user's prior
milestone decision. Semantic separation does not create a complete hidden body,
full eyeballs or closed standalone garments. Stored back/side/face images still do
not condition the current primary-only reconstruction. Auto Mesh Tuner stays paused.