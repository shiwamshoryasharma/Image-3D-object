# Auto Mesh Tuner framework: engineering report

The Blender-side framework is built and passes all 59 tests in 99.5 s (the full suite including the two real-job runs, on the latest code). The non-real-job subset (57 tests) passed again after the final formatting edits. With the stub steps, it runs end to end on synthetic meshes and on real TRELLIS jobs. I looked at the before/after renders myself, and the analysis numbers match the earlier research probe exactly.

## Files

**Created in `E:/14-OCT-2026/Img3D/backend/blender_scripts/`:**
- `auto_mesh_tuner.py` (entry script)
- `img3d_amt/`: `__init__`, `protocol`, `config`, `context`, `io_mesh`, `meshdata`, `analysis`, `pipeline`, `cli`, `report`, `previews`, `validation`
- Stub steps `repair.py`, `face.py`, `topology.py`, `rig.py`. Each returns `StepOutcome("skipped", "not implemented yet", {})`.

**Tests in `E:/14-OCT-2026/Img3D/test/mesh_tuner/framework/`:**
- `conftest.py` registers the `blender` marker and holds the fixtures.
- `amt_testlib.py` runs Blender subprocesses, parses events and GLB JSON.
- `test_amt_config_protocol.py` has 35 pure-Python tests.
- `test_amt_framework_blender.py` has 24 tests.
- `blender_helpers/`:
  - `make_synthetic.py` builds the defect meshes.
  - `selftest_meshdata.py` runs 15 checks inside Blender.
  - `inspect_glb.py` re-imports a model and reports what Blender sees.
  - `run_with_injected_steps.py` swaps in failing steps to test the failure policy.

**Artifacts** are in `E:/14-OCT-2026/Img3D/test/outputs/mesh_tuner/framework/`: `runs/*`, `logs/*.log`, `synthetic/`, `real_jobs_summary.json`.

## Public API

- **Entry script:**
  - Flushes stdout line by line and puts its own folder first on `sys.path`.
  - On Blender older than 5.1 it emits `hello`, then `error` with code `blender_incompatible`, and exits 2.
  - Otherwise it runs `sys.exit(img3d_amt.cli.main(argv))`.
- **`protocol`:**
  - `emit(event, **fields)` writes `@@AMT@@` plus compact ASCII JSON and flushes; it never raises, and NaN/inf and numpy values are converted to JSON-safe values.
  - Typed helpers: `hello`, `plan`, `step`, `progress`, `log`, `result`, `error`.
  - `install_logging()` turns `logging.getLogger("img3d_amt.*")` records into `log` events.
- **`config`:**
  - `load_config(argv)` accepts `--config <cfg.json>` or `--input/--output-dir/--options/--log-level`.
  - `TuningOptions.from_dict()` applies the preset first, then explicit fields; a `null` field keeps the preset value.
  - Unknown keys and out-of-range values are warnings (values are clamped); wrong types raise `ConfigError` (exit 2).
- **`context`:**
  - `StepOutcome(status, message, details)`; `StepError(code, message, details)` exits 4; `InputError` exits 3.
  - `STEP_SPECS` holds the 8 steps with their weights and required/rollback flags.
  - `TunerContext` provides:
    - **Mesh access:** `require_primary()`, `primary`, `mesh`, `mesh_objects`, `armatures`.
    - **Original snapshot:** `original` (hidden copy of the loaded mesh), `original_geometry()` (positions plus triangles), `original_bvh()`.
    - **Size reference:** `diag`, `bbox_min`, `bbox_max`, `update_bounds()`.
    - **Shared state:** `options`, `store`, `set_metric(key, before=, after=)`.
    - **Reporting:** `progress(fraction, message)` (throttled to about 4 events/s), `log`, `warn`, `timer(name)`, `memory()`.
    - **Environment:** `gpu_available` (probe uses `gpu.init()` and falls back to CPU), `temp_dir`.
- **`meshdata`** (documented shapes and dtypes):
  - **Read/write:** positions, edges, loop vertices/edges, polygon loops, triangles and loop triangles, face areas/normals/centres, vertex normals, UVs.
  - **Topology:** the `EdgeFaces` edge-to-faces map with boundary/manifold/non-manifold masks and `manifold_pairs`; `edge_faces_from_triangles` for pure-numpy arrays; inconsistent-winding edges.
  - **Graphs:** CSR vertex/face/vertex-to-face adjacency, `connected_components`, `face_components`, `boundary_loops` and `order_loop_vertices`.
  - **Mesh operations:** `weld_by_distance`, `snapshot_mesh`/`restore_mesh`/`discard_mesh`, `clear_custom_normals`.
  - **Queries and smoothing:** BVH from arrays or mesh, `nearest_on_surface`, `sample_surface`, `laplacian_fair`, `taubin`.
  - **Attributes and images:** `get_attribute`/`set_attribute`/`remove_attributes`; base-colour image lookup, `image_pixels`, `sample_image`.
- **Analysis and validation:**
  - `analysis.analyze(ctx, obj)` returns the DESIGN §3 metric keys, with details in `ctx.store["analysis"]`.
  - `validation.run(ctx)` computes deviation against the original in both directions (the reverse direction uses the visible-face mask when the repair step provides one), plus the checks and the pose checks the rig step provides.
- **Previews:** `previews.render_previews` writes 768 px Workbench renders, before and after, and restores the scene state afterwards. Views are front, three_quarter, side and back with matcap and cavity, plus face (matcap) and face_texture for upright humanoids.
- **`io_mesh`:**
  - Imports glb/gltf/obj/fbx/ply/stl/blend, then welds duplicate vertices itself.
  - Exports GLB and compares the output JSON with the input; material `alphaMode`, `alphaCutoff` and `doubleSided` are patched back if they changed.
  - Optionally saves a `.blend`.
- **`pipeline` / `cli` / `report`:** run the 8 steps with protocol events, roll back optional steps on failure, write `report.json`, emit `result`, and return exit codes 0/2/3/4/86.

## How I tested

- Full suite: `E:/14-OCT-2026/Img3D/backend/python/python.exe -s -m pytest -c E:/14-OCT-2026/Img3D/test/pytest.ini E:/14-OCT-2026/Img3D/test/mesh_tuner/framework -q -p no:cacheprovider` gave **59 passed** in 99.5 s. Blender runs used an isolated `BLENDER_USER_RESOURCES` and TEMP under the outputs folder.
- Python 3.11 syntax: all 17 files compile with the Blender 4.2 Python. This is also a test.
- What the tests cover:
  - Synthetic icosphere with 3 holes, a flipped patch, split duplicate vertices, 3 islands and 3 zero-area faces: exit 0; events in order with progress never going backwards; report schema valid.
  - Metrics on that mesh are exact: holes 3, small parts 6, degenerate faces 3, flipped-normal edges 24.
  - The tuned GLB re-imports with the same faces, UV layers, materials, images and backface culling; no `amt_*` attributes remain; 8 PNGs at 768 px; `.blend` written.
  - Other inputs: multi-object input is joined, a rigged input keeps its skin (2 bones, max 2 influences), OBJ and STL work.
  - Material round trip: a MASK material came back with a float32-drifted cutoff and was patched to 0.3; BLEND survived natively.
  - Failure policy, with test-double steps:
    - A failed optional step rolls back geometry, modifiers, stored data and metrics.
    - A crash in an optional step is reported as `internal_error` and the run continues.
    - A failed required step exits 4 and writes a partial report.
    - A step returning the wrong type is caught, and a step left in Edit mode is recovered.
  - Bad config exits 2 (bad JSON, missing arguments, wrong option type, unknown preset, path in output name, wrong schema). Missing, corrupt or unsupported input exits 3. Blender 4.2 exits 2 with "requires Blender 5.1".
- **Real jobs** (stub steps, so deviation should be about 0):

| | job_04535ae38930 (78 MB, 1.89M faces) | job_046e19f0ca62 (39 MB, 0.99M faces) |
|---|---|---|
| Wall time | 24.4 s | 14.4 s |
| Load (import + weld) | 7.1 s (3.2 + 3.7) | 3.7 s |
| Analysis | 3.2 s | 1.6 s |
| Validation | 6.8 s | 3.9 s |
| Export (incl. 12 previews) | 4.8 s (previews 1.8 s) | 2.9 s |
| Peak memory (working set / private) | 2.9 / 3.2 GB | 1.7 / 2.0 GB |
| Deviation p95 / max (% diag) | 1e-6 / 1.9e-5 | 1e-6 / 5e-6 |

  - Round trip is faithful on both jobs: alphaMode/doubleSided match, the WebP images are byte-identical, texture counts match, and `EXT_texture_webp` stays required.
  - Metrics for 04535 match the research probe exactly: 907,500 vertices, non-manifold edges 33,076, holes 191 (largest 112 edges), parts 259 (252 small), flipped-normal edges 160, symmetry 0.056%.
- **Visual check:** I looked at the before/after renders: front, three_quarter, side, back, face matcap and face texture for 046e and 04535, and the synthetic sphere. Framing is correct, holes, islands and the flipped patch are visible, and the face close-up shows crown to neck.

## Known limitations

- Previews and the GPU probe need a working OpenGL context. Preview failure is only a warning; a hard driver crash cannot be caught. Setting `IMG3D_AMT_DISABLE_GPU=1` skips the probe.
- The face view uses a bbox heuristic, so non-humanoids get no face view (fox and castle are correctly excluded).
- Rigged inputs are not joined and their transforms are not applied; the steps only process the primary mesh.
- Deviation is measured on a sample: up to 150k vertices plus 50k surface points. Loose vertices are excluded, because TRELLIS meshes have a few that otherwise show up as about 0.1% false deviation.
- The weight statistics loop over vertices in Python, which is slow, but only runs when there is a rig.
- `__pycache__` folders for Python 3.13 and 3.11 appear under `img3d_amt` (gitignored).

## Deviations from DESIGN.md (I did not edit it)

1. **Preview entries**
   - The set adds a `back` view and a `face_texture` view.
   - Entries carry an extra `shading` key (`matcap` or `texture`); `variant` is `before` or `after`.
   - Names follow `<variant>_<view>.png`.
2. **Output names are relative:** `result` and `report.outputs` give `model`, `report` and `blend` as file names relative to `output_dir`, because the backend moves the folder.
3. **Additive keys**
   - `hello`: `gpu_renderer`.
   - `result`: `duration_s`, `peak_memory_mb`.
   - `report.json`: `status` (`completed`, `completed_with_warnings` or `failed`), `context`, `timings`, `analysis` details, `error` on failure, `steps[].memory_mb`, and more `environment` fields.
   - Optional `log_level` (`cfg.log_level` or `--log-level`).
4. **Analyze and validation failures do not abort the run.** DESIGN only names load/repair/export as fatal; these two are read-only steps, so they are marked failed with a warning.
5. **`duplicate_vertices` before** is the number of duplicates in the input (welded on load) plus any remaining. Otherwise it would always read 0.
6. **Multi-mesh inputs:** unrigged inputs with several meshes are joined into the largest, and object transforms are baked into the mesh (world positions stay the same).
7. **glTF import** also passes `disable_bone_shape=True`; otherwise the importer adds a bone-shape helper mesh that would be exported.
8. **`.blend`** is written only when `options.save_blend` is set. The name comes from `outputs.blend`, defaulting to `tuned.blend`. A save failure is only a warning.
9. **Validation thresholds:**
   - Bounding-box change: pass up to 1%, warn up to 5%, fail above 5%.
   - A failed check makes the run `completed_with_warnings` with `validation.passed=false`; the exit code is still 0.
   - The symmetry metric is marked `neutral`.
10. **Error codes used:**
    - Exit 2: `bad_config`, `bad_arguments`, `invalid_options`, `unsupported_schema`, `config_not_found`, `path_too_long`, `output_dir_unwritable`, `blender_incompatible`.
    - Exit 3: `input_not_found`, `input_unreadable`, `unsupported_format`, `no_mesh`.
    - Exit 4: `export_failed`, `internal_error`, `out_of_memory`, `bad_step_result`, `report_write_failed`.

## Requests for other areas

- **Step agents (repair, face, topology, rig):**
  - Implement `run(ctx) -> StepOutcome`, report progress with `ctx.progress`, and never modify `ctx.original`.
  - Call `meshdata.clear_custom_normals` after moving vertices, otherwise the imported custom normals shade stale.
  - Put cross-step data in `amt_*` attributes; they are stripped before export.
- **Repair agent:**
  - Set `ctx.store["original_visible_face_mask"]` as a boolean array over the original's faces, using the same row order as `ctx.original_geometry()[1]`.
  - Report `ctx.set_metric("hidden_faces"/"inner_shell_faces", before=, after=)`.
- **Rig agent:**
  - Put check dicts `{id, label, status: pass|warn|fail, value, threshold, message}` in `ctx.store["pose_checks"]`.
  - Report `ctx.set_metric("pose_max_stretch", ...)`.
- **Backend:** set `BLENDER_USER_RESOURCES` for every run (Blender creates an `extensions` folder there). Treat `result.model`/`report`/`blend` as names relative to the output folder; previews are at `previews/<name>`.
- **Frontend:** handle the `back` and `face_texture` views and the `shading` key.
- **DESIGN owner:** record the deviations above.