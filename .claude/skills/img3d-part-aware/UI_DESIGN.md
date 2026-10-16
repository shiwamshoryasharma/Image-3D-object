# Img3D part-aware milestone: AUTHORITATIVE frontend design (UI_DESIGN.md)

Date: 2026-10-16. Status: **authoritative** for the frontend half of the milestone "backend part-aware
architecture + four UI modes". The backend half is `DESIGN.md`. This document supersedes
`design/frontend_a_shells.md` and `design/frontend_b_command_engine.md`; they remain background only.

Precedence (highest first):
1. The user's messages.
2. `DECISIONS.md` (model choices, separation policy, environment changes).
3. `DESIGN.md` (backend and API; **every wire shape below is copied from DESIGN.md §6, never invented**).
4. This document.
5. `research/*.md`.

How this document was built:
- **Base:** proposal A ("shells over shared state"). It won all three frontend judges (A 41/41/40, B 38/39/38).
- **Grafts from B:** golden request fixtures shared by unit tests and the mock; a strict mock (`extra="forbid"`
  emulation) with a request log; a registry guard that rejects unlocked refine nodes; per-node `version`/`migrate`/
  `contribute` extension points; the legacy-stage → node status table; a session `runIndex`; overlay-mesh part
  highlighting with honest "Highlight unavailable"; the explode (reassembly) slider; phone-landscape rail in Mobile;
  one `GenerateAction` component with variants; one global Announcer; workflow import limits; `docFromGraph`
  ("load settings from a run"); a deletion-only import allowlist.
- **Not grafted from B (judges' explicit verdict):** the command bus, `AppSnapshot` syncers, the linear renderer /
  `WorkflowSlot`, moving the Default layout files, and any model-install UI.
- **Fixes:** every fatal or near-fatal flaw the judges listed is fixed. §14 is the record.
- **Reconciled with DESIGN.md (authoritative backend):** part results are `pa_` run resources
  (`/jobs/{id}/part-analyses`, `/part-analyses/{id}`), not fields inside the job; prompt-to-3D is a workflow
  (`POST /workflows`) or the two-call review flow (`POST /image-generations` → `…/use` → `POST /jobs`), never a new
  field on `POST /jobs`; `CreateJobRequest` is **unchanged**. Request builders emit the fixed DESIGN v1 key lists
  (§2.6); there is no catalog-driven key gating. Part options use DESIGN §2.2 group ids in `separate`; there is no
  `regions` request field (unselected groups become regions by policy, DESIGN §2.4). Export options live only in
  `parts.export` (`PartExportOptions`, DESIGN §4.6.2); `WorkflowRequest` has no top-level `export`.
- **Revised 2026-10-16** against the three critiques in `design/critic_*.md` and the revised DESIGN.md (same day).
  The "Revision log (2026-10-16)" at the end lists every item and how it was handled.

Legend: **[V]** verified in code or on disk in this session (file:line); **[R]** from a research report; **[D]** decided here; **[A]** assumption that
the named test or smoke run must confirm. Paths are relative to the repo root `E:/14-OCT-2026/Img3D`; frontend
source paths may omit `frontend/` (`src/…` = `frontend/src/…`). Test paths always start with `test/`.

Nothing in this document authorizes tuning, refinement, eye or rig correction, reference-aware optimisation or
Blender work. The existing tuner UI is preserved in the Default mode, where it stays visible but **locked** (§3.9):
it starts no tuning run and no Blender download or setup until the user approves, and past tuned results stay
viewable. Mobile, Tablet and Node never had tuner UI and do not add it.

---------------------------------------------------------------------------------------------------
## 0. Goals, constraints, invariants

### 0.1 Goals
| # | Goal | Where |
|---|---|---|
| UG1 | Four UI modes (Default, Node, Mobile, Tablet) rendered by **shells** over **one** shared API/state layer; selectable in App settings; persisted locally; Default is the default | §1, §3, §4 |
| UG2 | Default = today's UI, same structure and components, plus targeted polish (spacing, transitions, loading states, responsiveness, panels) | §4.2 |
| UG3 | Prompt-to-3D and image-to-3D in **every** mode, with the DESIGN §6.9 calls only | §2.8–§2.9, §5 |
| UG4 | Part-aware options before generation and part results after it (list, visibility, isolate, highlight, inspector, downloads) in every mode | §6 |
| UG5 | Node UI: ComfyUI-style graph over a pure TypeScript engine (registry from `CatalogOut`, typed ports, validation mirroring server issue codes, compile to `WorkflowRequest`, step → node status) with a lazily loaded React Flow adapter and an accessible list fallback | §4.3 |
| UG6 | Mobile: app-style, floating bottom nav, sheets, FAB, ≥ 48 px targets, safe areas | §4.4 |
| UG7 | Tablet: floating left rail in landscape, floating bottom nav in portrait, automatic switch with hysteresis | §4.5 |
| UG8 | Accessibility WCAG 2.2 AA, performance budgets, responsive at every listed viewport | §7–§9 |
| UG9 | Every slice is testable without a GPU: pure modules under Node's test runner, a strict mock mirroring DESIGN §6 | §10 |

### 0.2 Non-goals
- Any new tuning/refinement UI flow, eye/rig correction, Blender work, or new tuner code. `components/meshTuner/*` is
  not edited; the existing tuner UI is kept in Default (locked, §3.9), not removed.
- Model install or download UI (DESIGN D5). Models are read-only with honest states; the only model actions are the
  DESIGN §6.1 warm-up (`POST /models/{id}/load`) and unload.
- A paused workflow step for manual candidate pick (Q2 → NO for v1; the two-call review flow is used).
- SD1 intermediates and SD2 bf16 re-save (Q5, Q6 → deferred; no UI).
- Light theme, Playwright, new UI kits. The only new npm dependency is `@xyflow/react@12.12.0`.

### 0.3 Hard constraints → mechanism
| Constraint | Mechanism |
|---|---|
| Image-to-3D request byte-identical to today when parts are off | `jobStore.submit()` keeps its no-argument path; its body is built by the pure `lib/imageJobRequest.buildImageJobRequest`, which is `{reference_set_id, primary_image_id, settings: toRequestSettings(form, referenceMode)}` exactly as `store/jobStore.ts:L208-212` [V]. Golden fixture **G1** pins the bytes (key order included); the browser-pane check compares the raw `POST /jobs` body from the mock request log with G1 (ids substituted) in all four modes (§10.6). Every mode reaches this path through `startGeneration()` → plan `kind:'job'` |
| `CreateJobRequest`, `JobStage`, percent formula, `/system` keys unchanged | No edit to those types. Stage UI keeps `lib/stages.ts` [V]. New progress comes from the new resources (`WorkflowOut.steps`, `RunStep`) only |
| Additive API; request bodies reject unknown fields | New calls only go to new endpoints with the DESIGN §4/§6 shapes. Builders emit only the fixed DESIGN v1 keys of §2.8, optional keys only when non-default (DESIGN §6.0: nested objects accept partial objects). The key sets live in `test/frontend/fixtures/request_keys.json`; the mock runs `--strict-requests` (422 on unknown keys) by default and `workflow_request.test.mjs` asserts every built object's keys are a subset of them |
| Tuning disabled; the existing tuner UI preserved but locked (Default only) | One key: `/features.mesh_tuning.enabled` (DESIGN §6.1, §8.2 G1/G4). `tunerLock(snapshot)` (§2.5) is `locked` unless the backend is legacy (`/features` 404: today's behaviour, unlocked) or reports `mesh_tuning.enabled === true`. While locked, Default renders the Auto Tune card (three placements) and the Blender runtime pill where they are today, inside a disabled lock frame with the notice "Paused until you approve the part-aware milestone"; `BlenderSetupDialog` is never mounted; existing tuned results stay viewable (§3.9). The backend code gate S9 independently rejects `POST /jobs/{id}/tunings` with 503 `feature_disabled`. Approval flips the same flag: everything unlocks with no code change and no rebuild. Default build, locked: no POST that creates a tuning run and no POST that starts a Blender download (asserted from the mock request log, UI3); GET status/list requests are allowed. The tuner components stay `lazy()` (one chunk, §8.1); Mobile/Tablet/Node never download it |
| No fake data in the app | Every part, model, candidate and capability comes from the backend. Unavailable = the backend's `reason`. Test doubles only under `test/frontend/` |
| No deletion of working functionality | No file of the working UI is moved or deleted. `uiStore.mobileTab`/`historyOpen` stay (the paused `BlenderSetupDialog.tsx:L89` still calls `setMobileTab`) [V]. Existing exports keep their paths (moved functions are re-exported). The tuner UI keeps rendering in Default (locked, §3.9) and viewing past tuned results keeps working |
| Paused tuner frontend files untouched | `components/meshTuner/**`, `store/meshTunerStore.ts`, `lib/meshTuner.ts`, `api/blender.ts`, `api/tunings.ts`, `hooks/useTuneAvailability.ts` are sha256-pinned by `test/frontend/review/protected_frontend.mjs` (§10.5). Only their importers change (`lazy()` through `components/tunerLock/lazyTuner.ts`, wrapped by the lock frame, §3.9). The frontend tree is clean at `8654b90` [V `git status`] |
| React Compiler, zustand 5, oxlint, TS 6 rules | No manual memo; selectors return primitives or stored references (no new objects); no `setState` inside effects (derive during render or use event handlers); async error handling inside store actions; no `try/finally` in components/hooks; constants and tables in `.ts` files; no `enum`; `import type`; erasable syntax only (`tsconfig.app.json` `erasableSyntaxOnly`, `verbatimModuleSyntax`) [V] |
| Node v22.17.0 test runner | Pure modules use explicit `.ts` relative value imports (allowed by `allowImportingTsExtensions` [V]) and run under `node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"`. **A glob is mandatory**: the directory form fails with MODULE_NOT_FOUND on 22.17 (judge-verified) |

### 0.4 Invariants (acceptance for every slice)
| # | Invariant | Enforced by |
|---|---|---|
| UI1 | With `/features` → 404 (legacy backend) the Default UI is today's UI **including every tuner mount point** (the "Auto Tune" panel and the "Blender runtime" pill, unlocked exactly as today) **plus** one "App settings" gear and, at widths 1280–1439 px, the P21 "GPU memory details" button: otherwise the same landmarks, headings, notes and button names in the same order. With `/features` ready and `mesh_tuning.enabled=false` (tuner locked, §3.9) the same holds outside the marked part-aware additions (§4.2), plus exactly one lock notice "Paused until you approve the part-aware milestone" | `test/frontend/review/ui_probe.js`: `fingerprint() == applyDelta(BASELINE, EXPECTED_DEFAULT_DELTA.legacy)` on `legacy404` and `fingerprint({skipAdditions: true}) == applyDelta(BASELINE, EXPECTED_DEFAULT_DELTA.locked)` on `full` (§10.5) |
| UI2 | Default `POST /jobs` body with parts off equals fixture G1 byte for byte, in all four modes, with and without `/features` | `image_job_request.test.mjs` + raw-body comparison from the mock request log (§10.6 step 2) |
| UI3 | While the tuner is locked, a default build sends no POST that creates a tuning run (`POST /api/v1/jobs/{id}/tunings`) and no POST that starts a Blender download (`POST /api/v1/blender/runtime/download`); since every other tuner mutation is unreachable while locked, the log has no other non-GET `/blender*` or `/tunings*` entry either. GET status/list requests (`GET /blender/runtime`, `GET /jobs/{id}/tunings`, `GET /tunings/{id}`, tuned GLB/report files) are allowed | `ui_modes_mock_smoke.mjs` request-log assertion |
| UI4 | No request body contains a key absent from DESIGN §4/§6 for that endpoint (no top-level `export`, no `parts.segmentation_id` in workflows, no `segmentation` without `parts`) | mock `--strict-requests` (default on) + `workflow_request.test.mjs` (keys ⊆ `fixtures/request_keys.json`) |
| UI5 | Exactly one `ViewerPanel` (one R3F canvas) mounted at any time | `ui_probe.js` counts `canvas` elements; `viewerRuntime` singleton [V `viewerRuntime.ts:L32`] |
| UI6 | Exactly one Ctrl/Cmd+Enter listener and one `useBackendSession()` | code review + behaviour check (§10.6 step 7): clear the mock request log, dispatch one Ctrl+Enter `keydown`, expect exactly one `POST /api/v1/jobs` (or `/workflows`, `/image-generations`) entry |
| UI7 | `@xyflow/*` is imported only under `src/shells/node/canvas/`; `three`/`@react-three/*` only by the lazy viewer files; neither appears in an initial chunk | `import_boundaries.mjs`, `bundle_budget.mjs` |
| UI8 | Pure modules (§1.5) import no react/zustand/axios/three/@xyflow and touch no `window`/`document`/`localStorage`/`import.meta.env` (`lib/storage.ts` is exempt from the global-token scan only, §1.5) | `pure_imports.test.mjs` static scan |
| UI9 | The frontend never builds part, mask, GLB, candidate or overlay paths; it only resolves payload URLs (`resolveApiUrl`) | `import_boundaries.mjs` grep for `/part-analyses/`, `/candidates/`, `/masks/` string literals outside `src/api/*.ts` route functions |
| UI10 | A broken or stale Node graph never blocks Default, Mobile or Tablet: `pipelineBlock` is set only while the Node shell is mounted and cleared on unmount | `generate_availability.test.mjs` + C-slice acceptance |
| UI11 | A persisted `source='prompt'` or `parts.enabled=true` can never trap the user: the effective source is forced to `image` on a legacy backend or when prompt-to-3D is reported unavailable, parts are forced off on a legacy backend, and elsewhere the switch to the safe value is never disabled | `generate_plan.test.mjs` |
| UI12 | Every quality gate of §10.1 is green after the slice | slice report |
| UI13 | The paused tuner files are byte-identical to `8654b90` | `protected_frontend.mjs` |
| UI14 | No literal model id, part id or taxonomy label in `src/` outside `lib/featureMeta.ts` and type unions (no placeholder data) | `import_boundaries.mjs` negative grep |

### 0.5 Resolved questions applied (from DESIGN.md §12; not open)
| Q | Resolution | UI consequence |
|---|---|---|
| Q1 | The six `DECISIONS.md` models are approved by default | The UI renders whatever `/features` and `/models` report. No "approval pending" wording anywhere |
| Q2 | No paused manual-pick workflow step in v1 | Review-first = the two-call flow (§5.3). Node Prompt node runs one-shot (`auto_first`); its inspector offers "Preview image" + "Use as Input Image" |
| Q3 | Code gate in `MeshTunerService.create_run`; Blender detection/download endpoints unchanged; the four refine nodes are `hidden_by_default: true` in `/features` and the catalog | Amended by the user spec alignment (DECISIONS.md, 2026-10-16): Default keeps every tuner/Blender UI mount point where it is today, locked while `mesh_tuning.enabled` is false (§3.9); Mobile/Tablet/Node have none; refine nodes are listed only behind `VITE_FEATURE_REFINEMENT_PREVIEW` (§4.3.2) |
| Q4 | See-through and Sapiens2 vendored (git submodules + notices) | Licence conditions of any usable model with `license.conditions` (Sapiens2: exactly "No biometric identification", "No deepfakes or impersonation", "No military use") are shown in App settings ▸ Models and as a notice in segmentation options (§6.2) |
| Q5 | SD1 not approved | `flags.persist_generation_intermediates` is ignored by the UI |
| Q6 | SD2 not approved | Cold start stays ~337 s; the UI shows the cold-start notice and warm-up (§5.4) |
| Q7 | Eye cut = lifted eye faces only; eyelid ring stays a base region | Shown through the `/features` `separable_groups[eyes].note`; no UI option |

---------------------------------------------------------------------------------------------------
## 1. Folder structure and layering

### 1.1 Layers
```
Backend /api/v1 (REST + WS)                               contract: DESIGN.md §6 (merged into api-contract.md by S0)
  │
src/api/            typed axios client + RealtimeClient. EXISTING (unchanged except ws.ts, types.ts append)
  │                 + partAwareTypes.ts, features.ts, imageGenerations.ts, segmentations.ts, partAnalyses.ts,
  │                   workflows.ts, files.ts                                    — no store/React imports
  ▼
src/store/          zustand stores = single source of truth
  │                 existing: auth, system, reference(+adoptSet), settings(re-export), job(+adopt), ui, toast,
  │                           meshTuner (untouched), viewerRuntime (components/viewer)
  │                 new: features, preferences, navigation, input, partOptions, run, part, announcer,
  │                      planFromStores (glue), workflow (imported only by the Node shell → lazy chunk)
  ├── src/lib/  (PURE)       request builders, plan, availability, features normalisation, ui-mode/posture rules,
  │                          parts model, run-step views, labels, error text
  ├── src/workflow/ (PURE)   node engine: types, registry, graph ops, validate, derive, compile, status, serialize
  ▼
src/actions/        commands every shell uses: startGeneration, cancelActiveRun, runGenerateShortcut,
  │                 adoptCandidateAndGenerate, analyzePartsForViewedJob, downloadArtifact, warmUpModel, applyPipelineConfig
src/hooks/          derived/reactive: useGenerateAction, useActiveRun, useViewport, useNavPosture, useResolvedUiMode,
  │                 useReveal, useFeatures, useTunerLocked, useTunerVisible, usePartsView, useSoftKeyboard (+ existing hooks)
  ▼
src/components/     FEATURE CONTENT (renderer-agnostic): XxxContent + existing XxxPanel cards; primitives
  ▼
src/shells/         RENDERERS: default/ (eager) · mobile/ · tablet/ · node/ (lazy; node/canvas/ lazy again) · shared/
  ▼
src/app/            root: session, PreSession, ModeSwitch, GlobalOverlays, GlobalShortcuts, Announcer, document attrs
```

### 1.2 File plan (N = new, M = modified; everything else unchanged; owner slice in brackets, §11)
```
frontend/
  package.json, package-lock.json                M  "@xyflow/react": "12.12.0" (exact)                        [F11]
  vite.config.ts                                 M  anchored vendor group + nodeui group + build.manifest (§8.2) [F11]
  src/main.tsx                                   M  bootDocument() before createRoot                           [F6]
  src/App.tsx                                    M  root: session, PreSession | ModeSwitch, GlobalOverlays      [F6]
  src/vite-env.d.ts                              M  VITE_FEATURE_REFINEMENT_PREVIEW? (no tuner build flag, §3.9) [F5]
  src/api/
    types.ts                                     M  APPEND-ONLY block: ImageSourceOut + ReferenceImage.source? (interface
                                                    merging); WsServerMessage is NOT touched                    [F1]
    ws.ts                                        M  5 new event types in RealtimeEventMap; dispatch over
                                                    WsServerMessage | PartAwareWsMessage                        [F1]
    partAwareTypes.ts features.ts imageGenerations.ts segmentations.ts partAnalyses.ts workflows.ts files.ts  N [F1]
  src/lib/
    storage.ts                                   M  + 5 keys (§3.2)                                            [F2]
    settingsRequest.ts                           N  toRequestSettings + SettingsForm types MOVED VERBATIM      [F2]
    imageJobRequest.ts workflowRequest.ts generatePlan.ts generateAvailability.ts forms.ts
    featureFlags.ts uiModes.ts viewport.ts partsModel.ts runSteps.ts featureMeta.ts errorText.ts       N (PURE) [F2]
    buildFlags.ts motion.ts                      N  (impure: import.meta.env / matchMedia)                      [F5]/[F4]
  src/workflow/engine/ types.ts registry.ts graph.ts validate.ts plan.ts derive.ts compile.ts status.ts
                       serialize.ts template.ts index.ts                                            N (PURE) [F3]
  src/workflow/nodes/  overlays.ts builtin.ts index.ts                                              N (PURE) [F3]
  src/store/
    settingsStore.ts                             M  re-export the moved symbols (no behaviour change)          [F2]
    jobStore.ts                                  M  submit() builds via buildImageJobRequest; + adopt(jobId)    [F5]
    referenceStore.ts                            M  + adoptSet(set)                                            [F5]
    featuresStore.ts preferencesStore.ts navigationStore.ts inputStore.ts partOptionsStore.ts runStore.ts
    partStore.ts announcerStore.ts planFromStores.ts                                                  N [F5]
    workflowStore.ts                             N  (Node chunk only)                                          [F11]
  src/actions/ generation.ts parts.ts downloads.ts models.ts pipeline.ts                              N [F5]
  src/hooks/
    useGenerateAvailability.ts                   M  thin wrapper over lib/generateAvailability (same strings)  [F5]
    useBackendSession.ts                         M  features, runs, parts, WS events; tuner wiring as today (§2.12) [F5]
    (useViewedTuning.ts stays UNCHANGED: past tuned results stay viewable, §3.9)
    useBreakpoint.ts                             M  P7 phone-landscape rule (§4.2)                             [F12]
    useGenerateAction.ts useActiveRun.ts useViewport.ts useNavPosture.ts useResolvedUiMode.ts useReveal.ts
    useFeatures.ts useTunerLocked.ts useTunerVisible.ts usePartsView.ts                               N [F5]
    useSoftKeyboard.ts                                                                                N [F4]
  src/app/ ModeSwitch.tsx shellRegistry.ts ShellErrorBoundary.tsx ShellFallback.tsx(+css) PreSession.tsx
           GlobalOverlays.tsx GlobalShortcuts.tsx Announcer.tsx documentAttributes.ts                N [F6]
  src/shells/
    default/ DefaultShell.tsx index.ts                                                                N [F6]
    {mobile,tablet,node}/index.ts + ready.ts   N placeholders (index.ts = `export default function ShellPlaceholder(): null
                                               { return null }`, never loaded while ready.ts SHELL_READY=false) [F6→F9/F10/F11]
    shared/  CompactTopBar.tsx StatusSheetContent.tsx ProgressPill.tsx GenerateBlockedContent.tsx AppSettingsButton.tsx
             FeatureIcon.tsx PhoneLayout.tsx SectionHost.tsx SheetHost.tsx navItems.ts (+ .module.css)  N [F9a]
    mobile/  MobileShell.tsx MobileShell.module.css mobileSections.ts                                  N [F9]
    tablet/  TabletShell.tsx RailLayout.tsx BottomLayout.tsx TabletShell.module.css                    N [F10]
    node/    NodeShell.tsx NodeShell.module.css NodeToolbar.tsx NodeInspector.tsx NodeListView.tsx ProblemsPanel.tsx
             AddNodePalette.tsx ViewerDock.tsx NodeLibraryDrawer.tsx nodeShortcuts.ts useNodeRunStates.ts
             useWorkflowValidation.ts useNodeSummary.ts bindings.ts runNodeWorkflow.ts                                  N [F11]
    node/canvas/ NodeCanvas.tsx PipelineNode.tsx PortHandle.tsx nodeTypes.ts flowAdapter.ts flow.module.css  N [F11]
  src/components/
    common/  Sheet.tsx FloatingNav.tsx NavRail.tsx Skeleton.tsx RadioCardGroup.tsx ProgressRing.tsx VisuallyHidden.tsx
             (+ .module.css), modalStack.ts                                                            N [F4]
             Dialog.tsx Toaster.tsx               M  modal registration + toast portal target (§4.1)      [F4]
             Button/SegmentedControl/Switch/RangeSlider/NumberField/Dialog/Toaster/Panel/Menu .module.css M [F4]
    touch pass (min-block-size: max(<today's px>, var(--hit-min)) under data-density='touch', §3.6)   M [F4]:
             layout/{EngineStatus,ConnectionIndicator}.module.css, settings/{PolygonTargetControl,AdvancedSettings,
             Field,SeedControl}.module.css, generation/ProgressCard.module.css, viewer/ModelStatsCard.module.css,
             references/ReferenceTile.module.css (→ F12 for the P17 shimmer)
    featureBundle.ts lazyFeatures.ts               N  one lazy chunk for the post-/features content (§8.1)   [F7]
    generation/ GenerateAction.tsx(+css)                                                               N [F6]
                GenerateCard.tsx                 M  thin wrapper: <GenerateAction variant={variant}/>            [F6]
                ProgressContent.tsx PipelineSteps.tsx(+css)                                            N [F7]
                ProgressCard.tsx                 M  Panel + ProgressContent                                    [F7]
    references/ ReferenceContent.tsx N · ReferencePanel.tsx M (Panel + ReferenceContent)                 [F7]
    settings/   SettingsContent.tsx PartOptionsSection.tsx(+css) SegmentationOptionsContent.tsx LicenseNotice.tsx N
                SettingsPanel.tsx M (Panel + SettingsContent)                                          [F7]
    input/      InputSourceSwitch.tsx InputSourceCard.tsx InputContent.tsx PromptContent.tsx PromptPanel.tsx
                PromptReviewContent.tsx ColdStartNotice.tsx SegmentationPreview.tsx (+ .module.css)    N [F7]
    parts/      PartsContent.tsx PartsToolbar.tsx PartRow.tsx PartInspector.tsx PartsEmpty.tsx PartsPanel.tsx
                PartKindBadge.tsx (+ .module.css)                                                      N [F7]
    export/     ExportContent.tsx ExportPanel.tsx (+css)                                               N [F7]
    preferences/ AppSettingsDialog.tsx AppSettingsBody.tsx UiModePicker.tsx DensityPicker.tsx MotionPicker.tsx
                 NodeEditorPrefs.tsx FeatureStatusList.tsx ModelRegistryList.tsx LicenseBadge.tsx (+css) N [F6]
    viewer/     ModelStatsContent.tsx N · ModelStatsCard.tsx M (lazy TunedDownloadButton; tuned stats
                only where the tuner is visible, §3.9)                                                 [F7]
                PartsModel.tsx partsScene.ts partsIndex.ts(PURE) N · ModelScene.tsx ModelViewer.tsx ViewerPanel.tsx
                ViewerToolbar.tsx viewerTypes.ts viewerRuntime.ts sceneUtils.ts
                (+ ViewerPanel/ViewerToolbar .module.css)                                              M [F8]
    layout/     Header.tsx AppShell.tsx MobileTabBar.tsx M (gear, tuner lock slots, reveal)              [F6]
    tunerLock/  TunerLockFrame.tsx(+css) TunerLockNotice.tsx lazyTuner.ts tunerBundle.ts             N [F6]
                AppShell.tsx (placements P2/P3), AppShell/Header/MobileTabBar/GpuMeter .module.css, GpuMeter.tsx M [F12]
    history/    HistoryItem.tsx HistoryDrawer.tsx M [F6] · HistoryList.tsx M [F7] · *.module.css M [F12]
    meshTuner/  UNTOUCHED (rendered, locked, through tunerLock/, §3.9)
  src/styles/ tokens.css global.css M · modes.css N                                                    [F4]
  src/App.module.css M (P8/P19)                                                                        [F12]
test/frontend/  mock/part_aware_mock.mjs mock/glb_parts.mjs fixtures/** ui_modes_mock_smoke.mjs
                review/{import_boundaries.mjs, import_allowlist.json, bundle_budget.mjs, ui_probe.js,
                        protected_frontend.mjs, protected_frontend.sha256}                             N [F0]
                mock_backend.mjs                                                                       M [F0]
                unit/*.test.mjs                                                         N [owner of the tested module]
THIRD_PARTY_NOTICES.md  M  xyflow rows (DESIGN §11: S6 first, then F11 after S6 merged; until then F11
                           hands its rows to the S6 owner)                                            [F11]
frontend/README.md      M  modes, flags, storage keys, test commands                                  [F13]
```

### 1.3 What moves vs what stays
| Item | Decision |
|---|---|
| `components/layout/{AppShell,Header,MobileTabBar,mobileTabs}` | **Stay** at their paths; `DefaultShell` imports them. No file of the working UI moves |
| `XxxPanel` components (`ReferencePanel`, `SettingsPanel`, `ProgressCard`, `ModelStatsCard`) | **Stay** with the same name, path and DOM; their body moves into a sibling `XxxContent` (same file folder) so sheets, rail panels and node inspectors reuse it |
| `toRequestSettings`, `SAMPLER_FIELDS`, `SettingsForm`, `SamplerForm`, `SamplerField`, `hasAdvancedOverrides` | **Move verbatim** from `store/settingsStore.ts` (L26, L28, L45, L47-55, L107-109, L112-135 [V]) to `lib/settingsRequest.ts` (pure; imports `SAMPLER_KEYS` and types from `../api/types.ts`), **re-exported** from `settingsStore.ts` so every existing import compiles. `emptySampler`, `clampToCapabilities`, `formFromJobSettings` and the store stay |
| Ctrl/Cmd+Enter (`GenerateCard.tsx:L43-60` [V]) | **Moves** to `app/GlobalShortcuts.tsx` (single registration); `GenerateCard`'s `shortcut` prop and its three call sites in `AppShell.tsx` are removed (F6) |
| `setMobileTab('viewer')` after submit (`GenerateCard.tsx:L40`) and after opening history (`AppShell.tsx:L139`) | **Replaced** by `navigationStore.requestReveal('viewer')`; the Default shell maps it to `setMobileTab('viewer')`. `uiStore` itself is unchanged |
| `BlenderSetupDialog` mount (`App.tsx:L39` [V]) | **Moves** to `app/GlobalOverlays.tsx`, mounted only in the Default mode while the tuner is **unlocked** (legacy backend, or approved); never while locked (§3.9) |
| Static imports of the paused tuner components (`AppShell.tsx:L10` `TuningCard`, `Header.tsx:L8` `BlenderRuntimeStatus`, `App.tsx:L8` `BlenderSetupDialog`, `ModelStatsCard.tsx:L17` `TunedDownloadButton` [V]) | **Become `lazy()`** through `components/tunerLock/lazyTuner.ts` (F6): `tunerBundle.ts` re-exports the four components, so Rolldown emits **one** lazy chunk; `lazyTuner.ts` declares `LazyTuningCard = lazy(() => import('./tunerBundle.ts').then((m) => ({ default: m.TuningCard })))` etc. plus `preloadTuner()`. Each is rendered inside its own `<Suspense fallback={null}>`, in the same place as today, wrapped by `TunerLockFrame` (§3.9). The protected files stay byte-identical; only the importers (F6: `AppShell`, `Header`, `GlobalOverlays`; F7: `ModelStatsCard`) change. The ≈ 1.7 k lines of tuner components leave the initial chunk and are never downloaded by Mobile/Tablet/Node; Default downloads the chunk every session, preloaded at boot (§3.6, §8.1). `meshTunerStore`, `lib/meshTuner.ts` and the tuner API stay eager (session wiring needs them, §2.12) |
| `uiStore`, `meshTunerStore`, `stages.ts`, `api/jobs.ts`, `api/references.ts`, `api/client.ts` | **Unchanged** |

### 1.4 Import rules (enforced by `test/frontend/review/import_boundaries.mjs`; allowlist is deletion-only)
Definitions used by the table:
- **URL helpers** = exactly {`resolveApiUrl`, `fileUrl`, `withQuery`, `jobModelUrl`, `jobPreviewUrl`,
  `referenceThumbnailUrl`, `tunedModelUrl`, `API_BASE_URL`} (value imports from `api/client`, `api/files`,
  `api/jobs`, `api/references`, `api/tunings`) [V today: `BackendBanner.tsx:L3`, `ReferenceTile.tsx:L5`,
  `ViewerPanel.tsx:L4-5`, `HistoryItem.tsx:L4`].
- **API constants**: value constants of `api/types.ts` and `api/partAwareTypes.ts` (`SINGLE_SLOTS`,
  `REFERENCE_SLOTS`, `SAMPLER_KEYS`, `GRAPH_ISSUE_CODES`, …) are allowed from **every** layer (both files are
  import-free) [V today: `ReferencePanel.tsx:L5`, `ReferenceTile.tsx:L6`, `AdvancedSettings.tsx:L4`].

| Layer | May import | Must not import |
|---|---|---|
| `api/` | axios, `lib/storage`, `api/*` | `store/`, `hooks/`, `actions/`, `components/`, `shells/`, `app/` |
| PURE (`lib/` pure files, `workflow/**`, `components/viewer/partsIndex.ts`, `shells/node/canvas/flowAdapter.ts`) | values only from other pure files and `api/types.ts` / `api/partAwareTypes.ts` (both import-free), with `.ts` specifiers; `import type` from anywhere | react, zustand, axios, three, `@xyflow/*` values, `store/`, `components/`, globals `window`/`document`/`localStorage`/`import.meta.env` |
| `store/` | `api/`, `lib/`, `workflow/` (workflowStore only), other stores, acyclic: `run → job, part, reference, input, features, announcer, toast`; `part → features, announcer, toast`; `workflow → features, input, partOptions, settings, toast` (`syncFromStores` reads `input.source`/`partOptions.form`; "Load these settings" writes settings/input/partOptions; the newer-version restore toasts, §4.3.6, §4.3.10); `planFromStores → features, input, partOptions, reference, settings, job, run, navigation, system` (a glue module, not a store: it creates no state and is value-imported **only** by `actions/` and `hooks/`); `features, preferences, navigation, input, partOptions, announcer → none` (the announcer store holds the queue only; routing to the topmost modal region is done by `app/Announcer.tsx`, §4.1) | `hooks/`, `actions/`, `components/` (incl. `components/common/modalStack.ts`), `shells/`; no store imports `planFromStores` |
| `actions/` | `store/`, `api/`, `lib/` | react, `components/`, `shells/` |
| `hooks/` | react, `store/`, `actions/`, `lib/`, URL helpers; **one permanent exception**: `hooks/useBackendSession.ts` → `api/client` {`configureClientHooks`, `getAuthToken`} and `api/ws` (`RealtimeClient`) [V `useBackendSession.ts:L11-12`] | `components/`, `shells/`; any other `api/` value |
| `components/` | `hooks/`, `actions/`, `store/` (selectors + actions), `lib/`, URL helpers | `shells/`, `app/`, `@xyflow/*`; `three`/`@react-three/*` except `viewer/{ModelViewer,ModelScene,PartsModel}.tsx`, `viewer/{sceneUtils,partsScene}.ts` |
| `shells/<mode>/` | `components/`, `hooks/`, `actions/` (incl. `applyPipelineConfig`), `store/`, `shells/shared/`, `lib/`, `workflow/` (node only) | `api/*` values (the Node shell validates through `workflowStore.validateRemote`, §4.3.5); another `shells/<mode>/`; `@xyflow/*` (except `shells/node/canvas/*`) |
| `app/` | `shells/*/index.ts` (lazy, through `shellRegistry.ts`), `shells/*/ready.ts` (static, one boolean), `components/`, `hooks/`, `store/`, `lib/` | `api/*` values |

Grandfathered (in `import_allowlist.json`; an entry that no longer violates **fails** the script with the message
"stale allowlist entry: delete it"): `components/meshTuner/**` → `api/blender`, `api/tunings`;
`components/viewer/ModelStatsCard.tsx` → `api/jobs.downloadJobGlb` (until F7 routes it through
`actions/downloads.ts`). **Ownership rule (§11):** F0 owns the file and is the only slice that may *add* entries;
any later slice may *delete* the entries whose `from` file it owns, in the same commit that removes the violation
(F7 deletes the `ModelStatsCard.tsx` entry). A deletion touches only that slice's own rows, so it cannot conflict.

### 1.5 Pure-module rules (Node-testable)
- File list (checked by `pure_imports.test.mjs`): `lib/{settingsRequest,imageJobRequest,workflowRequest,
  generatePlan,generateAvailability,forms,featureFlags,uiModes,viewport,partsModel,runSteps,featureMeta,errorText,
  storage}.ts`, `workflow/**/*.ts`, `components/viewer/partsIndex.ts`, `shells/node/canvas/flowAdapter.ts`,
  `api/types.ts`, `api/partAwareTypes.ts`.
- Value imports use relative `.ts` specifiers; type imports use `import type` (erased; extensionless is fine).
- No top-level side effects except frozen constants. No `import.meta.env`, `window`, `document`, `localStorage`,
  `matchMedia`, `navigator`, timers.
- `lib/storage.ts` is in the list for its **import** rules only: it contains `window.localStorage` inside a guarded
  function (`typeof window !== 'undefined'`) [V `lib/storage.ts:L18-24`, token at L20], so `pure_imports.test.mjs`
  exempts this one file from the global-token scan; tests never call those functions.
- `store/settingsStore.ts` is **not** pure and is never imported under Node: it has extensionless value imports
  (`'../api/types'`, `'../lib/format'`, `'../lib/storage'`, [V `settingsStore.ts:L8-22`]) that fail with
  `ERR_MODULE_NOT_FOUND` under Node 22.17. Its re-exports are checked statically (§10.2).

### 1.6 Singletons (exactly one owner each)
| Singleton | Owner |
|---|---|
| `useBackendSession()` | `App.tsx` |
| Ctrl/Cmd+Enter, Ctrl+`,` (App settings), `?` (shortcut help) | `app/GlobalShortcuts.tsx` |
| Toaster, App settings dialog, Announcer, `BlenderSetupDialog` (Default mode, tuner unlocked only, §3.9) | `app/GlobalOverlays.tsx` (while a modal `<dialog>` is open, the toast stack and one announcer region pair are portaled into the topmost modal, §4.1 `modalStack`) |
| Modal registry (`modalStack`) | `components/common/modalStack.ts` (F4); `Dialog` and modal `Sheet` register themselves |
| 3D viewer | the mounted shell (one `ViewerPanel`) |
| Paste-to-upload listener | `ReferenceContent` while `active` |
| `storage` event listener (preferences cross-tab) | `app/GlobalOverlays.tsx` |
| Node run override (`navigationStore.runOverride`) | `shells/node/NodeShell.tsx` (set on mount, cleared on unmount) |

---------------------------------------------------------------------------------------------------
## 2. Shared API layer (one layer for all four modes)

### 2.1 Wire types: `src/api/partAwareTypes.ts` (PURE, import-free except `import type` from `./types.ts`)
Copied from DESIGN §3.10–§3.12, §4.4–§4.7 and §6. snake_case as on the wire. Every field the backend may add later
is ignored (`ApiModel` is `extra="ignore"` server-side; the UI reads only what is listed).
```ts
import type { GenerationSettings, IsoDateTime, JobError, JobStage, JobStatus, ReferenceImage,
  ReferenceSet, ReferenceSlot, MeshStats } from './types'

// ---------- shared run framework (DESIGN §4.3) ----------
export type RunStatus = JobStatus                               // queued | running | completed | failed | cancelled
export type RunKind = 'image_generation' | 'segmentation' | 'part_analysis'
export type RunStepStatus = 'pending' | 'running' | 'done' | 'warning' | 'skipped' | 'failed' | 'cancelled'
export interface RunStepOut { id: string; label: string; status: RunStepStatus; message: string | null
  progress: number | null /* 0..1 within the step */; duration_s: number | null; details: Record<string, unknown> }
export interface RunProgressOut { percent: number; step_id: string | null; step_index: number; step_count: number
  message: string | null; updated_at: IsoDateTime | null }
export interface RunWarningOut { code: string; message: string; step_id: string | null }
export interface ModelUseOut { id: string; revision: string | null; license_id: string }
export interface RunOut<K extends RunKind, O, R> { id: string; kind: K; status: RunStatus; created_at: IsoDateTime
  started_at: IsoDateTime | null; finished_at: IsoDateTime | null; workflow_id: string | null; options: O
  models: ModelUseOut[]; progress: RunProgressOut; steps: RunStepOut[]; warnings: RunWarningOut[]
  result: R | null; error: JobError | null }

// ---------- discovery (DESIGN §6.1) ----------
export type ModelKind = 'text_to_image' | 'segmentation' | 'part_extraction' | 'segmentation_3d' | 'completion' | 'part_generation'
export type ModelUiState = 'not_installed' | 'installed' | 'loading' | 'loaded' | 'error' | 'unavailable'
export type CommercialUse = 'allowed' | 'allowed_with_conditions' | 'revenue_capped' | 'non_commercial' | 'research_only' | 'unknown'
export type ReasonCode = 'ok' | 'unsupported' | 'disabled' | 'not_approved' | 'license_blocked' | 'vendored_source_missing'
  | 'missing_dependency' | 'not_installed' | 'incompatible' | 'insufficient_vram' | 'error' | 'not_implemented'
  | 'engine_unavailable'                                         // DESIGN §5.1: one vocabulary for every `code`
export interface CapabilityOut { available: boolean; code: string /* a ReasonCode; unknown future codes tolerated */
  reason: string | null }   // every pipeline, profile, strategy and node entry has all three (DESIGN §6.1)
export interface PartGroupInfoOut { id: string; label: string; default_kind: 'physical' | 'physical_deferred' | 'region'
  default_selected: boolean; note: string | null }
export interface StrategyOut extends CapabilityOut { id: string; label: string; requires_segmentation: boolean }
export interface ProfileOut extends CapabilityOut { id: 'baseline' | 'anime' | 'realistic' }
export interface FeatureNodeOut extends CapabilityOut { type: string; title: string; category: string; locked: boolean
  hidden_by_default: boolean }
export interface LicenseOut { id: string; name: string; url: string; commercial: CommercialUse; gated: boolean
  requires_acceptance: boolean; accepted?: boolean; conditions: string[]; attribution?: string | null }
export interface ModelOut { id: string; kind: ModelKind; roles: string[]; display_name: string; state: ModelUiState
  reason_code: string; reason: string | null; approved: boolean; is_default: boolean; repo_id: string | null
  revision: string | null; size_bytes: number | null; vram_estimate_gb: number | null; ram_gb: number | null
  cold_load_s: number | null; license: LicenseOut
  dependencies: { distribution: string; requirement: string; installed: string | null; ok: boolean }[]
  vendored_source: string | null; local_path: string | null
  install: { supported: boolean; hint: string | null; bytes_done: number | null; bytes_total: number | null }
  capabilities: Record<string, unknown>; loaded_device: 'cpu' | 'cuda' | null; last_used_at: IsoDateTime | null
  hidden_by_default: boolean }
export interface TaxonomyClassOut { value: number; id: string; group: string; label: string
  default_kind: 'base' | 'physical' | 'physical_deferred' | 'region'; attach_to: string[]; symmetric_with: string | null; sided: boolean }
export interface FeaturesOut {
  schema: 'img3d.features/1'
  api: { version: string; workflows: boolean; part_analyses: boolean; image_generations: boolean; segmentations: boolean }
  flags: { part_aware: boolean; image_generation: boolean; segmentation: boolean; part_analysis: boolean; workflows: boolean
           mesh_tuning: boolean; part_refinement: boolean; persist_generation_intermediates: boolean }
  pipeline: {
    image_to_3d: CapabilityOut
    prompt_to_3d: CapabilityOut & { max_prompt_chars: number; presets: ('character' | 'object' | 'raw')[]
      styles: ('realistic' | 'stylized' | 'anime')[]; supports_negative_prompt: boolean; default_model_id: string | null
      default_model_state: ModelUiState | null; cold_start_s: number | null; max_images: number
      sizes: [number, number][]; selection_modes: string[] }
    segmentation: CapabilityOut & { subjects: string[]; profiles: ProfileOut[]; default_profile: string
      max_extra_terms: number; before_reconstruction: boolean }
    part_extraction: CapabilityOut & { strategies: StrategyOut[]; default_strategy: string | null
      separable_groups: PartGroupInfoOut[]; region_groups: PartGroupInfoOut[]; max_views: number; default_views: number
      rebuild: boolean; per_part_glbs: string }
  }
  workflow: CapabilityOut & { catalog_url: string; catalog_etag: string; max_nodes: number; max_edges: number
    graph_max_bytes: number; selection_modes: string[]; legacy_job_node_map: Partial<Record<JobStage, string>> }
  nodes: FeatureNodeOut[]
  mesh_tuning: { enabled: boolean; reason: string | null }
  part_refinement: { enabled: boolean; reason: string | null }
  models: ModelOut[]
  taxonomy: { version: number; groups: string[]; classes: TaxonomyClassOut[] }
  queues: { runs: { pending: number; running_id: string | null; running_kind: RunKind | null; max_size: number } }
  limits: { prompt_max_chars: number; negative_prompt_max_chars: number; extra_terms_max: number; views_max: number
            workflow_graph_max_bytes: number; max_parts: number }
}

// ---------- image generations (DESIGN §4.4, §6.2) ----------
export type PromptPreset = 'character' | 'object' | 'raw'
export type PromptStyle = 'realistic' | 'stylized' | 'anime'
export interface ImageGenerationRequest { prompt: string; negative_prompt?: string | null; model_id?: string | null
  preset?: PromptPreset; style?: PromptStyle | null; part_hint?: boolean; num_images?: number; seed?: number
  randomize_seed?: boolean; width?: number | null; height?: number | null; steps?: number | null; guidance?: number | null }
export interface ImageCandidateOut { index: number; seed: number; width: number; height: number; url: string
  thumbnail_url: string; readiness: { ok: boolean; issues: string[]; method: string }
  used_as: { reference_set_id: string; image_id: string }[] }
export type ImageGenerationOut = RunOut<'image_generation',
  Required<Pick<ImageGenerationRequest, 'prompt' | 'preset' | 'part_hint' | 'num_images' | 'seed'>> & {
    effective_prompt: string; negative_prompt: string | null; model_id: string; style: PromptStyle | null
    width: number; height: number; steps: number; guidance: number; expected_terms: string[]; final_prompt_sha256: string },
  { candidates: ImageCandidateOut[]; timings: Record<string, number> }>
export interface UseCandidateRequest { reference_set_id?: string | null; slot?: ReferenceSlot; make_primary?: boolean; label?: string | null }
export interface UseCandidateOut { image: ReferenceImage; set: ReferenceSet }
export type { ImageSourceOut } from './types'   // declared in types.ts (append-only block) so types.ts stays import-free

// ---------- segmentations (DESIGN §4.5, §6.3) ----------
export type SegProfile = 'auto' | 'baseline' | 'anime' | 'realistic'
export type SegSubject = 'auto' | 'anime_character' | 'realistic_human' | 'object'
export interface SegmentationOptions { profile?: SegProfile; subject?: SegSubject; classes?: string[] | null
  extra_terms?: string[]; use_prompt_terms?: boolean; head_pass?: boolean; min_confidence?: number }
export type SegmentationSource = { kind: 'reference_image'; reference_set_id: string; image_id: string } | { kind: 'job'; job_id: string }
export interface SegmentationRequest { source: SegmentationSource; options?: SegmentationOptions }
export interface SegLabelOut { value: number; category: string; group: string; area_px: number; confidence: number
  bbox: [number, number, number, number]; side: 'left' | 'right' | 'center' | null; sources: string[]; mask_url: string }
export type SegmentationOut = RunOut<'segmentation', SegmentationRequest, { space: 'conditioning' | 'source'
  frame: Record<string, unknown> | null; subject: SegSubject; profile: SegProfile; labels: SegLabelOut[]
  annotations: Record<string, unknown>; expected_terms_missing: string[]; overlay_url: string; labels_url: string
  timings: Record<string, number> }>

// ---------- part analyses (DESIGN §3.10, §4.6, §6.4) ----------
export type SeparableGroupId = 'eyes' | 'eyebrows' | 'eyelashes' | 'hair' | 'clothes' | 'accessories' | 'detachable'
export type PartKind = 'base' | 'physical' | 'physical_deferred' | 'region'
export interface PartExportOptions { per_part_glbs?: boolean /* default false */; previews?: boolean /* default true */ }
export interface PartAnalysisOptions { strategy?: string | null
  segmentation_id?: string | null                // direct analyses only; MUST be absent/null inside workflows (422 conflicting_fields)
  segmentation?: SegmentationOptions | null; separate?: string[]; views?: number
  min_part_faces?: number | null                 // null (default) → max(50, round(1e-4·F)) server-side
  max_parts?: number; split_shells?: boolean; export?: PartExportOptions }   // no assembled_glb: model_parts.glb is always written
export interface PartRefinementOut { status: string; locked: boolean; eligible: boolean; needs: string[] }
export interface PartSummaryOut { id: string; label: string; category: string; group: string; kind: PartKind
  node_name: string | null; region_value: number | null; faces: number; confidence: number | null
  side: 'left' | 'right' | 'center' | null; model_url: string | null; download_url: string | null
  mask_url: string | null; thumbnail_url: string | null; refinement: PartRefinementOut }
export interface PartAnalysisResultOut { status: 'completed' | 'partial'; manifest_url: string
  assembled_url: string; download_url: string          // never null (DESIGN §4.6.7)
  zip_url: string; overlay_url: string | null /* null for 'components' (DESIGN §4.6.7) */; face_labels_url: string
  preview_urls: Partial<Record<'front' | 'back' | 'left' | 'right', string>>   // {} when the previews step is skipped
  region_attribute: string
  part_count: number; physical_count: number; deferred_count: number; region_count: number
  camera_fit_iou: number | null /* null for 'components' */; unlabelled_face_ratio: number
  partner_filled_ratio: number; cut_partner_consistency: number | null   // DESIGN §3.10 summary; null without cuts
  parts: PartSummaryOut[]; stats: MeshStats; timings: Record<string, number> }
export type PartAnalysisOut = RunOut<'part_analysis', PartAnalysisOptions, PartAnalysisResultOut> & { job_id: string }
/** Subset of manifest img3d.parts/1 the UI reads (DESIGN §3.10, §3.10.1); everything else is ignored. */
export interface PartRecordOut { id: string; category: string; group: string; label: string; kind: PartKind
  side: 'left' | 'right' | 'center' | null; image_side: string | null; parent: string | null; attach_to: string | null
  host: string | null; symmetric_with: string | null; node_name: string | null; mesh_index: number | null
  region: { attribute: string; value: number; face_count: number }
  files: { glb: string | null; mask: string | null; thumbnail: string | null }
  stats: { faces: number; vertices: number; area: number; bbox_min: number[]; bbox_max: number[]; centroid: number[] }
  geometry: { status: string; open_boundary_loops: number; watertight: boolean; occluded_geometry_missing: boolean
              components: number; source: string; tags: string[]
              floaters: { component: number; faces: number; centroid: number[] }[] }   // always present (DESIGN §3.10.1, §3.11); non-empty only on the base record
  confidence: number | null; expected: boolean; refinement: PartRefinementOut & { lock_reason?: string } }
export interface RelationOut { id: string; type: string; a: string; b: string; via: string | null
  metrics: Record<string, unknown>; source: string; confidence: number }
export interface PartsManifestOut { schema: string; analysis_id: string; job_id: string; taxonomy_version: number
  region_attribute: string; label_table: { value: number; category: string }[]
  meshes: { index: number; part_id: string; node_name: string; faces: number }[]; parts: PartRecordOut[]
  relations: RelationOut[]; seams: { id: string; a: string; b: string; host_opening: boolean }[]
  expectations: { source: string | null; expected_classes: string[]; missing: string[] }
  refinement: { enabled: boolean; policy: string; consumers: unknown[] }
  summary: { part_count: number; physical_count: number; deferred_count: number; region_count: number
    unlabelled_face_ratio: number; partner_filled_ratio: number; cut_partner_consistency: number | null
    camera_fit_iou: number | null }
  warnings: { code: string; message: string; part_id: string | null }[] }
  // buffers[] (face_labels, face_owner, face_part, face_flags, …) are listed in the manifest but the UI never fetches them

// ---------- workflows (DESIGN §4.7, §6.5) ----------
export type PortTypeId = 'image_set' | 'segmentation' | 'mesh' | 'part_set'
export type WorkflowSource = { kind: 'image'; reference_set_id: string; primary_image_id: string | null }
  | { kind: 'prompt'; generation: ImageGenerationRequest } | { kind: 'job'; job_id: string }
export interface WorkflowGraphNode { id: string; type: string; params: Record<string, unknown> }
export interface WorkflowGraphEdge { from: { node: string; port: string }; to: { node: string; port: string } }
export interface WorkflowGraph { schema: 'img3d.workflow_graph'; version: 1; nodes: WorkflowGraphNode[]; edges: WorkflowGraphEdge[] }
export interface WorkflowRequest { name?: string | null; source?: WorkflowSource
  segmentation?: SegmentationOptions | null          // only together with `parts` (else 422 conflicting_fields)
  reconstruction?: { settings: GenerationSettings } | null
  parts?: PartAnalysisOptions | null                 // `parts.export` carries the export options; `parts.segmentation_id` never sent
  graph?: WorkflowGraph | null; layout?: Record<string, unknown> | null }   // NO top-level `export` (422 extra_forbidden)
export const GRAPH_ISSUE_CODES = ['graph_too_large', 'unknown_node_type', 'unknown_port', 'type_mismatch',
  'port_already_connected', 'missing_required_input', 'no_source', 'multiple_sources', 'cycle', 'too_many_instances',
  'node_locked', 'unsupported_topology', 'capability_unavailable', 'feature_disabled', 'invalid_param', 'not_found',
  'no_sink'] as const
export type GraphIssueCode = (typeof GRAPH_ISSUE_CODES)[number]
export interface GraphIssueOut { severity: 'error' | 'warning'; code: GraphIssueCode; message: string; node_id: string | null
  port: string | null; edge_index: number | null; details: Record<string, unknown> | null }
export interface ValidationReport { valid: boolean; issues: GraphIssueOut[]; mode: 'job' | 'workflow' | null
  request: WorkflowRequest | null }   // no job_request: for mode 'job' the client posts its own CreateJobRequest (G1)
export type WorkflowStepId = 'text_to_image' | 'import_image' | 'segmentation' | 'reconstruction' | 'part_extraction' | 'export'
export interface WorkflowStepOut { id: WorkflowStepId; node: string; node_id: string | null
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped' | 'cancelled'; message?: string | null
  progress?: number | null /* 0..100 (unlike RunStepOut.progress) */
  warning_count?: number   /* DESIGN §4.7.5: len(child.warnings), default 0; done + > 0 → node state 'warning' */
  resource: { kind: 'image_generation' | 'reference_image' | 'segmentation' | 'job' | 'part_analysis'; id: string
              reference_set_id?: string; exists: boolean } | null
  artifacts: { role: string; url: string }[] }
export interface WorkflowOut { id: string; name: string | null; status: RunStatus; created_at: IsoDateTime
  started_at: IsoDateTime | null; finished_at: IsoDateTime | null; request: WorkflowRequest; graph_stored: boolean
  current_step: WorkflowStepId | null; progress: { percent: number; message: string | null; updated_at: IsoDateTime | null }
  steps: WorkflowStepOut[]; outputs: { reference_set_id: string | null; image_id: string | null
    segmentation_id: string | null; job_id: string | null; part_analysis_id: string | null; model_url: string | null
    parts_model_url: string | null; manifest_url: string | null }; error: JobError | null }
/** `error.details` when `error.code === 'step_failed'` (DESIGN §4.7.4 `StepFailedDetails`); read via a type guard. */
export interface StepFailedDetails { step: WorkflowStepId
  child_kind: 'image_generation' | 'segmentation' | 'job' | 'part_analysis'; child_id: string | null
  child_error: JobError }   // {code, message, details} — the child's own error (or the refused create call's)
export interface CatalogPortIn { id: string; label: string; accepts: PortTypeId[]; required: boolean; multiple: boolean }
export interface CatalogNodeTypeOut extends CapabilityOut { type: string; version: number; category: string; title: string
  inputs: CatalogPortIn[]; outputs: { id: string; label: string; type: PortTypeId }[]
  params_schema: { properties?: Record<string, unknown>; [key: string]: unknown }; defaults: Record<string, unknown>
  compiles_to: string | null; max_instances: number; locked: boolean; hidden_by_default: boolean }
export interface CatalogOut { schema: 'img3d.workflow_catalog/1'; etag: string; port_types: { id: PortTypeId; label: string }[]
  node_types: CatalogNodeTypeOut[]; templates: { id: string; title: string; graph: WorkflowGraph }[]
  legacy_job_node_map: Partial<Record<JobStage, string>>; limits: { max_nodes: number; max_edges: number; graph_max_bytes: number } }

// ---------- WebSocket (DESIGN §6.6) ----------
export interface PartAwareEventMap { 'features.updated': FeaturesOut; 'image_generation.updated': ImageGenerationOut
  'segmentation.updated': SegmentationOut; 'part_analysis.updated': PartAnalysisOut; 'workflow.updated': WorkflowOut }
export type PartAwareWsMessage = { [K in keyof PartAwareEventMap]: { type: K; ts: IsoDateTime; data: PartAwareEventMap[K] } }[keyof PartAwareEventMap]
```
`src/api/types.ts` append-only block (F1), still import-free:
```ts
export interface ImageSourceOut { kind: 't2i'; image_generation_id: string; candidate_index: number; model_id: string
  model_revision: string | null; license_id: string; seed: number }               // DESIGN §6.7
export interface ReferenceImage { source?: ImageSourceOut | null }                 // interface merging with L203 [V];
                                                                                   // null for uploads, absent on old backends
```
`WsServerMessage` (`types.ts:L693-699` [V]) is **not** touched (appending cannot widen a closed union, and its new
`data` types live in `partAwareTypes.ts`, which `types.ts` may not import). `ws.ts` parses incoming frames as
`WsServerMessage | PartAwareWsMessage` and its `dispatch` switch gains the five cases (§2.4).

### 2.2 Typed client functions (every DESIGN §6 endpoint the UI uses)
All use `apiClient` from `api/client.ts` (J routes: Bearer header). F routes are consumed as URLs through
`resolveApiUrl` (adds `?token=`) or downloaded with the header (§2.3). Errors throw the existing `ApiError` via
`toApiError` [V `api/client.ts`]; callers (stores) map them (§2.11).

| File | Function | Endpoint | Returns / special handling |
|---|---|---|---|
| `api/features.ts` | `getFeatures(): Promise<FeaturesResult>` with `type FeaturesResult = {kind:'ok'; data: FeaturesOut} \| {kind:'legacy'}` | `GET /features` | **404 → `{kind:'legacy'}`** (no throw); 401 throws (existing expire flow) |
| | `listModels(p?: {kind?: ModelKind; include_hidden?: boolean}): Promise<ModelOut[]>` | `GET /models` | `.items` |
| | `getModel(id: string): Promise<ModelOut>` | `GET /models/{id}` | |
| | `loadModel(id): Promise<ModelOut>` / `unloadModel(id): Promise<ModelOut>` | `POST /models/{id}/load` / `…/unload` | 202 |
| `api/imageGenerations.ts` | `createImageGeneration(body: ImageGenerationRequest): Promise<ImageGenerationOut>` | `POST /image-generations` | 202 |
| | `listImageGenerations(p?: {limit?: number; status?: RunStatus}): Promise<ImageGenerationOut[]>` | `GET /image-generations` | newest first; `limit` 1..100 (server default 20; DESIGN §6.0) for every list function |
| | `getImageGeneration(id)`, `cancelImageGeneration(id)` | `GET /image-generations/{id}`, `POST …/cancel` | |
| | `deleteImageGeneration(id): Promise<void>` | `DELETE /image-generations/{id}` | 204 |
| | `postCandidateUse(id, index, body: UseCandidateRequest): Promise<UseCandidateOut>` | `POST …/candidates/{index}/use` | 201 |
| `api/segmentations.ts` | `createSegmentation(body: SegmentationRequest)`, `listSegmentations(p?: {limit?; status?; reference_set_id?; job_id?})`, `getSegmentation(id)`, `cancelSegmentation(id)`, `deleteSegmentation(id)` | `/segmentations…` | |
| `api/partAnalyses.ts` | `createPartAnalysis(jobId, options?: PartAnalysisOptions): Promise<PartAnalysisOut>` | `POST /jobs/{id}/part-analyses` | body omitted when `options` is undefined |
| | `listPartAnalyses(jobId): Promise<PartAnalysisOut[]>` | `GET /jobs/{id}/part-analyses` | |
| | `getLatestPartAnalysis(jobId): Promise<PartAnalysisOut \| null>` | `GET /jobs/{id}/part-analyses/latest` | **404 with `details.reason === 'none'` → `null`**; other 404 throws |
| | `getPartAnalysis(id)`, `cancelPartAnalysis(id)`, `deletePartAnalysis(id)` | `/part-analyses/{id}…` | |
| | `getPartManifest(manifestUrl: string): Promise<unknown>` | the payload `manifest_url` (F route, header auth via `absolutePathConfig`) | raw JSON; normalised by `lib/partsModel` |
| `api/workflows.ts` | `getWorkflowCatalog(etag: string \| null): Promise<{kind:'ok'; data: CatalogOut; etag: string \| null} \| {kind:'not_modified'}>` | `GET /workflows/catalog` (`If-None-Match`) | the call passes `validateStatus: (s) => (s >= 200 && s < 300) \|\| s === 304` (today's default 2xx would route a 304 through the error interceptor and make it `ApiError http_304` [V `client.ts:L226-238`]); 304 (empty body, DESIGN §6.5) → `{kind:'not_modified'}` |
| | `validateWorkflow(body: WorkflowRequest, signal?: AbortSignal): Promise<ValidationReport>` | `POST /workflows/validate` | |
| | `createWorkflow(body: WorkflowRequest): Promise<WorkflowOut>` | `POST /workflows` | 202 |
| | `listWorkflows(p?: {limit?: number; status?: RunStatus; job_id?: string; image_generation_id?: string}): Promise<WorkflowOut[]>` | `GET /workflows` | |
| | `getWorkflow(id)`, `getWorkflowGraph(id): Promise<{graph: WorkflowGraph; layout: Record<string, unknown> \| null}>`, `cancelWorkflow(id)`, `deleteWorkflow(id)` | `/workflows/{id}…` | |
| `api/files.ts` | `fileUrl(payloadUrl: string): string` | – | `resolveApiUrl(payloadUrl)` (token-aware; for `<img>`, GLTFLoader) |
| | `withQuery(url: string, params: Record<string, string>): string` | – | appends DESIGN-defined query params only (`include_base`) |
| | `downloadFile(payloadUrl, o: {fallbackName: string; sizeHint?: number \| null; onProgress?: (p: number \| null) => void; signal?: AbortSignal}): Promise<{blob: Blob; filename: string}>` | any F attachment URL | same pattern as `downloadJobGlb` [V `api/jobs.ts:L60-80`]: header auth, `responseType:'blob'`, `Content-Disposition` filename |

The UI never calls `GET /segmentations/{id}/labels|source`, `GET /part-analyses/{id}/buffers/*`, `…/log` or the
DELETE routes of image generations, segmentations and workflows in v1 (retention handles them). They are still typed
(`api/*.ts` exports them) so a later slice needs no API work.

### 2.3 File URLs and downloads (payload URLs only)
| Artifact | URL source | Consumer |
|---|---|---|
| Candidate image / thumbnail | `ImageCandidateOut.url` / `.thumbnail_url` | `<img src={fileUrl(u)}>` |
| Segmentation overlay, label masks | `SegmentationOut.result.overlay_url`, `labels[].mask_url` | `<img>` |
| Assembled parts GLB (viewer) | `PartAnalysisResultOut.assembled_url` (never null) | GLTFLoader via `fileUrl` |
| Assembled parts GLB (download) | `.download_url` | `downloadFile` → `img3d_<job>_parts_<pa>.glb` |
| ZIP bundle | `.zip_url` (+ `withQuery(u, {include_base: 'true'})` when "Include base model" is ticked) | `downloadFile` |
| Per-part GLB | `parts[].download_url` (null for regions and deferred parts) | `downloadFile` (on demand server-side: shows "Preparing…" until bytes flow) |
| Part mask / thumbnail | `parts[].mask_url` / `.thumbnail_url` | `<img>` / `downloadFile` |
| Manifest | `.manifest_url` | `getPartManifest` (viewer data) and `downloadFile` |
| Workflow step artifacts | `WorkflowOut.steps[].artifacts[].url` | node thumbnails (`<img>`) |
| Original model | existing `jobModelUrl(result)` / `downloadJobGlb` [V] | unchanged |

### 2.4 WebSocket events (`api/ws.ts` M)
- `RealtimeEventMap` [V `ws.ts:L34-42`] gains the five keys of `PartAwareEventMap`; `dispatch` [V `ws.ts:L253-276`]
  gains five `case`s that `emit(type, message.data)`. Unknown types still fall to `default: break`.
- The client keeps connecting **without** `?job_id=` [V `useBackendSession.ts:L123`], so the job-scoped
  `part_analysis.updated` is delivered.
- Handlers (registered in `useBackendSession`, §2.12):

| Event | Handler | Notes |
|---|---|---|
| `features.updated` | `useFeaturesStore.getState().apply(data)` | debounced server-side (250 ms); recompute snapshot; if `workflow.catalog_etag` changed **and** the catalog was loaded before (`catalogStatus !== 'idle'`) → `ensureCatalog()` |
| `image_generation.updated` | `useRunStore.getState().upsertImageGeneration(data)` | stale guard below |
| `segmentation.updated` | `useRunStore.getState().upsertSegmentation(data)` | |
| `part_analysis.updated` | `usePartStore.getState().upsertAnalysis(data)` | terminal `completed` → `usePartStore.getState().loadSet(data.id)` (inside `upsertAnalysis`) |
| `workflow.updated` | `useRunStore.getState().upsertWorkflow(data)` | adopts the child job (§2.7) |

- **Stale guard** (all run kinds, same rule as `jobStore.isStale` [V `jobStore.ts:L58-63`]): status rank never
  decreases (`queued 0 < running 1 < terminal 2`); a terminal record is final; for equal status, a record whose
  `progress.updated_at` is older than the stored one is dropped. Pure: `lib/runSteps.isStaleRun(prev, next)`.

### 2.5 Features store and legacy handling (`store/featuresStore.ts`, `lib/featureFlags.ts`)
```ts
// lib/featureFlags.ts (PURE)
export type FeaturesStatus = 'loading' | 'ready' | 'legacy' | 'error'
export interface CapabilityView { readonly available: boolean; readonly code: string; readonly reason: string | null }
export interface FeaturesSnapshot {
  readonly status: FeaturesStatus; readonly apiVersion: string | null
  readonly flags: Readonly<{ partAware: boolean; imageGeneration: boolean; segmentation: boolean; partAnalysis: boolean
                             workflows: boolean; meshTuning: boolean; partRefinement: boolean }>
  readonly imageTo3d: CapabilityView
  readonly prompt: CapabilityView & { readonly maxChars: number; readonly presets: readonly PromptPreset[]
    readonly styles: readonly PromptStyle[]; readonly supportsNegativePrompt: boolean; readonly negativeMaxChars: number
    readonly defaultModelId: string | null; readonly defaultModelState: ModelUiState | null
    readonly coldStartS: number | null; readonly maxImages: number; readonly sizes: readonly (readonly [number, number])[] }
  readonly segmentation: CapabilityView & { readonly subjects: readonly SegSubject[]; readonly profiles: readonly ProfileOut[]
    readonly maxExtraTerms: number }
  readonly parts: CapabilityView & { readonly strategies: readonly StrategyOut[]; readonly defaultStrategy: string | null
    readonly separableGroups: readonly PartGroupInfoOut[]; readonly regionGroups: readonly PartGroupInfoOut[]
    readonly maxViews: number; readonly defaultViews: number; readonly rebuild: boolean }
  readonly workflow: CapabilityView & { readonly catalogEtag: string | null; readonly maxNodes: number
    readonly maxEdges: number; readonly graphMaxBytes: number; readonly legacyJobNodeMap: Readonly<Record<string, string>> }
  readonly nodes: readonly FeatureNodeOut[]
  readonly meshTuning: Readonly<{ enabled: boolean; reason: string | null }>
  readonly partRefinement: Readonly<{ enabled: boolean; reason: string | null }>
  readonly models: readonly ModelOut[]
  readonly taxonomy: Readonly<{ version: number; groups: readonly string[]; classes: readonly TaxonomyClassOut[] }>
  readonly runQueue: Readonly<{ pending: number; runningId: string | null; maxSize: number }> | null
}
export const LOADING_FEATURES: FeaturesSnapshot  // every new capability {available:false, code:'loading', reason:'Checking backend features…'}
export const LEGACY_FEATURES: FeaturesSnapshot   // {available:false, code:'legacy_backend', reason:'Needs a newer Img3D backend.'};
                                                 // imageTo3d {available:true, code:'ok'}; meshTuning.enabled false; models []
export const FALLBACK_LEGACY_JOB_NODE_MAP: Readonly<Record<JobStage, string>>   // DESIGN §4.7.2, verbatim
export const FALLBACK_LIMITS = { promptMaxChars: 2000, negativeMaxChars: 1000, extraTermsMax: 16, viewsMax: 24,
  maxNodes: 32, maxEdges: 64, graphMaxBytes: 65536 } as const
export function normalizeFeatures(raw: unknown): FeaturesSnapshot      // tolerant: a missing or malformed sub-object →
                                                                       // that capability {available:false, code:'missing'}
export type TunerLock = 'unlocked' | 'locked'
export function tunerLock(f: FeaturesSnapshot): TunerLock
  // 'unlocked' when f.status === 'legacy' (an old backend: today's tuner UI exactly, whatever LEGACY_FEATURES says)
  //   or (f.status === 'ready' && f.meshTuning.enabled === true) (approval: the same flag, no code change);
  // 'locked' otherwise: ready + disabled, and fail-closed while 'loading' or 'error' (the Blender download endpoints
  //   are not gated on the backend, DESIGN §8.2 G4, so an unknown state must not unlock them)
export const TUNER_LOCK_NOTICE = 'Paused until you approve the part-aware milestone'
export function tunerLockNotice(f: FeaturesSnapshot): string
  // 'ready' → TUNER_LOCK_NOTICE; 'loading' → 'Checking whether Auto Tune is available…';
  // 'error' → 'Locked: backend features could not be read.'
export function selectableT2IModels(f: FeaturesSnapshot): readonly ModelOut[]
  // kind 'text_to_image', approved, state ∈ {installed, loading, loaded}
export function defaultSeparate(f: FeaturesSnapshot): readonly string[]   // separable_groups with default_selected, in order
export function licenceNotices(f: FeaturesSnapshot): readonly { modelId: string; name: string; conditions: readonly string[] }[]
  // usable segmentation models with license.conditions.length > 0 (Sapiens2 today) — no model id is hard-coded.
  // features.models is the FULL ModelOut[] (DESIGN §6.1; hidden_by_default entries excluded), so no /models call is needed
```
```ts
// store/featuresStore.ts (not persisted)
interface FeaturesState {
  status: FeaturesStatus; snapshot: FeaturesSnapshot; error: ApiError | null
  catalog: CatalogOut | null; catalogEtag: string | null; catalogStatus: 'idle' | 'loading' | 'ready' | 'unsupported' | 'error'
  allModels: ModelOut[] | null          // GET /models?include_hidden=true, loaded by App settings ▸ Models on demand
  modelActionIds: string[]              // load/unload in flight
  refresh: () => Promise<boolean>
  apply: (raw: FeaturesOut) => void
  ensureCatalog: () => Promise<void>    // called only by the Node shell (mount) and on an ETag change once loaded;
                                        // no-op unless snapshot.status === 'ready' && api.workflows. The catalog feeds
                                        // the node registry and templates only (no request-key gating, §2.6)
  loadAllModels: () => Promise<void>
  loadModel: (id: string) => Promise<void>    // POST /models/{id}/load → then refresh()
  unloadModel: (id: string) => Promise<void>
}
```
Status machine (`status`): `loading` (initial; `snapshot = LOADING_FEATURES`) → `GET /features`:
- 200 → `ready` (`snapshot = normalizeFeatures(body)`);
- 404 → `legacy` (`snapshot = LEGACY_FEATURES`); a backend upgraded later is picked up on the next `hello`/sync;
- 401 → unchanged (the existing `onUnauthorized` expire flow runs);
- other error or network → if the previous status was `ready`, keep `ready` and its snapshot (transient), else
  `error` (`snapshot = LOADING_FEATURES` with `code:'error'`, reason "Could not read backend features. Retrying…");
  retried on the next poll, `hello` or sync.
`snapshot` is replaced only in `refresh`/`apply`, so selectors returning `s.snapshot.prompt` etc. are stable
references (zustand v5 rule).

### 2.6 Request key sets (fixed DESIGN v1 lists; no catalog-driven gating)
The builders of §2.8 emit only the keys listed there, in that order. Frontend and backend ship from one repository,
so version skew is handled by the existing paths only: `/features` 404 → legacy (§2.5), and 422 `extra_forbidden` →
toast + `featuresStore.refresh()` (§2.11). There is no `keyFilterFromCatalog`, no `dropped` list and no
`catalog_mismatch` block. The workflow catalog is used **only** by the Node shell (registry, templates).

The DESIGN §4/§6 key sets live in one test fixture, `test/frontend/fixtures/request_keys.json` (F0), used by the
mock's `--strict-requests` and by `workflow_request.test.mjs` ("every key of every built object is in the set"):
```json
{ "CreateJobRequest": ["reference_set_id", "primary_image_id", "settings"],
  "ImageGenerationRequest": ["prompt", "negative_prompt", "model_id", "preset", "style", "part_hint", "num_images",
                             "seed", "randomize_seed", "width", "height", "steps", "guidance"],
  "UseCandidateRequest": ["reference_set_id", "slot", "make_primary", "label"],
  "SegmentationRequest": ["source", "options"],
  "SegmentationOptions": ["profile", "subject", "classes", "extra_terms", "use_prompt_terms", "head_pass", "min_confidence"],
  "PartAnalysisOptions": ["strategy", "segmentation_id", "segmentation", "separate", "views", "min_part_faces",
                          "max_parts", "split_shells", "export"],
  "PartExportOptions": ["per_part_glbs", "previews"],
  "WorkflowRequest": ["name", "source", "segmentation", "reconstruction", "parts", "graph", "layout"],
  "WorkflowSource.image": ["kind", "reference_set_id", "primary_image_id"],
  "WorkflowSource.prompt": ["kind", "generation"], "WorkflowSource.job": ["kind", "job_id"],
  "node.input_image": ["reference_set_id", "primary_image_id"], "node.prompt": "=ImageGenerationRequest",
  "node.job_result": ["job_id"], "node.segmentation": "=SegmentationOptions", "node.image_to_3d": "=GenerationSettings",
  "node.part_extraction": ["strategy", "segmentation", "separate", "views", "min_part_faces", "max_parts", "split_shells"],
  "node.export": "=PartExportOptions",
  "GenerationSettings": ["resolution", "seed", "randomize_seed", "polygon_target", "texture_size", "remove_background",
                         "reference_mode", "advanced"],
  "SegmentationSource.reference_image": ["kind", "reference_set_id", "image_id"],
  "SegmentationSource.job": ["kind", "job_id"],
  "Reconstruction": ["settings"],
  "WorkflowGraph": ["schema", "version", "nodes", "edges"],
  "WorkflowGraph.node": ["id", "type", "params"],
  "WorkflowGraph.edge": ["from", "to"],
  "PortRef": ["node", "port"],
  "AdvancedSettings": ["sparse_structure", "shape", "texture"],
  "SamplerSettings": ["steps", "guidance_strength", "guidance_rescale", "rescale_t"],
  "opaque": ["layout"],
  "paths": { "SegmentationRequest.source": "SegmentationSource.<kind>", "SegmentationRequest.options": "SegmentationOptions",
    "WorkflowRequest.source": "WorkflowSource.<kind>", "WorkflowSource.prompt.generation": "ImageGenerationRequest",
    "WorkflowRequest.segmentation": "SegmentationOptions", "WorkflowRequest.reconstruction": "Reconstruction",
    "Reconstruction.settings": "GenerationSettings", "CreateJobRequest.settings": "GenerationSettings",
    "GenerationSettings.advanced": "AdvancedSettings", "AdvancedSettings.*": "SamplerSettings",
    "WorkflowRequest.parts": "PartAnalysisOptions", "PartAnalysisOptions.segmentation": "SegmentationOptions",
    "PartAnalysisOptions.export": "PartExportOptions", "WorkflowRequest.graph": "WorkflowGraph",
    "WorkflowGraph.nodes[]": "WorkflowGraph.node", "WorkflowGraph.node.params": "node.<type>",
    "WorkflowGraph.edges[]": "WorkflowGraph.edge", "WorkflowGraph.edge.from": "PortRef", "WorkflowGraph.edge.to": "PortRef" } }
```
(`<kind>` / `<type>` are replaced by the object's own `kind` / `type` value; `*` = any key of the parent set.)
- `paths` maps each nested request path to its object name; the recursive test (`assertKnownKeys`) and the mock's
  strict mode both read it, so an object reached through a path missing from `paths` fails both. `opaque` and
  `paths` are metadata, not object names. `AdvancedSettings`/`SamplerSettings` mirror the existing, unchanged job
  schema [V `backend/schemas/jobs.py:22-33`; `SAMPLER_KEYS` in `frontend/src/api/types.ts:L271`].
- `opaque` lists paths whose content is never key-checked: only `WorkflowRequest.layout` (DESIGN §4.7.1 "opaque
  Node-UI layout, never interpreted"); its own key is still checked against `WorkflowRequest`. `settings.advanced` is
  **not** opaque: the backend validates it strictly, so the mock checks it with the two sets above.
- `GenerationSettings` (`reconstruction.settings`, `image_to_3d` params) is the unchanged existing schema;
  `toRequestSettings` output is pinned by G1 and never post-processed.
- Required keys are always emitted (`prompt`, `separate`, `reference_set_id`, `settings`, `source`).

### 2.7 Run tracking (`store/runStore.ts`) and the two small store edits
```ts
// store/runStore.ts (not persisted)
interface RunState {
  workflows: Record<string, WorkflowOut>; imageGenerations: Record<string, ImageGenerationOut>
  segmentations: Record<string, SegmentationOut>
  activeWorkflowId: string | null            // the workflow this session started and still tracks
  reviewGenerationId: string | null          // the latest review-first image generation of this session
  previewSegmentationId: string | null       // the latest segmentation preview (Default/Mobile/Tablet/Node); display
                                             // only: never sent in a workflow (DESIGN §4.7.1: parts.segmentation_id null)
  workflowByJobId: Record<string, string>    // index: job id → workflow id (GET /workflows?limit=100 + events)
  adoptedJobIds: string[]                    // jobs already adopted from workflows (idempotent)
  submitting: boolean; cancellingIds: string[]
  startWorkflow: (request: WorkflowRequest) => Promise<WorkflowOut | null>
  startImageGeneration: (request: ImageGenerationRequest) => Promise<ImageGenerationOut | null>
  adoptCandidate: (generationId: string, index: number) => Promise<UseCandidateOut | null>
  startSegmentationPreview: (request: SegmentationRequest) => Promise<SegmentationOut | null>
  cancel: (kind: 'workflow' | 'image_generation' | 'segmentation', id: string) => Promise<void>
  upsertWorkflow: (wf: WorkflowOut) => void; upsertImageGeneration: (ig: ImageGenerationOut) => void
  upsertSegmentation: (sg: SegmentationOut) => void
  loadIndex: () => Promise<void>             // GET /workflows?limit=100; fills workflowByJobId
  resyncActive: () => Promise<void>          // GET each non-terminal tracked run (on hello and in fallback polling)
}
export const selectActiveWorkflow = (s: RunState): WorkflowOut | null
export const selectReviewGeneration = (s: RunState): ImageGenerationOut | null
```
`upsertWorkflow` side effects (in this order, idempotent):
1. Stale guard; store; index `outputs.job_id`.
2. If the `reconstruction` step has `resource.kind === 'job'` and that id is not in `adoptedJobIds` →
   `useJobStore.getState().adopt(resource.id)` (the existing progress card, toasts, viewer auto-open and history
   then work unchanged for workflow jobs).
3. If `outputs.reference_set_id` is set and the source was a prompt → nothing (the generated set is reachable from the
   job's reference ids; the user's current set is not replaced by one-shot runs).
4. If `outputs.part_analysis_id` is set → `usePartStore.getState().loadForJob(outputs.job_id, true)`.
5. Terminal transitions → toast + announce: `completed` "Parts ready" (when parts were requested) or nothing extra
   (the job toast already fired); `failed` "Generation failed" + `errorText(error)` (step name from
   `error.details.step`); `cancelled` "Generation cancelled".

`store/jobStore.ts` (M, two edits only):
- `submit()` keeps its signature, guards, toasts and error handling; the request literal at L208-212 [V] becomes
  `buildImageJobRequest({referenceSetId: references.id, primaryImageId: references.primary_image_id, form,
  referenceMode})`, which returns the identical object.
- `adopt(jobId: string): Promise<boolean>` — `getJob(jobId)` → `set({activeJobId: job.id})` → `upsertJob(job)`;
  errors are swallowed into `false` (the next `job.updated` adopts anyway through the existing
  "track jobs started elsewhere" rule [V `jobStore.ts:L186-187`]).

`store/referenceStore.ts` (M): `adoptSet(next: ReferenceSet): void` — writes `STORAGE_KEYS.referenceSetId`, sets
`{set: next, status: 'ready', error: null, tileErrors: {}}`. Used after `…/candidates/{n}/use`.

### 2.8 Request builders (PURE, `lib/imageJobRequest.ts`, `lib/workflowRequest.ts`, `lib/forms.ts`)
```ts
// lib/imageJobRequest.ts
export interface ImageJobInputs { referenceSetId: string; primaryImageId: string | null; form: SettingsForm; referenceMode: ReferenceMode }
export function buildImageJobRequest(i: ImageJobInputs): CreateJobRequest
  // { reference_set_id, primary_image_id, settings: toRequestSettings(i.form, i.referenceMode) }   — G1

// lib/workflowRequest.ts
export interface BuildContext { features: FeaturesSnapshot }
export function buildGeneration(p: PromptForm, numImages: number, c: BuildContext): ImageGenerationRequest
export function buildSegmentationOptions(f: PartOptionsForm, c: BuildContext): SegmentationOptions | null
export function buildPartAnalysisOptions(f: PartOptionsForm, c: BuildContext): PartAnalysisOptions
  // the same object for workflows (`parts`) and direct analyses (POST /jobs/{id}/part-analyses)
export function buildExportParams(f: PartOptionsForm): { per_part_glbs: boolean }   // Node Export node params (G7)
export interface WorkflowInputs { source: WorkflowSource; partOptions: PartOptionsForm; settings: GenerationSettings }
export function buildWorkflowRequest(i: WorkflowInputs, c: BuildContext): WorkflowRequest      // simple form
```
Emission rules (fixed key order = DESIGN schema order; this is what makes the golden bytes stable). DESIGN §6.0 and
§4.6.2: every nested options object accepts **partial** objects, so only non-default keys are sent:

| Object | Keys in order | Emitted when |
|---|---|---|
| `ImageGenerationRequest` | `prompt` · `negative_prompt` · `model_id` · `preset` · `style` · `part_hint` · `num_images` · `seed` · `randomize_seed` · `width` · `height` | `prompt` always (trimmed); `negative_prompt` iff `supportsNegativePrompt` and trimmed non-empty; `model_id` iff non-null; `preset` always; `style` iff non-null; `part_hint` always; `num_images` always (1 inside workflows); `seed`, `randomize_seed` always; `width`/`height` iff `size` non-null. `steps`/`guidance` are never sent by v1 (model defaults) |
| `SegmentationOptions` | `profile` · `subject` · `extra_terms` | `profile` iff ≠ `auto`; `subject` iff ≠ `auto`; `extra_terms` iff non-empty (trimmed, deduped, ≤ `maxExtraTerms`, each ≤ 40 chars). All three default → `null` |
| `PartAnalysisOptions` | `strategy` · `segmentation` · `separate` · `views` · `export` | `strategy` iff non-null; `segmentation` iff `buildSegmentationOptions` non-null; `separate` **always** (`form.separate ?? defaultSeparate(features)`, filtered to `separableGroups` ids, in features order); `views` iff non-null; `export` iff `perPartGlbs` → `{"per_part_glbs": true}` (workflows **and** direct analyses; `previews` is never sent, default true). `segmentation_id`, `min_part_faces` (default `null` = F-relative), `max_parts`, `split_shells` are never sent by v1 |
| `WorkflowRequest` (simple form) | `source` · `segmentation` · `reconstruction` · `parts` | `source` always; `segmentation` iff parts enabled **and** `previewSegmentation` (value `buildSegmentationOptions(f) ?? {}`; DESIGN §4.7.1: `segmentation` requires `parts`); `reconstruction` iff source ≠ `job` (`{settings}`); `parts` iff parts enabled. There is **no** top-level `export` (DESIGN §4.7.1; 422 `extra_forbidden`). `name`, `graph`, `layout` never in the simple form |
| `WorkflowSource` image | `kind` · `reference_set_id` · `primary_image_id` | always all three (`primary_image_id` may be `null`, as in G1) |
| `UseCandidateRequest` | `slot` · `make_primary` | always `{"slot":"front","make_primary":true}`; `reference_set_id` omitted (→ the backend creates "Prompt: …" set); `label` omitted |
| Node Export params | `per_part_glbs` | always (`buildExportParams`: `{"per_part_glbs": form.perPartGlbs}`); compiles server-side to `parts.export` (DESIGN §4.7.2) |

Golden bodies G1–G9 are listed with their exact JSON in §10.3.

### 2.9 Generation plan: one decision table for every mode (`lib/generatePlan.ts`, PURE)
This is DESIGN §6.9 as code. Every Generate trigger in every mode (Default card/bar, Mobile FAB, Tablet rail button,
Node toolbar Run with a `job` graph, Ctrl/Cmd+Enter) calls `actions/generation.startGeneration()`, which calls
`planFromStores()` (store glue in `store/planFromStores.ts`, which also exports `availabilityFromStores()`) →
`planGeneration(inputs)`.
```ts
export type EffectiveSource = 'image' | 'prompt'
export interface PlanInputs { features: FeaturesSnapshot; source: InputSource; prompt: PromptForm; parts: PartOptionsForm
  references: { id: string; primaryImageId: string | null; imageCount: number } | null
  form: SettingsForm; referenceMode: ReferenceMode }
export type GenerationPlan =
  | { kind: 'job'; request: CreateJobRequest; needsEngine: true }
  | { kind: 'workflow'; request: WorkflowRequest; needsEngine: true }
  | { kind: 'image_generation'; request: ImageGenerationRequest; needsEngine: false }
  | { kind: 'blocked'; code: PlanBlockCode; reason: string }
export type PlanBlockCode = 'no_references' | 'empty_prompt' | 'prompt_too_long' | 'parts_unavailable'
  | 'workflows_unavailable' | 'features_loading' | 'features_error'
export function effectiveSource(source: InputSource, f: FeaturesSnapshot): EffectiveSource
  // 'image' when source === 'image'; 'image' when f.status === 'legacy' or (f.status === 'ready' && !f.prompt.available)
  // (judge fix: never trap the user on an unsupported prompt source); otherwise 'prompt' (incl. 'loading'/'error',
  // which block with rules 5a/5b instead of silently switching to an image job). The stored choice is kept.
export function effectivePartsEnabled(p: PartOptionsForm, f: FeaturesSnapshot): boolean
  // f.status === 'legacy' → false (UI11); otherwise p.enabled
export function shouldRenderPartOptions(f: FeaturesSnapshot, p: PartOptionsForm): boolean
  // f.status === 'ready' || (p.enabled && f.status !== 'legacy')  — gate of the lazy PartOptionsSection (§4.2):
  // on 'loading'/'error' a stored enabled=true keeps the off switch reachable (rule 7a says "Turn it off");
  // on 'legacy' parts are already forced off (effectivePartsEnabled), so the featureBundle is never requested
export function planGeneration(i: PlanInputs): GenerationPlan
```
| Effective source | Parts | Prompt flow | Plan (call) | DESIGN §6.9 row |
|---|---|---|---|---|
| image | off | – | `job` → `POST /jobs` (G1, via `jobStore.submit()`) | Image → 3D (no parts) |
| image | on | – | `workflow` → `POST /workflows {source:{kind:'image'}, segmentation?, reconstruction, parts}` (G2; export options inside `parts.export`) | Image → 3D + parts |
| prompt | off/on | `one_shot` | `workflow` → `POST /workflows {source:{kind:'prompt', generation:{…num_images:1}}, reconstruction, parts?}` (G3/G4) | Prompt → 3D one-shot |
| prompt | off/on | `review` | `image_generation` → `POST /image-generations {…num_images: prompt.numCandidates}` (G5). The 3D step is the candidate action "Use & generate 3D" (§5.3) | Prompt → review image → 3D |

Blocked rows (first match): effective prompt source with `features.status === 'loading'` → `features_loading`
(`'error'` → `features_error`); empty/too long → `empty_prompt`/`prompt_too_long`; effective prompt source, flow
`one_shot` and `!workflow.available` → `workflows_unavailable` "Straight to 3D needs workflows: ${reason}. Use Review
image first." (DESIGN §4.4.8: one-shot needs `workflow.available`; the review flow does not); image source without
images → `no_references`; parts on and `!parts.available` → `parts_unavailable`; parts on and `!workflow.available`
→ `workflows_unavailable` ("Part-aware output needs workflows: ${reason}.").

`actions/generation.ts`:
```ts
export async function startGeneration(): Promise<boolean>
//  0. const override = useNavigationStore.getState().runOverride; if (override) return override()   // Node shell
//  1. if (!availabilityFromStores().canGenerate) { announce(reason, 'assertive'); return false }   // store/planFromStores.ts
//  2. const plan = planFromStores(); if (plan.kind === 'blocked') { toast.warning(...); return false }
//  3. if (plan.needsEngine && system.engine.state === 'not_loaded') void loadEngine()   // today's GenerateCard.tsx:L36-38
//  4. 'job' → ok = await useJobStore.getState().submit()                            // the unchanged G1 path
//     'workflow' → ok = (await useRunStore.getState().startWorkflow(plan.request)) !== null
//     'image_generation' → ok = (await useRunStore.getState().startImageGeneration(plan.request)) !== null
//  5. ok → requestReveal(plan.kind === 'image_generation' ? 'review' : 'viewer'); announce('Generation queued')
export async function startDefaultGeneration(): Promise<boolean>   // steps 1–5 without the override (Node 'job' graphs)
export async function adoptCandidateAndGenerate(generationId: string, index: number): Promise<boolean>
//   adoptCandidate → referenceStore.adoptSet(set) → inputStore.setSource('image') → toast.info('Image added',
//   'The generated image is now the front reference.') → startDefaultGeneration()
export async function cancelActiveRun(): Promise<void>
//   active workflow → cancel workflow (cascades to its job, DESIGN §4.7.4); else active job → jobStore.cancel;
//   else active review generation → cancel it
export function runGenerateShortcut(): void
//   blur a focused input/textarea first (numeric drafts commit on blur, GenerateCard.tsx:L45-47 [V]); ignore while busy
export async function regenerateFromPrompt(jobId: string): Promise<boolean>
//   prompt source: workflowByJobId[jobId] → request.source.generation; else (review-first jobs) getReferenceSet(
//   job.reference_set_id) → image job.primary_image_id → source.image_generation_id → getImageGeneration(id).options
//   → inputStore.loadPrompt(...) → setSource('prompt') → startGeneration(); no prompt found → toast, false
```

### 2.10 Generate availability (`lib/generateAvailability.ts`, PURE; `hooks/useGenerateAvailability.ts` wraps it)
The returned shape is today's `GenerateAvailability` [V `useGenerateAvailability.ts:L5-16`] plus
`code: AvailabilityCode | null` and `plan: GenerationPlan['kind'] | null`. Rule order (first match wins). Rows marked
**same** return byte-identical strings to `useGenerateAvailability.ts:L55-73` [V] (pinned by tests).

| # | Condition | `reason` | Code |
|---|---|---|---|
| 1 | `reachable === false` | **same** `The backend is unreachable.` | `unreachable` |
| 2 | `!system` | **same** `Waiting for the backend…` | `waiting_backend` |
| 3 | engine problem | **same** (summary) | `engine_problem` |
| 4 | `pipelineBlock` (Node shell only) | `Fix the workflow: ${pipelineBlock}` | `pipeline_invalid` |
| 5a | effective source prompt, features `loading` | `Checking prompt-to-3D availability…` | `features_loading` |
| 5b | … features `error` | `Could not check prompt-to-3D availability. Retrying…` | `features_error` |
| 5c | … trimmed prompt empty | `Describe what to generate.` | `empty_prompt` |
| 5d | … longer than `maxChars` | `The prompt is too long (max ${maxChars} characters).` | `prompt_too_long` |
| 5e | … flow `one_shot` and `!workflow.available` | `Straight to 3D needs workflows: ${workflow.reason}. Use Review image first.` | `workflows_unavailable` |
| 6a | effective source image, no images | **same** `Add at least one reference image.` | `no_references` |
| 6b | uploading | **same** `Wait for uploads to finish.` | `uploading` |
| 7a | parts effective on and `!parts.available` | `Part-aware output is unavailable: ${parts.reason}. Turn it off to generate.` | `parts_unavailable` |
| 7b | parts effective on and `!workflow.available` | `Part-aware output needs workflows: ${workflow.reason}. Turn it off to generate.` | `workflows_unavailable` |
| 8 | active job non-terminal, **or** active workflow non-terminal, **or** review generation non-terminal | **same** `A generation is already in progress.` | `busy` |
| 9a | job queue full (plan needs reconstruction) | **same** `The generation queue is full.` | `queue_full` |
| 9b | run queue full (`runQueue.pending >= maxSize`, plan is workflow or image_generation) | `The run queue is full.` | `run_queue_full` |
| 10 | submitting (job or run store) | **same** `Submitting…` | `submitting` |

While the Node shell is mounted (`navigationStore.runOverride !== null`), rules 5–7 are skipped: Node validation
(§4.3.5, incl. `invalid_param` and `capability_unavailable`) covers them and rule 4 carries its first error — so a
`job_result` graph is not blocked by an empty reference set. An unavailable prompt never reaches rule 5 elsewhere
because `effectiveSource` already switched to `image` (§2.9).

Notes (only when `reason === null`): the engine note and queue note are **same** and shown only when the plan
needs reconstruction; new note for prompt plans when `defaultModelState === 'installed'`:
`The text-to-image model loads first (about ${Math.ceil(coldStartS / 60)} min).`

### 2.11 Error → UI mapping (`lib/errorText.ts`, PURE: `errorText(e: {status, code, message, details}, ctx)`)
| Error | Where | UI |
|---|---|---|
| `/features` 404 | features | `legacy`: Default hides prompt/parts UI (UI1); other modes show them disabled with "Needs a newer Img3D backend." |
| 422 `validation_error`, `details[].loc` ends in `prompt` (`["body","prompt"]` or `["body","source","generation","prompt"]`) | create IG/workflow | inline error under the prompt field (`aria-describedby`) + toast "Could not start generation"; `type: invalid_characters` → "Remove control characters from the prompt." |
| 422 `unsupported_by_model` / `unsupported_by_engine` | create IG/workflow | toast naming the field and `ctx.allowed` |
| 422 with `type: extra_forbidden` (any field) | any | toast "The backend rejected an unknown field (`loc`)." + `featuresStore.refresh()`; dev console logs the body hash (version skew between frontend and backend builds) |
| 422 with `type: conflicting_fields` | create workflow | toast "The backend rejected the request (conflicting fields: `loc`)." — never produced by the v1 builders (§2.8 follows DESIGN §4.7.1), so it indicates a bug; dev console logs the body |
| 422 `invalid_workflow` (`details.issues`) | Node run | issues → Problems panel (`origin:'server'`); other modes: toast with the first issue message |
| 503 `feature_disabled` / `capability_unavailable` / `model_unavailable` | any create | toast with the server message (+ `details.reasons[].reason`), then `featuresStore.refresh()` |
| 503 `engine_unavailable` | any create | "The server is shutting down." |
| 429 `queue_full` | any create | existing text "The generation queue is full." (job) / "The run queue is full." (runs) |
| 409 on `…/use` (not completed) / 404 run or candidate | review | refresh the run; toast "That image is no longer available." |
| 404 `/part-analyses/latest` with `details.reason:'none'` | parts | "no analysis yet" state (not an error) |
| Run/workflow `error.code` | progress, parts, review | message table: `out_of_memory` "Ran out of GPU memory. Close other GPU apps and retry."; `insufficient_vram` "Not enough free GPU memory for this step."; `interrupted` "Interrupted by a server restart."; `run_timeout` "The step took too long and was stopped."; `step_failed` "${stepLabel(details.step)} failed: ${details.child_error.message}" (`details` narrowed to `StepFailedDetails`, §2.1; a malformed `details` falls back to `${label}: ${error.message}`); `queue_full` "A queue was full when the next step started."; `segmentation_failed`, `camera_fit_failed`, `lifting_failed`, `partition_failed`, `export_failed`, `publish_conflict`, `job_deleted`, `unsupported_base_mesh`, `source_missing`, `generation_failed`, `run_error` → `${label}: ${error.message}` |
| Run warning codes (DESIGN §6.8) and `readiness.issues` (same `t2i_*` strings, DESIGN §4.4.5) | review, parts, progress | `WARNING_TEXT` in `lib/featureMeta.ts`, one entry per DESIGN code: `t2i_touches_border` "Touches the image border", `t2i_multiple_subjects` "Several subjects", `t2i_coverage_low` "Subject too small", `t2i_coverage_high` "Subject too large", `t2i_aspect_mismatch` "Wider than tall", `profile_degraded` "A detector profile was unavailable; a simpler one was used", `frame_unverified` "Image alignment could not be verified", `head_pass_low_res` "Face details were too small to refine", `segmentation_rerun_for_alignment` "The parts step segmented the model's own view again (the image had no real transparency)", `camera_fit_low_iou` "Camera alignment was uncertain; labels have lower confidence", `too_small_for_geometry` "Too small to cut; kept as a surface region", `max_parts_exceeded` "Too many parts; the smallest were kept as regions", `expected_part_missing` "A part asked for in the prompt was not found", `charts_cross_boundary` "Texture charts cross part borders", `low_confidence` "Low label confidence", `floater_candidates` "Small loose pieces were kept on the body", `gpu_defer_timeout` "Waited the maximum time behind image-to-3D jobs, then continued"; unknown codes show the server `message` |
| Run step message while held by the jobs-first gate (DESIGN §4.3: "Waiting for the GPU (generation queued)" / "(generation in progress)") | progress, review, parts | shown verbatim as the step message (`progress.message`); not an error, no toast |

### 2.12 Session wiring (`hooks/useBackendSession.ts` M; line refs are today's [V])
The tuner wiring stays exactly as today, locked or not: every tuner call the session makes is a GET
(`refreshRuntime` → `GET /blender/runtime`, the read-only pill status — detection is not tuning; `loadRunsForJob` →
`GET /jobs/{id}/tunings` and `refreshRun` → `GET /tunings/{id}`, which keep past tuned results viewable; `resync` =
those three). The session never calls a tuner mutation (`startTuning`, `cancelTuning`, `detect`, `selectRuntime`,
`resolveRelease`, `startDownload`, `cancelDownload`); those are reachable only from tuner components, which are
locked (§3.9).
1. `syncSessionData` (L59-69): run `featuresStore.refresh()` in the existing `Promise.all` (next to the unchanged
   `refreshRuntime()`); after the `Promise.all`: `runStore.loadIndex()` when `snapshot.status === 'ready'` (the
   catalog is loaded by the Node shell only, §2.5); `partStore.loadForJob(viewedJobId)` when `snapshot.status ===
   'ready'` (GET routes work even when the flags are off, DESIGN §6.0). The existing
   `loadRunsForJob(viewedJobId, true)` stays unconditional (viewing past results).
2. Realtime (L123-138): add the five handlers of §2.4. The `blender.updated`/`tuning.updated` handlers stay (they
   only write the tuner store and keep the pill and past results current).
3. `pollBackend` (L47-56): `pollMeshTuner(mode)` stays as today; add `runStore.resyncActive()` and
   `partStore.refreshActive()` in `fallback` mode; `featuresStore.refresh()` in `safety` mode. Inside
   `pollMeshTuner` the runtime refresh keeps the pill current. The `refreshRun` loop over non-terminal runs is the
   only tuner poll that exists to drive an active run; while locked it has nothing to iterate (S9 rejects every new
   run, and the backend restart that changes the flag interrupts running ones), so it is skipped by construction.
   No explicit lock guard is added: it would freeze a leftover non-terminal record from before that restart as
   "running" (Cancel button, mobile pulse) instead of letting the first refresh settle it.
4. Viewed-job subscription (L146-150): add `partStore.resetView(id)` + `partStore.loadForJob(id)`; the tuner
   `loadRunsForJob` stays.
5. Re-hello (L154-157): add `featuresStore.refresh()`, `runStore.resyncActive()`, `partStore.loadForJob(viewed, true)`;
   `meshTuner.resync()` stays (GETs only).
No tuner-specific features subscription is needed: the lock is derived during render from `featuresStore`
(`useTunerLocked`, §3.9), and the wiring above does not depend on it.

---------------------------------------------------------------------------------------------------
## 3. Preferences, storage, App settings, mode application

### 3.1 `store/preferencesStore.ts` (persisted `img3d.preferences` v1)
```ts
import type { UiMode } from '../lib/uiModes.ts'
export type DensityPreference = 'auto' | 'comfortable' | 'touch'
export type MotionPreference = 'system' | 'reduce'
export type NodeViewPreference = 'auto' | 'canvas' | 'list'
export type LinkStyle = 'bezier' | 'smoothstep' | 'straight'
export interface Preferences {
  uiMode: UiMode                                    // default 'default'
  density: DensityPreference                        // default 'auto'
  motion: MotionPreference                          // default 'system'
  node: { view: NodeViewPreference; minimap: boolean; snapToGrid: boolean; linkStyle: LinkStyle }
                                                    // default { view: 'auto', minimap: true, snapToGrid: true, linkStyle: 'bezier' }
  dismissedHints: string[]                          // default []
}
interface PreferencesState extends Preferences {
  setUiMode(mode: UiMode): void; setDensity(d: DensityPreference): void; setMotion(m: MotionPreference): void
  setNodePreference<K extends keyof Preferences['node']>(key: K, value: Preferences['node'][K]): void
  dismissHint(id: string): void; reset(): void
}
// create<PreferencesState>()(persist(..., { name: STORAGE_KEYS.preferences, version: 1,
//   storage: createJSONStorage(() => localStorage), partialize: (s) => ({uiMode, density, motion, node, dismissedHints}),
//   merge: (persisted, current) => ({ ...current, ...sanitizePreferences(persisted) }) }))
```
- `sanitizePreferences(raw: unknown): Partial<Preferences>` (pure, `lib/uiModes.ts`) keeps only values that are
  members of their `as const` tuples; anything corrupt or unknown falls back to the default (so a broken key can
  never select a non-existent mode).
- Hydration of a synchronous `localStorage` persist store happens inside `create`, so the first render already has
  the saved mode (no flash). Storage errors (private mode) → defaults, as `lib/storage.ts` does [V].
- Cross-tab: `app/GlobalOverlays.tsx` registers one `storage` listener (in an effect; cleanup removes it). It loads
  the target shell chunk **before** rehydrating, so a cold lazy shell never suspends the viewer (§3.7 READY row):
  ```ts
  function onStorage(e: StorageEvent): void {
    if (e.key !== STORAGE_KEYS.preferences) return
    const mode: UiMode | null = sanitizePreferences(readPersistedState(e.newValue)).uiMode ?? null
    void (mode ? prepareMode(mode) : Promise.resolve())
      .catch(() => {})                                   // a failed chunk load still rehydrates; ShellErrorBoundary handles it
      .then(() => usePreferencesStore.persist.rehydrate())
  }
  ```
  The stored value is zustand's persist envelope `{state, version}` (`createJSONStorage`), so the state is read by
  the pure helper `readPersistedState(raw: string | null): unknown` in `lib/uiModes.ts` (`JSON.parse(raw).state`;
  `null` for `null` input, invalid JSON or a missing `state`; the `try/catch` lives in that `.ts` file, so the
  component has no `try` block, §10.1 code rules). `ui_modes.test.mjs` covers `readPersistedState` (valid envelope,
  bare object, invalid JSON, `null`).

### 3.2 Storage keys (added to `lib/storage.ts → STORAGE_KEYS`; existing keys unchanged [V `storage.ts:L6-16`])
| Key | Writer | Version | Content |
|---|---|---|---|
| `img3d.preferences` | preferencesStore | 1 | §3.1 |
| `img3d.input` | inputStore | 1 | `{source, prompt}` (§3.3) |
| `img3d.partOptions` | partOptionsStore | 1 | `{form}` (§3.3) |
| `img3d.workflow` | workflowStore (Node chunk) | 1 | `{doc}` — Node topology, modes, layout, values of non-bound nodes; never ids of user data |
| `img3d.workflow.backup` | workflowStore | – | raw string, written only when a newer/unreadable doc was found |

No migration of existing keys is needed. All new persisted forms are validated in `merge` by pure sanitizers in
`lib/forms.ts`, which also export the defaults (`DEFAULT_PROMPT_FORM`, `DEFAULT_PART_OPTIONS_FORM`).

### 3.3 Shared input stores (persisted; one form for all four modes)
```ts
// store/inputStore.ts  (img3d.input v1)
export type InputSource = 'image' | 'prompt'
export type PromptFlow = 'review' | 'one_shot'
export interface PromptForm {
  prompt: string                          // '' (merge truncates anything > 8000 chars; the real limit comes from features)
  preset: PromptPreset                    // 'character'
  style: PromptStyle | null               // null = no style clause
  partHint: boolean                       // true (DESIGN §4.4.1 default)
  flow: PromptFlow                        // 'review'
  numCandidates: 1 | 2 | 3 | 4            // 2 (review flow only; clamped to features.prompt.maxImages)
  seed: number; randomizeSeed: boolean    // 0, true
  modelId: string | null                  // null = backend default
  size: [number, number] | null           // null = preset default; must be one of features.prompt.sizes
  negativePrompt: string                  // '' (only shown and sent when supports_negative_prompt)
}
interface InputState { source: InputSource /* 'image' */; prompt: PromptForm
  setSource(s: InputSource): void; updatePrompt(patch: Partial<PromptForm>): void; resetPrompt(): void
  loadPrompt(g: ImageGenerationRequest): void   // "Reuse prompt" / "Regenerate from prompt"; unknown values → defaults
}

// store/partOptionsStore.ts  (img3d.partOptions v1)
export interface PartOptionsForm {
  enabled: boolean                         // false → every request is today's (G1)
  strategy: string | null                  // null = features default_strategy (not sent)
  separate: string[] | null                // null = groups with default_selected (sent resolved)
  subject: SegSubject                      // 'auto'
  profile: SegProfile                      // 'auto'
  extraTerms: string[]                     // []
  views: number | null                     // null = features default_views (not sent)
  previewSegmentation: boolean             // false → no pre-reconstruction sg_ in workflows
  perPartGlbs: boolean                     // false
}
interface PartOptionsState { form: PartOptionsForm; update(patch: Partial<PartOptionsForm>): void; reset(): void }
```

### 3.4 `store/navigationStore.ts` (not persisted)
```ts
export const REVEAL_TARGETS = ['create', 'prompt', 'review', 'settings', 'viewer', 'progress', 'parts', 'export', 'history'] as const
export type RevealTarget = (typeof REVEAL_TARGETS)[number]
export type SheetId = 'create' | 'parts' | 'library' | 'progress' | 'blocked' | 'status' | 'review'
export type SheetSnap = 'peek' | 'half' | 'full'
export type ShellSection = 'create' | 'viewer' | 'parts' | 'library'
interface NavigationState {
  reveal: { target: RevealTarget; seq: number } | null     // monotonic seq: the same target can be requested again
  appSettingsOpen: boolean; appSettingsSection: 'interface' | 'node' | 'features' | 'about'
  sessionUiOverride: UiMode | null                          // from ?ui= or "Use Default UI for this session"
  pipelineBlock: string | null                              // set ONLY by the Node shell (UI10)
  runOverride: (() => Promise<boolean>) | null              // set ONLY by the Node shell
  sheet: SheetId | null; sheetSnap: SheetSnap               // Mobile + Tablet(bottom/compact)
  createTab: 'input' | 'settings'
  section: ShellSection; sidePanelOpen: boolean             // Tablet rail + Mobile landscape rail
  requestReveal(t: RevealTarget): void; setAppSettingsOpen(open: boolean, section?: NavigationState['appSettingsSection']): void
  setSessionUiOverride(m: UiMode | null): void; setPipelineBlock(r: string | null): void
  setRunOverride(fn: (() => Promise<boolean>) | null): void
  openSheet(id: SheetId | null, snap?: SheetSnap): void; setSheetSnap(s: SheetSnap): void
  setCreateTab(t: 'input' | 'settings'): void; setSection(s: ShellSection): void; setSidePanelOpen(open: boolean): void
}
```
`useReveal(handler)` subscribes to `reveal.seq` with `useNavigationStore.subscribe` inside an effect and calls the
handler through `useEffectEvent` (no `setState` in the effect body). Each shell maps targets to its own navigation
(tables in §4).

### 3.5 Resolution rules for every "auto" value (`lib/uiModes.ts`, `lib/viewport.ts`; PURE)
| Value | Rule (first match) |
|---|---|
| **UI mode** | `resolveUiMode(pref, override, ready)`: `candidate = override ?? pref`; return `ready.has(candidate) ? candidate : 'default'`. `ready` = modes whose shell has landed (`SHELL_READY` in each `shells/<mode>/ready.ts`, §3.7). There is **no** "auto" UI mode: the user asked for four modes with Default as the default (§12 Q-U1) |
| `?ui=` override | `parseUiOverride(search)`: `?ui=default\|node\|mobile\|tablet` (case-insensitive, last occurrence wins); anything else → `null`. Session only, never persisted. App settings shows "Temporarily overridden by the page address (Node). [Clear override]" |
| Suggested mode (badge only, never applied) | `suggestUiMode(v)`: `width < 600` or (`coarse` and `min(width, height) < 520`) → `mobile`; `coarse` and `width < 1366` → `tablet`; else `default`. Rendered as "Suggested for this screen" on one card |
| **Density** | `resolveDensity(pref, mode, coarse)`: mode `mobile`/`tablet` → `touch` (forced); `pref === 'touch'` → `touch`; `pref === 'comfortable'` → `comfortable`; `auto` → `coarse ? 'touch' : 'comfortable'` (`coarse` = `(pointer: coarse)`) |
| **Motion** | `data-motion = pref === 'reduce' ? 'reduce' : 'system'`; CSS zeroes durations for `[data-motion='reduce']` and for `prefers-reduced-motion: reduce` (existing tokens L113-119 [V]); JS `prefersReducedMotion()` (`lib/motion.ts`) checks both |
| **Node view** | `resolveNodeView(pref, v)`: `canvas`/`list` explicit; `auto` → `list` when `width < 600` or (`coarse` and `min(width, height) < 520`), else `canvas` (real tablets get the ComfyUI-style canvas; phones in landscape get the list; matches the §9 matrix for both pointer types) |
| **Tablet posture** | `nextTabletPosture(prev, v)` with hysteresis (§4.5.1) |
| **Mobile posture** | `nextMobilePosture(prev, v)`: enter `landscape` when `width ≥ 1.05·height` and `height < 520`; stay while `width ≥ 0.95·height` and `height < 540`; else `portrait` (§4.4) |
| **Low memory** (viewer) | `lowMemory = deviceMemory !== null && deviceMemory <= 4` or (`coarse` and `width < 1024`) |

`lib/viewport.ts`:
```ts
export interface ViewportMetrics { readonly width: number; readonly height: number; readonly coarse: boolean
  readonly hover: boolean; readonly deviceMemory: number | null; readonly typing: boolean }
export interface ViewportSnapshot extends ViewportMetrics {
  readonly posture: Readonly<{ tablet: TabletPosture; mobile: MobilePosture }> }   // computed OUTSIDE render (below)
export type WidthClass = 'compact' | 'medium' | 'expanded' | 'large'        // <600 · 600–839 · 840–1199 · ≥1200
export function widthClass(w: number): WidthClass
export type TabletPosture = 'rail' | 'bottom' | 'compact'
export type MobilePosture = 'portrait' | 'landscape'
export function nextTabletPosture(prev: TabletPosture | null, v: ViewportMetrics): TabletPosture
export function nextMobilePosture(prev: MobilePosture | null, v: ViewportMetrics): MobilePosture
export function isLowMemory(v: ViewportMetrics): boolean
```
`hooks/useViewport.ts` is a **module-level external store** (`viewportStore = {subscribe, getSnapshot,
getServerSnapshot}`) read through `useSyncExternalStore`. Its listeners (`resize` rAF-throttled, `orientationchange`,
`matchMedia('(pointer: coarse)')`/`('(hover: hover)')` changes, `focusin`/`focusout` for `typing` = an
`input`/`textarea`/`[contenteditable]` has focus) recompute the metrics and then the postures **inside the
listener**: `posture.tablet = nextTabletPosture(prevSnapshot.posture.tablet, metrics)`, same for mobile. Width-only
changes are applied after a 120 ms trailing debounce (`setTimeout` in the listener); `orientationchange` applies
immediately. The store then publishes one new frozen snapshot (cached per value tuple; no allocation in
`getSnapshot`). Hysteresis therefore never reads or writes a variable during render, which the React Compiler
rejects (a module-level reassignment gives `CompileError: Cannot reassign variables declared outside of the
component/hook` [V critic probe]). `hooks/useNavPosture.ts` is just `useViewport().posture[shell]` (a primitive).
The initial snapshot is computed at module load from `window` (postures from `prev = null`); `bootDocument()` reads
`viewportStore.getSnapshot()`. Server snapshot: `{width: 1280, height: 800, coarse: false, hover: true, deviceMemory:
null, typing: false, posture: {tablet: 'rail', mobile: 'portrait'}}`.

### 3.6 No-flash mode application
```ts
// src/main.tsx (F6) — order matters
import './styles/global.css'
import { bootDocument } from './app/documentAttributes.ts'
bootDocument()                        // BEFORE createRoot
createRoot(container).render(<StrictMode><App /></StrictMode>)

// src/app/documentAttributes.ts
export interface DocumentAttributes { mode: UiMode; density: 'comfortable' | 'touch'; motion: 'system' | 'reduce'
  posture: TabletPosture | MobilePosture | null }
export function applyDocumentAttributes(a: DocumentAttributes): void
  // <html data-ui-mode data-density data-motion [data-posture]> ; removes data-posture when null
export function bootDocument(): void
  // reads usePreferencesStore.getState() (already hydrated), parseUiOverride(location.search) →
  // useNavigationStore.getState().setSessionUiOverride(override), resolves mode/density/motion from the preferences
  // and posture from viewportStore.getSnapshot().posture (hooks/useViewport.ts, §3.5), then
  // applyDocumentAttributes(...); if the resolved mode is lazy, starts `void SHELL_LOADERS[mode]()` immediately so
  // the chunk download overlaps React's first render; if it is 'default', starts `void preloadTuner()`
  // (components/tunerLock/lazyTuner.ts, §3.9) so the tuner chunk overlaps the session handshake and is normally
  // resolved before `phase === 'ready'` mounts the pill and the Auto Tune card
```
- `ModeSwitch` re-applies the attributes in `useLayoutEffect` whenever the resolved mode, density, motion or posture
  changes (layout effect → no visible frame with stale attributes).
- `src/styles/modes.css` (F4, imported by `global.css`) holds every per-mode override, keyed only on these attributes:
```css
:root { --shell-inset-bottom: 0px; --shell-inset-left: 0px; --floating-gap: var(--space-3);
        --floating-nav-height: 64px; --fab-size: 60px; --rail-width: 80px; --sheet-peek: 96px;
        --sheet-max-width: 640px; --side-sheet-max: 420px; --top-bar-height: 56px; }
:root[data-density='touch'] { --control-h-sm: 44px; --control-h-md: 44px; --control-h-lg: 52px; --hit-min: 44px; }
:root[data-ui-mode='mobile'], :root[data-ui-mode='tablet'] {
  --hit-min: 48px; --control-h-sm: 48px; --control-h-md: 48px; --control-h-lg: 52px; }
/* Every interactive element consumes these tokens: common components through --control-h-*, and every other
   control in the F4 touch-pass files (§1.2) through
   :global(:root[data-density='touch']) .x { min-block-size: max(<today's px>, var(--hit-min)); min-inline-size: … }
   or, for small icon buttons whose visual size must stay (reference star, dialog close), a centred ::before hit
   area of var(--hit-min) × var(--hit-min). Without data-density the computed values equal today's. */
:root[data-motion='reduce'] { --duration-fast: 0ms; --duration-normal: 0ms; --duration-slow: 0ms; }
:root[data-ui-mode='mobile'][data-posture='portrait'],
:root[data-ui-mode='tablet'][data-posture='bottom'], :root[data-ui-mode='tablet'][data-posture='compact'] {
  --shell-inset-bottom: calc(var(--floating-nav-height) + 2 * var(--floating-gap) + env(safe-area-inset-bottom, 0px)); }
:root[data-ui-mode='mobile'][data-posture='landscape'], :root[data-ui-mode='tablet'][data-posture='rail'] {
  --shell-inset-left: calc(var(--rail-width) + 2 * var(--floating-gap) + env(safe-area-inset-left, 0px)); }
/* phone-style shared overlays at any width in Mobile mode (Default keeps its max-width:767px rules): the CSS modules
   add mode variants with :global(), e.g. Dialog.module.css:
   :global(:root[data-ui-mode='mobile']) .dialog { …same rules as its @media (max-width: 767px) block… } */
```
- `Dialog`, `Toaster` and the `EngineStatus` popover keep their `@media (max-width: 767px)` rules for Default
  [V `frontend_architecture_map` §3.1] and gain the `:root[data-ui-mode='mobile']` equivalents; the Toaster offset
  becomes `--shell-inset-bottom` [V `Toaster.module.css:L14-20`].

### 3.7 App root and mode switch (`src/App.tsx`, `src/app/*`)
```tsx
// src/App.tsx (F6)
export default function App() {
  const { nextRetryAt } = useBackendSession()                       // exactly once (UI6)
  const phase = useAuthStore((s) => s.phase)
  return (
    <div className={styles.app}>
      <a className={styles.skipLink} href="#main-content">Skip to content</a>
      {phase === 'ready' ? <ModeSwitch nextRetryAt={nextRetryAt} /> : <PreSession nextRetryAt={nextRetryAt} />}
      <GlobalOverlays />
    </div>
  )
}
// app/PreSession.tsx: today's Header + BackendBanner + #main-content with the splash (App.tsx:L27-31) or LoginScreen — identical in every mode
// app/GlobalOverlays.tsx: <Toaster/>, <AppSettingsDialog/>, <Announcer/>, <GlobalShortcuts/>, and
//   {phase === 'ready' && tunerVisible && !tunerLocked && <Suspense fallback={null}><LazyBlenderSetupDialog/></Suspense>}
//   (was App.tsx:L39; tunerVisible = useTunerVisible(), tunerLocked = useTunerLocked(), LazyBlenderSetupDialog from
//   components/tunerLock/lazyTuner.ts, §1.3, §3.9)
//   Toaster and Announcer follow the topmost open modal (§4.1 modalStack), because a modal <dialog> makes everything
//   outside it inert and removes it from the accessibility tree.
```
```ts
// app/shellRegistry.ts (module scope, .ts → no only-export-components warning)
export const SHELL_LOADERS = {
  node: () => import('../shells/node/index.ts'),
  mobile: () => import('../shells/mobile/index.ts'),
  tablet: () => import('../shells/tablet/index.ts'),
} as const
export const READY_MODES: ReadonlySet<UiMode>   // 'default' plus every mode whose ready.ts exports SHELL_READY = true;
                                                 // built from static `import { SHELL_READY } from '../shells/<m>/ready.ts'`
                                                 // tiny files so the flag is known without loading the shell chunk
export async function prepareMode(mode: UiMode): Promise<void>   // 'default' → starts preloadTuner() (not awaited: the
                                                                  // tuner slots have their own Suspense, so they never
                                                                  // suspend the shell) and resolves; else await SHELL_LOADERS[mode]()
export interface ShellProps { nextRetryAt: number | null }   // the only prop any shell receives
```
Each `shells/<mode>/ready.ts` exports `export const SHELL_READY: boolean` (placeholders: `false`, F6; flipped to
`true` by the shell slice). `shells/<mode>/index.ts` `export default` the shell component. The F6 placeholder
`index.ts` is `export default function ShellPlaceholder(): null { return null }` (it imports nothing, so the
cross-shell rule of §1.4 holds, and it is never loaded while `SHELL_READY` is false).

`ModeSwitch` (declares `const NodeShell = lazy(SHELL_LOADERS.node)` etc. at module scope; `DefaultShell` is a
static import):

| State | Behaviour |
|---|---|
| LOADING | `Suspense` fallback `<ShellFallback mode/>`: a pure-CSS skeleton of that shell (top bar + nav + viewer box), in the initial chunk |
| READY | shell mounted; `useLayoutEffect` applies attributes; focus moves to the new shell's `<main id="main-content" tabIndex={-1}>`; Announcer says "Node UI" (etc.); the previous shell unmounts (all domain state lives in stores; the R3F canvas remounts and the GLB comes from the `useLoader` cache within the 250 ms `scheduleDisposal` window [V `ModelScene.tsx:L44-52`]). This holds because **every mode switch first awaits `prepareMode(mode)`** (`UiModePicker`, the "Use Default UI" button and the cross-tab `storage` listener all do; `?ui=` boot starts the load in `bootDocument`, before any viewer exists). zustand updates go through `useSyncExternalStore` and cannot be transitions, so a cold lazy shell would otherwise suspend, show the fallback, unmount the viewer for up to 600 ms and drop the GLB cache (an 80 MB parts GLB would download again) |
| FAILED | `ShellErrorBoundary` (`key = mode`): "Couldn't load the Node UI." [Retry] calls `location.reload()` (`React.lazy` caches a rejected payload and the browser caches the failed module fetch, so a remount would re-throw) · [Use Default UI for this session] (`setSessionUiOverride('default')`; the saved preference is not changed) |

Prefetch: focusing or hovering a mode card in App settings calls `SHELL_LOADERS[mode]()`.

### 3.8 App settings dialog (`components/preferences/*`, F6)
Built on `common/Dialog` [V `Dialog.tsx:L25`] (`size="lg"`; full-height sheet in Mobile). `AppSettingsDialog.tsx`
is tiny and eager; its body `AppSettingsBody.tsx` is `lazy` (≤ 8 kB gz, loaded on first open). Entry points (same
label "App settings", Settings2 icon, `aria-haspopup="dialog"`): Default header (every breakpoint), Mobile top bar,
Tablet rail footer / top bar, Node toolbar, and the shortcut Ctrl+`,`.

| Section (`<section aria-labelledby>`) | Controls |
|---|---|
| **Interface** | **UI style**: `RadioCardGroup` (`role="radiogroup"`, roving tabIndex, arrow keys) with four cards (CSS schematic, label, one-line description from `UI_MODE_META`, "Current" chip, "Suggested for this screen" chip, "Not included in this build" + disabled when not in `READY_MODES`). Selecting applies as soon as the shell chunk is ready: `await prepareMode(mode)` (the card shows a small spinner and `aria-busy` meanwhile; a failed load shows "Couldn't load this UI style. Try again." on the card and leaves the mode unchanged), then `setUiMode(mode)`; the dialog survives the swap (it lives in `GlobalOverlays`) and the mode-switch announcement reaches the dialog's own announcer region (§4.1 modalStack). Override notice for `?ui=`. **Density**: Auto (touch on touch screens) / Comfortable / Touch (forced Touch in Mobile/Tablet, explained). **Motion**: Follow system / Reduce motion. Note "Saved on this device" |
| **Node editor** | Default view (Auto / Canvas / List), Minimap, Snap to grid, Link style (Bezier / Smooth step / Straight) |
| **Features & models** (read-only + warm-up) | `FeatureStatusList`: Image-to-3D, Prompt-to-3D, Segmentation (profiles), Part extraction (strategies), Workflows, **Auto Mesh Tuner: "Paused until you approve it"** (from `mesh_tuning.reason`; the Default UI shows the Auto Tune card locked, §3.9), **Part refinement: "Paused until you approve it"** — each with state text + reason (never colour alone). `ModelRegistryList`: `features.models` grouped by kind: display name, state ("In memory", "Installed", "Loading…", "Not installed", "Unavailable: reason"), size, VRAM estimate, cold start, `LicenseBadge` ("Permissive", "Permissive with conditions", "Revenue-capped", "Non-commercial", "Research only", "Unknown"), licence link, and `license.conditions` as a bullet list (Sapiens2). Actions: **Warm up** (`POST /models/{id}/load`; T2I and segmentation models in state `installed`) and **Free memory** (`unload`, state `loaded`, with a one-line warning "Next use reloads it (about N min)"). "Show unsupported models" toggle loads `GET /models?include_hidden=true` (3D segmenters etc. shown with their `reason`). Footer: "Models are installed manually; see DECISIONS.md." There is **no install button** (DESIGN D5) |
| **About** | App name/version (`/system.app`), backend API version (`features.apiVersion` or "legacy"), active UI mode, storage keys note, keyboard shortcuts list |

### 3.9 Tuner lock (UI only; no tuner file edited)
Rule (user spec alignment 2026-10-16, `DECISIONS.md`): the Default mode keeps today's tuner UI in its current places
and keeps rendering it, but **locked** while the backend reports `mesh_tuning.enabled=false`. Locked = every action
that would start or configure a tuning run, or start a Blender download/setup, is disabled with a visible reason;
runtime status and past tuned results stay readable, because viewing them is working functionality, not tuning.
Mobile, Tablet and Node never had tuner UI and do not add it. No protected tuner file (§0.3, UI13) is edited; the
lock lives in the importers and one small wrapper folder, `components/tunerLock/` (F6).

**Two hooks, two questions** (F5; both return primitives):
- `useTunerLocked(): boolean` = `useFeaturesStore((s) => tunerLock(s.snapshot) === 'locked')` (§2.5). Legacy backend
  → `false` (today's behaviour exactly, UI1); `ready` + `mesh_tuning.enabled === true` → `false` (approval unlocks
  everything with no code change and no rebuild); `ready` + disabled, `loading`, `error` → `true` (fail-closed).
  There is no frontend build flag for the tuner. The backend code gate S9 (503 `feature_disabled` on
  `POST /jobs/{id}/tunings`, DESIGN §8.2 G1) holds independently of the UI.
- `useTunerVisible(): boolean` = `useResolvedUiMode() === 'default'`. Only the Default shell has tuner mount points;
  the shared components that contain tuner parts (`ViewerPanel`, `ModelStatsContent`, `HistoryItem`) render those
  parts only when it is `true`, so the new shells never add tuner UI.

**Lock frame** (`components/tunerLock/TunerLockFrame.tsx` + `.module.css`; imports nothing from `components/meshTuner/`):
```tsx
export function TunerLockFrame({ locked, variant, children }:
  { locked: boolean; variant: 'card' | 'pill'; children: ReactNode }) {
  const noticeId = useId()
  if (!locked) return children                       // unlocked: today's DOM, no wrapper
  if (variant === 'pill') return (
    <fieldset className={styles.pillLock} disabled aria-disabled="true" aria-describedby={noticeId} data-tuner-lock="locked">
      {children}<VisuallyHidden id={noticeId}>Blender setup: {TUNER_LOCK_NOTICE}</VisuallyHidden>
    </fieldset>)
  return (
    <div className={styles.cardLock} data-tuner-lock="locked">
      <TunerLockNotice id={noticeId} />             // <p role="note">{Lock icon}{tunerLockNotice(snapshot)}</p>
                                                     // + <p>Existing tuned results stay viewable.</p>
      <fieldset className={styles.fieldset} disabled aria-disabled="true" aria-describedby={noticeId}>{children}</fieldset>
    </div>)
}
```
- **Why a disabled `<fieldset>`:** it is the only way to disable controls that live inside protected files without
  editing them. The browser treats every `button`/`input`/`select`/`textarea` descendant as disabled (no click, no
  keyboard activation, reported as disabled in the accessibility tree), and the common `Button`/`Switch`/
  `SegmentedControl` styles already cover `:disabled` [V `Button.module.css:L24`]. `aria-disabled="true"` on the group
  (inherited by its focusable descendants) plus `aria-describedby` → the notice carry the reason. `<details>`/
  `<summary>` and links are not form controls, so the report sections and the report/log links of `TuningReport`
  [V `TuningReport.tsx:L77, L116, L215, L220`] keep working. CSS: `border: 0; margin: 0; padding: 0;
  min-inline-size: 0` (the default `min-content` would break narrow columns); `.cardLock` is `display: grid; gap:
  var(--space-2)`; `.pillLock` is `display: inline-flex`, so it sits in the header's `.status` row exactly where the
  pill sits.
- **Notice** (`TunerLockNotice.tsx`): a strip on the card's top edge, same width, `--color-warning-soft` background,
  `--color-warning` `Lock` icon (`aria-hidden`), text ≥ 4.5:1; text = `tunerLockNotice(snapshot)` (§2.5), in `ready`
  exactly "Paused until you approve the part-aware milestone"; the second line "Existing tuned results stay viewable."
  is outside the `role="note"` element. It enters with the P14 card fade (instant under reduced motion).
- **Target sizes:** tuner CSS is protected, so the §9/P12 target counts exclude the tuner subtree (the probe's
  `tunerEntries` selectors plus `[data-tuner-lock]`) and report it separately (informational): while locked its
  controls are disabled, and unlocked it is today's UI.

| Mount point | File (owner) | Behaviour while locked | Unlocked (legacy backend, or approved) |
|---|---|---|---|
| `TuningCard` ×3 (desktop `AppShell.tsx:L64-66`, tablet `L94-96`, mobile tab `L134`) | `layout/AppShell.tsx` (F6) | renders in place as `<TunerLockFrame variant="card" locked={useTunerLocked()}><Suspense fallback={null}><LazyTuningCard/></Suspense></TunerLockFrame>`: normal Panel frame with the notice above it. Disabled inside the fieldset: the runtime line's "Change" / "Set up Blender" / "Show progress" (they open `BlenderSetupDialog`), the whole `TuningOptionsForm` (preset, options disclosure, switches, reset), "Auto Tune" / "Auto Tune again" and "Cancel" (no run can be active, §2.12) [V `TuningCard.tsx:L34-66, L105-131`]. Still readable: the runtime line text, the empty state, the status badge, `TuningSteps` and `TuningReport` of the latest run. `useRevealTuning` and the desktop/tablet `<div ref={tuningRef}>` stay as today (it fires only for an active run) | today's card exactly |
| `BlenderRuntimeStatus` pill | `layout/Header.tsx:L40` (F6) | visible as read-only status (detection is not tuning): dot/spinner, label and download bar stay live from the session's `GET /blender/runtime`; the pill button is inside the disabled `.pillLock` fieldset, so the popover with "Set up Blender…", "Change runtime…" and "Re-detect" [V `BlenderRuntimeStatus.tsx:L101-109, L180-197`] cannot open | today's pill exactly |
| `BlenderSetupDialog` | `app/GlobalOverlays.tsx` (F6) | **not mounted**, also when the store's auto-open sets `setupOpen` on `needs_download` [V `meshTunerStore.ts:L263-271`]: the flag simply has no renderer. Nothing calls `setSetupOpen(false)` to clean up: that writes `blenderSetupDismissed` [V `meshTunerStore.ts:L357-363`] and would suppress today's first-run prompt after approval | mounted in Default (as `App.tsx:L39` today; after approval it may open once if Blender is missing, exactly like today's first run); never in Mobile/Tablet/Node |
| Original/Tuned `VariantBar`, tuned secondary GLB | `viewer/ViewerPanel.tsx` (F8) | **unchanged** in Default [V `ViewerPanel.tsx:L135-149, L199`]; where `useTunerVisible()` is false the viewer shows the original only (no `VariantBar`, effective variant `'original'`) | same |
| `TunedStats`, `TunedDownloadButton` | `viewer/ModelStatsCard.tsx` + `ModelStatsContent.tsx` (F7) | unchanged in Default (`LazyTunedDownloadButton`; a GET download) [V `ModelStatsCard.tsx:L61-110, L136`]; not rendered where `useTunerVisible()` is false | same |
| `useViewedTuning` | `hooks/useViewedTuning.ts` | **unchanged** (not edited) | same |
| History tuned marker + `loadRunsForJob` effect | `history/HistoryItem.tsx:L54-60` (F6) | unchanged in Default (GET list per completed job); skipped where `useTunerVisible()` is false | same |
| Mobile tab "auto tune running" pulse | `layout/MobileTabBar.tsx:L31` | unchanged (status only; stays off because no run can start) | same |
| Session `refreshRuntime`, `pollMeshTuner`, `loadRunsForJob`, `resync` | `hooks/useBackendSession.ts` (F5) | unchanged, all GET (§2.12) | same |
| Node palette refine group | `workflow/engine/registry.ts` (F3) | unchanged: the backend reports the four refine nodes `hidden_by_default: true` (DESIGN §4.7.2, §6.1); the locked group is shown only with `VITE_FEATURE_REFINEMENT_PREVIEW==='true'` (default false), never through "Show unsupported"; never runnable | – (refinement has its own `part_refinement` flag) |
| Mobile, Tablet, Node shells | – | no tuner UI; never import `components/meshTuner/**` or `components/tunerLock/**`; never request the tuner chunk | same |

Boundaries: `shells/**` import neither `components/meshTuner/**` nor `components/tunerLock/**`
(`import_boundaries.mjs`); after F6/F7, `components/meshTuner/**` is value-imported only by
`components/tunerLock/tunerBundle.ts` and by the protected files themselves (code review; today's four static
importers are exactly the ones F6/F7 convert, §1.3).
**Approval** = the user sets `IMG3D_MESH_TUNING_ENABLED=true` (DESIGN §8.2 G1) and restarts the backend:
`/features.mesh_tuning.enabled` becomes true, every lock frame disappears on the next features refresh (`hello` /
`features.updated`), `BlenderSetupDialog` mounts, and the same build behaves as today. Nothing is deleted, and
`test/frontend/mesh_tuner_mock_smoke.mjs` keeps proving the tuner contract.

---------------------------------------------------------------------------------------------------
## 4. The four modes

### 4.1 Shared building blocks
**Feature content rules (Content + Card).**
1. `XxxContent` renders the feature body without `Panel`, owns its store subscriptions (as today), declares
   `container-type: inline-size` on its root and adapts with container queries only (no `useBreakpoint`, no viewport
   media queries). Props are limited to `active?: boolean`, `headingLevel?: 2 | 3`, `compact?: boolean`; the only
   feature-specific props are `SettingsContent.showPartOptions?: boolean` (default true) and
   `PartOptionsSection.mode?: 'all' | 'parts'`.
2. `XxxPanel` keeps its name, path and DOM: `<Panel id title icon subtitle actions><XxxContent/></Panel>`.
3. Titles, short titles and descriptions come from `lib/featureMeta.ts` (`FEATURE_META`), so every shell uses the
   same words (WCAG 3.2.4): Create, Viewer, Parts, Library, App settings, Generate.

**Primitives (`components/common/*`, F4).**
| Component | Contract |
|---|---|
| `Sheet` | `{id, title, open, onClose, modal?: boolean, side?: 'bottom' \| 'right', snaps?: readonly SheetSnap[], snap?, onSnapChange?, headerActions?, footer?, children}`. Modal = native `<dialog>.showModal()` (top layer, inert background, Escape via `cancel`, manual backdrop light-dismiss as `Dialog.tsx` does, focus returns to the opener). **The `<dialog>` element itself is full-viewport and never transformed** (`inset: 0; width: 100vw; height: 100dvh; max-width/max-height: none; background: transparent`), so `position: fixed` children (the portaled toasts) still lay out against the viewport; the visual panel is an inner `<div>` that carries the snap height and the drag transform; a click whose target is the `<dialog>` itself (outside the panel) is the backdrop light-dismiss. The modal registers with `modalStack` while open. Standard = `<section role="region" aria-labelledby>` with no focus trap. Snaps: `peek` = `var(--sheet-peek)` (96 px), `half` = `50dvh`, `full` = `calc(100dvh - env(safe-area-inset-top) - 24px)`; side sheets are full-height with `width: min(var(--side-sheet-max), 100vw - 2*gap)`. **Drag only on the handle/header** (Pointer Events, `setPointerCapture`, `touch-action: none` there); the body never starts a drag (with `touch-action: pan-y` the browser claims vertical pans and fires `pointercancel`; with `none` the body could not scroll). During the drag `transform` is written to the ref'd panel element (no React state per frame); release uses velocity projection `y + v·200 ms` → nearest snap; a modal sheet dragged below 40 % of its height closes. Body: `overflow-y: auto; overscroll-behavior: contain; padding-bottom: env(safe-area-inset-bottom)`. Handle = `<button>` 48×48 hit area, 36×4 visual pill, label "Resize panel, currently half height"; Enter/Space cycles snaps, ArrowUp/ArrowDown step, Escape closes a modal sheet (WCAG 2.5.7 alternative). Entry/exit via `@starting-style` + `transition-behavior: allow-discrete` on `--duration-slow`/`--ease-out` (zero under reduced motion). Panel `max-width: var(--sheet-max-width)` centred |
| `modalStack` (`common/modalStack.ts`) | `registerModal(el: HTMLDialogElement): () => void` (push; the returned function pops), `getTopModal(): HTMLDialogElement \| null`, `subscribeModalStack(cb: () => void): () => void` — an external store read with `useSyncExternalStore`. `Dialog.tsx` (M) and modal `Sheet` call `registerModal(ref.current)` in an effect while `open` (cleanup pops; no `setState`). `Toaster` (M) renders its stack through `createPortal(stack, getTopModal() ?? <its normal container>)`. `app/Announcer.tsx` renders the permanent root region pair plus, while a modal is open, a second `role="status"` + `role="alert"` pair portaled into the top modal (`createPortal(pair, getTopModal())`, subscribed with `useSyncExternalStore(subscribeModalStack, getTopModal)`); `announcerStore` only queues messages (it imports nothing, §1.4), and `app/Announcer.tsx` renders each message in the topmost pair only (the root pair stays empty while a modal is open). `--z-toast` still orders toasts above the panel inside the dialog. Rule for `Dialog.module.css`/`Sheet.module.css`: no `transform`, `filter`, `perspective` or `contain` on the `<dialog>` element itself (they would make it the containing block of the fixed toasts); animate the inner surface/panel only (`::backdrop` may keep its `backdrop-filter`) |
| `FloatingNav` | `<nav aria-label>` of `<button>`s (items open overlays, so not tabs): `{label, items: NavItem[], value, onSelect, center?: ReactNode, hidden?: boolean}`; `NavItem = {id, label, icon, badge?: string \| null, controls?: string, expanded?: boolean, current?: boolean}`. Roving focus Left/Right/Home/End (pattern of `MobileTabBar.tsx:L18-23` [V]). 64 px tall, items ≥ 48×48, `bottom: calc(var(--floating-gap) + env(safe-area-inset-bottom, 0px))`, `left/right: max(var(--floating-gap), env(safe-area-inset-left/right))`, `max-width: 560px` centred, grid `1fr 1fr var(--fab-size) 1fr 1fr`, glass `var(--surface-glass)` + `backdrop-filter: blur(16px)` with a solid `@supports not` fallback, `z-index: var(--z-header)` |
| `NavRail` | the same semantics vertically (ArrowUp/ArrowDown): `{label, items, value, onSelect, footer?: ReactNode}`; 80 px wide glass card, `left: calc(var(--floating-gap) + env(safe-area-inset-left, 0px))`, vertically centred, `max-height: calc(100dvh - 2*gap)`, items 56×56 |
| `ProgressRing` | SVG, `role="progressbar"`, `aria-valuenow`, indeterminate when `value === null` (static under reduced motion) |
| `Skeleton` | `{shape: 'line' \| 'block' \| 'circle', width?, height?, lines?}`; shimmer on `--skeleton-base/--skeleton-highlight`; static under reduced motion; `aria-hidden` (the container carries `aria-busy`) |
| `RadioCardGroup` | `role="radiogroup"` + `role="radio"` cards, roving tabIndex, arrow keys, `aria-checked`, optional `onPrefetch(value)` on focus/hover |
| `VisuallyHidden` | the existing `visually-hidden` class as a component |

**Shared generation control (`components/generation/GenerateAction.tsx`, F6) over `hooks/useGenerateAction.ts`
(F5).** One component replaces the per-shell Generate buttons (judge graft); `GenerateCard` becomes
`<GenerateAction variant={variant ?? 'card'} />` with the **same DOM and CSS** for `card` and `bar`.
```ts
export type GenerateUiPhase = 'blocked' | 'ready' | 'submitting' | 'queued' | 'running' | 'cancelling'
export interface GenerateActionState { phase: GenerateUiPhase; availability: GenerateAvailability
  activeRun: ActiveRunView | null; percent: number | null; supportsCancel: boolean
  label: string; ariaLabel: string; run(): void; cancel(): void }
// label: 'Generate 3D model' (plan job/workflow) | 'Generate images' (plan image_generation) | 'Submitting…' | 'Cancelling…'
// ActiveRunView (hooks/useActiveRun.ts): {kind: 'job'|'workflow'|'image_generation', id, status, percent, stepLabel}
```
| Variant | Used by | Look |
|---|---|---|
| `card` / `bar` | Default (today's `GenerateCard` card and mobile bar) | identical to today [V `GenerateCard.tsx:L65-127`] |
| `fab` | Mobile, Tablet bottom/compact | 60 px circle; ready = accent + Sparkles + visible label "Generate"; blocked = dimmed + Info badge, `aria-disabled="true"` (still focusable), tap opens the `blocked` sheet; submitting = spinner `aria-busy`; queued/running = `ProgressRing` around a Square icon, tap opens the `progress` sheet (Cancel inside, with confirm); cancelling = spinner + "Cancelling" |
| `rail` | Tablet rail, Mobile landscape rail | 56 px round, same state table as `fab` |
| `toolbar` | Node toolbar | Run / Cancel button pair with the issue count badge; `aria-disabled` + reason popover when blocked |

**Shared shell chrome (`shells/shared/*`, F9a).** `CompactTopBar` (`var(--top-bar-height)` = 56 px +
`env(safe-area-inset-top)`, so its 48 px targets fit: Logo, a status pill = `EngineStatus compact` +
`ConnectionIndicator` dot → opens the `status` sheet, App settings gear; the pill's 30 px height today
[V `EngineStatus.module.css:L10`] is raised by the F4 touch pass);
`StatusSheetContent` (full `EngineStatus`, `ConnectionIndicator`, `GpuMeter`); `ProgressPill` (floating top-centre
of the viewer while a run is queued/running or ended < 8 s ago; tap → `progress`); `GenerateBlockedContent` (reason,
note, `engineProblem` details + "Retry loading model", "Open Create"); `AppSettingsButton`; `FeatureIcon` (id → lucide
icon); `PhoneLayout` (the whole Mobile portrait layout, reused by Tablet `compact`); `SectionHost` (renders the
Create/Parts/Library section content for side panels and sheets); `SheetHost` (mounts the sheets of §4.4 from
`navigationStore.sheet`); `navItems.ts` (the four destinations + badges).

### 4.2 Default mode (eager; preserved, then polished)
**Composition (`shells/default/DefaultShell.tsx`, F6)** — mirrors today's `App.tsx:L19-41` ready branch [V]:
```tsx
export function DefaultShell({ nextRetryAt }: ShellProps) {
  const breakpoint = useBreakpoint()
  useReveal((target) => defaultReveal(target, breakpoint))   // table below
  return (<>
    <Header breakpoint={breakpoint} />
    <BackendBanner nextRetryAt={nextRetryAt} />
    <div id="main-content" className={styles.content}><AppShell breakpoint={breakpoint} /></div>
    {breakpoint !== 'mobile' && <HistoryDrawer />}
  </>)
}
```
| Reveal target | desktop / tablet breakpoint | mobile breakpoint |
|---|---|---|
| `viewer`, `progress`, `parts`, `export` | `parts`/`export` → `revealPanel('parts')`; others no-op (today's `useRevealProgress` keeps working) | `setMobileTab('viewer')` |
| `create`, `prompt`, `review` | `revealPanel('input-source')` and focus its source switch / `revealPanel('prompt-review')` | `setMobileTab('inputs')` |
| `settings` | `revealPanel('settings')` | `setMobileTab('settings')` |
| `history` | `setHistoryOpen(true)` | `setMobileTab('history')` |

`revealPanel(id)` (in `shells/default/DefaultShell.tsx`) =
`document.getElementById(`${id}-title`)?.closest('section')?.scrollIntoView({behavior: prefersReducedMotion() ?
'auto' : 'smooth', block: 'nearest'})`. `Panel` puts the id only on its heading (`<h2 id={`${id}-title`}>`; the
`<section>` has no id [V `Panel.tsx:L20-23, L31`]; `SettingsPanel` passes `id="settings"` [V `SettingsPanel.tsx:L53`]),
so no DOM change is needed and UI1 is unaffected. New panels use the same convention (`PartsPanel id="parts"`,
`InputSourceCard id="input-source"`, `PromptReviewContent` inside a `Panel id="prompt-review"`).

**Guarantees.** Legacy backend → today's structure, tuner mount points included and unlocked, plus one gear (UI1).
With `/features` ready: the additions below are cards in today's columns; the tuner mount points stay where they are,
locked while `mesh_tuning.enabled` is false (§3.9); nothing existing moves, nothing is removed.

**Placements (F12 owns the `AppShell.tsx` edits; F6 only adds the tuner lock slots and reveal):**
| Addition | Desktop (`AppShell.tsx:L46-72`) | Tablet (`L74-100`) | Mobile tabs (`L116-149`) |
|---|---|---|---|
| `InputSourceCard` (Image \| Prompt) | top of the left column, above `ReferencePanel` | top of the left scroll area | top of the Inputs tab |
| `PromptPanel` (instead of `ReferencePanel` while the effective source is prompt) | left column | left scroll area | Inputs tab |
| `PartOptionsSection` | inside `SettingsPanel` (via `SettingsContent`), last section | same | Settings tab |
| `PartsPanel` | after `ModelStatsCard` | after `ModelStatsCard` | Viewer tab, after `ModelStatsCard` |
| `PipelineSteps` | inside `ProgressCard` (via `ProgressContent`) when a workflow/run is active | same | same |
`InputSourceCard` renders when `features.status === 'ready'` **or** the stored source is not `image` (UI11; it is
eager, so this downloads nothing); `PartOptionsSection` when `shouldRenderPartOptions(features, partOptions.form)`
(§2.9: `status === 'ready'`, or a stored `parts.enabled=true` while `status` is `loading`/`error`, so the off switch
that rule 7a asks for stays reachable); `PartsPanel` only when `status === 'ready'`. On a **legacy** backend neither
renders, whatever is stored: `effectivePartsEnabled` already forces parts off there (UI11), so no switch is needed and
the featureBundle chunk is not requested. `InputSourceCard` is eager (tiny). `PromptPanel`, `PromptReviewContent`,
`SegmentationPreview`, `PartOptionsSection` (+ `SegmentationOptionsContent`, `LicenseNotice`),
`PartsPanel`/`PartsContent`, `ExportContent` and `PipelineSteps` are imported **only** through
`components/lazyFeatures.ts` (F7): each is `lazy(() => import('./featureBundle.ts').then((m) => ({default:
m.PartsPanel})))`, where `featureBundle.ts` re-exports all of them, so Rolldown emits **one** lazy chunk. They are
rendered inside `<Suspense fallback={<Skeleton shape="block" height={…}/>}>` and only under the conditions above, so a
legacy backend never downloads the chunk (§8.1). The shells import the same wrappers. The root element of each
addition in the table above (and of `PartOptionsSection` inside `SettingsContent`) carries `data-ui-addition`; it has
no styling or behaviour and is read only by `ui_probe.js` `fingerprint({skipAdditions: true})` for the locked UI1
check (§10.5).

**Polish list (concrete, testable; no redesign).**
| # | Item | Where | Acceptance |
|---|---|---|---|
| P1 | App settings gear | `Header.tsx` actions (L47-68) | visible at every breakpoint; `IconButton label="App settings"`, rendered as the **last** child of `.actions` (after History and Sign out); opens the dialog (F6) |
| P2 | Input source card + Prompt panel | `AppShell.tsx` (table above) | switch toggles Reference/Prompt; hidden on legacy (F12) |
| P3 | Parts panel + part options | `AppShell.tsx`, `SettingsContent` | states per §6.3 (F12/F7) |
| P4 | Reveal instead of direct tab writes | `GenerateCard.tsx:L40`, `AppShell.tsx:L139` | mobile Default still switches to Viewer after submit/open (F6) |
| P5 | Tuner lock | §3.9; `AppShell.tsx` (3 placements), `Header.tsx:L40`, `GlobalOverlays` | while locked: the Auto Tune card renders at all three placements with its normal frame and the notice strip, every control inside the lock frame is `:disabled`, the Blender pill shows live status but does not open, `BlenderSetupDialog` never opens; tuned results stay viewable; no tuning-run or Blender-download POST (UI3); with legacy404 or approval: today's tuner UI exactly (F6) |
| P6 | 320 px toolbar clip [V map §3.2 row 1] | `ViewerToolbar.module.css:L19` | `.modes { flex: 1 1 auto; min-width: 0 }`; `@container viewer-toolbar (max-width: 359px)` wraps the view-mode control to its own row; at 320 px every segment has `scrollWidth ≤ clientWidth` (F8) |
| P7 | Phone landscape gets the mobile layout [row 3] | `hooks/useBreakpoint.ts` | `'mobile'` when `(max-width: 767.98px)` **or** `((max-height: 500px) and (pointer: coarse))`; 812×375 coarse → tabs; desktop short windows unchanged (F12) |
| P8 | Short-viewport chrome [row 4] | `App.module.css`, `Header.module.css`, `GenerateCard.module.css` | `@media (max-height: 500px)`: `--header-height: 48px`; generate bar ≤ 56 px (status text clamped to 1 line, `-webkit-line-clamp: 1`); viewer-tab content ≥ 220 px at 740×360 (F12) |
| P9 | Tablet portrait [row 2] | `AppShell.module.css:L51-65` | `@media (orientation: portrait) and (min-width: 768px) and (max-width: 1279.98px)`: viewer first, full width, `height: clamp(280px, 42dvh, 560px)`; cards below in `grid-template-columns: repeat(2, minmax(0, 1fr))`; viewer ≥ 700 px wide at 768×1024 (F12) |
| P10 | Viewer min-heights [rows 3, 6] | `ViewerPanel.module.css:L7`, `AppShell.module.css:L85-89` | `.viewer { min-height: min(320px, 100%) }`; mobile viewer `height: clamp(220px, 55dvh, 520px)`; tablet viewer `height: clamp(240px, 58dvh, 640px)`; no inner overflow at 812×375 (F8/F12) |
| P11 | Toast offset [row 12] | `Toaster.module.css`, Default mobile | `--shell-inset-bottom` includes the generate bar when visible (the mobile layout sets `<html data-default-bar="on">` on the Inputs/Settings tabs); toasts never overlap the bar (F4/F12) |
| P12 | Touch targets under a coarse pointer [row 13] | tokens + common CSS + the F4 touch-pass files (§1.2) | `data-density='touch'` (auto) → buttons, segments, switch label rows, slider hit areas (`::before`), menu items, the status pill, polygon presets, advanced-settings rows, seed/field info buttons, progress cancel, model-stats buttons, reference-tile star/menu ≥ 44 px; 0 controls < 44 px at 768×1024 coarse (was 43/62; the protected tuner subtree is excluded and reported separately, §3.9) (F4; F12 for `GpuMeter`, `MobileTabBar`, `History*`, `GenerateCard` CSS) |
| P13 | Contrast (WCAG 1.4.3/1.4.11) | `tokens.css:L27, L31-33` [V] | `--color-text-subtle: #8a92a6` (≥ 4.6:1 on surface-4); primary button background `var(--color-accent-active)` `#7a58f5` (4.62:1 with white), pressed `#6d4ae8`; new `--color-border-control: #5b6580` (3.17:1) for input boundaries; `--color-accent` stays for focus rings/accents (F4) |
| P14 | Transitions | `AppShell.module.css`, `Panel.module.css`, `HistoryDrawer.module.css` | mobile tab panels cross-fade (opacity, `--duration-fast`, `@starting-style`); cards enter with fade + 4 px rise (`--duration-normal`, `--ease-out`); drawer and dialog exits animate (`allow-discrete`); all zero under reduced motion (F4/F12) |
| P15 | Smooth scroll respects reduced motion | `AppShell.tsx:L25, L41` | `behavior: prefersReducedMotion() ? 'auto' : 'smooth'` (F6) |
| P16 | History drawer modality | `HistoryDrawer.tsx:L18-31` | while open: `inert` on `#main-content` and the header; Tab cannot leave the drawer; Escape closes; focus returns to the opener (F6) |
| P17 | Skeleton loading | `HistoryList.tsx`, `ReferenceTile.module.css`, `PartsContent` | 3 skeleton rows (64 px) instead of the history spinner; thumbnail shimmer; parts list skeleton; static under reduced motion; CLS < 0.05 when history loads (probe) (F7/F12) |
| P18 | Spacing rhythm | `AppShell.module.css`, `Panel.module.css` | panel padding `var(--panel-padding)` = `--space-4` (≥ 768) / `--space-3` (< 768); `scrollbar-gutter: stable` on `.scrollArea`/`.scrollColumn`; `scroll-padding-bottom` = bar height on mobile tab panels; column gap `var(--layout-gap)` everywhere (F4/F12) |
| P19 | Safe areas | `Header.module.css`, `AppShell.module.css` | header `padding-top/left/right: env(safe-area-inset-*)`; mobile layout left/right insets (landscape notch) (F12) |
| P20 | iOS background | `global.css:L25` [V] | `background-attachment: fixed` only under `(pointer: fine)` (F4) |
| P21 | Header crowding 1280–1439 px | `GpuMeter.tsx`, `GpuMeter.module.css` | below 1440 px (desktop breakpoint only; `Header.tsx:L41` renders `GpuMeter` only there [V]) the meter collapses to an icon + percent inside a `<button aria-label="GPU memory details" aria-expanded>` that opens a popover; no header overflow at 1280 and 1366; this button is the only P21 change to the UI1 fingerprint (`EXPECTED_DEFAULT_DELTA`, §10.5) (F12) |
| P22 | Loading states of the new features | `InputSourceCard`, `PartsPanel`, `ColdStartNotice`, `PipelineSteps` | Prompt segment shows "Checking…" while features load; parts list skeleton; cold-start notice; live step list (F7) |

**New tokens (`styles/tokens.css`, F4; semantic, no literal colours in new modules):** `--panel-padding`,
`--control-h-sm: 28px; --control-h-md: 34px; --control-h-lg: 44px; --hit-min: 24px` (today's sizes as defaults),
`--surface-glass: color-mix(in srgb, var(--color-surface-2) 86%, transparent)`, `--skeleton-base: var(--color-surface-3)`,
`--skeleton-highlight: var(--color-surface-4)`, `--color-border-control`, `--port-image: var(--color-accent-2)`,
`--port-masks: #34d399`, `--port-mesh: var(--color-accent)`, `--port-parts: #f472b6`, `--cat-input`, `--cat-analyze`,
`--cat-generate`, `--cat-parts`, `--cat-output`, `--cat-refine` (warning), `--part-highlight: #fbbf24`,
`--part-select: var(--color-accent-2)`, and a 12-colour `--part-1 … --part-12` palette (each ≥ 3:1 on
`--color-viewer-bg`).

### 4.3 Node UI (lazy chunk; React Flow canvas in a second lazy chunk)

#### 4.3.1 Engine types (`src/workflow/engine/types.ts`, PURE)
```ts
export const PORT_TYPES = ['image_set', 'segmentation', 'mesh', 'part_set'] as const          // = CatalogOut.port_types ids
export type PortType = (typeof PORT_TYPES)[number]
export const NODE_CATEGORIES = ['input', 'analyze', 'generate', 'parts', 'output', 'refine'] as const
export type NodeCategory = (typeof NODE_CATEGORIES)[number]
export type NodeMode = 'active' | 'bypass'
export type Binding = 'references' | 'prompt' | 'viewed_job' | 'segmentation' | 'settings' | 'part_options' | 'export' | 'none'
export interface InputPortDef { readonly id: string; readonly label: string; readonly accepts: readonly PortType[]
  readonly required: boolean; readonly multiple: boolean }
export interface OutputPortDef { readonly id: string; readonly label: string; readonly type: PortType }
export type ParamDef =                                      // generic inspector for future (Binding 'none') nodes
  | { readonly kind: 'boolean'; readonly key: string; readonly label: string }
  | { readonly kind: 'number'; readonly key: string; readonly label: string; readonly min: number; readonly max: number; readonly step: number }
  | { readonly kind: 'enum'; readonly key: string; readonly label: string; readonly options: readonly { value: string; label: string }[] }
  | { readonly kind: 'text'; readonly key: string; readonly label: string; readonly maxLength: number }
export interface NodeTypeDef {
  readonly type: string                        // server node type id ('image_to_3d'); future UI-only types use 'ui.<name>'
  readonly version: number; readonly category: NodeCategory; readonly title: string; readonly description: string
  readonly inputs: readonly InputPortDef[]; readonly outputs: readonly OutputPortDef[]
  readonly maxInstances: number                // 1 for every v1 type (server rule)
  readonly isSource: boolean                   // input_image | prompt | job_result
  readonly binds: Binding                      // where params come from at compile time
  readonly params: readonly ParamDef[]; readonly defaults: Readonly<Record<string, unknown>>
  readonly bypass: { readonly from: string; readonly to: string } | 'drop' | null   // part_extraction {mesh→parts}; segmentation 'drop'
  readonly locked: boolean; readonly available: boolean; readonly code: string; readonly reason: string | null
  readonly hiddenByDefault: boolean
  readonly migrate?: (values: Readonly<Record<string, unknown>>, fromVersion: number) => Record<string, unknown>
  readonly contribute?: (values: Readonly<Record<string, unknown>>) => Record<string, unknown>   // future nodes only
}
export interface GraphNode { readonly id: string; readonly type: string; readonly version: number
  readonly mode: NodeMode; readonly values?: Readonly<Record<string, unknown>> }   // values only for Binding 'none'
export interface PortRef { readonly node: string; readonly port: string }
export interface GraphEdge { readonly from: PortRef; readonly to: PortRef }        // key = edgeKey(e) = 'a.p->b.q'
export interface Graph { readonly nodes: readonly GraphNode[]; readonly edges: readonly GraphEdge[] }
export interface Layout { readonly positions: Readonly<Record<string, { readonly x: number; readonly y: number }>>
  readonly collapsed: readonly string[]; readonly viewport: { readonly x: number; readonly y: number; readonly zoom: number } }
export interface WorkflowDoc { readonly schema: 'img3d.ui_workflow'; readonly version: 1; readonly revision: number
  readonly graph: Graph; readonly layout: Layout }
export type IssueCode = GraphIssueCode | 'ui_parked_source' | 'ui_unreachable_node'      // client-only codes are warnings
export interface GraphIssue { readonly severity: 'error' | 'warning'; readonly code: IssueCode; readonly message: string
  readonly nodeId: string | null; readonly port: string | null; readonly edgeIndex: number | null
  readonly origin: 'client' | 'server' }
export interface PipelineConfig { readonly source: 'image' | 'prompt' | 'job'; readonly segmentation: boolean
  readonly partExtraction: boolean }
export type NodeRunState = 'idle' | 'queued' | 'running' | 'done' | 'warning' | 'failed' | 'skipped' | 'cancelled'
  | 'bypassed' | 'locked' | 'unavailable'
export interface NodeStatusView { readonly state: NodeRunState; readonly percent: number | null
  readonly message: string | null; readonly thumbnailUrl: string | null }
```
**Node parameters are not stored in the graph for built-in nodes.** Built-ins bind to the shared stores
(`binds`), so Default, Mobile, Tablet and Node always edit the same form. Only future nodes with `binds: 'none'`
keep `values` in the doc (rendered by the generic `ParamDef` inspector). Node ids are `n_<type>` (unique because
`max_instances = 1`; matches the server's `^[a-z][a-z0-9_]{0,31}$`).

#### 4.3.2 Registry from `CatalogOut` (`registry.ts`, `nodes/overlays.ts`, `nodes/builtin.ts`)
```ts
export interface NodeRegistry { get(type: string): NodeTypeDef | undefined; list(): readonly NodeTypeDef[]
  palette(o: { showHidden: boolean; refinementPreview: boolean; present: ReadonlySet<string> }): readonly PaletteGroup[] }
export interface PaletteGroup { readonly category: NodeCategory; readonly title: string
  readonly entries: readonly { def: NodeTypeDef; enabled: boolean; reason: string | null }[] }
export class RegistryError extends Error { readonly code: 'duplicate_type' | 'refine_unlocked' | 'port_clash' }
export function createRegistry(defs: readonly NodeTypeDef[]): NodeRegistry
  // throws duplicate_type; port_clash (two ports of one def share an id); refine_unlocked: category 'refine' without
  // locked:true — the judge-mandated guard (unit-tested)
export function registryFromCatalog(c: CatalogOut, overlays: typeof NODE_OVERLAYS): NodeRegistry
export const BUILTIN_REGISTRY: NodeRegistry      // legacy / catalog-not-loaded fallback
```
- `NODE_OVERLAYS` (UI-only facts the catalog does not carry): `input_image` → `binds:'references'`;
  `prompt` → `'prompt'`; `job_result` → `'viewed_job'`; `segmentation` → `'segmentation'`, `bypass:'drop'`;
  `image_to_3d` → `'settings'`; `part_extraction` → `'part_options'`, `bypass:{from:'mesh', to:'parts'}`;
  `export` → `'export'`; refine and unsupported types → `'none'`. `isSource` for the three input types.
- Everything else (title, category, ports, `accepts`, `required`, `multiple`, `max_instances`, `available`,
  `locked`, `code`, `reason`, `hidden_by_default`, `version`, `defaults`) comes from `CatalogOut.node_types`.
- `BUILTIN_REGISTRY` mirrors DESIGN §4.7.2 with `input_image`, `image_to_3d`, `export` available and every other type
  `available:false, code:'legacy_backend', reason:'Needs a newer Img3D backend.'` (used for `legacy` features and
  until the catalog loads).
- Palette: grouped by category in `NODE_CATEGORIES` order; `hidden_by_default` entries of the non-refine
  categories only with "Show unsupported" (the 3D segmenter/completer/generator rows); the `refine` group (which the
  backend reports `hidden_by_default: true`, DESIGN §4.7.2) only when `VITE_FEATURE_REFINEMENT_PREVIEW==='true'`,
  never through "Show unsupported" (all entries locked, "Paused until you approve refinement"); an entry whose type
  is already present is disabled ("Only one Image-to-3D node is supported."); unavailable entries carry the backend
  `reason`.

#### 4.3.3 Ports and colours (WCAG 1.4.1: glyph + label, never colour alone)
| Port type | Label (catalog) | Glyph | Token |
|---|---|---|---|
| `image_set` | Image | ● | `--port-image` |
| `segmentation` | Masks | ■ | `--port-masks` |
| `mesh` | Mesh | ▲ | `--port-mesh` |
| `part_set` | Parts | ⬢ | `--port-parts` |
Edges take the source-port colour; an edge is `animated` only while its source node is running and motion is not
reduced. Category stripes use `--cat-*`.

#### 4.3.4 Graph operations (`graph.ts`, PURE; every op returns a new doc with `revision + 1`, structural sharing)
`addNode(doc, def, position)` (id `n_<type>`; throws on a second instance), `removeNodes(doc, ids)` (and incident
edges), `connect(doc, from, to)` (replaces an existing edge into a single-input port), `disconnect(doc, edgeKey)`,
`setMode(doc, id, mode)` (only when `def.bypass !== null`), `moveNodes(doc, positions)`, `setCollapsed`,
`setViewport`, `setValues(doc, id, values)` (Binding `none` only), `canConnect(doc, registry, from, to):
{ok:true} | {ok:false; code: IssueCode; message}` (output type ∈ `accepts`; no self loop; no cycle: DFS from
`to.node` must not reach `from.node`; ports exist), `topoOrder(graph)` (Kahn, ties by node id), `edgeKey(e)`.
Limits from `features.workflow.maxNodes/maxEdges` (fallback 32/64).

#### 4.3.5 Validation (`validate.ts`) — server issue codes mirrored
`validateGraph(doc, registry, ctx): GraphIssue[]` with `ctx = {features, bound: {imageCount, referenceSetId,
promptLength, promptMax, viewedJobCompleted}}`. Client rules emit **the server codes** (DESIGN §4.7.2) so both
sources read the same:

| Code | Client rule | Message |
|---|---|---|
| `graph_too_large` | nodes > maxNodes or edges > maxEdges | `The workflow is too large (max ${n} nodes, ${e} links).` |
| `unknown_node_type` | type not in the registry (node kept, forced to bypass) | `Unknown node type "${type}". It is ignored.` |
| `unknown_port` | edge references a missing port | `${title}: unknown port "${port}".` |
| `type_mismatch` | output type ∉ accepts | `${fromTitle} → ${toTitle}: ${outLabel} cannot connect to ${inLabel}.` |
| `port_already_connected` | two edges into a non-multiple input | `${title}: "${port}" already has a connection.` |
| `missing_required_input` | required input of an active, reachable node unconnected | `${title}: connect "${portLabel}".` |
| `no_source` | no active source reaches Image-to-3D (or Part Extraction for `job_result`) | `Connect an Input Image or Prompt node.` |
| `multiple_sources` | more than one source connected to the run, or Segmentation and Image-to-3D fed by different sources | `Use one source for the whole run.` |
| `cycle` | Kahn leftovers | `The workflow contains a loop (${ids}).` |
| `too_many_instances` | defensive (import) | `Only one ${title} node is supported.` |
| `node_locked` | an active locked node | `${title} is paused until you approve refinement.` |
| `unsupported_topology` | `job_result` + `image_to_3d`; Part Extraction without a mesh source (the server also uses this code for a Segmentation node that does not feed `part_extraction.masks`; the client never sends such a node: it is excluded with `ui_unreachable_node`, below) | `${title} cannot be used here.` |
| `capability_unavailable` | active node with `available:false` | `${title} is unavailable: ${reason}` |
| `feature_disabled` | `!features.flags.workflows` and the compiled mode is `workflow` | `Workflows are turned off on the server.` |
| `invalid_param` | bound value invalid: no reference image (`input_image.reference_set_id`), empty/too-long prompt, viewed job not completed for `job_result` | `Add at least one reference image.` / `Describe what to generate.` / `The prompt is too long (max ${n} characters).` / `Open a finished model first.` |
| `not_found` | server only | (server message) |
| `no_sink` | warning: no active Export reachable (compile adds one implicitly, like the server) | `No Export node: the result is still kept.` |
| `ui_parked_source` | warning: a source node not connected to the run (dropped from the server graph) | `${title} is not connected and is ignored.` |
| `ui_unreachable_node` | warning: an active node not on the path source → sink, **or an active Segmentation node whose `masks` reach no active Part Extraction** (DESIGN §4.7.1: segmentation only together with parts); the node is excluded from the run set | `${title} is not connected and will be skipped.` / Segmentation: `Segmentation is skipped: connect its Masks to an active Part Extraction.` |

**Server report merge** (`shells/node/useWorkflowValidation.ts`): 600 ms after a doc revision change, when the doc
compiles locally and `features.workflow.available`, the hook calls `workflowStore.validateRemote(doc.revision,
out.request, signal)` (the store calls `api/workflows.validateWorkflow` = `POST /workflows/validate {graph, layout}`;
shells never import `api/*`, §1.4); an `AbortController` cancels the previous call. The report is stored with the
revision it was computed for and discarded when the doc moved on.
Displayed issues = client issues ∪ server issues, deduped by `(code, nodeId, port)`; server issues are
authoritative (an error only the server reports still blocks Run).

#### 4.3.6 Projection onto the shared stores (`derive.ts`, `template.ts`, `actions/pipeline.ts`)
```ts
export function deriveConfig(doc: WorkflowDoc, r: NodeRegistry, ctx: ValidationContext): { config: PipelineConfig | null; issues: readonly GraphIssue[] }
// source = type of the active source connected to the run ('job' for job_result);
// segmentation = Segmentation active, fed by that source, and its masks reach an active Part Extraction;
// partExtraction = Part Extraction active, mesh from Image-to-3D (or job_result), output reaches Export.
// Invariant (DESIGN §4.7.1): segmentation ⇒ partExtraction. The valid configs are 8: source image|prompt ×
// {none, parts, parts+segmentation} and source job × {parts, parts+segmentation}.
export function templateFromConfig(c: PipelineConfig, r: NodeRegistry): WorkflowDoc   // deterministic layout below
export function reconcile(doc: WorkflowDoc, c: PipelineConfig, r: NodeRegistry): WorkflowDoc
// minimal edit so deriveConfig(result).config equals c: set modes, rewire the source edges, insert missing nodes
export function configFromStores(docSource: 'image' | 'prompt' | 'job' | null, input: InputSource, parts: PartOptionsForm): PipelineConfig
// source = docSource === 'job' && parts.enabled ? 'job' : input (a job source needs Part Extraction);
// partExtraction = parts.enabled; segmentation = parts.enabled && parts.previewSegmentation
```
Template layout (columns 320 px apart): `n_input_image (0,0)`, `n_prompt (0,260)`, `n_segmentation (320,0)`,
`n_image_to_3d (320,260)`, `n_part_extraction (640,130)`, `n_export (960,260)`, viewport `{x: 40, y: 40, zoom: 0.9}`;
edges source→`n_image_to_3d.images`,
source→`n_segmentation.images`, `n_image_to_3d.mesh`→`n_part_extraction.mesh`, `n_segmentation.masks`→
`n_part_extraction.masks`, `n_part_extraction.parts`→`n_export.asset`; the other source is parked (unconnected, with a
"Use this source" button that rewires in one click); Part Extraction is `active` iff `partOptions.enabled`, and
Segmentation is `active` iff `partOptions.enabled && partOptions.previewSegmentation` (otherwise `bypass`).
`job_result` is added from the palette or the `rebuild_parts` template.

Write-through (`NodeShell` registers one `useWorkflowStore.subscribe` listener in an effect; it runs after every
topology/mode change, i.e. whenever `doc.revision` changes; `workflowStore` itself never imports `actions/` or
`navigationStore`, §1.4):
```
{config, issues} = deriveConfig(doc, registry, ctx)
config → applyPipelineConfig(config)   // actions/pipeline.ts: inputStore.setSource('image'|'prompt') (not for 'job');
                                       //   partOptionsStore.update({enabled: config.partExtraction}) and, ONLY when
                                       //   config.partExtraction is true, {previewSegmentation: config.segmentation}
                                       //   (with parts off the stored preference is kept; it is inert, §2.8); no-op writes skipped
       → navigationStore.setPipelineBlock(null)
null   → navigationStore.setPipelineBlock(firstError(issues).message)
```
`pipelineBlock` is recomputed from the full `validateGraph` result (not only `deriveConfig`) after every mutation
and whenever a bound input changes (NodeShell subscribes to the reference image count, the prompt length and the
viewed job), so `invalid_param` errors block Run with their message.
The first write-through that changes `partOptions.enabled` in a session shows the info toast "Part-aware output
turned on from the Node workflow" (or "off"). On Node shell mount: `featuresStore.ensureCatalog()`, then
`syncFromStores()` = `reconcile(doc, configFromStores(deriveConfig(doc).config?.source ?? null, input.source,
partOptions.form))`. On unmount: `setPipelineBlock(null)` and `setRunOverride(null)` (UI10).

#### 4.3.7 Compile to the server graph (`compile.ts`)
```ts
export interface BoundParams {                       // gathered by shells/node/bindings.ts from the stores with the §2.8 builders
  input_image: { reference_set_id: string | null; primary_image_id: string | null }
  prompt: ImageGenerationRequest | null              // buildGeneration(prompt, 1, ctx)
  job_result: { job_id: string | null }              // viewed job when completed
  segmentation: SegmentationOptions                  // buildSegmentationOptions(form) ?? {}
  image_to_3d: GenerationSettings                    // toRequestSettings(form, referenceMode) — unfiltered (§2.6)
  part_extraction: Omit<PartAnalysisOptions, 'export' | 'segmentation_id'>   // buildPartAnalysisOptions(form, ctx) minus `export`
  export: { per_part_glbs: boolean }                 // buildExportParams(form) (PartExportOptions; compiles to parts.export)
}
export type CompileOutput =
  | { ok: true; mode: 'job' | 'workflow'; request: WorkflowRequest /* {graph, layout} */; config: PipelineConfig
      nodeIds: readonly string[]; warnings: readonly GraphIssue[] }
  | { ok: false; issues: readonly GraphIssue[] }
export function compileDoc(doc: WorkflowDoc, r: NodeRegistry, bound: BoundParams, ctx: ValidationContext): CompileOutput
```
Algorithm:
1. `validateGraph`; any error → `{ok:false}`.
2. Run set = the active source connected to the run plus every active node on a path from it to the sink, plus the
   Segmentation node **only when** its masks feed an active Part Extraction in the run set. Parked sources,
   unreachable nodes and a Segmentation node feeding nothing are excluded (warnings), so the server never sees a
   graph it would reject with `unsupported_topology` (DESIGN §4.7.2).
3. Bypassed nodes are removed: `bypass:'drop'` drops the node and its edges; `bypass:{from,to}` rewires
   `X → node.from` + `node.to → Y` into `X → Y` (e.g. `n_image_to_3d.mesh → n_export.asset`). Bypassing Part
   Extraction also drops the `masks` edge, so an active Segmentation node becomes unreachable (rule 2).
4. Params: `bound[type]` for built-ins (the fixed §2.8 key lists; no catalog filtering); `def.contribute(node.values)`
   for future nodes (none in v1).
5. Nodes sorted by `topoOrder`; edges sorted by (topo index of `from.node`, `from.port`, topo index of `to.node`,
   `to.port`) → deterministic bytes (G7).
6. `mode = 'job'` iff the run node types are exactly {`input_image`, `image_to_3d`, `export`} (mirrors the server's
   `CompileResult.mode`, DESIGN §4.7.2).
7. `layout = {positions: <run nodes, rounded to integers>, viewport}` (≤ `graph_max_bytes` together with the graph;
   otherwise `graph_too_large`).

#### 4.3.8 Run (`shells/node/runNodeWorkflow.ts`; registered as `navigationStore.runOverride` while the shell is mounted)
```
runNodeWorkflow():
  out = compileDoc(doc, registry, boundFromStores(), ctx)
  !out.ok                → open Problems, announce the first error (assertive), return false
  out.mode === 'job'     → ok = await startDefaultGeneration()        // jobStore.submit() → the G1 body (UI2)
                            ok && rememberRun(activeJobId, 'job', out.nodeIds); return ok
  features legacy / !workflow.available → toast "Needs a newer Img3D backend" (or the reason); return false
  report = await workflowStore.validateRemote(doc.revision, out.request)   // not debounced at Run time
  report === null        → toast.error('Could not validate the workflow', 'Check the connection and try again.')
                            announce('Could not validate the workflow. Check the connection and try again.', 'assertive')
                            return false                              // abort or network error (§4.3.10); nothing posted
  !report.valid          → (stored by validateRemote); open Problems; return false
  wf = await runStore.startWorkflow(out.request)                     // POST /workflows {graph, layout}
  wf && rememberRun(wf.id, 'workflow', out.nodeIds); reveal('viewer'); return wf !== null
```
For `mode:'job'` the client posts its own G1 body through `jobStore.submit()`, so the bytes are today's in every mode;
the server returns no copy (DESIGN §6.5 `ValidationReport` has no `job_request`). Ctrl/Cmd+Enter and the toolbar Run
both reach `runNodeWorkflow` through `startGeneration()`'s override, so there is one shortcut.

#### 4.3.9 Status projection (`status.ts`)
```ts
export type RunView =
  | { kind: 'workflow'; workflow: WorkflowOut; job: Job | null; nodeIds: readonly string[] | null }
  | { kind: 'job'; job: Job; nodeIds: readonly string[] | null; legacyMap: Readonly<Record<string, string>> }
export function projectRunState(doc: WorkflowDoc, r: NodeRegistry, run: RunView | null): Readonly<Record<string, NodeStatusView>>
```
Which run is shown: the active run (job or workflow) if not terminal; else the viewed job (and its workflow from
`runStore.workflowByJobId`, if known); else none. `nodeIds` comes from the session `runIndex` (`workflowStore`,
`Record<runId, {kind, nodeIds}>`); nodes not in `nodeIds` show `idle` (a node re-created after Run never inherits the
old state). Runs from other sessions map by node **type**.
- Base states without a run: `bypassed` (mode), `locked`, `unavailable` (`available:false`), else `idle`.
- **Workflow path** (`WorkflowOut.steps`): the step's `node_id` (or, for implicit steps with `node_id: null`, the node
  of type `step.node`): `pending → queued`, `running → running` (`percent = step.progress`, 0..100), `done → done`
  (`warning` when `step.warning_count > 0`, DESIGN §4.7.5; a missing value counts as 0), `failed → failed`,
  `skipped → skipped`, `cancelled → cancelled`;
  `message = step.message`; `thumbnailUrl = fileUrl(step.artifacts[0].url)`. While the `reconstruction` step runs,
  the Image-to-3D node shows the job's `progress.stage_label` and `percent`.
- **Legacy job path** (plain `POST /jobs` runs; DESIGN `legacy_job_node_map`, from `features.workflow` or the
  catalog, fallback constant): order = [`input_image`, `image_to_3d`, `export`]; `k` = index of
  `map[job.progress.stage]`:

| `job.status` / stage | input_image | image_to_3d | export |
|---|---|---|---|
| `queued` (map → image_to_3d) | done | queued ("Queued, position {queue_position}") | queued |
| `running` + `waiting_for_model` | done | queued ("Waiting for the model") | queued |
| `running` + `preprocessing` (map → input_image) | running | queued | queued |
| `running` + `conditioning`…`decoding` (map → image_to_3d) | done | running (`percent`, `stage_label`) | queued |
| `running` + `exporting`/`finalizing` (map → export) | done | done | running |
| `completed` | done | done | done |
| `failed` at stage S | done before `k` | `failed` at `k` | skipped after `k` |
| `cancelled` at stage S | done before `k` | `cancelled` at `k` | skipped after `k` |

#### 4.3.10 Persistence, versioning, import/export (`serialize.ts`, `store/workflowStore.ts`)
- Persisted `img3d.workflow` v1 = `{doc}`. Load: parse → `schema === 'img3d.ui_workflow'` → `version === 1` (a newer
  version or unreadable JSON → raw string copied to `img3d.workflow.backup`, `templateFromConfig(configFromStores(null, input.source, partOptions.form))`
  loaded, info toast "The saved workflow was made by a newer version; the default workflow was restored.") → each node
  `def.migrate(values, node.version)` when the def version is higher → unknown types kept with `mode:'bypass'` +
  `unknown_node_type` → layout numbers clamped to finite ±100000.
- File export/import: `.img3d-workflow.json` = `{schema:'img3d.ui_workflow', version:1, app:'img3d-frontend@<version>',
  doc}`; never contains reference set ids, job ids, prompts or tokens (built-ins have no values). Import limits:
  ≤ 256 KB text, ≤ `maxNodes` nodes, ≤ `maxEdges` edges (fallback 32/64, the server limits); it reports issues and
  never throws on unknown types.
- `loadServerGraph(wfId)`: `GET /workflows/{id}/graph` → `fromServerGraph(graph, layout)` (topology + positions; node
  params are **not** written to the doc) + an optional "Load these settings" action that writes the params back to
  the stores (`settingsStore.replaceForm(formFromJobSettings(...))`, `inputStore.loadPrompt`, `partOptionsStore.update`)
  — the B `docFromJob` graft. Unknown param keys are dropped with a warning.
- `workflowStore` (Node chunk): `doc`, `past`/`future` (≤ 50, not persisted), `selection: {kind: 'node' | 'edge';
  id: string} | null` (node id or `edgeKey`), `serverReport: {revision, report} | null`, `runIndex`; actions
  `addNode, removeNodes, connect, disconnect, setMode, moveNodes (history entry on drag stop only), setCollapsed,
  setViewport (no history), select(sel), undo, redo, loadTemplate(id), importFile(text), exportFile(),
  syncFromStores(), loadServerGraph(id), applyServerReport(revision, report), rememberRun(id, kind, nodeIds),
  validateRemote(revision: number, request: WorkflowRequest, signal?: AbortSignal): Promise<ValidationReport | null>`
  (calls `api/workflows.validateWorkflow`, stores the report through `applyServerReport` when `revision` is still
  current, returns `null` on abort or network error).

#### 4.3.11 Locked future tuning nodes
The catalog lists `part_refine`, `mesh_tune`, `eye_correction`, `rig_correction` as `locked:true, available:false`
(DESIGN §4.7.2). The registry accepts them only because they are locked (guard). They appear only under
`VITE_FEATURE_REFINEMENT_PREVIEW==='true'`, render with a lock badge and "Paused until you approve refinement",
cannot be activated (an imported active one → `node_locked` error), are never compiled, and have no inspector
controls. A future approved tuning node needs, on the client: a catalog entry (served by the backend), an overlay
row (`binds: 'none'` + `ParamDef`s or a new binding), optional `contribute`/`migrate` — no change to the client
`compile`, `validate` or the shells. On the server it needs the three additive items of the DESIGN §4.7.2 "Extension
rule" (catalog entry with params model and `compiles_to`, one optional `WorkflowRequest` field, one
`WORKFLOW_STEPS` row); nothing is reserved now (DESIGN D13).

#### 4.3.12 React Flow adapter (`shells/node/canvas/*` — the only files importing `@xyflow/react@12.12.0`)
- `NodeShell` loads the canvas with `const NodeCanvas = lazy(() => import('./canvas/NodeCanvas.tsx'))`, so the List
  view (phones) never downloads xyflow.
- `nodeTypes = { pipeline: PipelineNode }` in `canvas/nodeTypes.ts` (module scope).
- `flowAdapter.ts` (PURE; `import type` from `@xyflow/react` only): `toFlowNodes(doc, registry, selection)` →
  topology only: `{id, type: 'pipeline', position, data: {id, type}, selected: selection?.kind === 'node' &&
  selection.id === id}`; `toFlowEdges(doc, statuses, reducedMotion, selection)` → `selected` from `selection?.kind ===
  'edge'`, colour by source port, `animated` only for running sources when motion is allowed; `mergeFlowNodes(prev,
  next)` (keeps `measured`, `width`, `height`, `dragging` from `prev` by id); `classifyChanges(nodeChanges,
  edgeChanges): {moved: Record<id,{x,y}>; removed: string[]; removedEdges: string[]; selected: {kind: 'node' |
  'edge'; id: string} | null | undefined}` (`undefined` = no selection change).
- **Controlled mode with measurements kept** (judge-found gap): `NodeCanvas` holds `const [nodes, setNodes] =
  useState(() => toFlowNodes(...))` and `syncedKey` state, with
  `syncKey = `${doc.revision}|${selection?.kind ?? ''}:${selection?.id ?? ''}|${catalogEtag ?? ''}``. During render,
  `if (syncKey !== syncedKey) { setSyncedKey(syncKey); setNodes((prev) => mergeFlowNodes(prev, toFlowNodes(...))) }`
  (render-time derivation, allowed; a conditional render-time `setState` compiles under the React Compiler [V critic
  probe]). So selection made in the List view or the Inspector, and catalog availability changes, reach the canvas.
  `onNodesChange = (changes) => { setNodes((n) => applyNodeChanges(changes, n)); forward(classifyChanges(changes,
  [])) }` — `dimensions` changes stay local; `position` with `dragging === false` → `moveNodes`; `remove` →
  `removeNodes`; `select` → `select({kind: 'node', id})`. Edges are derived each render from the doc, the statuses
  and the selection (no measured state); `onEdgesChange` → `classifyChanges([], changes)`: `remove` → `disconnect`,
  `select` → `select({kind: 'edge', id: edgeKey})`; `onConnect` → `connect`; `isValidConnection` → `canConnect`.
- **Run status never goes through `nodes`.** `NodeCanvas` provides `NodeStatusContext` (value = the
  `projectRunState(doc, registry, run)` result, recomputed whenever the active run, the viewed job or the doc
  changes; the React Compiler memoises it). `PipelineNode` reads `useContext(NodeStatusContext)[data.id]` (context
  does pass into React Flow custom nodes), so status chips and the inline progress bar update on every
  `workflow.updated`/`job.updated` without touching `revision`.
- `nodeShortcuts.ts` Delete/Backspace: a selected node → `removeNodes([id])`; a selected edge → `disconnect(edgeKey)`
  (React Flow's own `deleteKeyCode` stays `null`).
- Props: `colorMode="dark"`, `fitView` on first mount, `minZoom 0.25`, `maxZoom 2`, `zoomOnDoubleClick={false}`,
  `deleteKeyCode={null}` (Delete handled by `nodeShortcuts.ts`), `selectionOnDrag={false}`, `connectOnClick`
  (tap-tap connect), `zoomOnPinch`, `panOnDrag`, `connectionRadius` 32 under a coarse pointer (20 otherwise),
  `snapToGrid` + `snapGrid [16,16]` per preference, `<Background variant="dots"/>`, `<Controls
  showInteractive={false}/>` (single-pointer zoom, WCAG 2.5.1), `<MiniMap pannable zoomable/>` when the preference is
  on and width ≥ 840, `ariaLabelConfig` strings set, the React Flow attribution kept and restyled (MIT; authors ask
  to keep it). Theme: `@xyflow/react/dist/base.css` + `flow.module.css` mapping `--xy-*` variables to tokens
  [R `ui_modes_research` §5]; under `(pointer: coarse)` handles get a 44 px hit area via `::before`.
- `PipelineNode`: header (category stripe, title, mode chip Active/Bypassed, status chip icon + text, lock or
  unavailable badge, collapse button); body (summary lines from `useNodeSummary(type)` — e.g. "3 images · primary:
  front", "a knight with a red… (one-shot)", "Auto · anime character", "1024 · 500k tris · 2K tex", "7 groups ·
  semantic parts", "GLB + parts GLB"; an inline `ProgressBar` while running; a 2D thumbnail from step artifacts —
  never a 3D view, which protects the viewer singleton); `PortHandle` per port (glyph, colour token, visible label,
  `data-port-type`); `role="group"`, `aria-label` "Image-to-3D, running 45%". **No editable widgets in node bodies**
  (editing happens in the Inspector; no `nodrag` hazards).
- If the compiler check reports a bail-out in these files, the file gets `'use no memo'` with the reason in its
  header (documented in the slice report).

#### 4.3.13 Node shell UI (`shells/node/*`, F11)
| Width | Layout |
|---|---|
| ≥ 1024 | `NodeToolbar` (48 px) over a grid `minmax(0,1fr) clamp(320px, 30vw, 440px)`: left = canvas (or list) above `ViewerDock` (resizable 30–60 % height via a keyboard-operable splitter, collapsible); right = `NodeInspector` |
| 600–1023 | canvas/list full width; the Inspector opens as a right side `Sheet` (standard) on selection; a "Graph \| Viewer" segmented toggle in the toolbar swaps the main area (the viewer stays mounted with `active=false` while hidden) |
| `resolveNodeView` = list (`width < 600`, or coarse with `min(width, height) < 520`, or the preference) | `NodeListView` + the same toggle; the Inspector expands inline in the card |

- **Toolbar:** `GenerateAction variant="toolbar"` (Run/Cancel + issue count), status chip, `+ Add` (palette), Undo,
  Redo, Tidy (layered auto-layout by topological depth), Fit, Canvas|List, Problems, Templates (catalog templates +
  "Default for current settings"), Import, Export JSON, Library (`NodeLibraryDrawer` = `HistoryList` + "Load into
  canvas" for workflow-made jobs), App settings.
- **Inspector** (same Content components as the other modes): Input Image → `ReferenceContent`; Prompt →
  `PromptContent` + `PromptReviewContent` (its "Preview image" runs the review flow; "Use as Input Image" adopts the
  candidate's set and rewires the source to Input Image); Existing Result → viewed job summary + "Pick from Library";
  Segmentation → `SegmentationOptionsContent` + `SegmentationPreview`; Image-to-3D → `SettingsContent`
  (`showPartOptions={false}`); Part Extraction → `PartOptionsSection mode="parts"` + `PartsContent`; Export →
  `ExportContent`; locked node → explanation only; nothing selected → `ProgressContent` + `ProblemsPanel` + graph
  summary.
- **List view (`NodeListView`):** cards in topological order (title, status, mode switch, summary, issues, "Edit");
  each input port is a `<select>` of compatible upstream outputs (connect/disconnect without dragging, WCAG 2.5.7);
  "Add node" menu. It is the canonical screen-reader representation; the canvas has `aria-describedby` → a graph
  summary ("6 nodes, 2 bypassed, 0 problems, Image-to-3D running 45 %").
- **Shortcuts** (`nodeShortcuts.ts`, canvas-scoped, ignored inside text inputs): `/` or Ctrl+K palette; Delete /
  Backspace remove the selected node or disconnect the selected edge; Ctrl+B bypass; Alt+C collapse; Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z) undo/redo; `.` fit view;
  Enter opens the Inspector. Run = the global Ctrl/Cmd+Enter. Double-click on the pane or a connection dropped on
  the pane opens the palette filtered by compatible port types; on touch a 500 ms long-press (cancelled by > 8 px)
  opens the palette or the node menu.
- **"Run from here"** on a completed viewed job: Part Extraction's node menu → `analyzePartsForViewedJob()` (DESIGN
  `POST /jobs/{id}/part-analyses`). Nothing executes client-side.

### 4.4 Mobile mode (`shells/mobile/*`, lazy, F9)
```
<MobileShell> (posture portrait = <PhoneLayout/>)
  <CompactTopBar/>                    logo · status pill (→ status sheet) · App settings gear
  <BackendBanner nextRetryAt/>        (unchanged component)
  <main id="main-content" tabIndex={-1}>   full-bleed ViewerPanel (home; always mounted)
     <ProgressPill/>                   padding-bottom: var(--shell-inset-bottom); scroll-padding-bottom equal
  </main>
  <FloatingNav label="Main" items=[Create, Viewer, Parts, Library] center=<GenerateAction variant="fab"/>
               hidden={softKeyboardOpen}/>
  <SheetHost/>
```
| Sheet | Type / snaps (default) | Content |
|---|---|---|
| `create` | modal, `half`/`full` (`full`) | tabs Input \| Settings → `InputContent` / `SettingsContent` (with `PartOptionsSection`); **sticky footer `GenerateAction variant="bar"`** — Generate stays reachable even with the soft keyboard up |
| `review` | modal, `half`/`full` (`half`) | `PromptReviewContent` (opens on reveal `review`) |
| `parts` | **standard** (viewer stays interactive), `peek`/`half`/`full` (`half`) | `PartsContent` + "Model" disclosure (`ModelStatsContent`) + "Export" disclosure (`ExportContent`) |
| `library` | modal, `full` | `HistoryList` (`onOpened` → reveal `viewer`) |
| `progress` | modal, `half` | `ProgressContent` + Cancel (confirm) |
| `blocked` | modal, `half` | `GenerateBlockedContent` |
| `status` | modal, `half` | `StatusSheetContent` |
- **Nav semantics:** Create/Parts/Library `aria-expanded` + `aria-controls`; Viewer `aria-current="page"` when no
  sheet is open. Badges: Create = reference image count (or "P" when the source is prompt); Parts = part count;
  Library = activity dot while a run is active.
- **FAB state table:** §4.1 `fab` variant.
- **Landscape posture** (`nextMobilePosture` = `landscape`, e.g. 812×375, 740×360): `FloatingNav` is replaced by a
  `NavRail` (items + `GenerateAction variant="rail"` + gear in the footer); sheets open from the right as side sheets
  (`side="right"`, `max-width: var(--side-sheet-max)` = 420 px); the viewer fills the rest (`--shell-inset-left`).
- **Touch density:** `data-density='touch'` forced and `--hit-min`/`--control-h-*` = 48 px (§3.6); every target
  ≥ 48 px (UI probe), which relies on the F4 touch pass over the reused feature CSS (status pill 30 px, polygon presets
  24 px, advanced rows 40 px, reference star 26 px today [V `EngineStatus.module.css:L10`,
  `PolygonTargetControl.module.css:L19`, `AdvancedSettings.module.css:L12`, `ReferenceTile.module.css:L207-208`]) and on
  F8/F12 for the viewer toolbar and history rows; no hover-only affordances (reference tile star/menu always visible,
  as under `hover: none` today).
- **Gestures on sheets:** drag on the handle/header only (§4.1); the body scrolls normally.
- **Soft keyboard:** `useSoftKeyboard()` (F4: `focusin`/`focusout` on editables + `visualViewport` resize with height
  < 75 % of the layout height) hides the nav/FAB; it writes `--keyboard-inset` from `visualViewport` so the sheet
  footer sits above the keyboard.
- **Gestures:** sheet handle drag + velocity snap; drag-down closes modal sheets; backdrop tap closes modal sheets;
  long-press (500 ms) on a part row opens its menu (Hide/Show, Isolate, Download); viewer keeps OrbitControls touch
  (one finger orbit, two-finger pan/pinch) and gains double-tap → reset camera; every gesture has a button
  alternative.
- **Wide screens in Mobile mode:** full-bleed viewer; nav (≤ 560 px) and sheets (≤ 640 px) centred.

| Reveal target | Mobile action |
|---|---|
| `viewer` | close any sheet |
| `create`, `prompt` | open `create`, tab `input` |
| `settings` | open `create`, tab `settings` |
| `review` | open `review` |
| `progress` | open `progress` |
| `parts`, `export` | open `parts` at `half` |
| `history` | open `library` |

### 4.5 Tablet mode (`shells/tablet/*`, lazy, F10)

#### 4.5.1 Posture rules (`lib/viewport.nextTabletPosture`, hysteresis; typing guard)
Evaluated in this order; `prev` is the current posture (`null` at boot):
1. **Typing guard:** when `v.typing` and `prev !== null` → keep `prev` (the soft keyboard must not flip the layout).
2. **compact:** enter when `width < 600`; stay compact while `width < 632`.
3. **rail:** enter when `width ≥ 1.05 · height` (landscape) **and** (`width ≥ 840` **or** (`height < 520` and
   `width ≥ 640`)); stay rail while `width ≥ 0.95 · height` **and** (`width ≥ 800` **or** (`height < 540` and
   `width ≥ 620`)).
4. otherwise **bottom**.
Width changes apply after a 120 ms trailing debounce; `orientationchange` applies immediately.

| Viewport | Expected posture (from `prev = null`) |
|---|---|
| 1024×768, 1180×820, 1366×768, 1920×1080, 1280×800 | rail |
| 812×375, 740×360 (phone landscape in Tablet mode) | rail |
| 768×1024, 820×1180, 600×960 | bottom |
| 360×800, 390×844, 320×640 | compact |
| 830×800 with `prev = rail` / `prev = bottom` | rail / bottom (hysteresis band) |
| 615×900 with `prev = compact` / `prev = bottom` | compact / bottom |

#### 4.5.2 Layouts
| Posture | Navigation | Content |
|---|---|---|
| `rail` | floating left `NavRail`: Create, Viewer, Parts, Library; footer `GenerateAction variant="rail"` + App settings gear; ArrowUp/ArrowDown roving focus | side panel `width: clamp(320px, 34vw, 420px)` (`container: panel / inline-size`) right of the rail, showing `SectionHost` for the selected section; the viewer fills the rest; `ProgressPill` at the top centre of the viewer; selecting Viewer, or re-selecting the active item, collapses the panel; Library opens in the panel |
| `bottom` | `CompactTopBar` + `FloatingNav` with `GenerateAction variant="fab"` (same as Mobile, ≤ 560 px centred) | viewer on top, `height: calc(100dvh - var(--top-bar-height) - env(safe-area-inset-top) - var(--sheet-peek) - var(--shell-inset-bottom))`; the selected section in a **standard** `Sheet` (`peek`/`half`/`full`, default `half`); at 768×1024 the half sheet leaves a viewer ≥ 400 px tall |
| `compact` | `PhoneLayout` (identical to Mobile portrait) | – |
- Sections (shared by the rail panel and the bottom sheet): **Create** = tabs Input \| Settings (sticky Generate footer
  in `bottom` only; the rail has its own button); **Viewer** = collapse; **Parts** = `PartsContent` + Model + Export
  disclosures; **Library** = `HistoryList`.
- The selected section and sheet snap live in `navigationStore`, so an orientation flip keeps them.
- Reveal mapping: as Mobile; in `rail`, set the section and open the panel (`viewer` keeps the panel open and only
  focuses the viewer).

---------------------------------------------------------------------------------------------------
## 5. Prompt and image input in every mode

### 5.1 Source switch and effective source
- `InputSourceSwitch` = `SegmentedControl` "Image | Prompt" bound to `inputStore.source`
  (`data-testid="source-switch"`). The Prompt segment is never hidden outside Default: while features load it shows
  "Checking…" (selectable; Generate is blocked with rule 5a); when prompt-to-3D is unavailable the segment is
  disabled with the reason (`aria-describedby`), the switch shows the **effective** source (Image) and a note says
  "Prompt-to-3D is unavailable (reason). Using your images."; the stored choice and the draft are kept and come back
  when the capability does. The **Image segment is never disabled** (UI11).
- `effectiveSource()` (§2.9) forces `image` on a legacy backend (Default then hides the card, UI1) and when the
  backend reports prompt-to-3D unavailable, so nothing can trap the user in an un-generatable state.
- Placement per mode:

| Mode | Where the switch and inputs live |
|---|---|
| Default | `InputSourceCard` + `ReferencePanel` or `PromptPanel` (§4.2 placements) |
| Mobile | Create sheet ▸ Input tab: `InputContent` (switch + `ReferenceContent` or `PromptContent` + `PromptReviewContent`) |
| Tablet | Create section ▸ Input tab (rail panel or bottom sheet): `InputContent` |
| Node | Input Image and Prompt nodes; one is connected, the other parked with "Use this source" (rewires and writes `inputStore.source`) |

### 5.2 Prompt form (`components/input/PromptContent.tsx`), driven by `/features.pipeline.prompt_to_3d`
| Field | Control | Values / limits | Request key |
|---|---|---|---|
| Prompt | `<textarea id="prompt-input">` with visible `<label>` "Describe the character or object", `enterkeyhint="go"`, `maxLength = prompt.maxChars` (fallback 2000), counter "n / max" (`aria-live` only within 10 % of the limit), inline error slot | trimmed; control characters stripped on paste except `\n`/`\t` (the server rejects them, DESIGN §4.4.2) | `prompt` |
| Preset | `SegmentedControl` | `presets` (labels: Character, Object, Raw prompt) | `preset` |
| Style | `SegmentedControl` | "None" + `styles` (Realistic, Stylized, Anime) | `style` (omitted for None) |
| Part-friendly prompt | `Switch` "Ask for clearly separated hair, clothes and accessories" (default on) | – | `part_hint` |
| Flow | `SegmentedControl` "Review image first \| Straight to 3D" (default Review) | "Straight to 3D" is disabled with `aria-describedby` → "Straight to 3D needs workflows: ${workflow.reason}. Use Review image first." when `!workflow.available` (DESIGN §4.4.8); "Review image first" is never disabled (UI11) | decides the plan (§2.9) |
| Candidates | `SegmentedControl` 1–`maxImages` (default 2), review flow only | – | `num_images` |
| Advanced (disclosure) | Seed (`SeedControl` [V `settings/SeedControl.tsx:L21`] with the prompt form's seed/randomize), Size (`<select>` "Preset default" + `sizes`), Model (`<select>` "Backend default" + `selectableT2IModels`, each with `LicenseBadge`), Negative prompt (rendered only when `supportsNegativePrompt`; false for Z-Image-Turbo) | – | `seed`, `randomize_seed`, `width`/`height`, `model_id`, `negative_prompt` |
- The Raw preset shows a hint "Your text is sent unchanged (no 3D-friendly template)."
- `ColdStartNotice` sits at the top of the form (§5.4).
- `PromptPanel` = `Panel` ("Prompt", Sparkles icon) + `PromptContent` + `PromptReviewContent`.

### 5.3 Review-first vs one-shot (DESIGN §4.4.8; Q2 resolved: no paused workflow step)
**One-shot** (`flow: 'one_shot'`): Generate → `POST /workflows {source:{kind:'prompt', generation:{…, num_images:1}},
reconstruction, parts?}` (G3/G4). Progress shows the workflow steps (`text_to_image` → `import_image` →
[`segmentation`] → `reconstruction` → [`part_extraction`] → `export`); the `text_to_image` step's candidate thumbnail
appears as soon as the step is done (step artifact). The user's current reference set is not replaced.

**Review first** (`flow: 'review'`, default):
```
Generate ("Generate images") → POST /image-generations (G5) → runStore.reviewGenerationId
   → reveal('review'): Default scrolls to #prompt-review; Mobile/Tablet open the review sheet/section; Node selects Prompt
image_generation.updated (steps load_model → generate → check → save; percent) → candidates[]
Candidate card (thumbnail_url, size, seed, readiness issues as text chips) actions:
   [Use & generate 3D]  → adoptCandidateAndGenerate(ig, i): POST …/candidates/{i}/use (G6) → referenceStore.adoptSet(set)
                          → inputStore.setSource('image') → startDefaultGeneration()   (G1, or G2 when parts are on)
   [Use as reference]   → same without generating (toast "Image added as the front reference")
   [Open full size]     → fileUrl(candidate.url) in a new tab
   [Generate again]     → new POST /image-generations (randomize_seed respected)
While running: [Cancel] → POST /image-generations/{id}/cancel
```
- Readiness chips map `readiness.issues` directly through `WARNING_TEXT` (§2.11; DESIGN §4.4.5 uses the same `t2i_*`
  strings for issues and run warnings): `t2i_touches_border` "Touches the image border", `t2i_multiple_subjects`
  "Several subjects", `t2i_coverage_low` "Subject too small", `t2i_coverage_high` "Subject too large",
  `t2i_aspect_mismatch` "Wider than tall". `readiness.ok` → "Looks 3D-ready". The per-candidate run warnings
  ("Candidate i: …") are not shown again as chips.
- `used_as` non-empty → "Already used" chip; using it again still works (the backend appends).
- The Generate label switches to "Generate images" whenever the plan is `image_generation`; the FAB/rail button
  `aria-label` becomes "Generate images from the prompt".

### 5.4 Cold start notice and warm-up (`components/input/ColdStartNotice.tsx`)
| `prompt.defaultModelState` | Notice | Action |
|---|---|---|
| `installed` (not in memory) | "The text-to-image model is not in memory. The first image takes about {ceil(coldStartS/60)} min; later ones take seconds." | **Warm up now** → `actions/models.warmUpModel(defaultModelId)` → `POST /models/{id}/load` (202, state `loading`; DESIGN §6.1: the weights are read on a CPU thread, then a GPU warm-up is queued behind any image-to-3D job, so warming never delays a 3D generation) |
| `loading` | "Loading the text-to-image model… (about {n} min the first time)" + indeterminate `ProgressRing` | – |
| `loaded` | hidden | – |
| `not_installed` / `unavailable` / `error` | the prompt capability is unavailable → its `reason` is shown by the form | – |
The notice also appears in `PipelineSteps` while an `ig_` `load_model` step runs ("Loading text-to-image model",
DESIGN §4.4.4). State changes arrive through `features.updated` (DESIGN §6.1 fires it on every
`default_model_state` transition; no client-side re-fetch timer). A run whose GPU step waits behind image-to-3D jobs
shows the server's step message ("Waiting for the GPU (generation queued)") in `PipelineSteps` and the progress pill.

### 5.5 Image input and segmentation preview
- Image input is today's `ReferenceContent` (slots, drag & drop, paste, primary star, replace, remove) — unchanged.
  Images generated from a prompt show a "From prompt" chip on their tile when `ReferenceImage.source?.kind === 't2i'`
  (DESIGN §6.7), linking to the `ig_` run in the review panel.
- **Segmentation preview (optional)** `SegmentationPreview` (in `PartOptionsSection` and the Node Segmentation
  inspector), visible when `segmentation.available`, the effective source is image and a primary image exists:
  [Preview parts on this image] → `POST /segmentations {source:{kind:'reference_image', reference_set_id,
  image_id: primary}, options: buildSegmentationOptions(form) ?? {}}` → `segmentation.updated` → the overlay
  (`result.overlay_url`) over the image, plus the label list (category, side, confidence %, area) and
  `expected_terms_missing` ("Not found: held object"). This is a **preview only**: its `sg_` id is never sent with a
  generation (DESIGN §4.7.1: `parts.segmentation_id` must be null inside workflows). The separate switch "Segment the
  image before 3D" (`previewSegmentation`, parts on only) makes the workflow run its **own** segmentation step before
  reconstruction (`segmentation` in G2a); helper text: "Shown while the model is built. The 3D parts reuse it only
  when the image has real transparency; otherwise the parts step segments the model's own view again." (DESIGN
  §4.6.3; that case records warning `segmentation_rerun_for_alignment`, shown in the parts warnings.)

---------------------------------------------------------------------------------------------------
## 6. Parts UX

### 6.1 Data (`store/partStore.ts`, `lib/partsModel.ts`)
```ts
// lib/partsModel.ts (PURE)
export type PartViewKind = 'base' | 'physical' | 'deferred' | 'region'
export interface PartView {
  id: string; label: string; category: string; group: string; kind: PartViewKind; side: 'left' | 'right' | 'center' | null
  parentId: string | null; attachToId: string | null; hostId: string | null; symmetricWithId: string | null
  nodeName: string | null              // 'part:<id>' (base + physical); null for regions/deferred
  regionValue: number | null           // _FEATURE_ID_0 value on the host node (every kind)
  faces: number; confidence: number | null; centroid: readonly [number, number, number] | null
  modelUrl: string | null; downloadUrl: string | null; maskUrl: string | null; thumbnailUrl: string | null
  groupNote: string | null             // /features separable_groups[group].note
  geometryNote: string | null          // 'Hidden geometry not generated' when geometry.occluded_geometry_missing
  floaterCount: number                 // base record only: geometry.floaters.length (else 0; a missing key counts as [])
  highlight: 'ok' | 'shared_label'     // regions/deferred: 'shared_label' when value 0 or (hostId, regionValue) is shared
                                       // with another region/deferred record (normalisation rule 7)
  refinement: { status: string; locked: boolean; eligible: boolean; needs: readonly string[] }
}
export interface PartSetView { analysisId: string; jobId: string; status: 'completed' | 'partial'
  regionAttribute: string; parts: readonly PartView[]; byId: Readonly<Record<string, PartView>>
  relations: readonly RelationOut[]; warnings: readonly { code: string; message: string; partId: string | null }[]
  counts: { parts: number; physical: number; deferred: number; regions: number }
  quality: { cameraFitIou: number | null; unlabelledFaceRatio: number; partnerFilledRatio: number
             cutPartnerConsistency: number | null }        // from the result (DESIGN §4.6.7)
  urls: { assembled: string; download: string; zip: string; manifest: string; overlay: string | null } }
export type NormalizeResult = { ok: true; set: PartSetView } | { ok: false; error: 'unsupported_manifest' | 'duplicate_part_id' | 'invalid_part_id' | 'malformed' }
export function normalizePartResult(run: PartAnalysisOut, manifest: unknown | null, f: FeaturesSnapshot): NormalizeResult
export function groupsForDisplay(s: PartSetView, f: FeaturesSnapshot): readonly { group: string; label: string; parts: readonly PartView[] }[]
export function hierarchy(s: PartSetView): readonly { part: PartView; depth: number }[]          // depth-first by parentId
export function relationsOf(s: PartSetView, id: string): readonly { type: string; otherId: string; direction: 'out' | 'in' }[]
export type PartDisplayState = 'normal' | 'dimmed' | 'highlighted' | 'selected' | 'hidden'
export function displayStates(s: PartSetView, v: Pick<PartViewState, 'hidden' | 'isolated' | 'selected' | 'hovered'>): Readonly<Record<string, PartDisplayState>>
export function partColorIndex(s: PartSetView, id: string): number                             // stable 1..12 by display order
```
Normalisation rules:
1. Base data = `run.result.parts` (`PartSummaryOut[]`); the manifest (`getPartManifest(result.manifest_url)`) adds
   `parent`, `attach_to`, `host`, `symmetric_with`, `stats.centroid`, `geometry`, relations, warnings. A manifest that
   fails to load leaves a usable list (relations empty, inspector shows "Details unavailable").
2. `manifest.schema` must start with `img3d.parts/1` (major 1) → else `unsupported_manifest`.
3. Part ids must match DESIGN §2.3 (`^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,5}$`, ≤ 80) → else `invalid_part_id`;
   **duplicate ids → `duplicate_part_id` (hard reject)** (judge graft). Cap 512 parts.
4. `kind`: `physical_deferred` → `deferred`; unknown kinds are skipped with a warning.
5. `regionAttribute = result.region_attribute ?? manifest.region_attribute`, matched **case-insensitively** against
   geometry attributes (GLTFLoader lowercases custom names: `_FEATURE_ID_0` → `_feature_id_0` [V
   `GLTFLoader.js:L4871`]).
6. URLs are copied from the payload; nothing is constructed (UI9).
7. **Shared labels.** The viewer can tell regions apart only by their `_FEATURE_ID_0` class value on a host. A region
   or deferred record whose value is `0` (an overflow `component_###` kept as a region, DESIGN §2.4 rule 3) or whose
   `(hostId, regionValue)` pair is shared with another region/deferred record gets `highlight: 'shared_label'`: its
   row stays in the list with its data, but hide/isolate/highlight are disabled with "Highlight unavailable: this
   part shares its surface label with another part", and canvas picks on that value select the host. (DESIGN
   disambiguates these with the `face_part` buffer, §3.3; the UI does not fetch buffers in v1, and the result
   carries no URL for it.)

```ts
// store/partStore.ts (not persisted)
interface PartJobEntry { status: 'idle' | 'loading' | 'ready' | 'none' | 'error'; analyses: PartAnalysisOut[]   // newest first
  latestCompletedId: string | null; error: string | null }
interface PartSetEntry { status: 'loading' | 'ready' | 'error'; set: PartSetView | null; error: string | null }
export interface PartViewState { jobId: string | null; show: boolean; colorize: boolean; explode: number   // 0..1
  hidden: string[]; isolated: string | null; selected: string | null; hovered: string | null }
interface PartState { byJob: Record<string, PartJobEntry>; sets: Record<string, PartSetEntry>; view: PartViewState
  requestingJobIds: string[]
  loadForJob(jobId: string, force?: boolean): Promise<void>   // GET /jobs/{id}/part-analyses (+ latest when none active)
  refreshActive(): Promise<void>                              // fallback polling of non-terminal analyses
  upsertAnalysis(run: PartAnalysisOut): void                  // WS + REST; stale guard; terminal completed → loadSet
  loadSet(analysisId: string): Promise<void>                  // manifest + normalizePartResult
  requestAnalysis(jobId: string, options: PartAnalysisOptions): Promise<boolean>   // POST /jobs/{id}/part-analyses
  cancelAnalysis(id: string): Promise<void>; deleteAnalysis(id: string): Promise<void>
  setShow(b: boolean): void; setColorize(b: boolean): void; setExplode(x: number): void
  toggleHidden(id: string): void; setHidden(ids: string[]): void; isolate(id: string | null): void
  select(id: string | null): void; hover(id: string | null): void; resetView(jobId: string | null): void }
export const selectPartJob = (s: PartState, jobId: string | null): PartJobEntry | null
export const selectActiveAnalysis = (s: PartState, jobId: string | null): PartAnalysisOut | null
export const selectLatestSet = (s: PartState, jobId: string | null): PartSetEntry | null
```

### 6.2 Part options before generation (`components/settings/PartOptionsSection.tsx`)
- Master `Switch` "Part-aware output" (`aria-describedby` → reason when unavailable). Turning it **off** is always
  possible; turning it on is disabled when `parts.available && workflow.available` is false (UI11).
- While on:
  - **Separate as physical assets**: one checkbox per `separable_groups` entry (features order), each with
    `PartKindBadge` from `default_kind` ("Physical" / "Geometry later") and its `note` as muted helper text
    (e.g. eyes: "Eye area is cut as an open surface; the hidden eyeball is not generated."; eyelashes: "Eyelash
    geometry deferred; mask + lid curve only."). Defaults = `default_selected`.
  - **Kept as regions on the continuous body**: read-only list of `region_groups` labels (Face, Body) + "Groups you
    do not separate are kept as regions too. Regions are labels on the surface, not cuts."
  - **Method** `<select>` from `strategies` (label; unavailable options disabled with the backend `reason`, e.g.
    "Per-part regeneration is not implemented in this milestone." — every strategy carries `reason`, DESIGN §6.1; no
    strategy id is special-cased, UI14); default `default_strategy`.
  - **Subject** (Auto / Anime character / Realistic human / Object), **Detector profile** (Auto + profiles; an
    unavailable profile shows its reason, e.g. "See-through code is not vendored…"), **Also look for** chips input
    (`extra_terms`, Enter adds, Backspace removes, max `maxExtraTerms`, ≤ 40 chars each).
  - **Segment the image before 3D** switch (`previewSegmentation`, default off) with the §5.5 helper text, plus
    `SegmentationPreview` ("Preview parts on this image", preview only).
  - **Extra views** `NumberField` 0–`maxViews` ("Default (8)") — in "More options".
  - **Export**: "Assembled parts GLB — always" (info; DESIGN writes `model_parts.glb` for every analysis),
    "Separate part files" switch (`perPartGlbs` → `parts.export.per_part_glbs`; otherwise per-part files are built on
    demand when downloaded).
  - `LicenseNotice` for every `licenceNotices(features)` entry (Sapiens2: licence name + conditions), shown when the
    realistic profile is available; emphasised when Subject = Realistic human or Profile = Realistic (Q4).
- The Subject / Detector profile / Also look for / licence-notice block is its own component,
  `SegmentationOptionsContent`, used inside this section and alone by the Node Segmentation inspector.
  `PartOptionsSection mode="parts"` renders everything except that block (Node Part Extraction inspector);
  `mode="all"` (default) renders both.

### 6.3 Parts panel states (`components/parts/PartsContent.tsx`; `PartsPanel` = `Panel` + content, `id="parts"`)
| State | Shown |
|---|---|
| Features legacy | Default: panel hidden; other modes: "Part output needs a newer Img3D backend." |
| `flags.part_analysis` false | "Part analysis is turned off on the server." |
| No completed model in the viewer | "Generate or open a model to see its parts." |
| No analysis (`latest` → none) | `PartsEmpty`: "No parts yet." + [Analyse parts] when `parts.available` and `parts.rebuild` (uses the current part options; strategy shown) |
| Analysis queued/running | `PipelineSteps` of the `pa_` run (step labels from the payload, percent) + [Cancel] |
| Analysis failed | `errorText(error)` + [Try again] (re-run) |
| Ready (`completed` or `partial`) | `PartsToolbar` + grouped list + `PartInspector`; `partial` → info banner listing the warnings; an "Analysis details" disclosure shows the read-only quality numbers as text: "Camera alignment 91 %", "Unlabelled surface 4 %", "Inner wall matched 43 %", "Cut parts keep both walls 97 %" (from `set.quality`; a null value is omitted) |
| Manifest unreadable | list from `result.parts` + "Some details could not be read (unsupported_manifest)." |

### 6.4 Part list (`PartsToolbar`, `PartRow`)
- Header counts: "{n} parts · {p} physical · {d} geometry later · {r} regions".
- Toolbar: **Parts view** toggle (`aria-pressed`; shows the assembled GLB instead of the original), **Colorize**,
  **Show all**, **Physical only**, **Regions only**, **Group by: Category | Hierarchy**, **Explode** `RangeSlider`
  0–100 % (Parts view only; 0 restores exact positions — the reassembly view), a filter input when > 12 parts.
- Groups (collapsible `<section>`s) in this order: Base, Eyes, Eyebrows, Eyelashes, Hair, Clothes, Accessories,
  Detachable objects, Face (regions), Body (regions), Other; labels from `features.taxonomy` / `separable_groups` /
  `region_groups`.
- Row (`role="option"` inside a `role="listbox"` group, roving focus, `aria-selected`): colour swatch (Colorize on;
  `--part-<n>`), label + side "(L)/(R)", `PartKindBadge` (text: Base / Physical / Geometry later / Region — never
  colour alone), faces, confidence %, visibility `<button aria-pressed>` ("Hide Hair" / "Show Hair"), Isolate
  `<button aria-pressed>`, overflow `Menu`: Zoom to part, Download part GLB (physical only), Download mask, Copy part
  id. Pointer hover → `hover(id)`; click/Enter → `select(id)`. Rows ≥ 44 px under touch density.
- Region rows on a mesh without the region attribute: toggles disabled with "Highlight unavailable for this region"
  (honest; nothing faked). Rows with `highlight: 'shared_label'` (§6.1 rule 7) likewise.

### 6.5 Inspector (`PartInspector`)
Category, kind (+ `groupNote`), side, faces, confidence, parent, attached to / held by, host ("Region on Body"),
symmetric with, relations from `relationsOf` ("layered over Body", "occludes Face", "symmetric with Eye (right)",
"held by Right hand"), geometry facts ("Open surface · 4 boundary loops", "Hidden geometry not generated"; base
only: "12 small loose pieces kept on the body" from `floaterCount`),
expected/missing ("Asked for in the prompt"), refinement **read-only**: "Untouched · refinement is paused until you
approve it" (from `refinement.locked`). Downloads: part GLB (physical), mask PNG. No refine/tune action exists.

### 6.6 Viewer integration (`components/viewer/*`, F8; all `three` code stays in the lazy viewer chunk)
```ts
// viewer/viewerTypes.ts (still three-free)
export interface ViewerPartsBinding {
  physical: readonly { partId: string; nodeName: string }[]           // base + physical (mesh-owning)
  regions: readonly { partId: string; value: number; hostId: string; shared: boolean }[]
                                                                      // regions + deferred parts; shared = highlight 'shared_label'
}
export interface ViewerPartsProps { analysisId: string; url: string; regionAttribute: string }
  // strings that change only when another part set is shown; everything interactive is read by PartsModel itself
export interface ModelViewerProps { /* existing [V viewerTypes.ts:L11-25] */ parts?: ViewerPartsProps | null }
export interface ViewerApi { resetCamera(): void; screenshot(): string | null; focusPart?: (partId: string) => void }
```
- **Props stay static; `PartsModel` subscribes itself.** `ViewerPanel` passes only `{analysisId, url:
  fileUrl(set.urls.assembled), regionAttribute}` down `ViewerPanel → ModelViewer → ModelScene → PartsModel`, so a
  hover never re-renders those three (§8.3). `PartsModel` reads `usePartStore` with primitive or stored-reference
  selectors only: `s.sets[analysisId]?.set` (stored ref), `s.view.show`, `s.view.colorize`, `s.view.explode`,
  `s.view.hidden` (the array is replaced on change, so the ref is stable otherwise), `s.view.isolated`,
  `s.view.selected`, `s.view.hovered`. Derived data (`bindingFor(set)`, centroids, `displayStates`, colour indices)
  comes from module-level `WeakMap<PartSetView, …>` caches in `partsScene.ts`, so no selector or render allocates a new
  object. `PartsModel` applies every change imperatively to three objects in a layout effect keyed on those values.
- **Loading.** `PartsModel` (new, `viewer/PartsModel.tsx`) is a third model role next to `primary` and the tuned
  `secondary` (as today, only where `useTunerVisible()`, §3.9), positioned with the primary layout offset (same unit-AABB frame: positions are bit-identical, DESIGN
  §3.2 A4). It is mounted on the first "Parts view" toggle or part interaction (hover/select/hide/isolate) — not
  preloaded (the assembled GLB is ~41–86 MB); a chip shows "Loading parts model 45 %" from `viewerRuntime.parts*`
  (new fields mirroring the secondary ones [V `viewerRuntime.ts:L14-29`]). The original stays visible until the parts
  model is ready.
- **Index** (`viewer/partsScene.ts` → `buildPartsIndex(root, binding, regionAttribute)`): one traversal; a mesh node
  matches a physical/base part by **`object.userData.img3d?.id`** (DESIGN §3.12: every `part:<id>` node carries
  `extras.img3d.id`; glTF node `extras` → `userData` [V `GLTFLoader.js` `assignExtrasToUserData`]) and, as a fallback,
  **`object.userData.name === nodeName`** (GLTFLoader keeps the original name there and **sanitises `object.name`**:
  `part:eye.left` → `parteyeleft` [V `GLTFLoader.js:L4428-4429`, `PropertyBinding.sanitizeNodeName`]). `object.name`
  is never used. **`_FEATURE_ID_0` is written with `byteStride: 4`** (DESIGN §3.12, glTF vertex alignment), so
  GLTFLoader exposes the lower-cased `_feature_id_0` (`regionAttribute.toLowerCase()` [V `GLTFLoader.js:L4871`]) as an
  `InterleavedBufferAttribute`: `partsScene.readFeatureIds(attr: BufferAttribute | InterleavedBufferAttribute):
  Uint16Array` copies it once per host with `attr.getX(i)` (never `.array[i]`, which would read the padding) into a
  compact array; the pure functions below work on that copy. Unmatched bindings → `viewerRuntime.unmatchedPartIds`;
  their rows show "Not found in the 3D file" with toggles disabled.
- **Per-face labels and subsets** (`viewer/partsIndex.ts`, PURE, unit-tested): `faceLabels(index: Uint32Array |
  Uint16Array, featureIds: Uint16Array): Uint16Array` uses `featureIds[index[3f]]` when the first 1000 faces are
  consistent (DESIGN §3.4 duplicates vertices at region borders on every mesh node, so all three corners share the
  label), else the 3-corner rule (`a===b===c` → value, else 0xFFFF); `filterFaces(index, labels, keep: (label) =>
  boolean): Uint32Array`; `subsetFor(index, labels, value)`; `explodeOffset(partCentroid, assetCentroid, amount):
  [x,y,z]` = `(p − a) × amount × 0.6`. Budget: ≤ 60 ms for 2 M faces per new subset; results cached (LRU 8) per
  value / hidden-set key (CPU arrays only).
- **Visibility: one draw index per host.** `partsScene.prepareHost(mesh)` (idempotent, `WeakMap<Mesh, HostState>`,
  run in a layout effect before the parts model's first frame) keeps `originalIndex = mesh.geometry.index` on the CPU
  (source of every subset and of the pick clone; it is never uploaded) and installs `drawIndex = new
  BufferAttribute(Uint32Array.from(originalIndex.array), 1)` (`setUsage(DynamicDrawUsage)`) as the host's own index.
  Hiding or isolating regions writes the kept faces into `drawIndex.array`, then `addUpdateRange(0, n)`,
  `needsUpdate = true` and `geometry.setDrawRange(0, n)`. The host keeps **one** geometry object, so the existing
  wireframe overlay (`new Mesh(mesh.geometry, …)` [V `sceneUtils.ts:L173`]), clay and normals hide the same faces
  automatically, and `disposeScene` frees exactly the GL buffers in use. On `PartsModel` unmount the full copy is
  written back and the draw range reset (the cached GLTF stays valid for the next mount). Physical/base nodes:
  `object.visible`. Isolate = only that part visible (physical: other nodes hidden; region: host draw index = that
  label).
- **Overlays (highlight / select / colorize) and `sceneUtils.ts` (M, F8).** `sceneUtils.ts` gains `export const
  PART_OVERLAY_FLAG = '__img3dPartOverlay'`; `forEachMesh` (used by `applyViewMode`, `setPolygonOffset`,
  `restoreOriginalMaterials`), the target collection of `setWireframeOverlay` and `disposeScene` skip objects carrying
  it (today only `WIRE_OVERLAY_FLAG` is skipped [V `sceneUtils.ts:L128-131, L190-199`]), so `applyViewMode` never
  replaces overlay materials (overlays keep their colours in PBR, clay and normals) and no wire overlay is added to an
  overlay. Overlay geometry:
  - physical/base part → `new Mesh(node.geometry, material)` (the node's own geometry object, like the wire overlay;
    never disposed by the overlay);
  - region/deferred part → `new BufferGeometry()` with `setAttribute('position', host.geometry.getAttribute('position'))`
    and its own subset index. `disposePartOverlay(o)` first calls `o.geometry.deleteAttribute('position')`, then
    `o.geometry.dispose()`: disposing a geometry that still lists the shared attribute would delete the GL buffer
    that the host still draws (three `WebGLGeometries` `onGeometryDispose` removes every attribute of the disposed
    geometry). Materials are owned and disposed by `PartsModel`.

  Materials: `MeshBasicMaterial` (`--part-highlight` hover, `--part-select` selected, opacity 0.35, `depthTest` true,
  `polygonOffset` −1); colorize = one overlay per part at opacity 0.85 with the `--part-<n>` colours (≤ 64 physical +
  ≤ 40 regions; rows with `highlight: 'shared_label'` get none). The selected physical part also gets an
  `EdgesGeometry` (30°) outline, built lazily on its first selection and cached per part (disposed on unmount); parts
  above 300 k faces get no outline (overlay only); the first build is exempt from the 16 ms budget. The A-proposal
  `onBeforeCompile`/DataTexture patch is **not** built in v1 (judge verdict; revisit only after a spike).
- **Explode.** Physical nodes move by `explodeOffset(centroid, assetCentroid, explode)` (centroids from the manifest
  `stats.centroid`, else the node bounding-box centre); regions never move; `explode = 0` restores the stored original
  positions exactly. Transitions respect reduced motion (no tween).
- **Picking (no R3F pointer props on parts).** R3F raycasts every object that has handlers on every DOM pointer
  event (`state.raycaster.intersectObject(obj, true)` [V `@react-three/fiber/dist/events-9ce18a08.esm.js:L655`]), so
  throttling a handler would not throttle the raycast, and it would not stop during orbit drags. Instead:
  - `PartsModel` adds `pointermove`, `pointerdown`, `pointerup` and `pointerleave` listeners to `gl.domElement`
    (`useThree((s) => s.gl)`) in an effect; they only write NDC, a `dirty` flag and the down position into refs.
    `controls = useThree((s) => s.controls)` (OrbitControls `makeDefault` [V `ModelScene.tsx:L279`]) gets `start`/`end`
    listeners that set `orbitingRef`.
  - `useFrame`: when `dirty && !orbiting` and the pointer can hover (`(hover: hover)`), one
    `raycaster.setFromCamera(ndc, camera)` + `raycaster.intersectObjects(pickTargets, false)`; the first hit whose part
    and label are not hidden gives the part id; if it differs from `hoveredRef` → `usePartStore.getState().hover(id)`.
    A `pointerup` within 4 px of its `pointerdown`, with no orbit in between, runs the same pick → `select(id)` (empty
    space → `select(null)`); under `(hover: none)` this tap is the only pick.
  - **Pick targets** = one pick-only clone per mesh-owning node: a `Mesh` whose `BufferGeometry` shares the node's
    `position` attribute and the **original** full index (so it never follows the draw index), with `visible = false`
    (never rendered or uploaded; `Raycaster` does not test `visible`), `matrixAutoUpdate = false` and its matrix copied
    from the node's `matrixWorld` before each raycast (so it follows explode). The clones live in a separate
    `pickGroup` wrapped in drei `<Bvh indirect firstHitOnly={false} strategy={0}>` (`@react-three/drei/core/Bvh.js`,
    drei 10.7.9; `three-mesh-bvh` 0.8.3 is already installed as a drei dependency and lives in the lazy `r3f` chunk
    [V `vite.config.ts` groups], so there is **no new npm dependency**; `strategy={0}` is three-mesh-bvh `CENTER`
    [V `three-mesh-bvh/src/core/Constants.js:L2`], passed as a literal because `three-mesh-bvh` is not a direct
    dependency; `indirect` keeps the index order, so `hit.faceIndex` indexes `faceLabels`). The `<Bvh>` is mounted one
    idle callback after the parts model is first displayed (`requestIdleCallback`, fallback `setTimeout(…, 300)`); its
    build time is logged once (`console.info('[parts] bvh built', ms)`). Until it exists, hover picking is off and a
    click uses the plain O(F) `Mesh.raycast` (one raycast per click).
  - Hit mapping: a physical clone → its part; a host clone → `faceLabels[hit.faceIndex]` → the most specific region or
    deferred part with that value on that host whose `highlight` is `'ok'`, else the host part itself. Keyboard users
    select from the list (WCAG 2.1.1).
  - On unmount the clones are dropped (`deleteAttribute('position')`, `setIndex(null)`; the `<Bvh>` cleanup disposes
    the bounds trees).
- **`focusPart(id)`** fits the camera to the part's bounding box (computed once per part).
- **Memory.** `isLowMemory(useViewport())` (§3.5): the parts model mounts only while `show` is true and the original is
  unmounted while it shows; otherwise both stay mounted after first use (instant toggle). The same check replaces the
  breakpoint check of the tuned preload [V `ViewerPanel.tsx:L136, L146-148`].
- **Toolbar** (`ViewerToolbar.tsx`): a "Parts" toggle (Layers icon, `aria-pressed`) when the viewed job has a ready
  part set; a "Colorize" toggle while parts are shown; caption chip "Parts view · 14 parts". The tuner `VariantBar`
  stays as today in Default (viewing past results, locked or not) and is not rendered where `useTunerVisible()` is
  false (§3.9). Under `data-density='touch'` every toolbar control is ≥ `var(--hit-min)` (F8 owns
  `ViewerToolbar.module.css`/`ViewerPanel.module.css`).

### 6.7 Downloads (`components/export/ExportContent.tsx` via `actions/downloads.downloadArtifact`)
```ts
export type ArtifactRef =
  | { kind: 'job_glb'; jobId: string }                                             // existing downloadJobGlb
  | { kind: 'parts_glb' | 'parts_zip' | 'part_glb' | 'mask_png' | 'manifest' | 'overlay_png'; url: string
      fallbackName: string; sizeHint: number | null }
export function downloadArtifact(ref: ArtifactRef, onProgress?: (p: number | null) => void, signal?: AbortSignal): Promise<boolean>
```
| Row | Source | Filename (server) |
|---|---|---|
| Original model (GLB) | existing `downloadJobGlb` [V] | `img3d_<job>.glb` |
| Assembled parts model (GLB) | `result.download_url` | `img3d_<job>_parts_<pa>.glb` |
| Everything (ZIP) + "Include base model" checkbox | `result.zip_url` (+ `include_base=true`) | `img3d_<job>_parts_<pa>.zip` |
| Per-part GLBs (disclosure list; physical parts only) | `parts[].download_url` | `img3d_<job>_<part_id>.glb` |
| Masks | `parts[].mask_url` | server name |
| Manifest (JSON) | `result.manifest_url` | `manifest.json` |
| Segmentation overlay | `result.overlay_url` | `overlay.png` |
Each row shows progress (`ProgressBar`), "Preparing…" until the first byte for on-demand files, and errors via toast
("Download failed"). Export lives in: Default `PartsPanel` downloads section + existing `ModelStatsCard` button;
Mobile/Tablet Parts ▸ Export disclosure; Node Export inspector.

### 6.8 Re-run parts on a finished job
`actions/parts.analyzePartsForViewedJob()` → `partStore.requestAnalysis(viewedJobId,
buildPartAnalysisOptions(form, ctx))` (G8; G8a with "Separate part files" — the same builder as for workflows, no
`segmentation_id`) → `part_analysis.updated` → ready. Available from
`PartsEmpty` ("Analyse parts"), the panel overflow ("Analyse again with current options"), and the Node "Run from
here" menu. A new analysis never changes `model.glb` (DESIGN I2); the latest completed one is shown; older ones are
listed in the overflow ("Earlier analyses (2)") and can be deleted (`DELETE /part-analyses/{id}`, confirm).

---------------------------------------------------------------------------------------------------
## 7. Accessibility checklist (WCAG 2.2 AA; each item is an acceptance check)
1. **Keyboard (2.1.1, 2.1.2).** Every function works without a pointer in every mode: Node through React Flow's focus
   model **and** `NodeListView`; parts through the list; sheets through the handle. The only focus traps are modal
   dialogs/sheets, and Escape closes them; focus returns to the opener.
2. **Focus visible and not obscured (2.4.7, 2.4.11).** The global `:focus-visible` ring (`--focus-ring` [V
   `tokens.css:L89`]) on every new control. Scroll containers under floating chrome use `scroll-padding-bottom:
   var(--shell-inset-bottom)` and `scroll-padding-left: var(--shell-inset-left)`; the probe asserts no focused element
   intersects the nav, rail, FAB or a peek sheet.
3. **Target size (2.5.8).** ≥ 24 px everywhere; **≥ 48 px in Mobile and Tablet**; ≥ 44 px under a coarse pointer in
   Default and Node; React Flow handles get a 44 px `::before` hit area under coarse pointers.
4. **Dragging and gestures (2.5.7, 2.5.1).** Sheet drag ↔ handle tap / arrow keys; node connect ↔ tap-tap
   (`connectOnClick`) and List-view selects; node move ↔ arrow keys and Tidy; pinch ↔ zoom buttons (`<Controls>`);
   explode ↔ slider with keyboard; long-press ↔ the visible overflow menu; double-tap reset ↔ toolbar button.
5. **Colour is never the only signal (1.4.1).** Ports: glyph + label; node status: icon + text; part kind: text badge;
   licence badge text; readiness chips are text; validation messages are text.
6. **Contrast (1.4.3, 1.4.11).** P13 tokens; port/category/part colours ≥ 3:1 on their surface (`ui_probe.js` samples
   them).
7. **Name, role, value (4.1.2).** Floating nav = `<nav aria-label>` with buttons (`aria-expanded`, `aria-controls`,
   `aria-current`); rail the same with vertical roving focus; FAB `aria-label` + `aria-disabled` + `aria-describedby`
   when blocked; sheets `<dialog aria-labelledby>` or `region` + handle `aria-expanded`; nodes `role="group"` with a
   status label; progress `role="progressbar"` + `aria-valuenow`; part rows `role="option"` + `aria-selected`; part
   toggles `aria-pressed` with explicit labels; UI-style picker `role="radiogroup"`.
8. **Status messages (4.1.3).** Only through the global Announcer (`app/Announcer.tsx`: one `role="status"
   aria-live="polite"` and one `role="alert"` region; message cleared after 4 s; identical message within 1 s
   deduped): generation queued/started/completed/failed/cancelled, "2 images ready to review", "14 parts loaded",
   "Hair hidden"/"Hair shown", mode switched, Run blocked (assertive), validation summary on Run. Never per-second
   timers or progress frames (existing rule). **While a modal `<dialog>` is open** (Create/Review/Progress/Library/
   Blocked/Status sheets, App settings), the background is inert and leaves the accessibility tree, so messages and
   toasts are rendered inside the topmost modal (§4.1 `modalStack`): a 429 `queue_full`, a 503, a 422
   `extra_forbidden`, "Generation queued" and "Run blocked" stay visible and announced from inside the Create sheet.
   Checked in §10.6 step 8.
9. **Reduced motion (2.3.3 + media query).** All motion uses `--duration-*` (zeroed for `prefers-reduced-motion` and
   `data-motion='reduce'`); JS smooth scroll, sheet springs, edge animation, the FAB ring spin, explode tweening and
   View Transitions check `prefersReducedMotion()`.
10. **Orientation (1.3.4) and reflow (1.4.10).** Never locked; Tablet supports both orientations, Mobile supports
    landscape (rail). Every mode reflows at 320 CSS px without two-dimensional scrolling; the Node canvas is exempt as
    2-D content and `NodeListView` is its reflow alternative.
11. **Consistent identification (3.2.3, 3.2.4).** Same labels and icons in every shell via `FEATURE_META`.
12. **Labels and errors (3.3.1–3.3.3).** The prompt has a visible label, a counter and an inline error that says how
    to fix it; blocked reasons are explicit ("Add at least one reference image.").
13. **Skip link and landmarks.** The existing skip link targets `#main-content` in every shell; each shell has one
    `<main>`.
14. **Node screen-reader parity.** Canvas `aria-describedby` → graph summary; `ariaLabelConfig` set; List view is the
    canonical representation.

---------------------------------------------------------------------------------------------------
## 8. Performance budget and chunking

### 8.1 Budgets (`test/frontend/review/bundle_budget.mjs` over `frontend/dist`; gzip -9 via `node:zlib`)
Measured today on the clean `8654b90` build (gzip -9) [V]: initial JS **178.2 kB** = index 108.2 + react 67.5 +
vendor 2.0 + rolldown-runtime 0.5; CSS 14.4 kB; `three` 203.7 kB, `r3f` 79.0 kB and `ModelViewer` 5.6 kB lazy. About
82 kB gz of the index is app code (≈ 7.7 B gz per source line), so the planned eager files (≈ 5–6 k lines: stores,
actions, hooks, pure libs, `app/*`, Default additions) would cost ≈ 40–45 kB and an absolute 195 kB cap cannot hold.
Three measures keep the initial chunk small, and every budget is a **delta over a baseline that F0 records** in
`test/frontend/review/bundle_baseline.json` (`{initialJs, initialCss, chunks: {three, r3f, ModelViewer}}`, from the
`8654b90` build):
- (a) the existing tuner components stay `lazy()` in their importers, as one `tunerBundle` chunk (§1.3): ≈ 1.7 k
  lines (≈ 13 kB gz) leave the initial chunk and are never requested by Mobile/Tablet/Node. Honest cost: because the
  tuner UI is preserved (locked) in Default (§3.9), the Default shell **does** download this chunk every session
  (preloaded at boot, §3.6), so Default's first-view JS total is ≈ initial + 13 kB, the same tuner cost as today, only
  moved off the critical path. The initial budget below is unchanged because the chunk is not initial; the eager
  `components/tunerLock/{TunerLockFrame,TunerLockNotice,lazyTuner}` (< 1 kB gz) count against it;
- (b) the post-`/features` content is one lazy chunk (`components/featureBundle.ts`, §4.2), mounted only when
  `features.status === 'ready'`;
- (c) the shells, the App settings body, the Node canvas and the viewer stay lazy (as before).

| Chunk | Budget (gzip) | Check |
|---|---|---|
| Initial JS (entry + statically imported chunks: Default shell, stores, actions, pure libs) | **≤ baseline + 25 kB**, enforced once F6 and F7 have both merged (wave C) and in every later slice; printed only before that | sum of the `<script type=module>` + `<link rel=modulepreload>` files in `dist/index.html` |
| Initial CSS | ≤ baseline + 5 kB | `<link rel=stylesheet>` files |
| Feature bundle (`featureBundle`) | ≤ 30 kB | the chunk `manifest["src/components/featureBundle.ts"]` |
| Tuner bundle (`tunerBundle`: `TuningCard`, `BlenderRuntimeStatus`, `BlenderSetupDialog`, `TunedDownloadButton` and their exclusive imports) | ≤ 15 kB (≈ 13 kB estimated + 2 kB; the tuner files are frozen, so only shared imports can grow it), **never initial** | the chunk `manifest["src/components/tunerLock/tunerBundle.ts"]`; before F11 (no manifest): F6's grep of the initial files for the component names |
| App settings body | ≤ 8 kB | `manifest["src/components/preferences/AppSettingsBody.tsx"]` |
| Mobile shell + `shells/shared` | ≤ 22 kB | manifest walk below; the chunks shared by mobile and tablet are counted once, here |
| Tablet shell (excluding shared) | ≤ 12 kB | manifest walk |
| Node shell + workflow engine + `workflowStore` (excluding canvas) | ≤ 30 kB until F11 lands; then the size measured at F11's merge + 10 % (written into `bundle_baseline.json` by F11) | manifest walk |
| Node canvas own code | ≤ 8 kB | `manifest["src/shells/node/canvas/NodeCanvas.tsx"]` |
| `nodeui` vendor group (`@xyflow/*`, `d3-*`, `classcat`, nested zustand 4) | ≤ 65 kB, **never initial** | no initial file contains the string `react-flow__`; `nodeui-*.js` is not reachable statically from the entry |
| `ModelViewer` chunk growth (PartsModel, partsScene, partsIndex) | ≤ baseline + 8 kB | manifest entry of `ModelViewer.tsx` |
| `three` / `r3f` | baseline ± 3 kB | idem |

**Chunk attribution** (all lazy shells and the entry would otherwise all be called `index-<hash>.js`, and
`dist/index.html` maps nothing): F11 sets `build.manifest: true` (§8.2). The script reads
`dist/.vite/manifest.json`; the chunk set of shell `m` = everything reachable from the manifest key
`src/shells/<m>/index.ts` through `imports` + `dynamicImports`, **minus** every chunk the entry (`index.html`)
reaches statically (`imports` only), minus the `nodeui`, `three` and `r3f` vendor chunks and the `tunerBundle` chunk
(budgeted separately; it is dynamically reachable from shared components such as `ModelStatsContent` but only
requested where `useTunerVisible()` is true, i.e. never by Mobile/Tablet/Node, §3.9).
Chunks reachable from both the mobile and the tablet shell are reported once as "Mobile + shared". Before F11 the
manifest does not exist yet; the script then prints the initial totals only.

### 8.2 `frontend/vite.config.ts` (F11) — fixes the judge-verified vendor-regex hazard
Today `vendor` is `/node_modules[\\/](zustand|use-sync-external-store)[\\/]/` at priority 40 [V `vite.config.ts:L77`];
it matches nested copies too (`tunnel-rat/node_modules/zustand` exists today [V], and `@xyflow/react` brings a nested
zustand 4.5.7 [V `environment_audit` §8]), which would pull zustand 4 into the initial chunk. New groups:
```ts
{ name: 'react', test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/, priority: 50 },
// top-level zustand ONLY: the module id contains exactly one node_modules segment
{ name: 'vendor', test: /^(?!.*[\\/]node_modules[\\/].*[\\/]node_modules[\\/]).*[\\/]node_modules[\\/]zustand[\\/]/, priority: 40 },
{ name: 'nodeui', test: /[\\/]node_modules[\\/](@xyflow[\\/]|d3-(color|dispatch|drag|ease|interpolate|selection|timer|transition|zoom)[\\/]|classcat[\\/])/, priority: 35 },
{ name: 'three', test: /node_modules[\\/]three[\\/]/, priority: 30 },
{ name: 'r3f', /* unchanged */ priority: 20 },
```
`use-sync-external-store` leaves the vendor group (the app's zustand 5 `create` does not use it; only the nested
zustand 4 copies of drei/xyflow do), so Rolldown places it with its lazy importers. The budget script's
initial-membership check proves the result; the r3f/three ±3 kB check catches a regression in the viewer chunks.
F11 also adds `build.manifest: true` (writes `dist/.vite/manifest.json`; not served by the app, used only by
`bundle_budget.mjs`).

### 8.3 Runtime budgets (browser pane, Performance panel + `performance.mark`)
| Interaction | Budget |
|---|---|
| Mode switch, warm chunk | ≤ 150 ms to the first shell paint (desktop); cold ≤ 600 ms on localhost |
| Sheet open / snap | animation starts in the same frame; transform/opacity only |
| Physical part hide/show/isolate/highlight | ≤ 1 frame (16 ms); no React re-render of `ViewerPanel`, `ModelViewer` or `ModelScene` (only `PartsModel` re-renders, §6.6); no GLB refetch |
| Region hide/isolate/highlight (new subset, 2 M faces) | ≤ 60 ms first time, then cached (≤ 16 ms) |
| Hover pick (BVH ready, 2 M faces) | ≤ 2 ms per frame, at most one raycast per frame, none while orbiting; BVH build logged once and excluded (built after the first display, §6.6) |
| First outline of a selected physical part | exempt (built once, cached); parts > 300 k faces get no outline |
| `job.updated` / `workflow.updated` progress ticks (≤ 5/s, ≤ 2/s) | re-render only progress consumers (`ProgressContent`, `ProgressPill`, FAB ring, the running node chip) — never a whole shell (React DevTools "highlight updates" check) |
| Node canvas with ≤ 12 nodes | first render ≤ 50 ms; drag at 60 fps |
| `validateGraph` + `compileDoc` (≤ 32 nodes) | ≤ 2 ms |
| Parts list with 200 rows | ≤ 16 ms render |
| Persistence | preference writes synchronous and tiny; workflow layout written on drag stop / move end only |
| Memory (`lowMemory`) | never the original and the assembled GLB mounted at the same time |

---------------------------------------------------------------------------------------------------
## 9. Responsiveness matrix
Viewports (CSS px), each × 4 modes via `?ui=` in the browser pane (`resize_window` custom sizes; `mobile` preset only
for 375×812 touch emulation): **320×640, 360×800, 375×812, 390×844, 740×360, 812×375, 768×1024, 820×1180, 1024×768,
1180×820, 1280×800, 1366×768, 1920×1080** = 13 × 4 = 52 cells.

| Viewport | Default (breakpoint) | Mobile (posture) | Tablet (posture) | Node (view) |
|---|---|---|---|---|
| 320×640, 360×800, 375×812, 390×844 | mobile tabs | portrait | compact (= Mobile) | list |
| 740×360, 812×375 (coarse) | mobile tabs (P7) | landscape rail + side sheets | rail | list (coarse, min side < 520) |
| 768×1024, 820×1180 | tablet, portrait variant (P9) | portrait (centred nav/sheets) | bottom | canvas, inspector sheet (600–1023; also under a coarse pointer, since min side ≥ 520) |
| 1024×768, 1180×820 | tablet | portrait layout, wide (centred) | rail | canvas + inspector + dock (≥ 1024) |
| 1280×800, 1366×768, 1920×1080 | desktop 3 columns | portrait layout, wide | rail | canvas + inspector + dock |

Pass criteria per cell (`ui_probe.js` JSON): `document.documentElement.scrollWidth === innerWidth` (no page
horizontal scroll; the Node canvas pane is excluded); interactive elements < 48 px = 0 in Mobile/Tablet, < 44 px = 0 in
Default/Node when the viewport is emulated coarse (Default: the protected tuner subtree is excluded and reported
separately, §3.9); 0 focusable elements obscured by fixed chrome; exactly one
`canvas` (viewer) element; `#main-content` present; `<html data-ui-mode>` equals the requested mode;
`data-testid="source-switch"`, `#prompt-input` (after switching to Prompt) and `[data-testid="reference-dropzone"]`
are reachable (visible after at most one navigation action); the viewer box is ≥ 220 px tall in every cell where it is
the main content. Default at 320 px: every `ViewerToolbar` segment has `scrollWidth ≤ clientWidth` (P6).

---------------------------------------------------------------------------------------------------
## 10. Quality gates and tests

### 10.1 Gate commands (PowerShell; run after every slice; all must pass)
```powershell
cd E:\14-OCT-2026\Img3D\frontend
npm run lint                                   # oxlint: 0 warnings, 0 errors
npm run build                                  # tsc -b (app + node configs) + vite build → frontend\dist
cd E:\14-OCT-2026\Img3D
node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"     # the glob is required on Node 22.17
node test\frontend\review\compiler_check.mjs   # prints [] and "non-success events: 0" (compiled count grows)
node test\frontend\review\import_boundaries.mjs                # 0 violations; 0 stale allowlist entries (§1.4)
node test\frontend\review\protected_frontend.mjs               # paused tuner files byte-identical (UI13)
node test\frontend\review\bundle_budget.mjs frontend\dist      # §8.1 budgets; nodeui not initial
# test double (background terminal):
node test\frontend\mock_backend.mjs --port 8765 --engine-ready --seed-jobs 2 --features full --strict-requests on
node test\frontend\ui_modes_mock_smoke.mjs --base http://127.0.0.1:8765        # all 5 scenarios via /api/__test/scenario
node test\frontend\mesh_tuner_mock_smoke.mjs                                   # tuner contract still valid
```
Code rules checked in review (and by `import_boundaries.mjs` where mechanical): no `try/finally`, `?.`, `??` or `&&`
inside `try` blocks in components/hooks (React Compiler); no new object/array from a zustand selector (`useShallow`
only with a comment); no `setState` in effects; **no non-hook function whose name starts with `use`** (oxlint
`rules-of-hooks` and the compiler treat them as hooks — hence `adoptCandidate`, `postCandidateUse`); constants and
tables only in `.ts` files.

### 10.2 Unit tests (`test/frontend/unit/*.test.mjs`, `node:test` + `node:assert/strict`; import
`../../../frontend/src/**.ts` directly; fixtures via `test/frontend/unit/_fixtures.mjs` → `readFixture(name)`)
| File (owner) | Asserts |
|---|---|
| `image_job_request.test.mjs` (F2) | `JSON.stringify(buildImageJobRequest(...))` equals the **bytes** of fixtures G1, G1a (advanced override), G1b (`primary_image_id: null`) for 6 settings forms; equals a re-implementation of `jobStore.ts:L208-212`; `toRequestSettings` behaviour unchanged (moved verbatim); **static** re-export check: the source text of `store/settingsStore.ts` contains `export { toRequestSettings, SAMPLER_FIELDS, hasAdvancedOverrides } from '../lib/settingsRequest.ts'` and `export type { SettingsForm, SamplerForm, SamplerField } from '../lib/settingsRequest.ts'` (`verbatimModuleSyntax` needs the separate type export; member order free; the store is never imported under Node, §1.5; `tsc -b` checks resolution) |
| `workflow_request.test.mjs` (F2) | G2, G2a, G3, G4, G5, G6, G8, G8a byte-equal; key order per §2.8; null/default omissions (style null, model null, size null, empty negative prompt, unsupported negative prompt, all-default segmentation → `null`); `separate` resolved from `default_selected` and filtered to known groups; `num_images` is 1 in workflows; **every key of every built object (recursively, per the §2.6 path → object-name table, incl. `SegmentationRequest.source`, `Reconstruction` and `AdvancedSettings`/`SamplerSettings`) is in `fixtures/request_keys.json`, and every object reached has a name in the table (an unnamed nested object fails the test); the walk is the F0 helper `assertKnownKeys(body, rootName)` in `_fixtures.mjs`, also used by `workflow_compile.test.mjs`**; no built `WorkflowRequest` has a top-level `export`, a `parts.segmentation_id`, or `segmentation` without `parts`; `parts.export` is `{"per_part_glbs":true}` exactly when "Separate part files" is on, for workflows and direct analyses alike; `buildExportParams` → `{"per_part_glbs":false}` or `{"per_part_glbs":true}` |
| `generate_plan.test.mjs` (F2) | the §2.9 table row by row; **effective source forced to `image` when features are `legacy` or `prompt.available` is false in a `ready` snapshot, and parts forced off when `legacy`** (UI11, judge fix); `loading`/`error` keep the prompt source and block; prompt + features `loading` → `features_loading`; **prompt + `one_shot` + `workflow.available:false` → `workflows_unavailable`, while `review` in the same snapshot → `image_generation`**; parts on + `no-seg` scenario (components only) → still a `workflow` plan; parts on + `disabled` → `parts_unavailable`; G9 (legacy features + parts on) → `job` plan equal to G1; **`legacy404` snapshot with stored `parts.enabled=true` and `source='prompt'` → `effectiveSource === 'image'`, `effectivePartsEnabled === false`, plan = G1 (`job`), and `shouldRenderPartOptions(features, form)` is `false`, so the featureBundle is not requested; the same stored forms with an `error` snapshot → `shouldRenderPartOptions === true` (off switch reachable) and plan `blocked` with `features_error` (prompt source, first match); with `source='image'` and one reference image instead → `blocked`/`parts_unavailable`** |
| `generate_availability.test.mjs` (F2) | image-source reasons and notes **byte-identical** to `useGenerateAvailability.ts:L55-73` for 9 scenarios (unreachable, waiting, engine unavailable, engine error, no images, uploading, busy, queue full, submitting) + engine-loading/queue notes; prompt rules 5a–5e in order; 7a/7b; `pipelineBlock` precedence; workflow and review generation count as busy; run queue full; cold-start note |
| `feature_flags.test.mjs` (F2) | `normalizeFeatures` for `features/{full,no-t2i,no-seg,disabled}.json`; malformed sub-objects → `missing`; every strategy keeps its `reason`; `LEGACY_FEATURES`; `tunerLock` truth table (status × `mesh_tuning.enabled`: `legacy` → `unlocked` with either flag value, `ready` + true → `unlocked`, `ready` + false / `loading` / `error` → `locked`) and `tunerLockNotice` per status (`ready` → exactly `TUNER_LOCK_NOTICE`); `selectableT2IModels`; `defaultSeparate`; `licenceNotices` finds exactly the three Sapiens2 conditions in `full` without any hard-coded id |
| `forms.test.mjs` (F2) | `sanitizePromptForm`/`sanitizePartOptionsForm` drop unknown/corrupt values, clamp `numCandidates`, truncate prompts > 8000 chars; defaults |
| `ui_modes.test.mjs` (F2) | `parseUiOverride` (valid, invalid, case, repeated); `sanitizePreferences`; `readPersistedState` (`'{"state":{"uiMode":"node"},"version":1}'` → `{uiMode:'node'}`; bare object without `state` → `null`; invalid JSON → `null`; `null` → `null`); `resolveUiMode` (override wins, non-ready → default); `suggestUiMode` table at all §9 viewports; `resolveDensity`; `resolveNodeView` |
| `viewport_posture.test.mjs` (F2) | `nextTabletPosture` and `nextMobilePosture` tables of §4.5.1/§3.5 including both hysteresis bands and the typing guard (pure functions only; the debounce lives in the impure `hooks/useViewport.ts` store); `widthClass`; `isLowMemory`; `resolveNodeView` at 812×375 coarse → list, 768×1024 coarse → canvas |
| `parts_model.test.mjs` (F2) | `normalizePartResult` on `part_analysis_completed.json` + `manifest_parts.json`; without manifest; `duplicate_part_id`, `invalid_part_id`, `unsupported_manifest`; kind mapping; region attribute case-insensitive; **rule 7: a value-0 overflow region and two regions sharing `(host, value)` get `highlight: 'shared_label'`, the others `'ok'`**; `floaterCount` from `geometry.floaters`; `quality` copied from the result; `urls.assembled` non-null; `groupsForDisplay` order; `hierarchy`; `relationsOf`; `displayStates` for hidden/isolated/selected/hovered combinations; `partColorIndex` stable |
| `run_steps.test.mjs` (F2) | `isStaleRun` (rank, terminal, timestamps); workflow step views (labels, 0..100 progress) vs run steps (0..1); `errorText` table for every run error code (`step_failed` from `workflow_failed.json` → `` `${stepLabel('part_extraction')} failed: ${child_error.message}` ``; `step_failed` with `details: null` → the `${label}: ${error.message}` fallback); warning text fallback |
| `pure_imports.test.mjs` (F2) | static scan of the §1.5 list: relative value imports end in `.ts`; no react/zustand/axios/three/`@xyflow` value import; no `window.`, `document.`, `localStorage`, `import.meta.env`, `matchMedia`, `navigator.` (global-token scan skipped for `lib/storage.ts` only, with a comment naming §1.5) |
| `workflow_registry.test.mjs` (F3) | `registryFromCatalog(catalog.json)` has the 7 + 4 + 3 types with catalog ports; **`createRegistry` throws `refine_unlocked` on an unlocked refine def** (judge graft); duplicate/port-clash throw; `BUILTIN_REGISTRY` availability; palette (non-refine hidden types only with "Show unsupported"; the refine group — `hidden_by_default: true` in the fixture — only with the preview flag, never with "Show unsupported" alone; present types disabled) |
| `workflow_graph.test.mjs` (F3) | ops are pure (deep-frozen input unchanged; untouched subtrees `===`); `revision` increments; ids `n_<type>`; second instance throws; `canConnect` matrix (4 output types × every input) incl. self-loop and cycle refusal; single-input replacement; `topoOrder` ties by id |
| `workflow_validate.test.mjs` (F3) | each code of §4.3.5 with a positive and a negative case (`fixtures/graphs/*.json`, each with `expected_issues`) and the exact messages; legacy registry → `capability_unavailable` only on active prompt/segmentation/part nodes |
| `workflow_compile.test.mjs` (F3) | template docs for all **8 valid configs** (§4.3.6) compile; `mode:'job'` exactly for the trivial graph; bypass rewiring (`part_extraction` mesh→parts, which also drops the masks edge; segmentation dropped); parked source, unreachable nodes and an active Segmentation node feeding no active Part Extraction are excluded (warning `ui_unreachable_node`, never sent); deterministic node/edge order; **G7 byte-equal**; G7 and every compiled template request pass `assertKnownKeys(request, 'WorkflowRequest')` (`WorkflowGraph`, `WorkflowGraph.node`, `node.<type>` params, `WorkflowGraph.edge`, `PortRef`; `layout` opaque); graph-form ≡ simple-form equivalence: a test-only mapper `graphToSimpleForm` (in the test file, mirroring DESIGN §4.7.2 "Compiles to": Export params → `parts.export` with default-valued keys removed, an empty object → key absent; Segmentation → top-level `segmentation`) applied to each compiled template equals `buildWorkflowRequest` for the same stores, for all 8 configs |
| `workflow_status.test.mjs` (F3) | every row of the legacy-job table (each stage via `fixtures/jobs/*.json`); workflow step mapping incl. implicit steps, artifacts → thumbnails, progress units, `done` + `warning_count: 1` → `warning`, `done` without `warning_count` → `done`; `runIndex` restricts to compiled ids (a re-created node shows `idle`); base states (bypassed/locked/unavailable) |
| `workflow_serialize.test.mjs` (F3) | round trip; newer version → backup + template signal; unknown type kept + bypass; `migrate` called when def version is higher; size/node/edge limits; exported files contain no ids of user data or prompts; `fromServerGraph(template graph)` |
| `workflow_derive.test.mjs` (F3) | `deriveConfig` for the canonical topologies (two sources with one parked, bypassed parts, job_result, missing export); **an active Segmentation node with Part Extraction bypassed → `segmentation: false` + `ui_unreachable_node` on the Segmentation node**; `templateFromConfig` → `deriveConfig` round trip for all 8 valid configs; `configFromStores` never yields `segmentation: true` with `partExtraction: false`; `reconcile` minimal and idempotent |
| `parts_index.test.mjs` (F8) | `faceLabels` fast path and 3-corner fallback on a compact `Uint16Array`; `filterFaces`/`subsetFor` on a synthetic 3-region mesh; 2 M-face timing logged (< 60 ms target, informational); `explodeOffset(…, 0)` is `[0,0,0]` |
| `flow_adapter.test.mjs` (F11) | `toFlowNodes` carries topology only (`data` = `{id, type}`) and sets `selected` from a node selection; `mergeFlowNodes` keeps `measured/width/height` by id and drops removed nodes; `toFlowEdges` colours by source port, animates only running sources when motion is allowed and sets `selected` from an edge selection; `classifyChanges` (position with `dragging:false` only, node remove, edge remove → `removedEdges`, node and edge select, `undefined` when no selection change) |

### 10.3 Fixtures (`test/frontend/fixtures/`, F0; shared by unit tests, the mock and the smoke)
- `features/{full,no-t2i,no-seg,disabled}.json` — complete `FeaturesOut` per DESIGN §6.1 (`full` = the DESIGN example
  with both parser profiles available, every strategy with its `reason`, the four refine nodes `hidden_by_default:
  true`, `models` as full `ModelOut` entries, and the Sapiens2 entry with exactly the three `license.conditions` "No
  biometric identification", "No deepfakes or impersonation", "No military use"; `no-t2i` =
  `prompt_to_3d {available:false, code:'not_installed', reason:'Z-Image-Turbo weights are not installed under
  backend/.model/t2i/Z-Image-Turbo'}`, `default_model_state:'not_installed'`, node `prompt` unavailable; `no-seg` =
  segmentation `{available:false, code:'not_installed'}`, semantic strategies unavailable, `default_strategy:
  'components'`, node `segmentation` unavailable; `disabled` = every flag false, new pipeline entries `{available:
  false, code:'disabled', reason:'Part-aware features are turned off (IMG3D_PART_AWARE_ENABLED=false)'}`,
  `image_to_3d` available, `workflow.available:false`). `legacy404` has no file (404).
- `request_keys.json` (§2.6: the DESIGN §4/§6 key sets, the path → object-name table and the `opaque` list, used by
  the mock's strict mode, `workflow_request.test.mjs` and `workflow_compile.test.mjs` through
  `_fixtures.mjs → assertKnownKeys(body, rootName)`).
- `catalog.json` (`CatalogOut` with all node types, the 5 templates, `params_schema.properties` for every node type,
  Export params `{per_part_glbs, previews}`, refine nodes `hidden_by_default: true`), `models.json` (`{items}` incl.
  hidden 3D entries), `image_generation_completed.json` (readiness issues as `t2i_*` codes),
  `segmentation_completed.json`, `part_analysis_completed.json` (non-null `assembled_url`/`download_url`,
  `partner_filled_ratio`, `cut_partner_consistency`), `manifest_parts.json` (the DESIGN §3.11 example, complete enough
  to validate, with `summary`, `face_part`/`face_flags` buffer descriptors, base `geometry.floaters` with 2 entries,
  and one value-0 overflow `component_###` region for rule 7), `workflow_running.json`, `workflow_completed.json`
  (every step carries `warning_count`; `text_to_image` has `1`), `workflow_failed.json` (`error.code: 'step_failed'`,
  `details = {step: 'part_extraction', child_kind: 'part_analysis', child_id: 'pa_…', child_error: {code:
  'partition_failed', message: …, details: null}}`),
  `jobs/*.json` (one legacy job per stage), `graphs/*.json` (validation cases incl. "segmentation not feeding part
  extraction").
- `requests/*.json` — **golden bodies** (`R` = `{"reference_set_id":"rs_0123456789ab","primary_image_id":"img_0123456789ab"}`
  fields; `S` = the default settings object `{"resolution":1024,"seed":0,"randomize_seed":true,"polygon_target":500000,"texture_size":2048,"remove_background":true,"reference_mode":"primary_only"}`; `SEP` =
  `["eyes","eyebrows","eyelashes","hair","clothes","accessories","detachable"]`; files contain the expanded literal JSON):

| Id | Endpoint | Body (expanded in the file) |
|---|---|---|
| G1 | `POST /jobs` | `{"reference_set_id":"rs_0123456789ab","primary_image_id":"img_0123456789ab","settings":S}` |
| G1a | `POST /jobs` | as G1 with `settings` = S + `"advanced":{"shape":{"steps":20}}` (last key) |
| G1b | `POST /jobs` | as G1 with `"primary_image_id":null` |
| G2 | `POST /workflows` | `{"source":{"kind":"image","reference_set_id":"rs_0123456789ab","primary_image_id":"img_0123456789ab"},"reconstruction":{"settings":S},"parts":{"separate":SEP}}` |
| G2a | `POST /workflows` | `{"source":{…as G2…},"segmentation":{"subject":"anime_character","extra_terms":["katana"]},"reconstruction":{"settings":S},"parts":{"strategy":"semantic_parts","segmentation":{"subject":"anime_character","extra_terms":["katana"]},"separate":["eyes","hair","clothes"],"export":{"per_part_glbs":true}}}` (no top-level `export`; `segmentation` only because `parts` is present) |
| G3 | `POST /workflows` | `{"source":{"kind":"prompt","generation":{"prompt":"a knight with a red cape and a sword","preset":"character","part_hint":true,"num_images":1,"seed":0,"randomize_seed":true}},"reconstruction":{"settings":S}}` |
| G4 | `POST /workflows` | `{"source":{"kind":"prompt","generation":{"prompt":"a knight with a red cape and a sword","preset":"character","style":"anime","part_hint":true,"num_images":1,"seed":0,"randomize_seed":true}},"reconstruction":{"settings":S},"parts":{"separate":SEP}}` |
| G5 | `POST /image-generations` | `{"prompt":"a knight with a red cape and a sword","preset":"character","style":"anime","part_hint":true,"num_images":2,"seed":0,"randomize_seed":true}` |
| G6 | `POST /image-generations/{id}/candidates/0/use` | `{"slot":"front","make_primary":true}` |
| G7 | `POST /workflows` (Node, template "image + parts", segmentation bypassed) | `{"graph":{"schema":"img3d.workflow_graph","version":1,"nodes":[{"id":"n_input_image","type":"input_image","params":{"reference_set_id":"rs_0123456789ab","primary_image_id":"img_0123456789ab"}},{"id":"n_image_to_3d","type":"image_to_3d","params":S},{"id":"n_part_extraction","type":"part_extraction","params":{"separate":SEP}},{"id":"n_export","type":"export","params":{"per_part_glbs":false}}],"edges":[{"from":{"node":"n_input_image","port":"images"},"to":{"node":"n_image_to_3d","port":"images"}},{"from":{"node":"n_image_to_3d","port":"mesh"},"to":{"node":"n_part_extraction","port":"mesh"}},{"from":{"node":"n_part_extraction","port":"parts"},"to":{"node":"n_export","port":"asset"}}]},"layout":{"positions":{"n_input_image":{"x":0,"y":0},"n_image_to_3d":{"x":320,"y":260},"n_part_extraction":{"x":640,"y":130},"n_export":{"x":960,"y":260}},"viewport":{"x":40,"y":40,"zoom":0.9}}}` |
| G8 | `POST /jobs/{id}/part-analyses` | `{"separate":SEP}`; G8a with "Separate part files" on: `{"separate":SEP,"export":{"per_part_glbs":true}}` |
| G9 | (plan) legacy features + parts on | plan `kind:'job'` with body = G1 |

(G10 and `catalog_no_part_hint.json` are deleted together with catalog-driven key gating, §2.6.)

### 10.4 Mock backend extensions (F0; test double only)
`test/frontend/mock_backend.mjs` (M) keeps every existing route and flag [V header L1-27] and gains:

| Flag | Values (default) | Behaviour |
|---|---|---|
| `--features` | `full` \| `no-t2i` \| `no-seg` \| `disabled` \| `legacy404` (`full`) | serves `fixtures/features/<scenario>.json`; `legacy404` → 404 for `/features`, `/models*`, `/image-generations*`, `/segmentations*`, `/jobs/{id}/part-analyses*`, `/part-analyses*`, `/workflows*` (an old backend). `disabled` → every mutating new route 503 `feature_disabled` with `details.feature/flag`; GET routes work. `no-t2i` → `POST /image-generations` and prompt workflows 503 `capability_unavailable` with `details.reasons`. `no-seg` → `POST /segmentations` 503; part analyses run the `components` strategy only |
| `--strict-requests` | `on` \| `off` (`on`) | every request body (incl. existing `POST /jobs`) is checked against `fixtures/request_keys.json` recursively with the §2.6 path → object-name table (nested: `POST /segmentations` `source` (`SegmentationSource.<kind>`) and `options`; workflow `source` (`WorkflowSource.<kind>`), `source.generation`, `reconstruction` (`Reconstruction`), `reconstruction.settings` and `settings.advanced`/`advanced.<stage>` (`AdvancedSettings`/`SamplerSettings`, also for `POST /jobs`), `parts`, `parts.export`, `parts.segmentation`, `segmentation`, `graph` (`WorkflowGraph`), `graph.nodes[]` (`WorkflowGraph.node`), `graph.nodes[].params` per node type, `graph.edges[]` (`WorkflowGraph.edge`), `edges[].from`/`.to` (`PortRef`); `layout` is opaque; partial nested objects are accepted, DESIGN §6.0); unknown key (incl. a top-level `export`) → 422 `{error:{code:'validation_error', message:'Request validation failed', details:[{loc:['body',…],type:'extra_forbidden',msg:'Extra inputs are not permitted'}], request_id}}`; on `POST /workflows`, `graph` together with simple-form fields, `segmentation` without `parts`, or a non-null `parts.segmentation_id` → 422 `validation_error` with `type:'conflicting_fields'` (DESIGN §4.7.1); `num_images ≠ 1` inside a workflow → 422 |
| `--parts-scenario` | `ok` \| `partial` \| `fail` \| `slow` \| `no-attribute` (`ok`) | `pa_` runs step through the 8 DESIGN §4.6.4 steps; `ok` publishes a manifest (from `manifest_parts.json`, ids rewritten) and an assembled GLB; `partial` sets `result.status:'partial'` + warnings; `fail` fails at `partition` with `partition_failed`; `slow` = 30 s; `no-attribute` writes the GLB without `_FEATURE_ID_0` |
| `--t2i-cold-seconds` | N (`0`) | the first `ig_` run spends N s in `load_model`; `POST /models/{id}/load` → `loading` for N s → `loaded` (`features.updated` on each change) |
| `--tuner-enabled` | flag | `mesh_tuning.enabled = true`, `flags.mesh_tuning = true` (proves the unlock: the same default build shows today's tuner UI, operable, with no rebuild) |
| `--tuning-gate` | `on` \| `off` (`off`) | `on` mirrors S9 while `mesh_tuning.enabled` is false (not in `legacy404`, which emulates an old backend with no gate): `POST /jobs/{id}/tunings` → 404/409 as today, then 503 `feature_disabled` with `details: {feature: 'mesh_tuning', flag: 'IMG3D_MESH_TUNING_ENABLED'}`; listing/reading runs, their files and every `/blender*` route behave as today (DESIGN §8.2 G1/G4). Default `off`, so `mesh_tuner_mock_smoke.mjs` (which starts the mock without `--tuner-enabled`) keeps passing unchanged |

New test-only routes: `GET /api/__test/requests` → `[{ts, method, path, query, status, body, body_sha256,
created_id}]` and `DELETE` to clear (the request log, judge graft). `body` is the **raw request text** exactly as
received (the mock is a test double; the pane tool can read only response bodies, so this log is the only source of
the request bytes); `created_id` is the id the mock created while handling the request (`rs_…` for `POST
/reference-sets`, `img_…` for an image upload, `job_…`, `wf_…`, `ig_…`, `pa_…`), else `null`. `POST
/api/__test/scenario` `{features?, parts?, t2i_cold_seconds?, tuner_enabled?, queue_full?}` switches the scenario at
runtime and broadcasts `features.updated`; `queue_full: true` makes every create route (`POST /jobs`, `/workflows`,
`/image-generations`, `/segmentations`, `/jobs/{id}/part-analyses`) answer 429 `queue_full` until reset.

`test/frontend/mock/part_aware_mock.mjs` (N) exports `createPartAwareMock(ctx)` returning
`{handle(req, res, url): Promise<boolean>, tick(): void}`; `ctx = {send, fail, readBody, broadcast, now, hex,
encodePng, createJob(body) /* the mock's own POST /jobs path */, jobs, sets, scenario}`. It implements **every**
DESIGN §6.1–§6.6 route with the §6 shapes (statuses, error envelope, F/J auth with `?token=`), run progress through
the DESIGN step tables (ig: `load_model, generate, check, save`; sg: `prepare, frame, parse, detect, refine, fuse`;
pa: the 8 steps), WS events with the DESIGN names, `…/use` creating a set + image through the mock's reference code
(with `source`), workflows chaining children with the DESIGN §4.7.3 steps/weights (`node_id` from the graph, step
artifacts, `outputs`; the `pa_` child gets `segmentation_id = outputs.segmentation_id` and, for every non-alpha
source, the warning `segmentation_rerun_for_alignment`), `/workflows/validate` implementing the §4.7.2 codes needed by
the fixtures (incl. `unsupported_topology` for a Segmentation node that does not feed `part_extraction.masks`),
`mode:'job'` detection and **no** `job_request` field, `/workflows/catalog` with ETag and a 304 with an empty body. `test/frontend/mock/glb_parts.mjs` (N) writes a multi-node GLB
(scene `img3d_parts` → node `img3d_asset` → `part:body` with `_FEATURE_ID_0` UNSIGNED_SHORT on 3 regions and
border-duplicated vertices, plus 4 physical nodes `part:hair`, `part:eye.left`, `part:eye.right`,
`part:clothing.top`, each with `extras.img3d = {id, kind, …}` and its own `_FEATURE_ID_0`), written exactly like DESIGN
§3.12: `_FEATURE_ID_0` in its own bufferView with `byteStride: 4` and `accessor.byteOffset = 0`, so the viewer meets
the same `InterleavedBufferAttribute` as with the real file; plus procedural candidate PNGs and an overlay PNG — test
data only.

### 10.5 Smoke and review scripts (F0)
| Script | Asserts |
|---|---|
| `test/frontend/ui_modes_mock_smoke.mjs` | For each scenario (switched via `/api/__test/scenario`): `/features` shape (or 404); G1–G8 bodies from `fixtures/requests` are accepted (202/201); each golden body plus one unknown key → 422 `extra_forbidden`; G2a with its `export` moved to the top level → 422 `extra_forbidden`; G2a without `parts` → 422 `conflicting_fields`; a G3 workflow streams `workflow.updated` → `job.updated` → completed; a G2 workflow ends with `part_analysis.updated` completed, the manifest and assembled GLB return 200 with the right content types, the GLB parses, node names equal `part:<id>` and `_FEATURE_ID_0` exists on `part:body`; `latest` returns 404 `details.reason:'none'` for a fresh job; with the tuner locked (any scenario except `legacy404` and `--tuner-enabled`) the request log after the scripted UI pass of §10.6 has **no** `POST /api/v1/jobs/{id}/tunings` (creates a tuning run) and **no** `POST /api/v1/blender/runtime/download` (starts a Blender download), and no other non-GET `/blender*` or `/tunings*` entry; GET status/list entries are allowed (UI3); with `--tuning-gate on` a direct `POST /jobs/{id}/tunings` on a completed job returns 503 `feature_disabled` while locked; writes `test/outputs/frontend/ui_modes_smoke.json` |
| `review/import_boundaries.mjs` + `import_allowlist.json` | §1.4 rules (regex over import specifiers), including the store edge table as an explicit adjacency map (`run`, `part`, `workflow → features, input, partOptions, settings, toast`, `planFromStores → features, input, partOptions, reference, settings, job, run, navigation, system`, the `→ none` stores) with a cycle check, `planFromStores` value-imported only from `actions/` and `hooks/`, and no `store/` file importing `components/**` (so `announcerStore` never reaches `modalStack.ts`); `@xyflow` only under `shells/node/canvas/`; `three` only in the lazy viewer files; `shells/**` importing neither `components/meshTuner/**` nor `components/tunerLock/**` (§3.9); no `use*`-named non-hook exports in `actions/`, `store/`, `lib/`, `api/`; UI9 path-literal grep; UI14 no-placeholder grep (model ids such as `z_image_turbo`, taxonomy ids such as `eye.left` outside `lib/featureMeta.ts` and type unions); the §1.4 URL-helper set, API constants and the `useBackendSession` exception; an allowlist entry that no longer violates fails with "stale allowlist entry: delete it" (deletion-only ownership rule, §1.4/§11) |
| `review/protected_frontend.mjs` + `protected_frontend.sha256` | sha256 (CRLF→LF) of `components/meshTuner/**`, `store/meshTunerStore.ts`, `lib/meshTuner.ts`, `api/blender.ts`, `api/tunings.ts`, `hooks/useTuneAvailability.ts` equals the `8654b90` baseline |
| `review/bundle_budget.mjs` + `bundle_baseline.json` | §8.1: initial set from `dist/index.html`; lazy chunk attribution from `dist/.vite/manifest.json` (after F11); gzip -9 per file; deltas over `bundle_baseline.json` (recorded by F0 from the `8654b90` build); prints a table; exit 1 on a violation of an enforced row |
| `review/ui_probe.js` | a function source pasted into the browser pane (`javascript_tool`) returning the §9 JSON report plus `fingerprint(opts?)` = ordered landmark labels, headings, `role="note"` texts and button names, disabled buttons included (ignoring `[data-a11y-announcer]` regions; with `{skipAdditions: true}` also ignoring every subtree marked `data-ui-addition`, §4.2). The file header holds, per baseline viewport (1280×800, 768×1024, 390×844; scenario legacy404), `BASELINE = {fingerprint, tunerEntries}` recorded by F0 from **today's** UI. `fingerprint` **includes** the tuner mount points. `tunerEntries` (formerly `tunerExcluded`) is information only and is never subtracted: every entry inside the closest `section` of `#tuning-title` (the "Auto Tune" panel, `TuningCard.tsx:L84/L96` [V]) and every button whose name starts with "Blender runtime" (`BlenderRuntimeStatus.tsx:L106` [V]); the probe prints it so a reviewer sees which entries belong to the locked tuner, and uses its selectors (plus `[data-tuner-lock]`) to report tuner targets apart from the §9 target counts. `EXPECTED_DEFAULT_DELTA[viewport] = {legacy: {remove: [], add: ['button:App settings'] + (1280 ≤ width < 1440 ? ['button:GPU memory details'] : [])}, locked: {remove: [], add: legacy.add + ['note:Paused until you approve the part-aware milestone']}}`. `applyDelta(baseline, delta)` passes when (1) the current fingerprint with the `add` entries removed equals the baseline fingerprint with the `remove` entries removed (same order), and (2) each `add` entry occurs exactly once. UI1 = `legacy` on `legacy404` with `fingerprint()`, and `locked` on `full` with `fingerprint({skipAdditions: true})`; the locked run also asserts that every `button, input, select, textarea` inside each `[data-tuner-lock="locked"]` frame matches `:disabled`, that each frame's fieldset has `aria-disabled="true"` and an `aria-describedby` resolving to non-empty text, and that `#tuning-title` exists exactly once (one placement renders per breakpoint) |

### 10.6 Browser-pane acceptance (agent-driven; no Playwright)
1. `npm run build`, then `npx vite preview --port 4174` from `frontend/` with `IMG3D_BACKEND_URL=http://127.0.0.1:8765`.
2. **Request-body check (UI2):** start a fresh mock, `DELETE /api/__test/requests` once, then with the mock at `full`
   and at `legacy404`, in each of the four modes (`?ui=`), upload `test/images/ALICE` (via `/api/__test/sample/`) and
   press Generate with defaults. Read `GET /api/__test/requests` (with `javascript_tool` `fetch`; `read_network_requests`
   returns only response bodies). Expected text = the bytes of `fixtures/requests/G1.json` with `rs_0123456789ab`
   replaced by the `created_id` of the latest `POST /api/v1/reference-sets` entry and `img_0123456789ab` by the
   `created_id` of the latest image-upload entry (`POST /api/v1/reference-sets/{rs}/images`) in the same log; the
   `body` of the newest `POST /api/v1/jobs` entry must be `===` that text (raw string comparison).
3. The same procedure for: parts on → Generate → G2; prompt one-shot → G3 (G4 with style anime + parts); review flow →
   G5, then G6 (`{id}` = the session's `ig_` `created_id`), then G1 with the `use` response's ids; "Separate part files"
   + Analyse parts on a finished job → G8a.
4. Node: template "image + parts" → Run → body equals G7 (ids substituted as in step 2).
5. **Tuner lock (§3.9):** (a) mock `full` with `--tuning-gate on` (locked), default build, Default mode at 1280×800,
   768×1024 and 390×844 (Viewer tab): the Auto Tune card is at today's placement with the notice "Paused until you
   approve the part-aware milestone", every control in the frame is disabled, the Blender pill shows its status but
   does not open, and `BlenderSetupDialog` never opens, also with `--blender-scenario none` (where today's auto-open
   would fire); after 30 s idle + one generation the request log has no tuning-run POST and no Blender-download POST
   (UI3), while `GET /blender/runtime` and `GET /jobs/{id}/tunings` entries are allowed. (b) Past results stay
   viewable: start the mock with `--tuner-enabled --tuning-gate on`, run Auto Tune on a seeded job to completion, then
   `POST /api/__test/scenario {"tuner_enabled": false}`: the card locks without a reload, and the Original/Tuned
   `VariantBar`, the tuned GLB, `TunedStats`, `TunedDownloadButton`, the History "tuned" marker and the report's
   sections and links still work; a `POST /jobs/{id}/tunings` sent with `fetch` returns 503 `feature_disabled`.
   (c) `{"tuner_enabled": true}` again (approval): the same build unlocks (no notice, controls enabled, setup dialog
   available) with no code change or rebuild. (d) `legacy404`: today's tuner UI, operable. (e) Mobile, Tablet, Node
   (fresh load with `?ui=`): no tuner UI (no card, no pill, no `VariantBar`) and no `tunerBundle-*.js` in the network log.
6. The §9 matrix with `ui_probe.js`, a keyboard-only pass per shell (generate, switch source, open App settings,
   switch mode, toggle a part, connect two nodes in List view), and a reduced-motion pass. Screenshots go to the
   scratchpad, never the repo.
7. **One shortcut (UI6):** in each mode, `DELETE /api/__test/requests`, dispatch one Ctrl+Enter `keydown` on
   `document.body`, wait 1 s: the log has exactly one `POST /api/v1/jobs` (or `/workflows`, `/image-generations`)
   entry. (`getEventListeners` exists only in the DevTools console, not in page JavaScript.)
8. **Feedback inside modals (WCAG 4.1.3):** Mobile mode at 390×844, open the Create sheet, switch the mock to a full
   run queue (`POST /api/__test/scenario {"queue_full": true}`, a mock flag that makes every create route return 429
   `queue_full`), press the sheet's Generate: the toast "The generation queue is full." is visible inside the open
   `<dialog>` (its element is a descendant of the dialog) and the dialog's `role="alert"` or `role="status"` region
   contains the text. Repeat with App settings open and a mode switch: the dialog's status region says "Mobile UI".

### 10.7 Real-backend acceptance (after the backend slices merge; user-approved GPU use, `test/part_aware/gpu_lock.py`)
**Real-weight rules (same as DESIGN §10):** inputs only from `test/images/{ALICE,ANIME_CHARACTER_01}`; jobs are
created fresh through the UI (`POST /jobs`) in this session, against a backend started with the **absolute** paths
`IMG3D_RUNTIME_DIR=E:\14-OCT-2026\Img3D\test\.runtime\f13` and
`IMG3D_LOG_DIR=E:\14-OCT-2026\Img3D\test\.runtime\f13\logs` (never `backend/runtime`; a relative
`test\.runtime\f13` would resolve against `backend/` [V `config/settings.py:39-44,206-220`], DESIGN §10 intro).
Start command (PowerShell, from `E:\14-OCT-2026\Img3D`; the GPU lock wraps it, so no other GPU run overlaps):
```powershell
$env:IMG3D_RUNTIME_DIR = 'E:\14-OCT-2026\Img3D\test\.runtime\f13'
$env:IMG3D_LOG_DIR = 'E:\14-OCT-2026\Img3D\test\.runtime\f13\logs'
backend\python\python.exe -s test\part_aware\gpu_lock.py --label f13 -- backend\python\python.exe -s backend\main.py --port 8000
```
Before the first UI action, apply DESIGN §10's runtime refusal rule by hand: the first `Configuration: ` record of
`test\.runtime\f13\logs\img3d.log` names `'runtime_dir': 'E:\\14-OCT-2026\\Img3D\\test\\.runtime\\f13'`, and after the
first Generate `test\.runtime\f13\jobs\<job_id>\job.json` exists; otherwise stop the backend and report. Nothing
reads, lists, opens or analyses `backend/runtime/jobs/`, and `job_269a864ac597` / `job_fa0c4771ec03` are never opened
(if either id ever appears in the History list of the test runtime, stop and report).
- After S2: `/features` real states render in App settings (Z-Image, GDINO, SAM 2.1, Florence-2, lifter installed;
  parsers per S6; the Sapiens2 conditions listed).
- Image job with parts off from each mode, compared with a job generated in the same session from the same image and
  seed on the pre-change build (`8654b90`, same test runtime): the echoed `settings` (`GET /jobs/{id}`) are equal, and
  the completed job displays exactly as before (viewer, stats, download). (The byte-level G1 check is §10.6
  step 2; a real backend has no request log.)
- After S4/S11: prompt one-shot and review flows on Z-Image-Turbo (cold-start notice visible on the first run).
- After S10/S11: ALICE and ANIME_CHARACTER_01 with parts: part list, visibility, isolate, highlight (region subsets on
  `part:body`), explode, per-part and assembled downloads; `unmatchedPartIds` empty.

---------------------------------------------------------------------------------------------------
## 11. Implementation slices (disjoint file ownership; parallel implementer agents)
Rules:
- "Owns" = only that slice creates or edits the file. Within a wave no two slices own the same file. A file owned in
  an earlier wave may be owned again later only after the earlier slice merged (marked "→").
- **Allowlist exception:** `test/frontend/review/import_allowlist.json` is owned by F0, which alone may *add* entries.
  Any later slice may *delete* the entries whose `from` file it owns, in the commit that removes the violation (F7
  deletes the `ModelStatsCard.tsx` entry). Such a deletion touches only that slice's own rows, so two slices never
  conflict, and "§10.1 green" stays reachable for the slice that removes a violation.
- Interfaces frozen by this document (§2.1 types, §2.5 `FeaturesSnapshot`, §3.3 forms, §4.3.1 engine types, the
  store signatures) change only with the F1/F2/F5 owner's sign-off.
- Every slice ends with §10.1 green, UI1–UI14 respected, and no `components/meshTuner/**` edit.
- Frontend slices develop against the mock (which mirrors DESIGN §6 exactly); their **real-backend** acceptance
  waits for the backend slice named in "Backend dep" (DESIGN §11) and follows the real-weight rules of §10.7
  (`test/images` inputs only, test runtime, `gpu_lock.py`, never the two excluded jobs). The legacy path never needs a
  backend change.

| Slice | Wave | Owns | Depends on | Backend dep | Acceptance |
|---|---|---|---|---|---|
| **F0 Test infrastructure** | A | `test/frontend/mock_backend.mjs` (M), `test/frontend/mock/{part_aware_mock,glb_parts}.mjs`, `test/frontend/fixtures/**` (incl. `request_keys.json`), `test/frontend/ui_modes_mock_smoke.mjs`, `test/frontend/review/{import_boundaries.mjs,import_allowlist.json,bundle_budget.mjs,bundle_baseline.json,ui_probe.js,protected_frontend.mjs,protected_frontend.sha256}`, `test/frontend/unit/_fixtures.mjs`, `test/README.md` (frontend row) | – | S0 (contract text) | Smoke passes for all 5 scenarios against the mock alone; strict mode returns 422 `extra_forbidden` for unknown keys (incl. a top-level `export`, and one unknown key inside a `POST /segmentations` `source`, `reconstruction`, `settings.advanced.shape`, `graph.nodes[0]` and `graph.edges[0].from`, while extra keys inside `layout` pass) and `conflicting_fields` for `segmentation` without `parts` / a non-null `parts.segmentation_id`; the request log returns raw `body` and `created_id`; `mesh_tuner_mock_smoke.mjs` still passes; `bundle_baseline.json` recorded from the `8654b90` build (initial JS 178.2 kB gz ± 1 kB) and `bundle_budget.mjs` prints it; `import_boundaries.mjs` passes on today's tree with the seeded allowlist (two entries, §1.4); `protected_frontend.sha256` recorded from `8654b90`; `ui_probe.js` `BASELINE = {fingerprint, tunerEntries}` recorded from today's UI (legacy404) at the three baseline viewports, the fingerprint including the tuner entries and `tunerEntries` (informational) non-empty at each; the mock's `--tuning-gate` flag (§10.4) |
| **F1 API layer** | A | `src/api/{partAwareTypes,features,imageGenerations,segmentations,partAnalyses,workflows,files}.ts`, `src/api/ws.ts` (M), `src/api/types.ts` (M, append-only; `WsServerMessage` untouched) | – | – | tsc + lint green; no caller yet, so behaviour is identical; every DESIGN §6 endpoint the UI uses has a function (§2.2); `getFeatures` maps 404 → legacy; `getLatestPartAnalysis` maps reason `none` → null; `getWorkflowCatalog` maps 304 → `not_modified` (with `validateStatus`); `ws.ts` dispatches the five `PartAwareWsMessage` types |
| **F2 Pure libraries** | A (merge after F1) | `src/lib/{settingsRequest,imageJobRequest,workflowRequest,generatePlan,generateAvailability,forms,featureFlags,uiModes,viewport,partsModel,runSteps,featureMeta,errorText}.ts`, `src/lib/storage.ts` (M), `src/store/settingsStore.ts` (M: move + re-export), unit tests `image_job_request, workflow_request, generate_plan, generate_availability, feature_flags, forms, ui_modes, viewport_posture, parts_model, run_steps, pure_imports` | F1 types | – | All listed unit tests green incl. G1–G9 byte equality, keys ⊆ `request_keys.json`, and the availability parity strings; existing `settingsStore` importers compile unchanged |
| **F3 Workflow engine** | A (merge after F1) | `src/workflow/engine/*.ts`, `src/workflow/nodes/*.ts`, unit tests `workflow_{registry,graph,validate,compile,status,serialize,derive}` | F1 types (F2 for the graph ≡ simple-form test helper inputs) | – | Unit tests green incl. the refine guard, G7, all 8 valid configs (graph ≡ simple form), the Segmentation-without-Part-Extraction exclusion, the legacy-job table; only `.ts` relative imports; no react/zustand/axios/xyflow |
| **F4 Tokens + primitives** | A | `src/styles/{tokens,global}.css` (M), `src/styles/modes.css`, `src/components/common/{Button,SegmentedControl,Switch,RangeSlider,NumberField,Dialog,Toaster,Panel,Menu}.module.css` (M), `src/components/common/{Dialog,Toaster}.tsx` (M: `modalStack` registration / portal target), `src/components/common/{Sheet,FloatingNav,NavRail,Skeleton,RadioCardGroup,ProgressRing,VisuallyHidden}.tsx` (+ css), `src/components/common/modalStack.ts`, touch pass (M): `src/components/layout/{EngineStatus,ConnectionIndicator}.module.css`, `src/components/settings/{PolygonTargetControl,AdvancedSettings,Field,SeedControl}.module.css`, `src/components/generation/ProgressCard.module.css`, `src/components/viewer/ModelStatsCard.module.css`, `src/components/references/ReferenceTile.module.css` (→ F12), `src/lib/motion.ts`, `src/hooks/useSoftKeyboard.ts` | – | – | Without `data-density` the desktop Default looks the same except the P13 contrast tokens (UI1 fingerprint unchanged); with `data-density=touch` set by hand every common control and every control styled by the touch-pass files is ≥ 44 px; with `data-ui-mode=mobile` + `data-density=touch` set by hand on today's Default UI (`javascript_tool`), the `ui_probe.js` target check reports 0 interactive elements < 48 px among the common components and the touch-pass files; Sheet keyboard snap cycling, Escape, backdrop dismiss, focus return, drag only from the handle/header, no per-frame React state (review); a toast raised while a `Dialog` is open renders inside it; P11/P12/P13/P18/P20 token parts done; compiler check 0 |
| **F5 Stores, actions, hooks, session** | B | `src/store/{featuresStore,preferencesStore,navigationStore,inputStore,partOptionsStore,runStore,partStore,announcerStore,planFromStores}.ts`, `src/store/{jobStore,referenceStore}.ts` (M), `src/actions/*.ts`, `src/hooks/{useGenerateAction,useActiveRun,useViewport,useNavPosture,useResolvedUiMode,useReveal,useFeatures,useTunerLocked,useTunerVisible,usePartsView}.ts`, `src/hooks/{useGenerateAvailability,useBackendSession}.ts` (M; `useViewedTuning.ts` is not edited), `src/lib/buildFlags.ts` (refinement preview only), `src/vite-env.d.ts` (M) | F1, F2 | – (mock); S2 for real `/features` | Today's Default renders unchanged; with the mock (`full`, tuner locked) the browser network log shows **no** POST that creates a tuning run or starts a Blender download (GET status/list requests such as `GET /blender/runtime` and `GET /jobs/{id}/tunings` are expected: the tuner wiring is unchanged, §2.12) and no `/workflows/catalog` request outside the Node shell; `useTunerLocked()` is `true` on `full`/`disabled`, `false` on `legacy404` and with `--tuner-enabled`; `/features` fetched at sync and on hello; WS events update the stores (devtools check); `legacy404` produces no errors or toasts; Default Generate posts G1; postures change only through the `viewportStore` listener (compiler check 0) |
| **F6 App root, mode switch, App settings, Default wiring, tuner lock** | C | `src/main.tsx`, `src/App.tsx` (M), `src/app/*`, `src/shells/default/*`, `src/shells/{mobile,tablet,node}/{index,ready}.ts` (placeholders), `src/components/preferences/*`, `src/components/layout/{Header,AppShell,MobileTabBar}.tsx` (M: gear, tuner lock slots with `lazy()` tuner imports, reveal, shortcut-prop removal, P15), `src/components/tunerLock/*` (`TunerLockFrame` + css, `TunerLockNotice`, `lazyTuner.ts`, `tunerBundle.ts`), `src/components/generation/GenerateAction.tsx` (+css), `src/components/generation/GenerateCard.tsx` (M), `src/components/history/{HistoryItem,HistoryDrawer}.tsx` (M: tuned marker/effect only where `useTunerVisible()`, P16, "Prompt"/"Parts" badges from `runStore.workflowByJobId`, "Regenerate from prompt"/"Reuse prompt" menu items) | F4, F5 | – | Mode persists across reload; `?ui=` overrides without persisting; App settings: radiogroup keyboard behaviour, mode switch only after `prepareMode`, read-only features/models with warm-up, tuner shown as paused; one Ctrl/Cmd+Enter listener (§10.6 step 7); blocking one shell chunk in the network panel shows the fallback with [Retry] (reload) and "Use Default UI for this session"; no `meshTuner` component in the initial chunk (`grep` of the initial files for `TuningCard`/`BlenderRuntimeStatus`/`BlenderSetupDialog` finds nothing); tuner lock per §10.6 step 5 (a), (c), (d), (e) and P5: locked card at all three placements with the notice, every control in the frame disabled, pill read-only, `BlenderSetupDialog` never opens (also with `--blender-scenario none`), no tuning-run or Blender-download POST; legacy404 and `--tuner-enabled` operable exactly as today; Default fingerprint = `applyDelta(BASELINE, EXPECTED_DEFAULT_DELTA.legacy)` on legacy404 at the three baseline viewports and the `locked` check on `full` (UI1); `GenerateCard` card/bar DOM unchanged |
| **F7 Feature content + new features** | C | `src/components/references/{ReferenceContent.tsx, ReferencePanel.tsx (M)}`, `src/components/settings/{SettingsContent,PartOptionsSection,SegmentationOptionsContent,LicenseNotice}.tsx` (+css), `settings/SettingsPanel.tsx` (M), `src/components/generation/{ProgressContent,PipelineSteps}.tsx` (+css), `generation/ProgressCard.tsx` (M), `src/components/viewer/ModelStatsContent.tsx`, `viewer/ModelStatsCard.tsx` (M: `downloadArtifact`, `LazyTunedDownloadButton`; `TunedStats` only where `useTunerVisible()`, §3.9), `src/components/history/HistoryList.tsx` (M, skeleton rows), `src/components/{input,parts,export}/*`, `src/components/{featureBundle,lazyFeatures}.ts`; deletes its own `import_allowlist.json` entry | F5, F4 | S4/S10/S11 (real) | Every `XxxPanel` renders the same DOM as before (fingerprint); the root of every new card/section of the §4.2 placements table carries `data-ui-addition`; prompt validation messages, counter, cold-start notice; review flow against the mock (`--t2i-cold-seconds 5`): candidates with `t2i_*` readiness chips, Use & generate 3D → G6 then G1; "Straight to 3D" disabled with the reason when the mock reports `workflow.available:false`; part options round-trip into G2/G2a/G8a; Parts list states of §6.3 against `--parts-scenario ok/partial/fail/no-attribute`, incl. the "Analysis details" numbers and a `shared_label` row; downloads via payload URLs only (UI9); with `legacy404` the `featureBundle` chunk is never requested, **also** with stored `img3d.partOptions` `{form: {enabled: true}}` and `img3d.input` `{source: 'prompt'}` (set by hand before reload): network log has no `featureBundle-*.js`, the Default fingerprint equals UI1, and Generate posts G1 |
| **F8 Viewer parts** | C | `src/components/viewer/{ModelScene,ModelViewer,ViewerPanel,ViewerToolbar}.tsx` (M), `viewer/{viewerTypes,viewerRuntime,sceneUtils}.ts` (M), `viewer/{ViewerPanel,ViewerToolbar}.module.css` (M), `viewer/{PartsModel.tsx,partsScene.ts,partsIndex.ts}`, unit test `parts_index` | F5, F2 | S10 (real GLB) | Against the mock parts GLB (byteStride-4 `_FEATURE_ID_0`): hide/show physical nodes and body regions; isolate; hover/select highlight overlays keep their colours when toggling PBR ↔ clay ↔ normals; hiding a region also hides its wireframe; pick from the canvas selects the row; no raycast while orbiting (counter in a dev log); hover does not re-render `ModelScene` (React DevTools highlight); colorize; explode 0 restores positions exactly; no GLB refetch on state changes; unmounting Parts view leaves the original model drawing correctly (no lost attribute buffers); `no-attribute` → "Highlight unavailable"; P6 + P10 done; viewer toolbar ≥ `--hit-min` under touch density; Default: tuned `VariantBar` and secondary GLB behave exactly as today, locked or unlocked (§10.6 step 5 (b)); where `useTunerVisible()` is false the viewer shows the original only (no `VariantBar`) |
| **F9a Shared shell chrome** | C | `src/shells/shared/*` | F4, F5, F6 (`GenerateAction`), F7 signatures | – | §10.1 gates green; review of the FAB/rail state tables (§4.1) against the `useGenerateAction` phases; `SheetHost` maps every `SheetId` of `navigationStore` to a sheet (code review). No in-app mount exists while `SHELL_READY` is false, so the browser checks of these components are part of F9's and F10's acceptance |
| **F9 Mobile shell** | D | `src/shells/mobile/*` (→ owns `index.ts`/`ready.ts` from F6; `SHELL_READY = true`) | F9a, F7, F8 | – | Probe green at 320×640, 360×800, 375×812, 390×844 (portrait) and 740×360, 812×375 (landscape rail + side sheets): no horizontal scroll, 0 targets < 48 px, nothing obscured; FAB/rail states and every sheet of `SheetHost` exercised; prompt (both flows) and image generation end to end; Generate reachable with the keyboard open; sheet drag from the handle, keyboard snap and Escape; toast + alert visible inside the Create sheet (§10.6 step 8); reduced motion instant; Default-equal G1 body (§10.6 step 2) |
| **F10 Tablet shell** | D | `src/shells/tablet/*` (→ `index.ts`/`ready.ts`) | F9a, F7, F8 | – | Posture table of §4.5.1 in the browser (incl. the hysteresis rows and no flip while typing at 768×1024); rail roving focus; side panel collapses on Viewer; section survives an orientation flip; probe green at all 13 viewports (0 targets < 48 px) |
| **F11 Node shell + React Flow** | D | `frontend/package.json`, `frontend/package-lock.json` (`@xyflow/react` 12.12.0 exact), `frontend/vite.config.ts` (§8.2 incl. `build.manifest`), `src/store/workflowStore.ts`, `src/shells/node/**` (→ `index.ts`/`ready.ts`), unit test `flow_adapter`, `test/frontend/review/bundle_baseline.json` (→ from F0: writes the measured Node shell size), `THIRD_PARTY_NOTICES.md` (xyflow MIT, `@xyflow/system` MIT, d3-* ISC, classcat MIT, nested zustand 4 MIT — licences read from the installed `package.json` files; DESIGN §11: S6 owns the file first; F11 edits it only after S6 merged, and until then hands its rows to the S6 owner) | F3, F5, F7, F8, F9a | S11 (real workflows) | Six user nodes + Existing Result render from the template; invalid connections refused with a reason; bypass/mode toggles write through (switch to Default and see the same part toggle); Run: trivial graph → G1 via `jobStore.submit()`, "image + parts" template → G7; **`runNodeWorkflow` review checklist (§4.3.8), each case returning the stated boolean:** (1) `!out.ok` → Problems opened + assertive announce, `false`, nothing posted; (2) `mode:'job'` → only `startDefaultGeneration()`; (3) legacy or `!workflow.available` → toast, `false`; (4) `validateRemote` → `null` (mock stopped before Run, or the request aborted) → error toast "Could not validate the workflow" + assertive announce, `false`, and the request log has no `POST /workflows`; (5) `!report.valid` → Problems with the server issues, `false`; (6) valid → exactly one `POST /workflows`, `rememberRun`, reveal viewer, `true`; node status chips and the inline progress bar follow mock workflow steps and the legacy-job table **while** the run is in progress (NodeStatusContext); a selection made in the List view shows on the canvas; a selected edge is removed with Delete; List view keyboard-only parity; import/export round trip; refine group hidden unless `VITE_FEATURE_REFINEMENT_PREVIEW=true` (then locked); `nodeui` chunk not initial and the manifest-based budgets green; compiler check 0 events or documented `'use no memo'` |
| **F12 Default integration + polish** | D | `src/components/layout/AppShell.tsx` (→ M again: P2/P3 placements through `lazyFeatures`), `layout/{AppShell,Header,MobileTabBar,GpuMeter}.module.css`, `layout/GpuMeter.tsx`, `src/App.module.css`, `generation/GenerateCard.module.css`, `history/{HistoryDrawer,HistoryList,HistoryItem}.module.css`, `references/ReferenceTile.module.css` (→ from F4: P17 shimmer), `src/hooks/useBreakpoint.ts` | F6, F7 | – | P2, P3, P6–P21 acceptance of §4.2; Default fingerprint with `legacy404` = `applyDelta(BASELINE, EXPECTED_DEFAULT_DELTA.legacy)` (incl. the "GPU memory details" button at 1280×800, and the tuner entries); with `full`: the added cards (marked `data-ui-addition`) appear in today's columns and nothing else moves: `fingerprint({skipAdditions: true}) = applyDelta(BASELINE, EXPECTED_DEFAULT_DELTA.locked)` (the locked Auto Tune card keeps its place); history rows and the GPU meter button ≥ `--hit-min` under touch density |
| **F13 Verification + docs** | E | `frontend/README.md`; generated `test/outputs/frontend/{ui_modes_smoke.json, ui_modes_matrix.json}` (gitignored) | all | S2, S4, S10, S11 | §10.1 gates green; the 52-cell matrix green; §10.6 steps 2–8 in all four modes; keyboard and reduced-motion passes; §10.7 real-backend acceptance (real-weight rules) once the backend slices merged |

Order and critical path: **Wave A** F0, F1, F4 in parallel; F2 and F3 start in parallel on the frozen §2.1 types and
merge after F1 → **Wave B** F5 → **Wave C** F6, F7, F8 in parallel, then F9a → **Wave D** F9, F10, F11, F12 in
parallel → **Wave E** F13. Critical path: F1 → F2 → F5 → F6/F7 → F9a → F9/F10/F11 → F13.

Disjointness check: `AppShell.tsx` (F6 then F12), `shells/{mobile,tablet,node}/{index,ready}.ts` (F6 then
F9/F10/F11), `references/ReferenceTile.module.css` (F4 then F12), `test/frontend/review/bundle_baseline.json` (F0
then F11) and `THIRD_PARTY_NOTICES.md` (backend S6 then F11) are the only re-owned files, each in a later wave;
`import_allowlist.json` follows the deletion-only exception above. `test/frontend/unit/*.test.mjs` files belong to
the slice of the module they test (listed above); `_fixtures.mjs` and every fixture belong to F0.

---------------------------------------------------------------------------------------------------
## 12. Open questions (genuinely undecided; defaults are applied until the user answers)
1. **Q-U1 An "Auto" UI style.** v1 offers exactly the four requested modes (Default by default) plus a "Suggested for
   this screen" badge in App settings. Should a fifth choice "Auto (follow this device)" exist that resolves to the
   suggested mode at load time? Default: no. Adding it later is one union member and one branch in `resolveUiMode`.
2. **Q-U2 Phone hint.** Should Default on a phone ever show a one-time, dismissible hint "Try the Mobile UI"
   (`preferences.dismissedHints`)? Default: no hint; only the badge inside App settings.

(Everything else is decided above: the existing tuner UI is preserved in Default and locked until the user approves,
unlocked by `/features.mesh_tuning.enabled` alone, with no tuner UI in the new modes (user spec, §3.9; not an open
question); review-first is the default prompt flow and the user can switch it in the form;
the React Flow attribution stays; workflow file import/export ships; contrast tokens P13 ship as an accessibility
fix; no Playwright; no model-install UI.)

---------------------------------------------------------------------------------------------------
## 13. Backend requests: all resolved in DESIGN.md (2026-10-16)
No open backend request remains. The former items, kept only for traceability (DESIGN "Revision log", "UI_DESIGN.md
§13 Backend requests"):

| # | Former request | Resolved in DESIGN | UI consequence (applied above) |
|---|---|---|---|
| BR1 | `ValidationReport.job_request` is informational | §6.5, §4.7.2: `job_request` removed from `ValidationReport` and `CompileResult`; for `mode:"job"` the client posts its own `CreateJobRequest` | §2.1 type, §4.3.8 |
| BR2 | Partial nested objects; how export options reach the workflow's `pa_` | §6.0, §4.6.2: every nested options object accepts partial objects; `PartExportOptions {per_part_glbs=false, previews=true}` in `parts.export`; no `assembled_glb`; no top-level `WorkflowRequest.export` (422 `extra_forbidden`) | §2.1, §2.8, G2a/G7/G8a, mock strict mode |
| BR3 | `params_schema.properties` for every node type | §4.7.2 | no longer needed by the UI: catalog-driven key gating is removed (§2.6) |
| BR4 | `extras.img3d.id` on every `part:<id>` node; `_FEATURE_ID_0` on every mesh node | §3.12 (plus `byteStride: 4` → `InterleavedBufferAttribute`) | §6.6 matching and `getX` read; mock GLB |
| BR5 | `GET /workflows?limit=100` | §6.0: `limit=1..100` (default 20) on every new list route | `runStore.loadIndex()`; the fallback is deleted |
| BR6 | `features.updated` on every `default_model_state` change | §6.1 | §5.4; the 2 s re-fetch fallback is deleted |
| BR7 | Shared validation cases | DESIGN accepted: `test_workflow_compiler.py` reads `test/frontend/fixtures/graphs/*.json` when present | F0 fixture format unchanged (`expected_issues`) |
| BR8 | Progress units | §4.7.5: `steps[].progress` 0..100; `RunStep.progress` 0..1 | §2.1 comments, `lib/runSteps.ts` |

---------------------------------------------------------------------------------------------------
## 14. Synthesis record (judge finding → resolution)
| Finding | Resolution |
|---|---|
| Base = A (won all three judges): shells over shared state, no file moves, Default eager | §1.2–§1.3, §4.2 |
| A's `PartOptionsIn`/`separate` used taxonomy ids and had no `regions` (would 422) | Superseded by DESIGN: part options are `PartAnalysisOptions` with DESIGN §2.2 group ids in `separate`; there is no `regions` field (policy makes unselected groups regions); region groups are shown read-only (§6.2) |
| Graft `request_keys` gating | Superseded 2026-10-16 (requirements critique #10): builders emit the fixed DESIGN v1 key lists; skew is handled by legacy 404 and the 422 `extra_forbidden` path; key sets live in `fixtures/request_keys.json` for the mock and tests (§2.6) |
| A's dual `runs`/`job` parts adapter | Dropped: only DESIGN's `pa_` resources (§2.2, §6.1) |
| Golden fixtures G1–G5 shared by tests and the mock; browser-pane body check in all four modes | G1–G9 in `test/frontend/fixtures/requests` (§10.3); raw-body comparison from the mock request log, §10.6 steps 2–4 |
| Mock `--strict-requests` + `/api/__test/requests` log; while the tuner is locked, no tuning-run or Blender-download POST asserted (GET status/list allowed); the unlock proved with `--tuner-enabled` (user spec alignment replaced the former "zero tuner requests, build flag off" check) | §10.4, §10.5, UI3 |
| Registry guard: unlocked refine def throws; locked group only behind `VITE_FEATURE_REFINEMENT_PREVIEW` | §4.3.2, §4.3.11, `workflow_registry.test.mjs` |
| `NodeTypeDef` extension points (`version`, `migrate`, `contribute`, `ParamDef`) | §4.3.1 |
| Lower-risk part display first (object.visible, overlay subsets, "Highlight unavailable"); A's shader patch deferred | §6.6 (plus real region hiding through one draw index per host, flagged overlays, BVH picking) |
| GLTFLoader attribute naming unverified | Verified: custom attributes are lowercased (`GLTFLoader.js:L4871`), node names sanitised with the original in `userData.name` (L4428-4429), extras → `userData`; `_FEATURE_ID_0` arrives as an `InterleavedBufferAttribute` (DESIGN §3.12 `byteStride: 4`), read with `getX`; matching rules §6.6 |
| Legacy stage → node status table + session `runIndex` | §4.3.9 (uses DESIGN `legacy_job_node_map`) |
| Mobile landscape (< 520 px height) → rail + side sheets (420 px); 812×375 in the matrix | §3.5, §4.4, §9 |
| `normalizeManifest`: hard reject duplicate ids; case-insensitive region attribute; backend must write the attribute | §6.1 rules 3, 5 and 7; DESIGN §3.12 writes `_FEATURE_ID_0` on every mesh node |
| Single `GenerateAction` with variants | §4.1 |
| One global Announcer (polite + assertive, 4 s clear, 1 s dedupe) | §7 item 8, `app/Announcer.tsx` |
| `docFromJob` "Reuse settings"/"Open in workflow" | `loadServerGraph` + "Load these settings" (§4.3.10); History "Reuse prompt"/"Regenerate from prompt" (F6) |
| Workflow import limits; newer version → backup | §4.3.10 (server limits 32/64; 256 KB; backup key) |
| xyflow controlled mode drops `measured` | `mergeFlowNodes` + `applyNodeChanges` on local state (§4.3.12), `flow_adapter.test.mjs` |
| Shrink-only import allowlist | Kept as a deletion-only allowlist with an ownership rule (any slice deletes its own entries; only F0 adds), §1.4, §10.5, §11 |
| Explode (reassembly) slider | §6.4, §6.6 |
| **Fatal/near-fatal: `node --test dir/` fails on Node 22.17** | glob form everywhere (§0.3, §10.1) |
| **Persisted `source='prompt'` traps Default on a legacy backend** | `effectiveSource` forces `image` (and `effectivePartsEnabled` forces off) on legacy; the safe value is never disabled (UI11, §2.9, `generate_plan.test.mjs`) |
| **Unanchored vendor regex pulls nested zustand 4 into the initial chunk** | anchored single-`node_modules` regex, `nodeui` group, budget membership check (§8.2) |
| **G1 parity cannot import `toRequestSettings` from `settingsStore.ts` under strip-types** | moved verbatim to pure `lib/settingsRequest.ts`, re-exported (§1.3); `jobStore.submit` uses the pure builder (§2.7) |
| B: Default generation compiled from a user-editable workflow doc | Not adopted: Default/Mobile/Tablet build from the stores; the Node graph is a projection; `pipelineBlock` only while Node is mounted (UI10) |
| B: moving `mobileTab`/`historyOpen` breaks the paused `BlenderSetupDialog` | Not adopted: `uiStore` unchanged (§1.3) |
| B: model install UI out of scope | Not adopted: read-only models + DESIGN warm-up/unload only (§3.8) |
| Stale prerequisite "commit tuner edits" | Dropped: the frontend tree is clean at `8654b90` [V]; the paused files are hash-pinned instead (UI13) |
| Model choices treated as pending | Updated to DECISIONS.md: six models approved by default (Q1); UI renders `/features` honestly (§0.5) |
| Q3: hide every tuner/Blender mount point in all four modes | **Superseded** (user spec alignment 2026-10-16): Default keeps every tuner/Blender mount point in place, locked by `mesh_tuning.enabled=false`; the new modes add none (§3.9) |
| Q4: Sapiens2 licence conditions shown in the UI | `licenceNotices` → App settings ▸ Models and `LicenseNotice` in part options (§3.8, §6.2) |
| Non-hook functions named `use…` would trip `rules-of-hooks`/React Compiler | renamed (`adoptCandidate`, `postCandidateUse`); boundary script check (§10.1, §10.5) |


---------------------------------------------------------------------------------------------------
## Revision log (2026-10-16)
Each item was checked against the frontend code at `8654b90` (or, for budgets, a fresh gzip -9 measurement of the
current `frontend/dist`) before any change. Nothing was rejected: every frontend or cross-doc item was confirmed.
Backend-only items are listed at the end with their UI consequence. "§" refers to this document.

### API shapes adopted from the revised DESIGN.md (backend reviser's summary)
| DESIGN change | Where applied |
|---|---|
| `WorkflowRequest` has no top-level `export` (422 `extra_forbidden`); `segmentation` needs `parts`; `parts.segmentation_id` must be null (422 `conflicting_fields`); the `pa_` gets the workflow's own `sg_` | §2.1 type, §2.8 emission table, §2.9 row, §5.5/§6.2 copy, G2a, mock strict mode (§10.4), smoke negatives (§10.5) |
| `PartAnalysisOptions.export` = `PartExportOptions {per_part_glbs=false, previews=true}`; no `assembled_glb`; `min_part_faces` default `null`; partial nested objects | §2.1, §2.8 (`buildPartAnalysisOptions` without `includeExport`), §6.8, G8a |
| `result.assembled_url` / `download_url` never null; `partner_filled_ratio`, `cut_partner_consistency` in result and `manifest.summary` | §2.1, §2.3, §6.1 `quality`, §6.3 "Analysis details" |
| Export node params `{per_part_glbs, previews}` → `parts.export`; Segmentation node must feed `part_extraction.masks` (`unsupported_topology`); `params_schema.properties` for every node | §2.8 `buildExportParams`, §4.3.5, §4.3.7, G7, `workflow_compile.test.mjs` mapper |
| `job_request` removed from `ValidationReport`/`CompileResult` | §2.1, §4.3.8 |
| `/features`: strategy `reason`; refine nodes `hidden_by_default: true`; full `ModelOut[]`; `ReasonCode` += `not_implemented`, `engine_unavailable` | §2.1 `ReasonCode`, §2.5, §3.9, §4.3.2, §6.2, fixtures (§10.3) |
| Sapiens2 `license.conditions` = exactly three strings | §0.5 Q4, `feature_flags.test.mjs`, `full.json` |
| `readiness.issues` use `t2i_*` codes | §2.11 `WARNING_TEXT`, §5.3 |
| `limit=1..100` (default 20) on list routes | §2.2; `loadIndex` fallback removed (§13) |
| `RunStep.progress` 0..1, `WorkflowStepOut.progress` 0..100 | §2.1 comments (unchanged) |
| Catalog 304 has an empty body | §2.2 (`validateStatus`), mock (§10.4) |
| Manifest buffers `face_part.u16`, `face_flags.u8`; base `geometry.floaters`; `floater_candidate` tag no longer produced | §2.1 `PartRecordOut`/`PartsManifestOut`, §6.1 `floaterCount` + rule 7, §6.5 |
| `model_parts.glb`: `extras.img3d.id` on every node; `_FEATURE_ID_0` with `byteStride: 4` → `InterleavedBufferAttribute` | §6.6 (`readFeatureIds` with `getX`), mock GLB (§10.4) |
| Warning `gpu_defer_timeout`; jobs-first gate step message; `IMG3D_RUN_MAX_DEFER_S` | §2.11 rows, §5.4 |

### `design/critic_frontend_feasibility.md`
| # | Sev. | Verification | Resolution |
|---|---|---|---|
| 1 | major | [V `TuningCard.tsx:L84/L96`, `AppShell.tsx:L65,L95,L134`, `Header.tsx:L40`, `BlenderRuntimeStatus.tsx:L106`, `GpuMeter.tsx` has no button] | UI1 = `applyDelta(BASELINE, EXPECTED_DEFAULT_DELTA)` (remove tuner entries, add "App settings" and, at 1280–1439 px, "GPU memory details"); §0.4, §4.2 P1/P21, §10.5 `ui_probe.js`, F0/F6/F12 acceptance. *Tuner removal superseded by "User spec alignment 2026-10-16": the fingerprint now includes the tuner; `remove` is empty* |
| 2 | major | Measured gzip -9: initial 178.2 kB (108.2 + 67.5 + 2.0 + 0.5); 4 static tuner importers [V `AppShell.tsx:L10`, `Header.tsx:L8`, `App.tsx:L8`, `ModelStatsCard.tsx:L17`] | Tuner components `lazy()` in their importers (§1.3, §3.9); one lazy `featureBundle` chunk (§4.2); budgets as deltas over `bundle_baseline.json` (§8.1), enforced from wave C. *Since the user spec alignment the tuner chunk is still lazy but downloaded by Default every session (§8.1 (a), `tunerBundle` row)* |
| 3 | major | [V `EngineStatus.module.css:L10` 30 px, `PolygonTargetControl.module.css:L19` 24 px, `AdvancedSettings.module.css:L12` 40 px, `ReferenceTile.module.css:L207-208` 26 px] | F4 touch pass over the listed CSS files (`ReferenceTile` → F12); Mobile/Tablet `--control-h-*` = 48 px, touch `--control-h-sm` 44 px (§3.6); F4 acceptance with `data-ui-mode=mobile`; F8/F12 cover their own CSS |
| 4 | major | [V `sceneUtils.ts:L128-131, L139-145, L162-178, L190-199`] | F8 owns `sceneUtils.ts`: `PART_OVERLAY_FLAG` skipped by `forEachMesh`/wireframe/dispose; one draw index per host instead of cloned geometries; region overlays delete the shared attribute before `dispose()` (§6.6); F8 acceptance (view modes, wireframe, unmount) |
| 5 | major | [V no R3F pointer handlers in `components/viewer` today; drei `Bvh.js` and three-mesh-bvh 0.8.3 installed; `CENTER = 0`] | DOM listeners + one `useFrame` raycast, none while orbiting; pick-only clones with the original index under drei `<Bvh indirect>`; lazy cached outline; `PartsModel` reads `partStore` itself, `ViewerPartsProps = {analysisId, url, regionAttribute}` (§6.6, §8.3) |
| 6 | major | Doc-internal (§4.3.12 synced only on `revision`) | `syncKey` = revision + selection + catalog ETag; topology-only `toFlowNodes`; `NodeStatusContext` for run status; selection union `{kind: 'node' \| 'edge'}`; edge Delete; `flow_adapter.test.mjs` cases (§4.3.10, §4.3.12) |
| 7 | major | Critic's compiler probe (module reassignment → `CompileError`) | Postures computed in the `viewportStore` listener with the 120 ms debounce; `useNavPosture` = `useViewport().posture[shell]`; `bootDocument` reads the store (§3.5, §3.6) |
| 8 | major | [V `Dialog.tsx` uses `showModal()`; Toaster/Announcer outside every dialog] | `modalStack` (F4); toasts and an announcer region pair portal into the topmost modal; full-viewport untransformed `<dialog>`, transforms on the inner panel (§4.1, §1.6, §7 item 8); §10.6 step 8 |
| 9 | major | Pane tool returns response bodies only; log had only `body_sha256` | Mock log returns raw `body` + `created_id`; §10.6 steps 2–4 rebuild G1/G2/… with the session's ids and compare raw strings |
| 10 | major | Doc-internal (F0 owns the allowlist; F7 removes a violation) | Deletion-only ownership rule (§1.4, §11 rules); stale entry message "delete it" |
| 11 | minor | [V `useBackendSession.ts:L11-12`, `BackendBanner.tsx:L3`, `ReferencePanel.tsx:L5`, `ReferenceTile.tsx:L5-6`, `AdvancedSettings.tsx:L4`, `ViewerPanel.tsx:L4-5`, `HistoryItem.tsx:L4`] | URL-helper set, API constants allowed everywhere, permanent `useBackendSession` exception; `workflowStore.validateRemote`; NodeShell calls `applyPipelineConfig`; store edges `part → features, announcer, toast`, `run → …` (§1.4, §4.3.5, §4.3.6, §4.3.8) |
| 12 | minor | [V `lib/storage.ts:L20`; `settingsStore.ts:L8-22` extensionless imports] | `lib/storage.ts` exempt from the global-token scan only; static re-export check (§1.5, §10.2) |
| 13 | minor | [V `types.ts:L693-699` closed union; `ReferenceImage` is an interface at L203] | `PartAwareWsMessage` in `partAwareTypes.ts`; `WsServerMessage` untouched; `ReferenceImage.source?` via interface merging (§2.1, §1.2) |
| 14 | minor | [V `Panel.tsx:L20-23, L31`; `SettingsPanel.tsx:L53`] | `revealPanel(id)` = `#${id}-title` → closest `section` (§4.2) |
| 15 | minor | Doc-internal | §2.4 → `loadSet(data.id)` |
| 16 | minor | Doc-internal | `SHELL_READY` in `ready.ts` (§3.5); placeholder `index.ts` = `ShellPlaceholder` returning null (§1.2, §3.7) |
| 17 | minor | `React.lazy` caches rejections | [Retry] = `location.reload()` (§3.7) |
| 18 | minor | [V `ModelScene.tsx:L44-52` 250 ms disposal] | `prepareMode(mode)` awaited before every `setUiMode`; `?ui=` boot preloads (§3.6, §3.7, §3.8) |
| 19 | minor | `getEventListeners` is DevTools-only | UI6 behaviour check (§0.4, §10.6 step 7) |
| 20 | minor | Pointer Events vs `touch-action` | Sheet drag only on the handle/header (§4.1, §4.4) |
| 21 | minor | Doc-internal | `resolveNodeView`: list when `width < 600` or (coarse and `min(width, height) < 520`) (§3.5, §4.3.13, §9) |
| 22 | minor | [V `vite.config.ts` has no `build.manifest`] | F11 sets `build.manifest: true`; manifest walk; "Mobile + shared" counted once (§8.1, §8.2) |
| 23 | minor | [V `client.ts:L226-238`] | `validateStatus` incl. 304 (§2.2) |
| 24 | minor | Doc-internal | F9a acceptance = gates + state-table review; browser checks move to F9/F10 (§11) |
| 25 | minor | DESIGN §11 now states the S6 → F11 hand-over | §1.2 and F11 row aligned with DESIGN §11 |

### `design/critic_requirements.md`
| # | Sev. | Resolution |
|---|---|---|
| 1 | major | DESIGN-side (applied by the backend reviser). UI §0.5 was already correct; Q3/Q4 rows now name the refine `hidden_by_default` flag and the three Sapiens2 strings |
| 2 | major | UI half applied: §4.3.6 "(or the run, for preview)" deleted; Segmentation active only with Part Extraction; an orphan Segmentation node → `ui_unreachable_node`, excluded (§4.3.5, §4.3.7); `previewSegmentationId` never sent; switch renamed "Segment the image before 3D" with the helper text (§5.5, §6.2); 8 valid configs and `workflow_derive`/`workflow_compile` cases |
| 3 | major | Applied: no `WorkflowRequest.export`; `parts.export {"per_part_glbs": true}` for workflows and direct analyses; G2a, G7, G8a; `assembled_url: string`; mock key sets; BR2 closed |
| 4 | major | Applied (merged with frontend #1): `BASELINE = {fingerprint, tunerExcluded}` and `EXPECTED_DEFAULT_DELTA` (§10.5). *Superseded by "User spec alignment 2026-10-16": `BASELINE = {fingerprint, tunerEntries}`, nothing subtracted, `EXPECTED_DEFAULT_DELTA.{legacy,locked}`* |
| 5 | major | UI half applied: §10.7 real-weight rules (test images only, fresh jobs, test runtime, `gpu_lock.py`, never the two excluded jobs), comparison "same session, same image and seed on `8654b90`"; §11 rules |
| 6 | minor | Applied (§2.4) |
| 7 | minor | Applied: §6.2 shows the strategy `reason`; no id special-casing |
| 8 | minor | Applied: chips map `readiness.issues` (`t2i_*`) through `WARNING_TEXT` (§2.11, §5.3) |
| 9 | minor | Applied: §13 is now a closed traceability table; deleted fallbacks (BR3 gating, BR5 page fallback, BR6 re-fetch timer) |
| 10 | minor | Applied: catalog-driven key gating, `dropped`, `catalog_mismatch`, G10 and `catalog_no_part_hint.json` deleted; `lib/requestKeys.ts` removed from the file plan; key sets moved to `fixtures/request_keys.json` (§2.6) |
| 11 | minor | Applied: plan row + availability rule 5e `workflows_unavailable`; "Straight to 3D" disabled with the reason (§2.9, §2.10, §5.2); `generate_plan.test.mjs` case |
| 12 | minor | Already typed as `ModelOut[]`; noted in `licenceNotices` (§2.5) |
| 13 | minor | Applied: backend is the source of `hidden_by_default: true`; the palette shows refine nodes only with the preview flag (§3.9, §4.3.2) |
| 14 | minor | Applied: §4.3.11 states the client needs no compile change and the server needs the three DESIGN §4.7.2 extension items (nothing reserved, DESIGN D13) |
| 15 | minor | Backend only; no UI change |

### `design/critic_backend_feasibility.md` (items with a UI consequence)
| # | UI consequence |
|---|---|
| 1 (blocker) | New result/summary fields shown read-only in "Analysis details" (§6.3) |
| 2 | `min_part_faces?: number \| null`; the UI never sends it (§2.1, §2.8) |
| 5 | Jobs-first gate message and `gpu_defer_timeout` text (§2.11, §5.4); warm-up copy (§5.4) |
| 7 | §10.7 real-weight rules |
| 9 | `_FEATURE_ID_0` read with `getX` from an `InterleavedBufferAttribute`; mock GLB uses `byteStride: 4` (§6.6, §10.4) |
| 10 | `geometry.floaters` (inspector line), `face_part` not fetched; shared-label rule 7 keeps overflow regions honest (§6.1, §6.4, §6.5) |
| 3, 4, 6, 8, 11–18 | Backend only; no UI change |

### Follow-up pass (re-check of applied fixes; cross-doc diff with `DESIGN.md`)
| Item | Verification | Resolution |
|---|---|---|
| backend #7 + requirements #5: relative `IMG3D_RUNTIME_DIR=test\.runtime\f13` | [V `backend/config/settings.py:39-44,206-220` resolves relative paths against `BACKEND_DIR`; `test/e2e/server_smoke.py:61-62` uses absolute paths] | **Applied.** §10.7: absolute `IMG3D_RUNTIME_DIR=E:\14-OCT-2026\Img3D\test\.runtime\f13` and `IMG3D_LOG_DIR=…\f13\logs`, the exact PowerShell start command under `gpu_lock.py --label f13`, and DESIGN §10's runtime refusal rule applied by hand (`Configuration:` log line, `job.json` under the test runtime). `/system` reports no paths, so it is not used |
| frontend #11 (store edges) | Doc-internal (§4.3.6, §4.3.10, §2.9, §4.1) | **Applied.** §1.4: `workflow → features, input, partOptions, settings, toast`; `planFromStores → features, input, partOptions, reference, settings, job, run, navigation, system` (glue, value-imported only by `actions/` and `hooks/`); `announcer → none`; no store imports `components/**`. §4.1: `app/Announcer.tsx` portals the region pair into `getTopModal()` and renders each message in the topmost pair. §10.5: `import_boundaries.mjs` encodes the edges as an adjacency map with a cycle check |
| frontend #18 (cross-tab switch skipped `prepareMode`) | Doc-internal (§3.1 vs §3.7) | **Applied, corrected.** §3.1 `onStorage` awaits `prepareMode(mode)` (errors swallowed) before `persist.rehydrate()`, in `app/GlobalOverlays.tsx`. The suggested `sanitizePreferences(JSON.parse(e.newValue)).uiMode` would always be `undefined`: zustand persist stores the `{state, version}` envelope. The pure `readPersistedState(raw)` in `lib/uiModes.ts` reads `.state` (and keeps the `try` out of the component, §10.1); `ui_modes.test.mjs` cases |
| frontend #2 / F7: featureBundle requested on legacy with stored `parts.enabled` | Doc-internal (§4.2, UI1, UI11) | **Applied, refined.** New pure `shouldRenderPartOptions(f, form)` (§2.9) = `status === 'ready' \|\| (form.enabled && status !== 'legacy')`. Legacy never renders the section (parts are forced off there). On `loading`/`error` a stored `enabled=true` still shows it, because rule 7a asks the user to turn parts off; a plain `ready`-only gate would trap them. F7 acceptance (stored `img3d.partOptions`/`img3d.input` + `legacy404` → no `featureBundle-*.js`, UI1, G1) and `generate_plan.test.mjs` cases (legacy → G1 + `false`; `error` → `true` + blocked) |
| requirements #10: `request_keys.json` incomplete for the recursive check | [V `backend/schemas/jobs.py:22-33` `AdvancedSettings`/`SamplerSettings` are `extra="forbid"` request models (`schemas/common.py:18-21`)] | **Applied, corrected.** §2.6 adds `SegmentationSource.{reference_image,job}`, `Reconstruction`, `WorkflowGraph`, `WorkflowGraph.node`, `WorkflowGraph.edge`, `PortRef`, an explicit `paths` table and `opaque: ["layout"]`. `settings.advanced` is **not** opaque, because the backend validates it strictly; `AdvancedSettings` and `SamplerSettings` key sets are added instead. §10.4 nested list, the F0 helper `assertKnownKeys`, the `workflow_request`/`workflow_compile` tests and F0 acceptance updated |
| frontend #11 follow-on: `validateRemote` → `null` not handled | Doc-internal (§4.3.10 contract) | **Applied.** §4.3.8: `report === null` → error toast "Could not validate the workflow" + assertive announce, `return false`, nothing posted; F11 acceptance has a six-case `runNodeWorkflow` review checklist |
| Cross-doc: `child_error` untyped | DESIGN §4.7.4 now types it (`JobError` shape) | §2.1 `StepFailedDetails`; §2.11 narrows `details` with a fallback; `workflow_failed.json` fixture; `run_steps.test.mjs` cases |
| Cross-doc: `PartAnalysisOut.result` nullability | DESIGN §4.6.7 now states it | §2.1 comments (`overlay_url`/`camera_fit_iou` null for `components`; `preview_urls` `{}` when previews are skipped); the types were already nullable |
| Cross-doc: `geometry.floaters` | DESIGN §3.11 example now has `"floaters": []` on every non-base record; §3.10.1 says the key is always present | §2.1 comment; `floaterCount` also treats a missing key as `[]` |
| Cross-doc: step `warning` state | DESIGN §4.7.5 adds `steps[].warning_count: int = 0` | §2.1 `WorkflowStepOut.warning_count?`; §4.3.9 `done` + `warning_count > 0` → `warning`; fixtures and `workflow_status.test.mjs` cases |
| Cross-doc: "8 valid configs" | Counts source × topology, a different set from DESIGN's {segmentation, parts, per_part_glbs} (6 valid) | No change |

### User spec alignment 2026-10-16: tuner UI preserved but locked
Trigger: the user's milestone spec ("Tuning/refinement remains disabled until I explicitly approve this backend
layer"; Default: "Preserve the existing UI as the default. Do not redesign it completely."; "Reuse working
components."; "Do not delete existing working functionality."). Hiding the working tuner UI removed working
functionality; the spec keeps it, disabled. Recorded in `DECISIONS.md` (defaults section), which ranks above
`DESIGN.md`; DESIGN §8.2 G4 and §12 Q3 still say "hide" and are superseded on this UI point. The backend gate (S9)
and the Node refine nodes (locked, hidden by default) are unchanged.

| Item | Before | Now | Where |
|---|---|---|---|
| Gate | build flag `VITE_FEATURE_MESH_TUNER` and `mesh_tuning.enabled`; tuner hidden in all modes, also on legacy | one key: `tunerLock(f)` is `locked` unless legacy or `mesh_tuning.enabled`; no tuner build flag; approval unlocks with no code change | §0.3, §2.5, §3.9, §1.2 (`vite-env.d.ts`) |
| Default tuner UI | hidden | rendered in place, locked: disabled-fieldset frame + notice "Paused until you approve the part-aware milestone"; pill read-only; `BlenderSetupDialog` not mounted while locked | §3.9, §4.2 P5, §1.3, §1.6, §3.7 |
| Past tuned results | hidden (`useViewedTuning` returned "no tuning") | viewable exactly as today in Default; `useViewedTuning` not edited | §3.9, §1.2, §6.6, F5/F7/F8 |
| New modes | hidden by the gate | `useTunerVisible()` false: no tuner parts in shared components, tuner chunk never requested | §3.9, §10.6 step 5 (e) |
| Hooks | `useTunerEnabled` | `useTunerLocked` + `useTunerVisible` | §1.1, §1.2, §3.9, F5 |
| Session wiring | tuner calls skipped while hidden | unchanged (all GET); the active-run poll is empty by construction while locked | §2.12 |
| UI1 | baseline minus `tunerExcluded` | fingerprint includes the tuner; `tunerEntries` informational; `EXPECTED_DEFAULT_DELTA.legacy` / `.locked` (+ lock notice); `data-ui-addition` + `skipAdditions` | §0.4, §4.2, §10.5, F0/F6/F7/F12 |
| UI3 | zero `/blender*` and `/tunings*` requests | no tuning-run POST and no Blender-download POST (and no other non-GET tuner request); GET status/list allowed | §0.4, §10.5, §10.6 step 5, F5 |
| Mock | `--tuner-enabled` proved the build-flag gate | `--tuner-enabled` proves the unlock; new `--tuning-gate on` mirrors S9 (default `off` keeps `mesh_tuner_mock_smoke.mjs` green) | §10.4, F0 |
| Bundle | tuner chunk never downloaded by a default build | still lazy (one `tunerBundle` chunk) but downloaded by Default every session, preloaded at boot; new ≤ 15 kB row; excluded from the shell walks | §1.3, §3.6, §3.7, §8.1 |
| Target sizes | tuner not rendered | protected tuner subtree excluded from the §9/P12 counts and reported separately | §3.9, §4.2 P12, §9 |

Follow-ups for wave-A code written before this pass (this pass edits documents only): F2 replaces
`isTunerVisible` in `lib/featureFlags.ts` with `tunerLock` / `TUNER_LOCK_NOTICE` / `tunerLockNotice` and updates the
`feature_flags.test.mjs` truth table; F0 updates the mock's `--tuner-enabled` help text ("the UI build flag still
hides it"), adds `--tuning-gate`, and writes `ui_probe.js` / `ui_modes_mock_smoke.mjs` to §10.5 as revised.
