# RESUME — part-aware milestone (read THIS first; ~5 min instead of reading code + 6k lines of design)

Last update: 2026-10-16 ~23:20 (third pause, user's token budget). Everything below is the verified state.

## 0. Rules that cost tokens/trust when forgotten
| Rule | Detail |
|---|---|
| **Lean mode** (user, explicit) | One implementer agent per slice; NO separate integrator / reviewer / checker / fixer agents; the main session runs tests and fixes small things itself; ONE adversarial review at the very end. Agent prompts: short, point to doc sections (`Grep "^### 4.6"`), never "read DESIGN.md fully". |
| **Don't re-read big docs** | `DESIGN.md` (3k lines) and `UI_DESIGN.md` (2.9k lines) are reference only: use the section index in §5 below + Grep. Never read whole files in the main session. |
| Tuning is OFF | No Auto Mesh Tuner / eye / rig / refinement / Blender work until the user approves the milestone. Backend gate done (S9). Default UI keeps the Auto Tune card + Blender pill **visible but LOCKED** (user spec "preserve the existing UI"; UI_DESIGN §3.9). |
| User spec is binding | The verbatim milestone request = `USER_REQUEST` in `workflows/design_workflow.js`. Before any design choice, check it literally (e.g. "preserve existing UI", "do not delete working functionality"). |
| Test locations | Tests, scripts, caches, configs, logs ONLY under `test\`; outputs `test\outputs\part_aware\`; real-weight runs use `test\images\*` or the fixtures in `test\part_aware\fixtures.json` (copy before writing). Never open `backend\runtime\jobs` (never `job_269a864ac597`, `job_fa0c4771ec03`). |
| GPU / build locks | Real weights on GPU: `backend\python\python.exe -s test\part_aware\gpu_lock.py --label X -- <cmd>`. Frontend tsc/vite/oxlint by parallel agents: `... gpu_lock.py --lock frontend-build --label X -- <cmd>`; private builds to `test\.runtime\frontend\<slice>\dist`, never `frontend\dist`. |
| Python / installs | Only `backend\python\python.exe -s`; installs only with `-c backend\constraints.txt --only-binary ":all:"` (never upgrade transformers 4.57.6 / huggingface-hub 0.36.2 / diffusers 0.39.0). HF public calls need `token=False`. |
| Git | Author `Shiwam Shorya Sharma <110902859+shiwamshoryasharma@users.noreply.github.com>`, **no Co-Authored-By trailer**, push to `main` only when the user asks; run the privacy scan of `img3d-repo-publishing` first. |

## 1. Slice status (backend = DESIGN §11, frontend = UI_DESIGN §11)
| Slice | What | Status | Report |
|---|---|---|---|
| S0 | api-contract.md §11, protected-file hashes, legacy-invariance tests | ✅ done | progress/S0.md |
| S1 | frozen interfaces (schemas/models/parts types/taxonomy/manifest/provider ABCs/workflow catalog) + plumbing (settings, GPU seam, container, run framework, RunWorker, stub services) | ✅ done, integrated | progress/S1a.md, S1b.md, S1_integrate.md |
| S2 | model registry, catalog, VRAM guard, `/features`, `/models` | ✅ done | progress/S2.md |
| S3a | parts geometry core: gltf_io, weld, components, shells, **pair_opposite_faces**, partition, seams, reassembly | ✅ done (real ALICE: 7.1 s, partner coverage 75.5 %) | progress/S3a.md |
| S3b | labels, policy, relations, frames, buffers, export, validate, publish, reader, consumers, lineage | ⚠️ **interrupted** — some files exist (`parts/{labels,policy,relations,frames,buffers}.py`), others missing (`export, validate, publish, reader, consumers, lineage`), tests not written | none |
| S4 | prompt-to-image (Z-Image), prompt templates, readiness, provenance, requirements pins | ⚠️ report says done (Z-Image smoke passed: first `.to("cuda")` 2.3 s, warm 3.6 s, 21.7 GB; TRELLIS smoke OK) but the agent was stopped before returning → re-verify its tests | progress/S4.md |
| S5 | segmentation (GDINO + SAM 2.1 + Florence-2), conditioning replica, seg2d fusion/eyes | ⚠️ **interrupted** — files exist, tests exist, real smoke result unknown | none |
| S6 | vendored See-through + Sapiens2 (git submodules) | ⏳ not started (no submodule added) | – |
| S7 | raster lifter + cameras (nvdiffrast) | ⚠️ **interrupted** — files + tests exist, camera-fit IoU not recorded | none |
| S8 | run retention | ✅ done | progress/S8.md |
| S9 | tuning code gate + conftest opt-in | ✅ done | progress/S9.md |
| S10 | part-analysis runs/API + `run_parts_smoke.py` | ⏳ not started (needs S3b, S5, S7) | – |
| S11 | workflows compiler/service/store/API | ⏳ not started (needs S4, S5, S10) | – |
| F0–F4 | test infra/mock/fixtures/probe, API layer, pure libs, workflow engine, tokens + primitives | ✅ done | progress/F0-F4.md, F_waveA.md |
| F5 | stores, actions, hooks, session | ✅ done | progress/F5.md |
| F6 | app root, mode switch, App settings, Default wiring, tuner lock | ⏳ interrupted before writing files | – |
| F7 | feature content (prompt/parts/export panels) | ⚠️ interrupted: its 2 unfinished files parked in `wip/F7/` (featureBundle.ts, lazyFeatures.ts referenced panels that don't exist yet) | – |
| F8 | viewer parts (highlight/isolate/explode) | ⏳ interrupted before writing files | – |
| F9a, F9, F10, F11, F12, F13 | shared shell chrome, Mobile, Tablet, Node + React Flow, Default polish, verification | ⏳ not started | – |

Gates at this point (all green): backend `test\backend test\aux_models` **581 passed, 4 skipped**; frontend `tsc -b`
0 errors, oxlint clean, compiler_check 0 events, unit tests **515/515**, mock smoke 198/198, tuner mock smoke 36/36;
protected backend files 32/32 ok, protected tuner frontend files 27/27.

## 2. Next steps (exact order, lean)
1. Re-check gates (commands in §4). For S3b/S5/S7 read only `git status` of their owned files (DESIGN §11 row) + their
   tests; do NOT read the design in full.
2. One workflow, one agent per slice, in parallel: **S3b** (complete), **S5** (complete + real smoke under gpu_lock),
   **S7** (complete + IoU on both fixtures), **S4** (verify only: run its tests, fix if red), **S6** (vendoring),
   **F6 ‖ F7 ‖ F8** (F7 restores `wip/F7/*` into `frontend/src/components/` once its panels exist). Tell each agent:
   "files from a stopped run may exist: complete them, don't rewrite".
3. Then **S10 → S11**, and **F9a → F9 ‖ F10 ‖ F11 ‖ F12**, then F13.
4. Main session: run gates after each wave; then ONE adversarial review (workflow with 2–3 reviewers max), fixes,
   real GPU validation: `test\part_aware\run_parts_smoke.py` on `test\images\ALICE\ALICE_full_front_view.png` and
   `test\images\ANIME_CHARACTER_01\Alice.png` (acceptance: unlabelled ≤ 0.10, cut partner consistency ≥ 0.90,
   model.glb hash unchanged), one real prompt → 3D → parts workflow, browser-pane checks of the 4 UI modes.
5. Ask the user to approve the milestone (contract §11 in api-contract.md is "pending user review"). Only then tuning.

## 3. Where things live (backend paths relative to `backend\`)
| Area | Files |
|---|---|
| Unchanged generator (protected, sha256-pinned) | `pipelines/image_to_3d.py`, `inference/trellis/*`, `services/job_service.py`, … list in `test/backend/protected_files.sha256` |
| GPU seam | `workers/generation_worker.py::submit_gpu_task` (only CUDA entry for new code) |
| Run framework (ig_/sg_/pa_ runs) | `models/runs.py`, `schemas/runs.py`, `storage/run_store.py`, `storage/run_layout.py`, `pipelines/runs/*`, `workers/run_worker.py`, `services/run_events.py` |
| Wiring | `core/part_aware_container.py` (lazy dotted-path executor/provider tables — never edit for new slices), `api/part_routes.py`, `api/v1/*.py`, `core/feature_errors.py`, `config/settings.py` (§8.1 flags) |
| Providers / registry | `inference/providers/{base,catalog,registry,unavailable,vram}.py`; implementations `inference/imagegen/zimage.py`, `inference/segmentation/{gdino,sam2,florence2}.py`, `inference/parts/{raster_lifter,cameras}.py` (ONLY these import torch/diffusers/transformers) |
| Parts library (torch-free) | `parts/types.py, taxonomy.py, manifest.py` (frozen) + geometry core `gltf_io, weld, components, shells, partition, seams, reassembly` + asset layer `labels, policy, relations, frames, buffers, export…` |
| Pipelines | `pipelines/image_generation.py`, `prompt_templates.py`, `segmentation.py`; `processing/conditioning.py`, `imagegen_readiness.py`, `seg2d/*` |
| Services | `services/{feature,model_registry,image_generation,segmentation,part_analysis,workflow}_service.py`, `workflow_catalog.py`, `part_files.py`, `run_retention_service.py`; tuning gate in `mesh_tuner_service.py::create_run` |
| Frontend | `src/api/{partAwareTypes,features,imageGenerations,segmentations,partAnalyses,workflows,files}.ts`; pure libs `src/lib/*.ts`; workflow engine `src/workflow/{engine,nodes}/*`; stores `src/store/*`; actions `src/actions/*`; hooks `src/hooks/*`; primitives `src/components/common/{Sheet,FloatingNav,NavRail,…}` |
| Tests | backend `test/backend/test_*.py` (+ `fakes_*.py`, `parts_fixtures.py`); GPU `test/engine/test_{aux_isolation,prepared_conditioning,raster_lifter}.py`; smokes `test/aux_models/test_smoke_*.py` (opt-in `IMG3D_GPU_SMOKE=1`); frontend `test/frontend/{mock_backend.mjs,mock/,fixtures/,unit/,review/}` |
| Real fixtures | `test/part_aware/fixtures.json` → `test/outputs/engine_smoke/job_smoke165749` (ALICE, 1.94M faces), `job_smoke170427` (anime, 1.87M) |

## 4. Commands (repo root `E:\14-OCT-2026\Img3D`)
```
backend\python\python.exe -s -m pytest -c test\pytest.ini test\backend test\aux_models -q -p no:cacheprovider
backend\python\python.exe -s test\backend\test_protected_files.py --check
npx --prefix frontend tsc -b frontend
npm --prefix frontend run lint
node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
node test\frontend\review\compiler_check.mjs
node test\frontend\ui_modes_mock_smoke.mjs
```

## 5. Doc section index (Grep these headings instead of reading)
| Topic | DESIGN.md | UI_DESIGN.md |
|---|---|---|
| Invariants / protected files / layering | §0.4, §1.3, §1.4 | §0.4, §1.4 |
| Taxonomy + physical vs region policy | §2.2–§2.5 | – |
| Manifest img3d.parts/1, buffers, seams, relations | §3.3–§3.14 | – |
| Run framework / GPU scheduling | §4.3, §4.8, §4.11 | – |
| Prompt-to-3D | §4.4 | §5 |
| Segmentation / part analysis / lifting | §4.5, §4.6 (§4.6.5 labelling incl. partner fill) | §6 |
| Workflows + node graph | §4.7 | §4.3 |
| API endpoints + JSON shapes | §6 | §2 |
| Flags / settings / tuning gate | §8 | §3.9 (tuner lock) |
| Tests per slice | §10.2 | §10 |
| Slices + owners | §11 | §11 |
| Decisions / defaults | §12 + `DECISIONS.md` | §12 |
