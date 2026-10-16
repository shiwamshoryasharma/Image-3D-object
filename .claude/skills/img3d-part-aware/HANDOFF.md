# Part-aware milestone — handoff (PAUSED 3rd time 2026-10-16 ~23:20; pushed to GitHub as WIP)

> **START WITH `RESUME.md`** (same folder): verified slice-status table, next steps, file map, commands and a doc
> section index. It replaces reading the code and the big design docs. The sections below are history.

> **Second pause (session b419e5e8, 2026-10-16 ~19:30).** Design is DONE. Implementation had just started: both
> implementation workflows were STOPPED mid-run by the pause. Read the section "Resume from the second pause" below first.

Milestone (user request, 2026-10-16): Tripo-style **part-aware backend** (semantic decomposition before/after
reconstruction; physical parts vs semantic regions; parts keep relationships for later optimisation/reassembly),
**prompt-to-3D + image-to-3D**, and **four UI modes** (Default polished, Node/ComfyUI-style, Mobile, Tablet)
selectable in Settings over one shared state/API layer. The full verbatim request is the USER_REQUEST constant in
`workflows/design_workflow.js`.

**TUNING IS ON HOLD.** Auto Mesh Tuner, eye/rig correction, refinement, reference-aware optimisation and any
Blender tuning must NOT be continued or started until the user explicitly approves this milestone. Do not resume
`.claude/skills/img3d-auto-mesh-tuner/` work. (Its own state is recorded in that folder's HANDOFF.md.)

Read in this order to resume: this file → `DECISIONS.md` (user choices + measured probes; authoritative) →
`DESIGN.md` (backend + API, critiqued + revised) → `UI_DESIGN.md` → `progress/*.md` → `research/*.md` as needed.

## Status
| Phase | Status |
|---|---|
| 1 Understand (9 read-only mappers/researchers) | DONE → `research/*.md` (backend_contract_map, backend_generation_map, trellis2_internals, frontend_architecture_map, part_aware_3d_research, segmentation_models_research, text_to_image_research, environment_audit, ui_modes_research) |
| 2 User decisions | DONE → `DECISIONS.md` (Z-Image-Turbo; GDINO + SAM 2.1 + Florence-2 + See-through anime parser + Sapiens2-seg, no SAM 3; cut hair/clothes/accessories/detachables/eye areas, brows+lashes as records; tuner WIP committed `8654b90`) |
| 3 Install + download + probe | DONE → diffusers 0.39.0 + accelerate 1.15.0 installed via `backend/constraints.txt` (uv pip check OK, TRELLIS stack unchanged); models in `backend/.model/t2i/Z-Image-Turbo` (32.85 GB) and `backend/.model/seg/*` (pinned shas in `download_manifest.json`); probes in `test/part_aware/probes/` with results in DECISIONS.md |
| 4 Design workflow | DONE (resumed 2026-10-16 ~16:45, workflow `workflows/design_finish_workflow.js` copy of run wf_f353dae8-9d7): `UI_DESIGN.md` (F0–F13 slices), critiques `design/critic_{requirements,backend_feasibility,frontend_feasibility}.md` (1 blocker = rim-joined double wall → partner fill; 22 majors), both docs revised + follow-up fix pass; §12 records Q1/Q4 as user decisions and Q2/Q3/Q5/Q6/Q7 as assistant defaults (DECISIONS.md) |
| 5 Implement backend + prompt-to-3D | IN PROGRESS (2026-10-16 ~19:00): Phase A workflow (S0, S1a interfaces, S1b plumbing → integrate → 2 reviewers → fix); then Phase B (S2, S3a→S3b, S4, S5→S6, S7, S8, S9), Phase C (S10 → S11). Per-slice reports in `progress/*.md` |
| 6 Implement four UI modes | IN PROGRESS (parallel with backend): Wave A workflow (F0, F4, F1 → F2 ‖ F3 → gate → review → fix); then B (F5), C (F6, F7, F8 → F9a), D (F9, F10, F11, F12), E (F13) |
| 7 Adversarial review + fixes | NOT STARTED |
| 8 Verify (tests, real generation incl. TRELLIS.2 smoke test after the diffusers install, viewports) | NOT STARTED |

## Resume from the second pause (do this first)
> **Status 2026-10-16 ~22:50 (lean mode at the user's request: one implementer per slice, no reviewer/integrator
> agents; one milestone review at the end).** DONE: S0, S1 (integrated by the main session, progress/S1_integrate.md;
> generation_worker.py pinned; suite 392 passed), S9 (progress/S9.md), frontend wave A F0-F4 (progress/F_waveA.md;
> 515 unit tests), UI_DESIGN tuner rule changed to "tuner preserved but LOCKED" (user spec). RUNNING: backend
> Phase B `workflows/impl_backend_phase_b.js` (S2, S3a→S3b, S4, S5→S6, S7, S8) and frontend `workflows/
> impl_frontend_waves_bc.js` (F5 → F6‖F7‖F8 → F9a). NEXT: S10 → S11; frontend wave D (F9, F10, F11, F12), E (F13);
> one adversarial review; real GPU validation on test/images. If paused, check `progress/*.md` for which slices
> reported and re-run only the missing ones (tell agents to complete existing files).
> **Resumed 2026-10-16 ~20:00 (same session).** Check on resume: backend suite 313 passed / 2 skipped; frontend unit
> 86/86, oxlint clean, tsc one error (missing `lib/partsModel.ts`). Relaunched `workflows/impl_backend_phase_a_resume.js`
> (S1a ‖ S1b → integrate → review → fix) and `workflows/impl_frontend_wave_a_resume.js` (F0 ‖ F4 ‖ F2 ‖ F3 → gate →
> review → fix). If paused again before they finish, repeat the steps below with these `_resume` copies.

State of the working tree (nothing committed; `main` at `8654b90`):
- **Finished cleanly:** `S0` (contract §11 in `img3d-architecture/api-contract.md`, architecture SKILL note,
  `test/backend/test_protected_files.py` + `protected_files.sha256`, `test_legacy_invariance.py` + fixture) → report
  `progress/S0.md`. `F1` (frontend API layer `src/api/{partAwareTypes,features,imageGenerations,segmentations,
  partAnalyses,workflows,files}.ts`, `api/ws.ts`, `api/types.ts`) → `progress/F1.md`.
- **Interrupted mid-edit (files exist but are INCOMPLETE/UNVERIFIED):** `S1a` (schemas/models/parts types/taxonomy/
  manifest/provider ABCs/workflow_catalog/workflow_store stub), `S1b` (settings, generation_worker seam, container,
  lifecycle, main, router, websocket hub, events, feature_errors, part_aware_container, empty v1 routers, runs
  schemas/models, run_store, run_layout, pipelines/runs, run_worker, run_events, stub services), `F0` (mock/fixtures/
  review scripts), `F4` (tokens + primitives CSS/TSX), `F2` (pure libs), `F3` (workflow engine). No progress report
  was written for them. `git status` lists every touched file.
- **Not started:** the S1 integrate/review/fix steps, frontend wave-A gate/review/fix, and everything after.

Resume steps:
1. Run the backend suite (`backend\python\python.exe -s -m pytest -c test\pytest.ini test\backend test\aux_models -q
   -p no:cacheprovider`; baseline before the milestone = 182 passed) and the frontend gates (UI_DESIGN §10.1) to see
   where the half-finished slices stand.
2. Re-run the two implementation workflows from their saved copies, telling the agents that files from the stopped
   run already exist and must be completed/verified, not rewritten from scratch, and SKIP the finished slices
   (S0, F1): `workflows/impl_backend_phase_a.js` (S1a ‖ S1b → integrate → 2 reviewers → fix) and
   `workflows/impl_frontend_wave_a.js` (F0 ‖ F4 ‖ F2 ‖ F3 → gate → review → fix). Workflow caches cannot be resumed
   across sessions, so edit the copies (drop the S0/F1 agents; pass their results from `progress/S0.md`, `F1.md`).
3. Then continue: backend Phase B (S2, S3a→S3b, S4, S5→S6, S7, S8, S9 in parallel; GPU steps under `gpu_lock.py`),
   Phase C (S10 → S11); frontend waves B (F5), C (F6, F7, F8 → F9a), D (F9, F10, F11, F12), E (F13); adversarial
   review → fix → verify (real runs on `test/images` + the two fixture meshes).
4. Real TRELLIS.2 test meshes already exist (`test/part_aware/fixtures.json`: ALICE front 1.94M faces / anime Alice
   1.87M faces, `test/outputs/engine_smoke/job_smoke165749` and `…170427`). That run also confirmed the TRELLIS.2
   smoke after the diffusers install (housekeeping item done; `requirements.txt` pins still belong to slice S4).
5. Tooling made this session: `test/part_aware/gpu_lock.py` (`--lock gpu` default for real-weight GPU commands,
   `--lock frontend-build` for tsc/vite/oxlint runs of parallel frontend agents; exit 124 = timeout).
6. The S0 "user approves the contract" gate was treated as part of the final milestone approval (user said
   "continue where we left off"); `api-contract.md` §11 is marked pending user review.

## Resume plan from the FIRST pause (historical; steps 1-2 are done)
1. Read DECISIONS.md and DESIGN.md §12 (open questions): most are answered by DECISIONS.md — fold the answers in
   (e.g. approved models = Z-Image-Turbo + the five seg models; separation policy).
2. Finish the design: run the remaining phases of `workflows/design_workflow.js` — the workflow cache cannot be
   resumed across sessions, so edit a copy to SKIP the Propose/Judge phases (feed the judge verdicts from
   `design/workflow_results.json` and the existing proposal files) and run only: frontend synthesis → UI_DESIGN.md
   (must use DESIGN.md's API shapes) → 2 critics (requirements, feasibility) → revision of both docs.
3. Show the user a short design summary; then implement in DESIGN.md §11 slices (disjoint file ownership) with a
   workflow: backend slices first (contract/flags/features endpoint, part data model + storage, providers +
   registry, segmentation, lifting + extraction, prompt-to-3D), then UI slices (shared layer/preferences, Default
   polish, Mobile, Tablet, Node engine + React Flow), then adversarial review → fix → verify.
4. Housekeeping still open: add `diffusers==0.39.0` and `accelerate==1.15.0` to `backend/requirements.txt`; re-run the
   real TRELLIS.2 smoke test (`test/engine/run_generation_smoke.py`) to prove the install didn't affect generation;
   `@xyflow/react@12.12.0` is NOT installed yet (frontend implementation will add it).

## Facts to remember
- Never upgrade transformers (4.57.6) / huggingface-hub (0.36.2); always install with `-c backend\constraints.txt
  --only-binary ":all:"`. diffusers must stay 0.39.0.
- HF Hub calls for public repos need `token=False` (expired cached OAuth token on this PC → 401).
- Z-Image-Turbo: cold first `.to("cuda")` ~307 s (disk-bound), warm 3.6 s/image, 23.3 GB VRAM → cache in CPU RAM,
  never co-resident with TRELLIS.2 on the GPU, sequence on the single GPU worker thread.
- Do not change the frozen JobStage list / percent formula / `/system` keys (tests pin them); keep `model.glb`
  untouched; all API additions optional (request bodies reject unknown fields).
- Excluded jobs: never open `job_269a864ac597` or `job_fa0c4771ec03`.
- Uncommitted on `main` (not pushed): `.claude/CLAUDE.md` edit, `.claude/skills/img3d-part-aware/**`,
  `backend/constraints.txt`, `test/part_aware/**`. Model weights are gitignored. Commit/push only when the user asks
  (author `Shiwam Shorya Sharma <110902859+shiwamshoryasharma@users.noreply.github.com>`, no co-author trailer).
