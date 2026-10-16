# Critique: DESIGN.md backend feasibility and safety

Date: 2026-10-16. Lens: backend feasibility and safety. I checked the DESIGN.md claims against the code under
`backend/` and `test/`. I also ran in-memory numpy/scipy measurements on the two real TRELLIS.2 fixtures listed in
`test/part_aware/fixtures.json`:
- `test/outputs/engine_smoke/job_smoke165749/model.glb` (ALICE): 1,936,495 faces, 1,618,334 vertices;
- `job_smoke170427/model.glb` (anime): 1,870,532 faces.

The fixtures were only read. No file was written, and no job under `backend/runtime` was opened.

Summary: 1 blocker, 7 major, 10 minor.

---------------------------------------------------------------------------------------------------

1. **BLOCKER: on real meshes the inner sheet is in the same welded component as the outer sheet, so about half of
   all faces never get a label and physical cuts take only the outer sheet.**
   - **Evidence (DESIGN):**
     - §4.6.5 step 2 (L1443-1449) detects inner sheets only at component level: "C is the inner sheet of K".
     - Step 5.2 (L1470) fills "unvoted inner-sheet faces" only for those merged components.
     - Every other unvoted face goes to Dijkstra with cost ≤ 64 (L1471-1472).
     - §2.1 (L294-296) promises physical parts made of the "outer and inner sheet".
   - **Evidence (measured on ALICE):**
     - Weld, welded edges and `connected_components` give 389 components. Component 0 has 1,746,218 faces (90.2 %).
     - 74.1 % of component-0 faces have a face with an opposite normal (dot < −0.7) within 4/1536.
     - 72.2 % of component-0 faces have such a face **inside component 0 itself**.
     - So the double wall of the body is one component, joined at the rims of thin sheets. Only 343 small
       inner-sheet components (19,080 faces) match the 95 % rule.
   - **Consequence:**
     - The inner sheet of component 0 is about 45 % of F. It is never visible, so it gets no votes.
     - Graph distance from the rims is far beyond 64 hops (median edge 0.0018), so most of it stays `unknown`.
     - The hair, clothes and eye cuts therefore take only the outer sheet. The inner sheet stays on the base as an
       unlabelled "ghost" layer, with ragged seams.
     - `unlabelled_face_ratio` comes out near 0.4, not the 0.04 of the §3.11 example.
     - The S10 acceptance (L2540) does not check label coverage, so this would ship unnoticed.
   - **Fix:**
     - Add `parts/shells.py::pair_opposite_faces(centroids, normals, eps) -> partner: int32[F]` (−1 = none):
       - Build a `cKDTree` over all face centroids and query `k=16` within `shell_eps`.
       - The partner is the nearest face g with `dot(n_f, n_g) < −0.5` and `dot(c_g − c_f, n_f) < 0`.
       - Measured cost: tree build 0.38 s, query 0.14 s on 1.94 M faces.
     - Replace §4.6.5 step 5.2 with: every unvoted face whose partner is voted takes the partner's label, with
       confidence × 0.9. Set bit 0 of a new `face_flags.u8` buffer (§3.3) to mark it. Dijkstra (step 5.3) then runs
       only on what is left.
     - Add a partition rule (§4.6.5 step 6): a face and its partner always get the same `face_owner`.
     - Keep the component-level merge for detached inner-sheet components.
     - Tests and acceptance:
       - Add a rim-joined double-walled open hemisphere to `parts_fixtures.py`. Assert that the inner wall takes the
         outer label and that the cut part contains both walls.
       - Add to the S10 acceptance: `unlabelled_face_ratio ≤ 0.10` on both fixtures, and ≥ 90 % of every cut part's
         faces have their partner in the same part.

2. **MAJOR: the absolute face thresholds delete the classes the user asked for at the default polygon target.**
   - **Evidence:**
     - The default job `polygon_target` is 500,000 (`test/backend/test_health_system.py:61-69`; `/system` defaults).
       DESIGN sizes everything for 2 M faces.
     - §4.6.5 step 5.7 (L1476) merges labelled islands smaller than `min_part_faces` (200) into the surrounding label.
       This runs before the policy, so a 120-face eyelash band or a 180-face eyebrow island is erased. That
       contradicts "never silently dropped" (§2.4, L396).
     - At 500 k faces an eye area is about 550 faces, so eyes drift towards `too_small_for_geometry`.
     - Step 5.6 (L1475) snaps any component in which ≥ 90 % of the labelled faces share one label. On a robed
       character, component 0 (90 % of F) can be ≥ 90 % `clothing.dress`, which overwrites the face, eye and hand
       labels on the base.
   - **Fix:**
     - Step 5.7: use `min_island_faces = max(8, round(2e-5·F))` instead of `min_part_faces`. Never merge islands of
       the groups `eyes`, `eyebrows`, `eyelashes` or of any `face` region.
     - Step 5.6: never apply it to component 0, or to any component holding ≥ 25 % of F. Only fill `unknown` faces;
       never overwrite a voted label.
     - Make `PartAnalysisOptions.min_part_faces` default to `null`, meaning `max(50, round(1e-4·F))` (200 at 2 M
       faces). This is additive, since explicit values still work.
     - Add a 500 k-face case (a decimated fixture) to `test_parts_labels.py` asserting that brow and lash records
       survive.

3. **MAJOR: providers can import transformers or diffusers before TRELLIS has configured the Hugging Face
   environment.**
   - **Evidence:**
     - `backend/inference/trellis/environment.py:195-213`: `configure_hf_environment` "Must run before
       huggingface_hub/transformers import".
     - It runs only inside `prepare_runtime` at engine load (`environment.py:565`, called from `engine.py:199-201`).
     - When `preload_model=false`, or when the first action is a prompt, warm-up or segmentation, a provider imports
       transformers or diffusers first.
     - transformers then fixes `HF_MODULES_CACHE` at its default in the user's home. BiRefNet loads with
       `trust_remote_code=True` (`third_party/TRELLIS.2/trellis2/pipelines/rembg/BiRefNet.py:10-11`), so its module
       copy is written outside the project.
     - The engine's runtime report gains the warning "huggingface_hub was imported before HF_HOME was configured",
       which shows in `/system` engine details.
   - **Fix:**
     - In `core/part_aware_container.build_part_aware()`, before any provider can be imported, call
       `inference.trellis.environment.configure_hf_environment(settings.hf_home)` and `enable_hf_offline()`. Both are
       torch-free public functions; add them to the §1.4 rule-4 read-only allowlist.
     - `ProviderRegistry.provider()` asserts `os.environ["HF_HOME"] == str(settings.hf_home)` before
       `importlib.import_module`.
     - Test (`test_model_registry.py`, run in a subprocess): build the app, import a fake provider that imports
       `huggingface_hub`, then call `prepare_runtime` with a stub source. Assert that no "imported before HF_HOME"
       warning appears.

4. **MAJOR: "same-seed reconstructions are unchanged" (§4.9) relies only on reseeding. Nothing guards the global
   torch state that providers can leave changed on the shared gpu-worker thread.**
   - **Evidence:**
     - TRELLIS draws its noise with global-default-dtype CPU `torch.randn(...)` after `torch.manual_seed`
       (`generation.py:202`; `trellis2_image_to_3d.py:208,254,299,343,412`).
     - Any of these left set by a diffusers or transformers provider changes TRELLIS numerics or the RNG
       fingerprint, and survives `manual_seed`: `torch.set_default_dtype`, `torch.backends.cudnn.benchmark`,
       `cuda.matmul.allow_tf32`, `set_float32_matmul_precision`, `enable_*_sdp(False)`, or grad mode (thread-local,
       but the thread is shared).
     - `test_generation_parity.py` never runs an auxiliary task first.
   - **Fix:**
     - Add `inference/providers/vram.py::torch_globals_guard()`, a context manager that snapshots and restores:
       - `get_default_dtype()`;
       - `cudnn.benchmark/deterministic/allow_tf32`;
       - `cuda.matmul.allow_tf32`;
       - `get_float32_matmul_precision()`;
       - the flash, mem-efficient and math SDP enable flags;
       - `are_deterministic_algorithms_enabled()`;
       - `is_grad_enabled()`.
     - `RunWorker._gpu_phase` and the warm-up task wrap `phase.fn` in it, through `ctx.registry.guard()`.
     - Tests: `test_gpu_task_seam.py` uses a fake phase that flips every flag and asserts they are restored. The S10
       GPU smoke runs two same-seed jobs with an `sg_` and an `ig_` in between and asserts equal RNG fingerprints
       (the `test_generation_parity.py` hook).

5. **MAJOR: the 337 s Z-Image cold load runs on the single GPU thread and cannot be cancelled. Existing jobs that
   arrive meanwhile sit at `preprocessing` 0 %.**
   - **Evidence:**
     - §4.4.4 (L1144) puts `load_model` (30 s load plus 307 s first `.to("cuda")`) in the GPU phase. §5.1 says
       `load()` runs only on the GPU thread.
     - `.to("cuda")` has no cancellation point, which breaks the S-a contract "must honour its CancellationToken".
     - `GenerationWorker._process` (`generation_worker.py:316-338`) marks a job `running/preprocessing` ("Starting
       generation") **before** queuing `_execute_pipeline` on the FIFO executor. The job then shows 0 % for up to
       5.6 min, and the frozen worker cannot say why.
     - The same happens behind any long auxiliary phase, such as `label_lift` with `views=24`.
   - **Fix:**
     - Make `load_model` a CPU step: `cpu[load_model] → gpu[generate] → cpu[check, save]`.
       - `TextToImageProvider.load()` runs on `parts-cpu` and fully materializes the weights in RAM
         (`from_pretrained(..., low_cpu_mem_usage=False)`, or re-materialize every parameter). It must not touch
         CUDA.
       - The GPU phase moves components one at a time (text_encoder, transformer, vae) and checks the token between
         them.
       - `POST /models/{id}/load` likewise runs `load()` on the CPU and then queues a GPU `warm()`.
       - S4 acceptance: the first `.to("cuda")` after `load()` takes < 30 s (measured in `test_smoke_zimage.py`).
     - Add a "jobs first" gate in `RunWorker`. Before submitting any GPU phase, wait until
       `job_queue.pending == 0 and gpu.running_job_id is None`, with the message "Waiting for the GPU (generation
       queued)". Give up waiting after `run_max_defer_s` (new setting, default 600).
       - This needs `running_job_id` on `GpuTaskSubmitter` (it already exists on `GenerationWorker:96`) and
         read-only access to the job `JobQueue`.

6. **MAJOR: importing vendored See-through and Sapiens2 code through a "path shim" will hit name collisions.**
   - **Evidence:**
     - §5.6 (L2032-2034) imports the vendored code "through a path shim".
     - Img3D's own top-level packages have generic names: `models`, `utils`, `config`, `core`, `api`, `schemas`,
       `services`, `storage`, `processing`, `pipelines`, `inference` and the new `parts`. They sit first on
       `sys.path` (`backend/main.py:26-35`).
     - `ensure_source_on_path` puts the TRELLIS.2 root at `sys.path[0]` (`environment.py:188-192`). That root exposes
       the namespace packages `configs`, `assets` and `data_toolkit` (`backend/third_party/TRELLIS.2/`).
     - A vendored `import utils`, `from models import …` or `import configs` therefore binds to the wrong package.
     - Prepending the vendored root can also shadow Img3D modules that have not been imported yet.
   - **Fix:**
     - Never insert the vendored root into `sys.path`. Load it as a private package with
       `importlib.util.spec_from_file_location("img3d_vendor.see_through", root/"__init__.py",
       submodule_search_locations=[root])`, or rewrite its absolute imports in the vendoring commit.
     - The S6 acceptance adds a test: after a provider import, `sys.path` is unchanged, and
       `sys.modules["models"].__file__` / `sys.modules["utils"].__file__` still point under `backend/`.
     - The registry reports `incompatible` when the vendored tree has a top-level module whose name matches an Img3D
       package.

7. **MAJOR: the GPU acceptance runs are not serialized, and "a real completed job" is not pinned to the safe
   fixtures.**
   - **Evidence:**
     - Phase B runs S4, S5, S6 and S7 in parallel, each with real-weight smokes. Peaks: Z-Image 23.3 GB,
       TRELLIS 16.8–17.8 GB (`test/part_aware/fixtures.json`). The GPU has 32 GB.
     - The repo already has `test/part_aware/gpu_lock.py` for exactly this case: its docstring says "Every command
       that loads real weights onto the GPU … must run under this lock". DESIGN never mentions it.
     - The S7 and S10 acceptances (L2537, L2540) say "a real completed … job" without naming one. That invites
       opening `backend/runtime/jobs`, which includes the excluded `job_269a864ac597` and `job_fa0c4771ec03`
       (HANDOFF.md:51).
   - **Fix:**
     - Wrap every GPU command in §9.3, §10.2 (the smokes, `test/engine/test_raster_lifter.py`), §11 S4–S7, S10 and
       S11 as `backend\python\python.exe -s test\part_aware\gpu_lock.py --label <slice> -- <cmd>`.
     - Or give the GPU tests a session fixture that calls `gpu_lock.gpu_lock(label=…)`.
     - Name the S7 and S10 inputs: the `alice_front` and `anime_alice` fixtures of `test/part_aware/fixtures.json`,
       **copied** into a `test/.runtime/<case>` runtime (a job folder plus a synthesized `job.json`). They must never
       be read from `backend/runtime/jobs`.
     - Add the two excluded ids to every smoke script as a hard refusal.

8. **MAJOR: S8 is scheduled in Phase B, but it depends on S11.**
   - **Evidence:**
     - §7.2 (L2347-2349) has S8 delete `workflows/` and skip "runs referenced by an active workflow".
     - `storage/workflow_store.py` and the workflow index are created only in S11 (§1.1, L140; §11 L2541).
     - S8 is listed as "Depends on S1" in Phase B (L2538).
   - **Fix (pick one):**
     - Move S8 to Phase C after S11.
     - Or let S1 create `storage/workflow_store.py` as a frozen-API stub, owned by S11 afterwards:
       `WorkflowStore.list_terminal() -> list[WorkflowRecord]`, `delete(wf_id) -> bool`,
       `active_child_ids() -> frozenset[str]`. S8 then codes against that API, and `test_run_retention.py` uses a fake.

9. **MINOR: the `_FEATURE_ID_0` UNSIGNED_SHORT vertex attribute breaks the glTF vertex alignment rule.**
   - **Evidence:**
     - §3.12 (L848, L851) writes tightly packed `UNSIGNED_SHORT` and aligns only the buffer views.
     - glTF 2.0 §3.6.2.4 requires every vertex-attribute element to be 4-byte aligned. Tightly packed u16 has a
       2-byte stride, so glTF-Validator reports `MESH_PRIMITIVE_ACCESSOR_UNALIGNED`. three.js and trimesh are lenient.
   - **Fix:**
     - Write `_FEATURE_ID_0` into its own buffer view with `byteStride: 4` (2 bytes of padding per vertex), keeping
       componentType 5123.
     - `test_gltf_io.py` asserts `byteStride == 4` for that view and `accessor.byteOffset % 4 == 0`.

10. **MINOR: region membership is ambiguous for deferred `component_###` and overflow parts.**
    - **Evidence:**
      - With the k=64 rule, ALICE has 45 separate non-inner shells (20 under 200 faces) and the anime fixture has 82
        (46 under 200).
      - §2.4 rule 2 (L401-408) makes each unknown shell a `component_###` part. Shells under 200 faces become
        `physical_deferred` records with region value 0 on `body`.
      - `AssetReader.region_faces` (§3.13) selects by (value, host), so it returns **all** unknown base faces for
        every such record.
      - Rule 3 (L409-410) merges overflow parts "as `unknown` regions", which drops their class.
    - **Fix:**
      - Unknown shells below `max(min_part_faces, 0.0005·F)` faces get no part record. They stay on the base and
        are listed in `parts[body].geometry.floaters = [{component, faces, centroid}]`, plus one
        `floater_candidates` warning.
      - Add a `face_part.u16` buffer: the index into `parts[]` of the finest record covering each face, 0xFFFF for
        none. `region_faces(id) = flatnonzero(face_part == i)`. Validate it in `validate_staging`.
      - Overflow merges keep their class value as a `region`.

11. **MINOR: the watchdog and `stop()` can clean up while the GPU callable is still running.**
    - **Evidence:**
      - §4.3 steps 5-6 (L1045-1047): watchdog → `failed`, then `safe_rmtree(work_dir)` in `finally`.
      - Cancelling the awaited `asyncio.wrap_future` does not stop a running `concurrent.futures` task.
      - The watchdog also counts the time a `pa_` spends waiting between phases while TRELLIS jobs run (5 queued
        jobs × 330 s = 27.5 min ≥ 1800 s), which gives spurious `run_timeout`.
    - **Fix:**
      - On timeout or stop, call `token.cancel(reason)`, then `await asyncio.shield(gpu_future)`, and only then set
        the final status and remove `work_dir`.
      - Count the watchdog over active phase time only.
      - `test_run_framework.py`: a GPU fake that ignores the token for 2 s must not have its `work_dir` deleted
        before it returns.

12. **MINOR: Windows file races in RunStore and publish.**
    - **Evidence:**
      - "RunStore.save refuses to recreate" (L1793, L2350) is check-then-write. Retention `safe_rmtree`
        (`job_store.py:112-118`) can run in between.
      - Publish calls a bare `os.replace(staging_dir, target_dir)` (L911). A directory rename on Windows fails with
        `PermissionError` while Defender or the indexer holds a file inside. The codebase already has `retry_os`
        for this (`utils/files.py:18-60`).
    - **Fix:**
      - `RunStore.save` never creates `jobs/<job>/` itself. It runs `mkdir(parents=False, exist_ok=True)` for
        `parts/` and `<pa>/` under an existing job directory, so a concurrent delete raises `FileNotFoundError` →
        `job_deleted`.
      - Publish uses `retry_os(lambda: os.replace(staging_dir, target_dir))`.

13. **MINOR: Z-Image with several candidates has no VRAM rule.**
    - **Evidence:**
      - `num_images` is 1..4 (§4.4.1), and "each candidate uses its own generator" (L1145) reads as one batched call
        with a generator list.
      - The live check (§4.8 rule 2) uses the single-image peak of 23.3 GB. The VAE decode of 4 × 896×1152 can
        push past 32 GB.
    - **Fix:** `ZImageProvider.generate` loops over the candidates with batch size 1. It checks the token between
      candidates and calls `torch.cuda.empty_cache()` after each VAE decode. State this in §4.4.4 and §5.6.

14. **MINOR: Grounding DINO's 256-token text limit is ignored.**
    - **Evidence:**
      - `Detector2D.detect(..., phrases: Mapping[str, tuple[str, ...]])` (L1890) receives the whole taxonomy alias
        vocabulary (§2.3).
      - grounding-dino-base truncates text at `max_text_len = 256`, so classes past the cut are silently never
        detected.
      - The probe used only 9 phrases (`test/part_aware/probes/seg_stack_probe.py:27,58`).
    - **Fix:**
      - `inference/segmentation/gdino.py` packs the phrases into batches of ≤ 200 tokenizer tokens, with the
        `". "` separators counted, and runs one forward pass per batch.
      - `test_smoke_segmentation.py` logs the batch count per image and asserts that every requested class id
        reached the detector.

15. **MINOR: the camera-fit inputs are under-specified.**
    - **Evidence:**
      - §4.6.5 step 3 (L1451-1456) varies the FOV over {10, 30, 50}° with no distance rule. A fixed distance clips
        the object at 10° and shrinks it at 50°.
      - For `preprocess = "untouched"` jobs (`generation.py:166-170`), the preview keeps its real background, so
        `preview_luma > 8` (§4.6.3, L1414) marks the whole frame as foreground.
    - **Fix:**
      - Use `distance = 1.05·r_bs / sin(fov/2)`, where `r_bs` is the bounding-sphere radius of `model.glb` positions.
      - For `untouched` jobs, foreground = `labels > 0` only, and the fit also solves a 2D scale and translation. If
        it is not accepted: `camera_fit_low_iou` → `partial`.

16. **MINOR: the `reused` segmentation path can never match.**
    - **Evidence:**
      - §4.6.3 (L1409-1410) compares the `sg_` source sha256 (the snapshot of the **original** file, §4.5.1) with
        `inputs/<primary>.png`.
      - That file is re-encoded by `normalize_to_png` (`job_service.py:146-163`), so the hashes differ for every
        JPEG, WebP or EXIF-rotated input.
    - **Fix:** snapshot `sg_` reference sources with the same `processing.image_ops.normalize_to_png` (a read-only use
      of the protected module). Then compare a sha256 of the decoded RGBA pixel bytes, not of the file bytes.

17. **MINOR: three code-level defects in specified snippets and rules.**
    - **(a) The weld snippet (L1439).**
      - `positions.view(np.void(12))` passes a void *scalar*, not a dtype.
      - On numpy 2.4.1, `np.unique(..., return_inverse=True)` returns the inverse with shape `(V, 1)` (measured:
        `(1618334, 1)`), which silently turns `weld_map[faces]` into `(F, 3, 1)`.
      - Fix: `np.unique(np.ascontiguousarray(p).view(np.dtype((np.void, 12))).ravel(), return_inverse=True)[1]
        .reshape(-1)`.
    - **(b) Provider imports (§1.4 rule 2, L273).** They map only `ImportError` to `UnavailableProvider`, but a broken
      CUDA DLL raises `OSError` (`inference/device.py:42-43`). Fix: catch `Exception` and record the reason.
    - **(c) `IMG3D_APPROVED_MODELS` (L2381).** Comma or semicolon values crash `Settings()`, because
      pydantic-settings JSON-decodes list fields. Fix: declare `approved_models: Annotated[list[str] | None,
      NoDecode]` with a `mode="before"` validator, as `cors_origins` does (`config/settings.py:65`). Treat an empty
      string as `None`.

18. **MINOR: one `test_features_api.py` assertion contradicts the G2 opt-in.**
    - **Evidence:**
      - §10.2 (L2492) asserts "`mesh_tuning.enabled` is false by default".
      - §8.2 G2 adds `os.environ.setdefault("IMG3D_MESH_TUNING_ENABLED", "true")` to `test/conftest.py`, so
        `make_settings()` sees `true` (`test/backend/conftest.py:60-75`).
    - **Fix:** that test passes `mesh_tuning_enabled=False` explicitly, or uses `monkeypatch.delenv`, as
      `test_settings.py:18-25` does. Add a `test_settings`-style assertion that `Settings(_env_file=None)
      .mesh_tuning_enabled is False` under `clean_env`.

---------------------------------------------------------------------------------------------------
## Verified as correct (no change needed)

**Code references.** I checked every cited [V] line against the code, and they are right:
- `generation_worker.py:85,96-102,124-128`; `lifecycle.py:44,60,84,105`; `job_service.py:60`;
  `websocket_hub.py:229`; `mesh_tuner_service.py:97`;
- `mesh_stats.py:72-97` (sums nodes, so it is multi-node safe); `deps.py:88-101`; `utils/ids.py:39`;
  `job_store.py:112-118`; `retention_service.py:60-77`;
- `device.py:30,98`; `engine.py:312-326`; `generation.py:202`; upstream `preprocess_image` L127-162;
- the test pins `test_progress.py:56-67`, `test_health_system.py:37,54,61`, `test_engine_unit.py:81`, and
  `test_mesh_tuner_api.py:57,404,460,522`.

**Tuning gate and tests.** The G1/G2 tuning gate holds, because pydantic-settings reads the environment even with
`_env_file=None`. No existing test pins the `ReferenceImageOut` key set, so adding `source` is safe.

**Real `model.glb` structure.** The real file has 1 mesh, 1 primitive, `POSITION/NORMAL/TEXCOORD_0`, `UNSIGNED_INT`
indices, two `EXT_texture_webp` images, no samplers and no compression, so `read_base_glb` is feasible. Its texture
entries have no `source`, so the writer must copy them, together with `extensionsRequired`, verbatim.

**CPU budget.** Measured on 1.94 M faces:

| Step | Time |
|---|---|
| Weld | 0.36 s |
| Welded edges (7 boundary, 16,011 non-manifold) | 1.0 s |
| Components | 0.1 s |
| `cKDTree` shell queries | 0.4–0.5 s |
| Multi-source Dijkstra (limit 64) | 0.25 s |

So the S3a target of "2 M faces < 60 s" is easily feasible with numpy and scipy only.

**GPU scheduling.** Sequencing on the single `gpu-worker` is sound for overlap and VRAM: there is one executor,
`is_busy` counts every future, `low_vram=True` releases TRELLIS after each run, and the seg stack (2.1 GB), the lifter
and Z-Image (23.3 GB) never co-reside. Issues 4, 5 and 11 above are the remaining gaps.
