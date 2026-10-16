# Critique: requirements coverage + cross-doc consistency (DESIGN.md x UI_DESIGN.md)

Lens: each clause of the user request checked against both docs; every endpoint, field, event, flag, issue code and URL
the UI uses checked against DESIGN.md; the 2026-10-16 open-question resolutions checked as applied; over-engineering.
Line numbers refer to the files as of 2026-10-16 17:25.

Clauses with concrete coverage (no issue): Default preserved + polish P1-P22 (UI §4.2); Node UI with the six requested
nodes, typed ports, locked future nodes (DESIGN §4.7.2, UI §4.3); Mobile floating bottom nav, FAB, sheets, >= 48 px
(UI §4.4); Tablet rail/bottom auto-switch with hysteresis (UI §4.5.1); prompt + image in every mode (UI §2.9, §5);
one shared API/state layer (UI §1.1, §2); mode persisted in `img3d.preferences` (UI §3.1); physical vs region policy per
DECISIONS §3 (DESIGN §2.2-§2.5); relations/seams/reassembly/AssetReader/PartConsumer seam (DESIGN §3); tuning gate
(DESIGN §8.2, UI §3.9); legacy `POST /jobs` body G1 (UI §0.3, verified against `frontend/src/store/settingsStore.ts:112-135`).
Wire shapes of §6.1-§6.7 match UI §2.1 field for field except the items below.

Totals: 0 blockers, 5 majors, 10 minors.

---

## Majors

### 1. [major] DESIGN.md does not apply the Q1-Q7 resolutions; the two docs disagree on what is scheduled
Evidence:
- DESIGN.md:2560-2579 (§12) still asks Q1-Q7 as open; UI_DESIGN.md:96-105 (§0.5) treats them as resolved.
- DESIGN.md:2536 S6 "(needs Q4)", :2539 S9 "(needs Q3)", :2548 Phase B "S6 (after Q4) … S9 (after Q3)", :158/:170
  "(approval Q4)", :2447 "only after Q4": S6/S9 read as blocked although Q3/Q4 are approved.
- DESIGN.md:2542-2543 + :2550 keep SD1/SD2 as Phase D slices although Q5/Q6 are NOT approved; :262-263 "The only
  planned change is SD1 (Q5)"; :71 (I2) SD1 clause; :2478 parity row lists `test_capture_parity.py` (SD1); :1771-1772;
  :2612 R12 lists SD2 as a mitigation.
- DESIGN.md:65 row "Model choices pending … (Q1)"; :1212 "(Q2)"; :411-412 "(Q7)".
- Dead SD1 code still in S5/S10 scope: `recover_frame` priority 1 `persisted` (DESIGN.md:1268-1269), §4.6.3 step 1
  (:1405-1406), `test_frame_recovery.py` "A persisted json is honoured" (:2503). The file it reads can never exist
  while SD1 is deferred.
- Q4 requires the Sapiens2 conditions in the UI; UI reads `license.conditions` (UI_DESIGN.md:583-584), but DESIGN
  §5.4 (:1997) gives them only as prose in a table cell.

Fix (DESIGN.md only; UI §0.5 is already correct):
- Replace §12 with "## 12. Resolved questions (2026-10-16)" and this table:
  | Q | Resolution | Source | Applied in |
  |---|---|---|---|
  | Q1 | The six DECISIONS.md models are approved by default (`default_approved=True`); `IMG3D_APPROVED_MODELS` stays as an override | user | §5.4, §8.1 |
  | Q2 | No paused manual-pick workflow step in v1; review-first = the two-call flow; D9 stays deferred | assistant default (user may override) | §4.4.8, §13.1 |
  | Q3 | Code gate in `MeshTunerService.create_run` + the one `test/conftest.py` line; Blender detection/download endpoints unchanged; every tuner/Blender UI mount point hidden in all four modes | assistant default | §8.2, UI §3.9 |
  | Q4 | See-through + Sapiens2 vendored as pinned git submodules under `backend/third_party/`, `THIRD_PARTY_NOTICES.md` rows, Sapiens2 conditions shown in the UI | user | S6, §5.4, UI §6.2 |
  | Q5 | SD1 not approved: deferred (D1); `persist_generation_intermediates` stays inert and is always reported `false`; protected files untouched | assistant default | §1.3, §11, §13.1 |
  | Q6 | SD2 not approved: deferred (new D12) | assistant default | §11, §13.1 |
  | Q7 | Cut only the lifted eye faces; eyelid ring (26/27) stays a base region | assistant default | §2.4 rule 4 |
- §11: S6 "Depends on" = `S1, S5`; S9 "Depends on" = `S1`; drop "(needs Q4)/(needs Q3)"; Phase B = "S2, S3a, S3b, S4,
  S5, S6, S7, S8, S9"; delete the SD1 and SD2 rows and "Phase D"; §13.1 D1 text "SD1 generation intermediates — not
  approved 2026-10-16 (Q5)"; add D12 "SD2 Z-Image bf16 re-save — not approved 2026-10-16 (Q6)".
- §1.3 last paragraph: "No protected-file change is planned in this milestone." §0.4 I2: delete the SD1 sentence.
  §10.1 parity row and §4.9: drop `test_capture_parity.py`. §13.2 R12: drop "SD2". §0.3 row → "Approved models: the
  six DECISIONS.md models (Q1)".
- §4.5.2/§4.6.3: remove the `persisted` branch from the S5/S10 implementation (keep `"persisted"` reserved in the
  `method`/`source` literals); `recover_frame` priority starts at `recompute`; delete the "persisted json" assertion.
- §5.4 Sapiens2 row: `license=LicenseInfo(id="sapiens2-license", …, commercial="allowed_with_conditions",
  conditions=("No biometric identification", "No deepfakes or impersonation", "No military use"))`; add to
  `test_model_registry.py`: `status("sapiens2_seg_0_4b")`'s `ModelOut.license.conditions` has exactly these 3 strings.

### 2. [major] "Segmentation before reconstruction" in workflows has undefined/contradictory semantics
Evidence:
- DESIGN.md:1542 `segmentation` non-null → sg_ "(preview for the UI)"; :1642-1643 only a connected
  `part_extraction.masks` edge links sg_ → pa_; the simple form has no such rule; :1583 says `part_extraction` params
  exclude `segmentation_id` but the simple form's `parts` is full `PartAnalysisOptions` (incl. `segmentation_id`).
- DESIGN.md:1407-1417 (§4.6.3): a reference-image sg_ is reused only for real-alpha inputs; prompt images and opaque
  uploads are always re-segmented (`segmentation_rerun_for_alignment`).
- UI_DESIGN.md:1727-1728 and :1813 promise "the generation itself uses the pa_ internal pass unless 'Use this
  segmentation before 3D' is on" — false for every prompt job and every opaque upload; the previewed `sg_`
  (`previewSegmentationId`, :645) is never passed to the workflow, which runs a second segmentation.
- UI internal conflict: :708 emits `segmentation` only when parts are on, but Node `deriveConfig` (:1365) allows a
  Segmentation node "or the run, for preview" with Part Extraction bypassed, and the template (:1376) activates
  Segmentation from `previewSegmentation` alone; so config {segmentation: true, partExtraction: false} compiles to a
  graph with an sg_ step while the simple form has none — the graph ≡ simple-form test (:2103) fails for 2 of the 8
  configs.
Fix:
- DESIGN §4.7.3, after the step table: "When `segmentation` and `parts` are both non-null, the pa_ child is created
  with `segmentation_id = outputs.segmentation_id`; §4.6.3 reuses it only for a verified `recompute` frame, otherwise
  the pa_ runs its internal pass and records warning `segmentation_rerun_for_alignment`. A workflow request whose
  `parts.segmentation_id` is non-null is 422 `validation_error` type `conflicting_fields`. `segmentation` with
  `parts: null` is 422 `conflicting_fields` (the standalone `POST /segmentations` is the preview path)."
- DESIGN §4.7.2: replace the "When `part_extraction.masks` is connected…" sentence with "A Segmentation node must feed
  `part_extraction.masks`; otherwise the compiler emits `unsupported_topology` (node_id = the segmentation node)."
- UI §4.3.6: delete "(or the run, for preview)"; a Segmentation node whose masks reach no active Part Extraction gets
  `ui_unreachable_node` and is excluded from the run set; `workflow_derive.test.mjs` adds that case.
- UI §5.5/§6.2 copy: switch label "Segment the image before 3D"; helper text "Shown while the model is built. The 3D
  parts reuse it only when the image has real transparency; otherwise the parts step segments the model's own view
  again."

### 3. [major] Two export knobs for one pa_ with no precedence; `assembled_glb` is a dead option
Evidence: `PartAnalysisOptions.export {assembled_glb, per_part_glbs, previews}` (DESIGN.md:1397) and
`WorkflowRequest.export {assembled_glb, per_part_glbs}` (:1545; Export node compiles to it, :1584) both target the
workflow's pa_; nothing says which wins or whether partial objects are accepted. UI sends top-level `export` for
workflows (UI_DESIGN.md:708, G2a :2134) and `parts.export` for direct analyses (G8a :2140) and has to ask (BR2 :2259).
`assembled_glb=false` would make `result.assembled_url` null (UI types it nullable, :396) and break the Parts view,
while the UI says "Assembled parts GLB — always" (:1815) and never sends false.
Fix (simpler, keeps every requirement):
- DESIGN §4.7.1: delete `export` from `WorkflowRequest` and from the `graph`-exclusion list; §4.7.2 Export node row
  "Params model `{per_part_glbs}` · Compiles to `parts.export` (ignored when there is no part_extraction)".
- DESIGN §4.6.2: `export: PartExportOptions = {per_part_glbs: bool = False, previews: bool = True}` (pydantic sub-model,
  per-field defaults, `extra="forbid"`); delete `assembled_glb` everywhere (`model_parts.glb` is always written;
  `result.assembled_url`/`download_url` are never null).
- UI §2.8: drop the `export` row of `WorkflowRequest`; `buildPartAnalysisOptions` emits `export: {"per_part_glbs":
  true}` whenever `perPartGlbs` (workflows and direct). G2a → `"parts":{…,"export":{"per_part_glbs":true}}` with no
  top-level `export`; G7 Export params → `{"per_part_glbs":false}`; `PartAnalysisResultOut.assembled_url: string`;
  mock `--strict-requests` key sets updated; BR2 closed.

### 4. [major] Acceptance UI1 can never pass: today's Default renders the tuner, the new Default hides it
Evidence: UI_DESIGN.md:81 (UI1) and :1174 require "today's UI plus one App settings gear: same landmarks, headings
and button names" on a legacy backend, with the baseline fingerprint recorded "from today's UI" (:2180, F0 :2217).
Today `AppShell.tsx:65,95,134` renders `<TuningCard/>` unconditionally (Panel heading "Auto Tune",
`TuningCard.tsx:84`) and `Header.tsx:40` renders `BlenderRuntimeStatus`; §3.9 (:1085) hides both whenever the snapshot
is not `ready`, i.e. always on legacy404. F6 and F12 acceptance (:2223, :2230) inherit the impossible check.
Fix: UI1 (:81), §4.2 Guarantees (:1174), §10.5 `ui_probe.js` row and F0/F6/F12 acceptance: "today's UI **minus the
paused tuner mount points of §3.9** plus one 'App settings' gear". F0 records the baseline from today's UI and stores
`{fingerprint, tunerExcluded}` where `tunerExcluded` = the landmark/heading/button names found inside `#tuning` and
inside the Blender runtime pill (listed literally in the file header); `fingerprint()` comparison removes them.

### 5. [major] Hard constraints "never open the two excluded jobs" and the GPU lock are not encoded in any acceptance step
Evidence: DESIGN.md:2537 (S7 "On a real completed front-view character job"), :2540 and :2513 (S10 "real ALICE and
ANIME_CHARACTER_01 jobs"), UI_DESIGN.md:2199 ("compared with a pre-change job of the same seed") let an implementer
pick any job in `backend/runtime/jobs/`, including `job_269a864ac597` / `job_fa0c4771ec03`; neither doc names them.
DESIGN never mentions `test/part_aware/gpu_lock.py` (mandated by `SKILL.md` for every real-weight command) in §10.2
smoke rows or the S4/S5/S6/S7/S10/S11 acceptance; only UI §10.7 does.
Fix: add to DESIGN §10 (intro) and UI §10.7: "Real-weight runs: inputs only from `test/images/{ALICE,
ANIME_CHARACTER_01}`; jobs are created fresh through `POST /jobs` by the script; never read, list-open or analyse
`runtime/jobs/job_269a864ac597` or `runtime/jobs/job_fa0c4771ec03` (`test/part_aware/run_parts_smoke.py` defines
`EXCLUDED_JOB_IDS = {"job_269a864ac597", "job_fa0c4771ec03"}` and exits 2 if one is passed); every command is wrapped
`backend\python\python.exe -s test\part_aware\gpu_lock.py --label <slice> -- <cmd>`." S7 acceptance → "on the job that
`run_parts_smoke.py` creates from ALICE"; UI §10.7 → "compared with a job generated in the same session from the same
image and seed on the pre-change build".

---

## Minors

### 6. [minor] UI §2.4 calls the wrong store action on `part_analysis.updated`
Evidence: UI_DESIGN.md:535 "terminal `completed` → `loadModel(analysisId)`"; `loadModel` is
`featuresStore.loadModel` = `POST /models/{id}/load` (:597); the parts store action is `loadSet(analysisId)` (:1786-1787).
Fix: :535 → "terminal `completed` → `usePartStore.getState().loadSet(data.id)` (inside `upsertAnalysis`)".

### 7. [minor] `/features` strategy entries lack `reason`; the capability `code` vocabulary is undefined
Evidence: DESIGN.md:2087-2090 strategies have no `reason`; UI `StrategyOut extends CapabilityOut` and shows "unavailable
options disabled with their reason" (UI_DESIGN.md:307, :1808-1809), and special-cases `per_part_regeneration` by id
(conflicts with UI14). Codes `not_implemented` (DESIGN.md:1385, :2525) and `engine_unavailable` (:2139) are not in
`ReasonCode` (:1808-1809), so a pydantic `code: ReasonCode` would reject them.
Fix: DESIGN §5.1 `ReasonCode` += `"not_implemented"`, `"engine_unavailable"`; §6.1 every `strategies[]` item gains
`"reason"` (`per_part_regeneration`: "Per-part regeneration is not implemented in this milestone."); UI §6.2 shows
`reason` instead of the literal "Not implemented".

### 8. [minor] Readiness issue ids differ from the warning codes the UI keys its texts by
Evidence: DESIGN.md:1157-1162 `readiness.issues` = `touches_border`, `multiple_subjects`, …; warnings are
`t2i_<issue>` (:1161-1162, :2294-2295); UI chips use `WARNING_TEXT` keyed `t2i_touches_border` (UI_DESIGN.md:823,
:1702) for `readiness.issues`.
Fix: DESIGN §4.4.5: "`readiness.issues` holds the same `t2i_*` codes as the run warnings" (one vocabulary); update the
§4.4.6 example and `test_image_generation_api.py`.

### 9. [minor] UI §13 BR1-BR8 are left unanswered although DESIGN can state them in one line each
Evidence: UI_DESIGN.md:2255-2265; DESIGN §6.5 lists `?limit` for `/workflows` and `/segmentations` without a range
(:2184, :2228); §6.5 shows `params_schema` only for `segmentation` (:2239-2246); §3.12 shows `extras.img3d.id` only for
`part:body`/`part:hair` (:830-833); progress units only by example (:1706, :1714).
Fix (DESIGN): §6.3/§6.5 "`limit` 1..100 (default 20)" for `GET /segmentations` and `GET /workflows`; §6.5 "every
node type has `params_schema` = `model_json_schema()` of its params model, with `properties`; `prompt` lists every
`ImageGenerationRequest` field"; §3.12 "every `part:<id>` node has `extras.img3d.id`; every mesh node carries
`_FEATURE_ID_0` with region-border duplicates"; §4.7.5 "`steps[].progress` is 0..100; `RunStep.progress` is 0..1";
BR2 closed by item 3; BR6 is already answered by DESIGN.md:2148-2149 — UI §13 marks it resolved.

### 10. [minor] Over-engineering: catalog-driven request-key gating and the unused `job_request`
Evidence: UI §2.6 (UI_DESIGN.md:611-635) filters every optional key through `params_schema.properties`, with
`dropped`, `catalog_mismatch`, `lastBuildDropped`, G10 and `catalog_no_part_hint.json`; it lists `settings` as a
"required key" although no node `params_schema` has a `settings` property (`image_to_3d` params are the
`GenerationSettings` fields), so the check is ill-defined. Frontend and backend ship from one repo; skew is already
handled by `/features` 404 (legacy) and the 422 `extra_forbidden` → toast + refresh path (:815). DESIGN
`CompileResult.job_request`/`ValidationReport.job_request` (DESIGN.md:1614, :2226) is never posted (UI :1438-1439).
Fix: UI builders emit the fixed DESIGN v1 key lists (`V1_PARAM_KEYS` becomes the only table); delete
`keyFilterFromCatalog`, `pickKeys`, `dropped`, `catalog_mismatch`, G10 and its fixture; keep the 422 handling. DESIGN
drops `job_request` from `CompileResult` and `ValidationReport` (keep `mode`); BR1 closed.

### 11. [minor] Prompt one-shot is not blocked when workflows are off
Evidence: DESIGN `prompt_to_3d.available` does not depend on `flags.workflows` (DESIGN.md:2140) — correct for the
review flow, but one-shot is `POST /workflows` (§4.4.8). UI blocks only "parts on and !workflow.available"
(UI_DESIGN.md:746-749, rule 7 :794), so one-shot with `IMG3D_WORKFLOWS_ENABLED=false` always ends in 503.
Fix: UI §2.9 blocked rows + §2.10: "effective prompt, flow `one_shot`, `!workflow.available` → `workflows_unavailable`
'Straight to 3D needs workflows: ${reason}. Use Review image first.'"; §5.2 disables the "Straight to 3D" segment with
that reason; `generate_plan.test.mjs` adds the case.

### 12. [minor] `FeaturesOut.models` is called "summaries" but the UI needs the full `ModelOut`
Evidence: DESIGN.md:2128 "ModelOut summaries"; UI `licenceNotices` (Q4 Sapiens2 notice) and `selectableT2IModels`
read `license.conditions`, `approved`, `kind`, `state` from `features.models` (UI_DESIGN.md:580-584, :1079).
Fix: DESIGN §6.1 "`models`: `ModelOut[]` — the full §6.1 shape, `hidden_by_default` entries excluded"; add to
`test_features_api.py`: each entry equals `GET /models/{id}`.

### 13. [minor] Locked refine nodes are `hidden_by_default: false` in DESIGN but hidden by a UI-only flag
Evidence: DESIGN.md:2121-2124 `hidden_by_default: false` for `part_refine`, `mesh_tune`, `eye_correction`,
`rig_correction`; UI hides the refine group unless `VITE_FEATURE_REFINEMENT_PREVIEW==='true'` (UI_DESIGN.md:1303-1305,
:1496-1497); Q3 requires them hidden in all four modes, so the backend should be the single source.
Fix: DESIGN §4.7.2 + §6.1: the four locked nodes have `hidden_by_default: true`; `test_features_api.py` asserts it;
UI keeps the preview flag as the only way to list hidden locked nodes.

### 14. [minor] No backend extension seam for future tuning nodes, while the UI claims none is needed
Evidence: the user asks for a "modular engine for future tuning nodes". DESIGN compiles every graph to the fixed simple
form; refine nodes "Compiles to: –" (DESIGN.md:1585) and `WorkflowRequest` is frozen in S1 (:2520). UI §4.3.11
(UI_DESIGN.md:1498-1500) says a future node needs "no change to `compile`, `validate` or the shells" (true only for the
client).
Fix: DESIGN §4.7.2 add "Extension rule: an approved future node type adds (1) a catalog entry with its params model,
(2) one additive optional `WorkflowRequest` field (reserved now: `refine: list[RefineStepRequest] | None = None`,
rejected with `node_locked` while `part_refinement_enabled=false`), (3) one `WorkflowStepDef(id, node, weight,
create_child)` row in `services/workflow_service.py`; the compiler maps node type → field via `compiles_to`." UI
§4.3.11: "…no change to the client compile; the server needs the three DESIGN §4.7.2 items".

### 15. [minor] The conftest opt-in contradicts the `/features` "false by default" test
Evidence: DESIGN.md:2411 puts `os.environ.setdefault("IMG3D_MESH_TUNING_ENABLED", "true")` in `test/conftest.py`;
`make_settings` (test/backend/conftest.py:60-74) does not override it, so every settings object in tests has
`mesh_tuning_enabled=True`; `test_features_api.py` (DESIGN.md:2492) asserts "`mesh_tuning.enabled` is false by default".
Fix: DESIGN §10.2 `test_features_api.py`: "with `make_settings(mesh_tuning_enabled=False)`: false; with
`monkeypatch.delenv('IMG3D_MESH_TUNING_ENABLED')` + `Settings(_env_file=None)`: the code default is false; with
`blender_enabled=False, mesh_tuning_enabled=True`: false".
