I finished the rig_optimization step in `rig.py`, replacing the stub; pose-check results go into `ctx.store["pose_checks"]` for validation. All 12 tests in my rig suite pass (103 s). The framework's own rigged test also passes, but one framework test will now fail (see Requests, item 1).

## Files
**Edited (owned):**
- `E:/14-OCT-2026/Img3D/backend/blender_scripts/img3d_amt/rig.py` (about 1,430 lines, Python 3.11 syntax, numpy and bpy only)

**Created (owned):**
- `E:/14-OCT-2026/Img3D/test/mesh_tuner/rig/`: `conftest.py` (registers the `blender` marker), `rig_testlib.py`, `test_rig_blender.py`, `probe_rig_api.py`
- `E:/14-OCT-2026/Img3D/test/mesh_tuner/rig/blender_helpers/`:
  - `make_rig.py`: builds the synthetic bad and clean arm as .blend, .glb and .fbx.
  - `inspect_rig.py`: reads a model's skin without using the tuner code and renders posed views.
  - `selftest_rig.py`: 15 unit tests run inside Blender.
  - `perf_rig.py`: rigs a real TRELLIS mesh for a scale test.
  - `compare_weights.py`: compares two models' weights.
- Artifacts are in `E:/14-OCT-2026/Img3D/test/outputs/mesh_tuner/rig/` (`synthetic/`, `runs/`, `renders/`, `perf/`, `logs/`).

## What the step does
- **Skip cases:**
  - No armature: `skipped`, "No rig present (TRELLIS output is unrigged)".
  - Armature but no skinned mesh: a separate skip message.
  - `optimize_rig` false: "Rig optimization is disabled".
- **Which groups it touches:** only vertex groups named after a deform bone. All other groups are left exactly as they were.
- **Cleanup, per skinned mesh:**
  1. Remove weights below 0.001 and non-finite weights. Weights on glTF's `neutral_bone` (the joint Blender's exporter invents for unweighted vertices) count as no weight.
  2. Remove a vertex's influence on a far bone that none of its neighbours share. This is what catches the spike.
  3. Drop a vertex if the pose test shows it popping away from all its neighbours and its weights differ from theirs.
  4. Remove weight bleeding: small influences on a far bone that sit in a detached, weak patch of that bone's area.
  5. Keep the 4 strongest influences per vertex and normalize.
  6. Fill unweighted vertices from their neighbours across the mesh. Separate parts with no weights at all are attached rigidly to the nearest weighted vertex.
  7. Lightly smooth only the vertices that were repaired or filled.
  8. Remove empty groups that are not deform-bone groups and not used by a modifier, shape key, particle system or constraint. This happens last.
- **Pose checks:**
  - All pose channels are set to rest, bone constraints are muted and non-armature modifiers are hidden.
  - Up to 40 bones, chosen by weighted vertex count, are each rotated ±45° about local X and Z. Dense meshes get fewer bones.
  - Each rotation measures edge stretch (> 2x, < 0.5x) and collapsed triangles (area < 0.05 of rest).
  - The checks run on the original weights and again on the cleaned ones.
  - The pose is restored exactly afterwards and checked by re-evaluating the mesh. A keyed armature is also restored correctly.
- **Validation checks added:** `rig_weights`, `pose_stretch` (warn above 2.5x or more than 1 % bad edges, fail above 5x) and `pose_collapse`.
- **Metrics:** I set before/after values for `max_bone_influences`, `unnormalized_weight_verts` and `pose_max_stretch`. `armatures` still comes from the analysis.

## Testing
Command: `E:/14-OCT-2026/Img3D/backend/python/python.exe -s -m pytest -c E:/14-OCT-2026/Img3D/test/pytest.ini test/mesh_tuner/rig -q -p no:cacheprovider` gives **12 passed in 103 s**.
- **Synthetic bad rig through the full CLI**, with the geometry steps switched off:

  | Input | Influences | Unnormalized vertices | Worst pose stretch | Pose restored |
  |---|---|---|---|---|
  | .blend | 6 → 4 | 96 → 0 | 37.7x → 1.50x (same as the clean reference) | yes, error 0 |
  | .glb | 6 → 4 | none in the input (the exporter normalizes) | 37.7x → 1.50x | yes |
  | .fbx | 6 → 4 | 96 → 0 | 37.7x → 1.50x | yes |

  - **.blend:** 96 tiny weights removed, 60 unweighted vertices plus the spike vertex filled, and `extra_empty` and `ik_target` removed. `displace_ref` and `paint_mask` were kept unchanged.
  - **.glb:** the 60 vertices on `neutral_bone` were re-filled.
  - **Saved .blend output:** the 20° pose on the hand is identical to the input.
  - **Re-imported .glb outputs** (from both the .blend and .glb inputs): one armature, the same bones in the same rest positions, at most 4 influences, weights summing to 1, no tiny or unweighted weights, and the spike vertex no longer follows the finger.
- **Clean rig:** nothing is written, and the message is "weights already clean".
- **Real TRELLIS job `job_046e19f0ca62`:** the rig step is skipped in 0.002 s, adds no rig checks, and validation passes. The whole run took 26 s.
- **Visual check:** I looked at matcap and weight-colour renders (768 px, in `renders/`). Before cleanup the spike needle, the torn unweighted patch and the elbow dent are all visible. After cleanup it matches the clean reference. The test also checks this numerically on the images.
- **Scale test:** I rigged the 480k-vertex TRELLIS mesh with 16 bones and injected defects.
  - The step took 16.7–22.3 s: about 1 s to read weights, 10–13 s for the first pose pass, 1.3–2 s to clean and 5 s for the second pass.
  - Peak memory was 1.9 GB.
  - All 5 injected spikes and the 2,469-vertex unweighted patch were fixed.
  - The renders look clean. The pose check still correctly fails (162x stretch) because my quick test armature is crude; that is the test rig, not the cleanup.
- The module compiles under Blender 4.2's Python 3.11.

## Known limitations
- Locked vertex groups are not respected.
- Drivers on bone channels can override the test pose.
- Twist (rotation about the bone's own axis) is not tested.
- Very dense meshes get fewer test bones (about 10 at 1M vertices).
- Reading weights costs about 1 µs per vertex because Blender has no bulk API for it.
- The far-bone rules depend on where bones sit, so rigs with bones placed outside the mesh may be judged less well.
- Not run on Blender 5.1, which is not installed.

## Deviations from DESIGN.md
- `pose_max_stretch` is the worst edge's elongation in percent, so 2x = 100 %. DESIGN gives the unit but no formula.
- The rig step overrides `max_bone_influences` and `unnormalized_weight_verts` across all skinned meshes, measured at the start and end of the step. The analysis only looks at the primary mesh.
- Pose tests start from the rest pose, not the current pose. The current pose is restored exactly afterwards.
- I added cleanups beyond the brief: `neutral_bone` handling, pose-detected spike repair and weight-bleeding removal.
- The 0.001 threshold and other limits are settable only through optional `rig_*` options that do not exist in `TuningOptions` yet.

## Requests for other areas
1. **Framework tests:** `test_synthetic_end_to_end` in `test/mesh_tuner/framework/test_amt_framework_blender.py` expects the rig step to say "not implemented yet". It now says "No rig present (TRELLIS output is unrigged)", so that assertion needs updating.
2. **config.py:** please add optional fields `rig_min_weight` (0–0.1, default 0.001), `rig_max_influences` (1–8, default 4), `rig_smooth_iterations` (0–20, default 3), `rig_pose_checks` (bool, default true) and `rig_pose_bones` (0–200, default 40). `rig.py` picks them up automatically.
3. **pipeline.py:** rollback only snapshots the primary mesh. If the rig step fails after writing weights, other skinned meshes are not rolled back. The window is small because all weights are computed before any are written.
4. **previews.py (optional):** "before" and "after" previews are in rest pose, so rig fixes are invisible there. A posed preview for rigged models would show them.
5. **Frontend:** `pose_max_stretch` can be very large (for example 3671 % before cleanup), so the UI should format it accordingly.