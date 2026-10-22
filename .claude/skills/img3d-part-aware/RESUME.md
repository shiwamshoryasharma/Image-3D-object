# RESUME — part-aware milestone (read THIS first; ~5 min instead of reading code + 6k lines of design)

Last update: 2026-10-16 ~23:20 (third pause, user's token budget). Everything below is the verified state.

> **Session 2026-10-22 (resumed; plain Agent calls, max 4 at once, no Workflow tool).** Gates re-checked at start:
> frontend tsc 0 / oxlint clean / unit 515/515 / compiler_check 0; protected backend files ok; tree clean at c8db6af.
> Found on disk: S7 camera fit already measured (`test/outputs/part_aware/s7/*_camera_fit.json`: alice 0.855,
> anime 0.926 IoU); S5 smoke already ran (`s5_smoke/report.json`: hair/face/both eyes, tiara eyebrow rejected,
> warm ~2.5 s). Wave 1 launched: **S3b** (complete), **S5+S7** (verify + reports), **F6**, **F7**. Wave 2 plan:
> S6, F8, then S10 → S11, F9a → F9‖F10‖F11‖F12 → F13. If paused: check which `progress/*.md` exist for these.
> Baseline suite 2026-10-22: 581 passed / 4 skipped (S4 tests included → S4 verified). **S5 + S7 DONE** (progress/S5.md,
> S7.md; suite 583/4): S5 fixed subject classification (Florence-2 `<DETAILED_CAPTION>`, `animated` anime word → all
> samples `anime_character`); open, tuning-only: no brow/lash masks (fusion min-area 0.02 %). S7 GPU test 19/19; front/back
> near-tie on anime (S10 should log it). **S6 launched** (fresh agent, S5 mask contract in brief).
> **F7 DONE** (progress/F7.md; gates green; featureBundle 27.1 kB gz / 30). Main session fixed
> `test/frontend/unit/review_scripts.test.mjs` allowlist expectation (F7 removed the ModelStatsCard row) → unit 515/515.
> Owner decision: the "From prompt" chip on `ReferenceTile.tsx` (unowned file) goes to **F12**; F7's placement requests
> (InputSourceCard / LazyPromptPanel / LazyPartsPanel in AppShell) → F12; reveal-target mapping → F6. **F8 launched.**
> **F6 DONE** (progress/F6.md; all gates green; tuner card + Blender pill locked in a disabled fieldset; App settings
> body 7.91/8.00 kB). Requests for F9/F10/F11: use `GenerateAction` fab/rail/toolbar, replace placeholder `index.ts`,
> set `SHELL_READY = true`, `<main id="main-content" tabIndex={-1}>`, show a backend banner. F12: keep `.content`/`.splash`
> in App.module.css. **F9a launched.** Running now: S3b, S6, F8, F9a.
> **S3b DONE** (progress/S3b.md; 60 tests; alice export 2.3 s, cut_partner_consistency 0.9987; labels.py mirror fix).
> Suite 639 passed / 4 skipped / 3 failed — the 3 are S2 registry-state tests pinning `vendored_source_missing`; S6 was
> cleared to update only those assertions. **S10 launched** (S0 sign-off for the legacy-invariance un-skip given).
> Running now: S6, F8, F9a, S10. Next: S11 (after S10), F9‖F10‖F11‖F12 (after F9a), F13, then one review + browser checks.
> **F9a DONE** (progress/F9a.md; unit 525/525; PhoneLayout, SheetHost Record<SheetId>, SectionHost, useShellNav).
> **F12 launched** (also owns: F7 AppShell placements, "From prompt" chip in ReferenceTile.tsx, EngineStatus
> `STATE_LABEL` export). F9/F10/F11 wait for F8. Running now: S6, F8, S10, F12.
> **S6 DONE** (progress/S6.md; suite 653 passed / 5 skipped). Submodules staged (not committed): see-through @df019de,
> sapiens2 @7e5bae8; `vendor_import.py` loads under `img3d_vendor.*`; Sapiens2 head re-declared (training engine needs
> uninstalled packages). Main session added parser `dependencies` to `catalog.py` + fixed the registry test docstring
> (registry/features tests 30 passed). Known flake: `test_run_framework::test_watchdog_does_not_count_time_queued_behind_the_gpu_thread`
> can fail while a GPU smoke runs in parallel. Running now: F8, S10, F12.
> **F8 DONE** (progress/F8.md; unit 525/525; viewer-parts API `focusPart`, `selectPartViewerStatus`; parts model draws
> only while Parts view is on; r3f chunk 92.3 kB gz vs 79 ±3 because of drei `<Bvh>` → F11 splits or rebaselines).
> PartRow wiring of F8's API handed to F12 (ownership passed, F7 finished). **Launched: F9+F10 (one agent, sequential),
> F11** (the one allowed install: `@xyflow/react@12.12.0` exact). Running now: S10, F12, F9/F10, F11.
> **F12 DONE** (progress/F12.md; unit 526/526; initial JS 196.5/203.45 kB; F7 placements, "From prompt" chip,
> `STATE_LABEL` export (first `oxlint-disable-next-line`), PartRow wired to F8 API). Main session fixed F12's request in
> `settings/SettingsContent.tsx`: skeleton (no lazy chunk) while `/features` is loading. Open budget items for F11/F9:
> r3f 92 vs 79 kB, mobile shell 84 vs 22 kB; import_boundaries `store/workflowStore.ts → api/files` (F11). F9a's
> `shellText` copy of engine-state words can now be replaced by `STATE_LABEL` (small follow-up). Running: S10, F9/F10, F11.
> **F9 + F10 DONE** (progress/F9.md, F10.md; both `SHELL_READY = true`; unit 526/526). The "84 kB mobile" was a bug in
> `test/frontend/review/bundle_budget.mjs` (shell walk followed index.html's lazy imports) — main session fixed `lazySet`
> to stop at entry-static chunks: Mobile 13.07/22 kB, Tablet 2.47/12 kB; only r3f 92.28 vs 79±3 fails (F11). Open: no
> system-status entry in rail postures (spec has none); posture flip re-creates the viewer canvas. Running: S10, F11.
> Main session: `shells/shared/CompactTopBar.tsx` now uses `STATE_LABEL`; the copy in `shellText.ts` was removed
> (lint, import_boundaries 0, compiler_check 0, unit 526/526).
> **S10 BUILT, smoke NOT yet passing** (progress/S10.md; suite 665 passed / 4 skipped; API tests 11). Real smoke on
> fresh jobs (ALICE 491,860 faces, anime 465,014): manifests valid, reassembly exact, model.glb hashes unchanged, IoU
> 0.881 / 0.934; FAILS: `cut_partner_consistency` 0.0 on all semantic runs (S3b `policy._instances` splits the two
> walls of one thin sheet), anime views=8 `export_failed` (region.face_count ≠ face_part count), empty base possible
> (`plan_parts`), ALICE views=0 unlabelled 0.1046. Decision: the S10 agent owns the S3b fixes (ownership passed), then
> re-runs `run_parts_smoke.py --reuse-jobs`; legacy test gets `previews:false` (CPU-only). **S11 launched** in parallel.
> Running: S10 (fixing), S11, F11.
> **F11 DONE** (progress/F11.md; all gates incl. bundle_budget green; unit 534/534; `@xyflow/react` 12.12.0 exact, lockfile
> only gained 19 packages; nodeShell baseline 33455 B; r3f rebaselined to 92.28 kB (PartsModel is a static import of
> ModelScene); d3-ease is BSD-3-Clause). Open: node bodies show no step thumbnails (needs a hooks-layer URL resolver,
> e.g. `hooks/usePayloadUrl.ts` — follow-up for F13/main); F11 wire-shape list relayed to S11. Running: S10, S11.
> Remaining after them: F13 (verification + docs) → one adversarial review → browser checks of 4 modes → user approval.
> **S10 fixes DONE** (S3b bugs a–c fixed by the S10 agent; suite 670/4; views=0 passes all gates; API default views=8 has
> `cut_partner_consistency` 0.747 ALICE / 0.676 anime — only tiny 74–105-face fragment instances; face-weighted 0.987).
> **Pending USER decisions:** (1) v8 gate: face-/size-weighted metric vs fragment rule (tuning) vs gate only 2M-face jobs;
> (2) sign-off of the S3b deviation from DESIGN §4.6.5 step 6 (voted partner walls with different classes → weaker vote
> follows its partner).
> **User feedback 2026-10-22 (main session, done):** (a) Node UI "doesn't look great vs ComfyUI": root bug = global
> `img,svg,canvas{max-width:100%}` squeezed every React Flow edge svg to 0 px → NO links drawn. Fixed in
> `shells/node/canvas/flow.module.css` (`.canvas svg{max-inline-size:none}`) + ComfyUI-style restyle (2-level line grid,
> 3 px type-coloured bezier links, category-tinted title bar, slot dots on node edges (ring = free, filled = linked),
> read-only widget rows from new `useNodeWidgets` in `useNodeSummary.ts`, purple bypass wash, green running glow,
> category-coloured translucent minimap, `fitViewOptions`), Node viewer dock starts collapsed until a model is viewed.
> (b) "Tripo multi-part from one image is missing": it existed but was hidden (Parts card at the bottom of the right
> column, generation toggle off by default, parts view/colorize manual). Added `SplitPartsButton` ("Split into parts")
> in `components/viewer/ViewerToolbar.tsx` and `partStore` auto-turns on parts view + colorize when a live analysis of
> the viewed model completes. Gates: tsc 0, oxlint, compiler_check 0, import_boundaries 0, protected 27/27, unit 534/534,
> bundle budget ok (initial 197.70/203.45). Browser-checked against the mock (test/.runtime/node_ui, vite preview :4180
> + mock :8765). Open question for the user: Tripo-style *part completion* (regenerating hidden surfaces per part) is
> not in this milestone.
> **User answered (DECISIONS.md "User decisions 2026-10-22"):** face-weighted consistency gate (implemented in
> `parts/export.py` + test; parts tests 165 passed), S3b partner-wall rule accepted, part completion = NEXT milestone.
> Real smoke re-run with the weighted metric launched (`test/.runtime/resume/parts_smoke_weighted.log`). Running: S11.
> **S10 real smoke: ALL GATES PASS** (2026-10-22 14:14, --reuse-jobs): ALICE v8 31 parts, unlabelled 0.0004, consistency
> 0.987, 18.9 s; anime v8 27 parts, 0.0002, 0.988, 19.5 s; v0 0.998/0.998; IoU 0.881/0.934; components ok.

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
