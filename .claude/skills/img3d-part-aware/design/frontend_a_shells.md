# Frontend proposal A: shells over shared state

Date: 2026-10-16. Status: design proposal. No project file other than this one was changed.
Angle: **SHELLS-OVER-SHARED-STATE.**
- Keep the existing `api/` layer and stores, and extract shared actions and hooks.
- Split each feature panel into a *content* component and a *card* wrapper.
- Add four shells (Default, Node, Mobile, Tablet). The shells only compose shared panels; three of them are
  lazy-loaded.
- Add a preferences store and an "App settings" dialog.
- Change the Default UI as little as possible, apart from targeted polish.

Legend:
- **[V]** I verified it by reading the file in this session (file:line).
- **[R]** It comes from a research report in `../research/` (named in place).
- **[A]** Assumption or proposal.
- **[BE]** A backend contract item that this proposal *assumes*. It must be reconciled with the backend design
  (see §4.10).

Inputs read in full or in the relevant part:
- Research reports: `frontend_architecture_map.md`, `ui_modes_research.md` and `backend_contract_map.md`.
- `segmentation_models_research.md` §0, §5–7; `part_aware_3d_research.md` §0, §5–6;
  `text_to_image_research.md` §0, §7–9; `environment_audit.md` §8–9; `backend_generation_map.md` §6.
- `api-contract.md` and `.claude/CLAUDE.md`.
- Frontend source files: `App.tsx`, `main.tsx`, `AppShell.tsx`, `GenerateCard.tsx`, `useGenerateAvailability.ts`,
  `jobStore.ts`, `uiStore.ts`, `storage.ts`, `stages.ts`, `useBreakpoint.ts`, `useBackendSession.ts`, `ws.ts`
  (dispatch), `jobs.ts`, `types.ts` (exports and the jobs section), `settingsStore.ts` (`toRequestSettings`),
  `ViewerPanel.tsx`, `ModelViewer.tsx`, `ModelScene.tsx`, `viewerTypes.ts`, `viewerRuntime.ts`, `Header.tsx`,
  `MobileTabBar.tsx`, `Panel.tsx`, `SettingsPanel.tsx`, `ProgressCard.tsx`, `ModelStatsCard.tsx`,
  `vite.config.ts`, `.oxlintrc.json`, `vite-env.d.ts`, `tsconfig.app.json` (flags).
- Tests: `test/frontend/mock_backend.mjs` (header and routes) and `test/frontend/review/compiler_check.mjs`.

---------------------------------------------------------------------------------------------------

## 0. Decisions at a glance

| # | Decision | Why |
|---|---|---|
| D1 | **Four shells** (`default`, `node`, `mobile`, `tablet`) render over **one** state/API layer (`api/` → `store/` → `actions/` + `hooks/`). Shells contain layout, navigation and composition only, and never import `api/*`. | The user requires that backend logic not be duplicated. The existing layer is already clean [V `frontend_architecture_map` §0.1]. |
| D2 | **Default stays eager.** `DefaultShell` is in the initial chunk and *imports today's `Header`, `AppShell` and `HistoryDrawer` from their current paths*, with no file moves. Node, Mobile and Tablet are `React.lazy` chunks. | Minimal churn and zero first-paint cost for the default mode. Git history of the working UI is preserved. |
| D3 | **Content + Card split.** Each feature gets `XxxContent` (body, owns its subscriptions), optional `XxxActions` (header buttons) and the existing `XxxPanel` (`Panel` wrapper, same export and path). | Default renders exactly as today. Sheets, rail panels and the node inspector reuse the same body. |
| D4 | **`preferencesStore`** is persisted at `img3d.preferences` v1: `uiMode` (default `'default'`), `density`, `motion` and Node-editor options. A session-only `?ui=` override exists for tests and screenshots. | This is the user requirement (persist locally, default = current UI). |
| D5 | **App settings dialog** (gear button "App settings" in every shell). It is mounted globally, outside the shells, so it survives a mode switch. | "Settings" already names the generation panel [V `SettingsPanel.tsx:L53`]. Renaming it would be churn. |
| D6 | **`navigationStore.requestReveal(target)`** replaces the direct `setMobileTab('viewer')` calls from feature code. Each shell maps a reveal to its own navigation. `uiStore` keeps `mobileTab` and `historyOpen` as Default-shell state, unchanged. | Feature code stops knowing about one layout. `uiStore` stays backward compatible. |
| D7 | **A single request builder.** The pure `lib/jobRequest.ts → buildCreateJobRequest()` is used by every mode, including Node. For image source with parts off, the output is **identical** to today's body (a test asserts this). | Hard constraint: unchanged image-to-3D behaviour. |
| D8 | **The Node graph is a projection of the shared pipeline config.** The graph stores topology, node modes and layout only. All parameters live in the shared stores. Topology edits write through to `inputStore.source` and `partOptionsStore`. Run calls the same `startGeneration()` as every other mode. | There is no second executor and no duplicated logic. Default and Node always agree. |
| D9 | **A pure TypeScript workflow engine** lives in `src/workflow/engine` and `src/workflow/nodes`, with no React, zustand, axios or xyflow. The renderer is `@xyflow/react@12.12.0` (MIT), imported **only** inside `src/shells/node/*` (lazy chunk). | [R `ui_modes_research` §2, §4; `environment_audit` §8: lock resolves cleanly] |
| D10 | **Part display** uses an assembled parts GLB as a third viewer model (`role: 'parts'`). Physical parts are toggled with `object.visible`. Regions on the continuous body are hidden or highlighted on the GPU through a 256×1 lookup texture indexed by the `_FEATURE_ID_0` vertex attribute. The single viewer is preserved. | Visibility and highlight need no GLB reload. Face/body topology stays continuous, as the user asked. |
| D11 | **Feature discovery** goes through `GET /api/v1/features` [BE]. A 404 means "legacy backend". The prompt and parts UI are then hidden in Default (pixel-identical to today) and shown disabled with the reason in the other modes. | Additive, backward compatible, and honest unavailable states. |
| D12 | **The tuner UI is hidden** unless the build flag `VITE_FEATURE_MESH_TUNER==='true'` **and** `features.tuning.mesh_tuner.state==='available'`. While hidden, no `/blender` or `/tunings` request is made. `components/meshTuner/*` is not touched. | The milestone keeps tuning disabled. Four of those files have uncommitted edits [V `git status`]. |
| D13 | **Model choices are not hard-coded.** The UI renders `features.models[]` (role, state, size, VRAM, licence) read-only. The prompt model picker lists only `installed` or `ready` text-to-image entries. There are no install buttons in this milestone. | Model selection is pending the user's decision. |
| D14 | **Density and touch.** `data-density='touch'` (forced in Mobile and Tablet, automatic under `pointer: coarse` in Default) raises control heights to 44–52 px through tokens. No markup changes. | 43 of 62 controls are below 44 px today [V `frontend_architecture_map` §3.2 row 13]. |
| D15 | **Tests run without new tools.** Pure modules use explicit `.ts` relative imports and are tested with `node --experimental-strip-types --test` (Node 22.17 is installed). UI checks use the existing mock plus a DOM probe run in the browser pane. Playwright is not added. | Nothing is added to the toolchain [V `environment_audit` §8]. |

---------------------------------------------------------------------------------------------------

## 1. Scope, non-goals and constraints

**In scope (this milestone):**
- the four UI modes;
- the Settings-based mode switch, persisted locally;
- prompt and image input in every mode;
- part options before generation;
- part results (outliner, visibility, isolate, highlight, inspector, downloads);
- the Node UI engine and its adapter;
- the Default polish;
- the tuner gate;
- tests and gates.

**Out of scope:**
- any tuning or refinement algorithm or UI flow, including eye correction, rig correction, reference-aware
  optimisation and Blender tuning;
- installing models from the UI;
- backend code;
- Python that runs in Blender;
- `components/meshTuner/*` edits;
- light theme.

**Hard constraints that bind the frontend:**
- **H1.** The image-to-3D request body stays byte-identical when part features are off. `JobStage`, the stage
  list and the percent formula are unchanged: the frontend keeps `lib/stages.ts` as is [V `stages.ts:L10-19`].
  New steps come only from the optional `job.steps`.
- **H2.** The API is additive. The backend rejects unknown request fields [R `backend_contract_map` §2.4], so the
  frontend never sends a field the backend has not advertised. `source` and `settings.parts` are sent only when
  `/features` says they are supported **and** the user enabled them. The frontend never sends `parts: null`.
- **H3.** There is no fake data in the app. Every part list, model list or prompt state comes from the backend.
  Test doubles live only under `test/`.
- **H4.** Exactly one 3D viewer is mounted at a time (`viewerRuntime` singleton) [V `viewerRuntime.ts`].
- **H5.** Follow the React Compiler, zustand v5 and oxlint rules [V `frontend_architecture_map` §8]:
  - no manual memo;
  - no new object or array from a selector;
  - no `setState` in effects;
  - async error handling goes in store actions;
  - constants and tables go in `.ts` files;
  - no `enum`;
  - `import type`.

---------------------------------------------------------------------------------------------------

## 2. What exists and is reused [V]

| Asset | Where | Reuse |
|---|---|---|
| Session and realtime wiring (auth gate, REST plus WS, polling, resync) | `hooks/useBackendSession.ts:L76-188` | Kept at the root, exactly once. It gains features, part events and the tuner gate (§5.6). |
| "Can generate" source of truth | `hooks/useGenerateAvailability.ts:L32-75` | Logic moves to the pure `lib/generateAvailability.ts`. The hook becomes a thin selector wrapper, with the same strings for image source. |
| Submit and retry | `store/jobStore.ts:L197-258` | `submit(request?)` is added. The no-argument path builds through `buildCreateJobRequest`. `retry` is unchanged. |
| Settings → request | `store/settingsStore.ts:L112-136` `toRequestSettings` | Moved to `lib/settingsRequest.ts` (pure) and re-exported from `settingsStore.ts`. |
| Default layouts | `components/layout/AppShell.tsx:L46-156` (desktop, tablet and mobile) | They become the body of `DefaultShell`. Placement of the Input card and the Parts panel is added there. |
| Mobile tab nav (tablist, roving focus) | `components/layout/MobileTabBar.tsx` | Default only. Its keyboard pattern is copied into `FloatingNav` and `NavRail`. |
| Generate and Cancel UI | `components/generation/GenerateCard.tsx:L22-129` | Keeps the card and bar variants. Logic moves to `useGenerateAction`; the shortcut moves to `app/GlobalShortcuts`. |
| Viewer | `ViewerPanel.tsx:L132-212`, `ModelViewer.tsx:L51-121`, `ModelScene.tsx:L74-117,L204` | Gains a `parts` prop (third model role) beside the existing secondary (tuned) slot. |
| Card chrome | `components/common/Panel.tsx:L21-44` | Used by every `XxxPanel` wrapper. |
| Native modal | `components/common/Dialog.tsx:L25` (`showModal`, backdrop, Escape) | Base for the App settings dialog and modal sheets. |
| Persisted keys | `lib/storage.ts:L6-16` | Gains `preferences`, `input`, `partOptions` and `workflow`. |
| Mock backend | `test/frontend/mock_backend.mjs` (route regex dispatch, L775-996) | A new module `test/frontend/mock/part_aware_mock.mjs` is plugged in. |
| Compiler gate | `test/frontend/review/compiler_check.mjs` | Unchanged. It must still report 0 non-success events. |

---------------------------------------------------------------------------------------------------

## 3. Target architecture

### 3.1 Layers

```
Backend (FastAPI /api/v1 REST + WS)                         contract: api-contract.md (+ §4 assumptions)
  │
  ▼
src/api/            typed client: types.ts (+featuresTypes.ts, partsTypes.ts), jobs.ts, features.ts, parts.ts,
                    files.ts, ws.ts (RealtimeClient)                                  — no store/React imports
  │
  ▼
src/store/          zustand domain + UI state (single source of truth)
                    existing: auth, system, reference, settings, job, ui, meshTuner, toast (+ viewerRuntime)
                    new: preferences, navigation, features, input, partOptions, part (+ workflow, lazy, Node only)
  │
  ├── src/domain/ + src/lib/   PURE: pipeline config, request builder, availability, parts model, ui-mode
  │                            resolution, viewport classes, step projection (testable under Node)
  ├── src/workflow/            PURE node engine + node definitions (Node UI semantics, no React)
  ▼
src/actions/        commands used by every renderer: startGeneration, cancel, regenerateFromPrompt,
                    downloadArtifact, analyzeParts, applyPipelineConfig
src/hooks/          derived/reactive: useGenerateAction, useGenerateAvailability, useViewport, useNavPosture,
                    useResolvedUiMode, useReveal, useFeatures, useTunerEnabled, usePartsView, useSoftKeyboard
  │
  ▼
src/components/     FEATURE content (renderer-agnostic) + primitives (Sheet, FloatingNav, NavRail, …)
  │
  ▼
src/shells/         RENDERERS: default/ (eager) · mobile/ · tablet/ · node/ (lazy) · shared/ (chrome pieces)
  │
  ▼
src/app/            App root: session, auth gate, ModeSwitch, GlobalOverlays (toasts, App settings,
                    gated Blender dialog), GlobalShortcuts, document attributes
```

### 3.2 Import rules (enforced by `test/frontend/review/import_boundaries.mjs`, §17)

| Layer | May import | Must not import |
|---|---|---|
| `api/` | axios, `lib/storage` (token), `api/*` | `store/`, `hooks/`, `actions/`, `components/`, `shells/`, `app/` |
| `domain/`, `lib/` (pure) | `import type` from anywhere; values only from `domain/`, `lib/` (pure), `api/types.ts` | react, zustand, axios, three, `@xyflow/*`, `store/`, `components/` |
| `workflow/engine`, `workflow/nodes` | same as pure `lib/` | same as pure `lib/` |
| `store/` | `api/`, `lib/`, `domain/`, other stores (acyclic) | `hooks/`, `actions/`, `components/`, `shells/` |
| `actions/` | `store/`, `api/`, `lib/`, `domain/` | `components/`, `shells/`, react |
| `hooks/` | react, `store/`, `actions/`, `lib/` | `components/`, `shells/`; `api/` except URL helpers |
| `components/` | `hooks/`, `actions/`, `store/` (selectors and actions), `lib/`, `api` URL helpers (`*Url`) | `shells/`, `app/`, `@xyflow/*`; `three`/`@react-three/*` except `viewer/ModelViewer.tsx`, `viewer/ModelScene.tsx`, `viewer/partsScene.ts`, `viewer/sceneUtils.ts` |
| `shells/<mode>/` | `components/`, `hooks/`, `actions/`, `shells/shared/`, `store/` | `api/*`; another `shells/<mode>/`; `@xyflow/*` (except `shells/node/`) |
| `app/` | `shells/*/index.ts` (lazy), `components/`, `hooks/`, `store/` | `api/*` |

Grandfathered exceptions (allowlist in the script):
- `components/meshTuner/**` → `api/blender`, `api/tunings` (untouched files);
- `components/references/*` → `api/references` URL helpers.

The `ModelStatsCard → downloadJobGlb` direct call [V `ModelStatsCard.tsx:L20-50`] moves to `actions/downloads.ts`.

### 3.3 File plan (N = new, M = modified; everything else unchanged)

```
frontend/src/
  main.tsx                                   M  pre-render document attributes, ?ui= override
  App.tsx                                    M  root: session, phase gate, ModeSwitch, GlobalOverlays
  app/
    ModeSwitch.tsx                           N  lazy shell host + ShellErrorBoundary + Suspense fallback
    shellRegistry.ts                         N  UI mode → loader table (module scope)
    ShellErrorBoundary.tsx  ShellFallback.tsx  PreSession.tsx  GlobalOverlays.tsx  GlobalShortcuts.tsx   N
    documentAttributes.ts                    N  writes <html data-ui-mode/density/motion/posture>
  shells/
    default/DefaultShell.tsx  default/index.ts                                  N (composes existing layout)
    mobile/index.ts  MobileShell.tsx (+css)                                      N
    tablet/index.ts  TabletShell.tsx  RailLayout.tsx  BottomLayout.tsx (+css)    N
    node/index.ts  NodeShell.tsx  NodeToolbar.tsx  NodeCanvas.tsx  PipelineNode.tsx  PortHandle.tsx
         flowAdapter.ts  NodeInspector.tsx  NodeListView.tsx  ProblemsPanel.tsx  AddNodePalette.tsx
         ViewerDock.tsx  nodeShortcuts.ts  useNodeRunState.ts  useNodeSummary.ts  flow.module.css          N
    shared/ CompactTopBar.tsx  StatusSheetContent.tsx  GenerateFab.tsx  RailGenerateButton.tsx
            ProgressPill.tsx  GenerateBlockedContent.tsx  AppSettingsButton.tsx  FeatureIcon.tsx
            PhoneLayout.tsx  SectionHost.tsx  navItems.ts  (+ css)                                       N
  components/
    common/ Sheet.tsx FloatingNav.tsx NavRail.tsx Skeleton.tsx RadioCardGroup.tsx ProgressRing.tsx (+css)  N
            Button/SegmentedControl/Switch/RangeSlider/NumberField/Dialog/Toaster/Panel/Menu .module.css  M
    references/ ReferenceContent.tsx N · ReferencePanel.tsx M
    settings/   SettingsContent.tsx N · PartOptionsSection.tsx N · SettingsPanel.tsx M
    generation/ ProgressContent.tsx N · PipelineSteps.tsx N · ProgressCard.tsx M · GenerateCard.tsx M
    viewer/     ModelStatsContent.tsx N · partsScene.ts N · ModelStatsCard.tsx M · ModelScene.tsx M
                ModelViewer.tsx M · ViewerPanel.tsx M · ViewerToolbar.tsx M · viewerTypes.ts M · viewerRuntime.ts M
    input/      InputSourceSwitch.tsx InputSourceCard.tsx InputContent.tsx PromptContent.tsx PromptPanel.tsx  N
    parts/      PartsContent.tsx PartsToolbar.tsx PartRow.tsx PartInspector.tsx PartsEmpty.tsx PartsPanel.tsx  N
    export/     ExportContent.tsx ExportPanel.tsx                                                         N
    preferences/ AppSettingsDialog.tsx UiModePicker.tsx DensityPicker.tsx MotionPicker.tsx NodeEditorPrefs.tsx
                 FeatureStatusList.tsx ModelRegistryList.tsx LicenseBadge.tsx                                N
    layout/     Header.tsx M · AppShell.tsx M · MobileTabBar.tsx M (+ css modules M in D4)
    history/    HistoryItem.tsx M · HistoryDrawer.tsx M · HistoryList.tsx M
    meshTuner/  (untouched)
  domain/pipeline.ts                                                                                      N
  workflow/engine/ types.ts registry.ts graph.ts validate.ts plan.ts derive.ts template.ts status.ts
                   serialize.ts index.ts                                                                  N
  workflow/nodes/  inputImage.ts prompt.ts segmentation.ts imageTo3d.ts partExtraction.ts export.ts
                   refineSlot.ts index.ts                                                                 N
  store/   preferencesStore.ts navigationStore.ts featuresStore.ts inputStore.ts partOptionsStore.ts
           partStore.ts requestFromStores.ts workflowStore.ts (Node chunk only)                           N
           jobStore.ts settingsStore.ts                                                                   M
  actions/ generation.ts downloads.ts parts.ts pipeline.ts                                                N
  hooks/   useGenerateAction.ts useViewport.ts useNavPosture.ts useResolvedUiMode.ts useReveal.ts
           useFeatures.ts useTunerEnabled.ts usePartsView.ts useSoftKeyboard.ts                           N
           useGenerateAvailability.ts useBackendSession.ts useViewedTuning.ts useBreakpoint.ts            M
  lib/     uiModes.ts viewport.ts featureFlags.ts buildFlags.ts jobRequest.ts settingsRequest.ts
           generateAvailability.ts partsModel.ts steps.ts featureMeta.ts motion.ts                       N
           storage.ts                                                                                     M
  api/     featuresTypes.ts partsTypes.ts features.ts parts.ts files.ts                                   N
           types.ts ws.ts                                                                                 M
  styles/  modes.css N · tokens.css M · global.css M
  vite-env.d.ts M
frontend/package.json, package-lock.json, vite.config.ts                                                  M (Node slice)
test/frontend/  unit/*.test.mjs  mock/part_aware_mock.mjs  ui_modes_mock_smoke.mjs                        N
                review/import_boundaries.mjs  review/bundle_budget.mjs  review/ui_probe.js                N
                mock_backend.mjs                                                                          M
```

---------------------------------------------------------------------------------------------------

## 4. Backend contract assumed by the frontend [BE]

The frontend reads every item below as **optional**. When the item is missing, the UI degrades to today's
behaviour. Field names are snake_case, as on the wire.

### 4.1 Summary table

| # | Endpoint or field | Status codes and errors the frontend handles | Used by |
|---|---|---|---|
| 1 | `GET /api/v1/features` → `FeaturesOut` (§4.2) | 200; **404 → legacy backend**; 401 → existing expire flow; network → `status:'error'`, retried on the next sync | `featuresStore` |
| 2 | `POST /api/v1/jobs` with optional top-level `source` (`PromptSourceIn`) and optional `settings.parts` (`PartOptionsIn`) | 202 `Job`; 422 `validation_error` (field `loc` mapped to the prompt field); 422 or 503 `capability_unavailable`; 404; 429 `queue_full` | `jobStore.submit` |
| 3 | `Job.kind?`, `Job.source?`, `Job.steps?`, `JobResult.parts?` | – (missing = legacy) | progress, history, viewer, Node status |
| 4 | Part-analysis run resource (§4.6): `GET`/`POST /jobs/{id}/part-analyses`, `GET /part-analyses/{id}`, `POST …/cancel`, `DELETE`, files | 200, 202, 204, 404, 409, 429, 503 `parts_unavailable` | `partStore` |
| 5 | Manifest `img3d.parts/1` (§4.7) | 200; 409 while not completed | `lib/partsModel.normalizeManifest` |
| 6 | WS `part_analysis.updated` (job-scoped), `features.updated` (global, optional) | unknown types are already ignored [V `ws.ts:L254-276` `default: break`] | `useBackendSession` |
| 7 | `GET /jobs?kind=` | optional; the frontend does not rely on it in this milestone | – |

### 4.2 `GET /api/v1/features`

```ts
// src/api/featuresTypes.ts
export const CAPABILITY_STATES = ['available', 'not_installed', 'not_approved', 'disabled', 'unavailable', 'error'] as const
export type CapabilityState = (typeof CAPABILITY_STATES)[number]

export interface CapabilityOut {
  state: CapabilityState
  /** Shown verbatim to the user whenever state !== 'available'. */
  reason: string | null
}

export interface PromptToImageOut extends CapabilityOut {
  default_model_id: string | null
  max_prompt_chars: number                                  // frontend fallback 2000
  presets: Array<{ id: 'character' | 'object' | 'raw'; label: string }>
  styles: Array<{ id: 'realistic' | 'stylized' | 'anime'; label: string }>
}

export type PartKindOut = 'physical' | 'region' | 'physical_deferred'
export interface PartCategoryOut {
  id: string                 // taxonomy id: 'eye', 'eyebrow', 'eyelash', 'hair', 'clothing.top', 'accessory.eyewear', ...
  label: string
  group: 'face' | 'hair' | 'clothing' | 'accessory' | 'object' | 'appendage' | 'body'
  default_kind: PartKindOut
  separable: boolean         // may be listed in PartOptionsIn.separate
}

export interface PartsFeatureOut extends CapabilityOut {
  api: 'runs' | 'job'                                      // result shape, see §4.10
  taxonomy_version: string                                 // e.g. 'img3d.taxonomy/1'
  categories: PartCategoryOut[]
  strategies: Array<{ id: string; label: string } & CapabilityOut>
  default_strategy: string | null
  segmentation: CapabilityOut & { provider_ids: string[] }
  rerun: boolean                                           // analysis can be (re)requested for a completed job
  segmentation_preview: boolean                            // optional pre-generation mask preview (§12.6)
}

export interface ModelLicenseOut {
  id: string; name: string; url: string | null
  commercial: 'allowed' | 'revenue_capped' | 'non_commercial' | 'research_only' | 'unknown'
  gated: boolean; requires_acceptance: boolean; attribution: string | null
}
export interface ModelEntryOut {
  id: string                                               // e.g. 'z-image-turbo', 'gdino-base', 'sam2.1-large'
  role: 'text_to_image' | 'detector' | 'segmenter' | 'part_parser' | 'captioner'
  display_name: string
  state: 'not_installed' | 'not_approved' | 'installed' | 'loading' | 'ready' | 'error' | 'unavailable'
  reason: string | null
  size_bytes: number | null
  vram_estimate_gb: number | null
  domains: Array<'anime' | 'realistic' | 'object' | 'any'>
  supports_negative_prompt: boolean                        // text_to_image only; false otherwise
  license: ModelLicenseOut
}

export interface FeaturesOut {
  schema: 'img3d.features/1'
  prompt_to_3d: PromptToImageOut
  parts: PartsFeatureOut
  tuning: { mesh_tuner: CapabilityOut; part_refinement: CapabilityOut }
  models: ModelEntryOut[]
}
```

Example for this milestone's expected state: no text-to-image model installed, regions-only parts, segmentation
models not installed, tuning disabled.

```json
{
  "schema": "img3d.features/1",
  "prompt_to_3d": { "state": "not_installed", "reason": "No text-to-image model is installed. Model choice is pending approval.",
                    "default_model_id": null, "max_prompt_chars": 2000,
                    "presets": [{"id": "character", "label": "Character"}, {"id": "object", "label": "Object"}, {"id": "raw", "label": "Raw prompt"}],
                    "styles": [{"id": "realistic", "label": "Realistic"}, {"id": "stylized", "label": "Stylized"}, {"id": "anime", "label": "Anime"}] },
  "parts": { "state": "available", "reason": null, "api": "runs", "taxonomy_version": "img3d.taxonomy/1",
             "categories": [{"id": "hair", "label": "Hair", "group": "hair", "default_kind": "physical", "separable": true},
                            {"id": "eyelash", "label": "Eyelashes", "group": "face", "default_kind": "physical_deferred", "separable": true},
                            {"id": "body.head.face", "label": "Face", "group": "body", "default_kind": "region", "separable": false}],
             "strategies": [{"id": "regions_only", "label": "Regions only (no new models)", "state": "available", "reason": null},
                            {"id": "image_masks", "label": "Image masks → 3D", "state": "not_installed", "reason": "Segmentation models are not installed."}],
             "default_strategy": "regions_only",
             "segmentation": {"state": "not_installed", "reason": "Grounding DINO / SAM 2.1 weights are not installed.", "provider_ids": []},
             "rerun": true, "segmentation_preview": false },
  "tuning": { "mesh_tuner": {"state": "disabled", "reason": "Auto Mesh Tuner is paused until approved."},
              "part_refinement": {"state": "disabled", "reason": "Part refinement is not approved yet."} },
  "models": [{"id": "z-image-turbo", "role": "text_to_image", "display_name": "Z-Image-Turbo", "state": "not_installed",
              "reason": "Not downloaded.", "size_bytes": 32800000000, "vram_estimate_gb": 16, "domains": ["any"],
              "supports_negative_prompt": false,
              "license": {"id": "apache-2.0", "name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0",
                          "commercial": "allowed", "gated": false, "requires_acceptance": false, "attribution": null}}]
}
```

### 4.3 Job creation (additive)

```ts
// src/api/types.ts — additions (only optional fields; the image branch is today's type unchanged)
export interface PromptSourceIn {
  type: 'prompt'
  prompt: string                                // trimmed, 1..features.prompt_to_3d.max_prompt_chars
  negative_prompt?: string                      // only when the chosen model supports it AND non-empty
  preset?: 'character' | 'object' | 'raw'
  style?: 'realistic' | 'stylized' | 'anime'
  part_hint?: boolean                           // ask the T2I template to separate hair/clothes/accessories
  model_id?: string                             // omitted → backend default
  image_seed?: number                           // omitted → random
}

export interface PartOptionsIn {                // GenerationSettings.parts (inside settings → survives retry)
  enabled: true                                 // the frontend omits `parts` instead of sending enabled:false
  segmentation: 'auto' | 'off'
  decomposition: string                         // a features.parts.strategies[].id with state 'available'
  separate: string[]                            // separable category ids requested as physical assets
  subject?: 'auto' | 'anime_character' | 'realistic_human' | 'object'
  export: { part_files: boolean; assembled_glb: boolean; keep_base_model: true }
}

export interface GenerationSettings { /* existing fields … */ parts?: PartOptionsIn }

export interface ImageJobRequest { reference_set_id: string; primary_image_id: string | null; settings: GenerationSettings }
export interface PromptJobRequest { source: PromptSourceIn; settings: GenerationSettings }
export type CreateJobRequest = ImageJobRequest | PromptJobRequest
```

Request bodies. The **legacy** body below is exactly today's [V `jobStore.ts:L208-212`]:

```json
{ "reference_set_id": "rs_0a1b2c3d4e5f", "primary_image_id": "img_0a1b2c3d4e5f",
  "settings": { "resolution": 1024, "seed": 0, "randomize_seed": true, "polygon_target": 500000,
                "texture_size": 2048, "remove_background": true, "reference_mode": "primary_only" } }
```
```json
{ "source": { "type": "prompt", "prompt": "a knight with a red cape and a sword", "preset": "character",
              "style": "stylized", "part_hint": true, "model_id": "z-image-turbo" },
  "settings": { "resolution": 1024, "seed": 0, "randomize_seed": true, "polygon_target": 500000,
                "texture_size": 2048, "remove_background": true, "reference_mode": "primary_only",
                "parts": { "enabled": true, "segmentation": "auto", "decomposition": "regions_only",
                           "separate": ["hair", "clothing.outer", "object.held"], "subject": "auto",
                           "export": { "part_files": true, "assembled_glb": true, "keep_base_model": true } } } }
```

Retry: `jobStore.retry` resends `job.settings` verbatim [V `jobStore.ts:L225-235`]. So `settings.parts` must
round-trip through the backend schema (`backend_contract_map` §4.3 states the same invariant). For a prompt job,
a legacy-style retry re-runs image-to-3D on the generated reference image (`reference_set_id` points at the
generated set). The frontend adds a separate action, "Regenerate from prompt", which posts a new `source`.

### 4.4 Job read additions

```ts
export type JobKind = 'image_to_3d' | 'prompt_to_3d' | 'part_decomposition'
export type JobSourceOut =
  | { type: 'image' }
  | { type: 'prompt'; prompt: string; negative_prompt: string | null; preset: string | null; style: string | null;
      model_id: string; model_name: string | null; license_id: string | null; image_seed: number | null }
export type PipelineStepStatus = 'pending' | 'running' | 'done' | 'warning' | 'skipped' | 'failed'
export interface PipelineStepOut {
  id: string                       // known: 'input_image' | 'prompt' | 'segmentation' | 'image_to_3d' | 'part_extraction' | 'export'
  label: string; status: PipelineStepStatus; message: string | null; duration_s: number | null; percent: number | null
}
export interface JobPartsSummaryOut {
  analysis_id: string | null; status: JobStatus; part_count: number; region_count: number
  manifest_url: string | null; assembled_url: string | null; warnings: string[]
}
// Job:       kind?: JobKind; source?: JobSourceOut | null; steps?: PipelineStepOut[] | null
// JobResult: parts?: JobPartsSummaryOut | null
```

The frontend treats a missing `kind` as `'image_to_3d'`. A step id it does not know is still rendered with its
`label` (forward compatible).

### 4.5 Progress interplay

`job.progress` keeps the pinned 9-stage table. When `job.steps` is present, `ProgressContent` shows a
**Pipeline** list built from the steps above the unchanged stage list. That list is the stage detail of
`image_to_3d`. When `steps` is absent, the UI is exactly today's.

### 4.6 Part-analysis run resource (variant "runs", recommended)

The frontend recommends this variant: re-runnable on old jobs, no history pollution, the same pattern as tuning
runs, and no stage changes.

```ts
// src/api/partsTypes.ts
export interface PartAnalysisOut {
  id: string                                   // 'pa_<12 hex>'
  job_id: string
  status: JobStatus
  created_at: IsoDateTime; started_at: IsoDateTime | null; finished_at: IsoDateTime | null
  options: PartOptionsIn
  progress: { percent: number; step_id: string | null; message: string | null; updated_at: IsoDateTime }
  steps: PipelineStepOut[]                     // ids: 'segmentation' | 'part_extraction' | 'export'
  result: null | { manifest_url: string; assembled_url: string | null; overlay_url: string | null
                   part_count: number; region_count: number; warnings: string[] }
  error: JobError | null
}
```

| Method | Path | Body | Success | Errors handled by the UI |
|---|---|---|---|---|
| GET | `/jobs/{job_id}/part-analyses` | – | 200 `{items: PartAnalysisOut[]}` newest first | 404 → drop the cached entry |
| POST | `/jobs/{job_id}/part-analyses` | `PartOptionsIn` | 202 `PartAnalysisOut` | 404; 409 (job not completed); 422; 429 `queue_full`; 503 `parts_unavailable` |
| GET | `/part-analyses/{id}` | – | 200 | 404 |
| POST | `/part-analyses/{id}/cancel` | – | 202 | 409 terminal → refresh |
| DELETE | `/part-analyses/{id}` | – | 204 | 409 running |
| GET | `/part-analyses/{id}/manifest` | – | 200 manifest JSON | 409 until completed |
| GET | `/part-analyses/{id}/model.glb` | – | `model/gltf-binary` inline (assembled) | 409, 404 |
| GET | `/part-analyses/{id}/parts/{part_id}.glb` | – | attachment `img3d_<job>_<part>.glb` | 404 |
| GET | `/part-analyses/{id}/segmentation/{name}` (`^[a-z0-9_]+\.png$`) | – | `image/png` | 404 |

All GET file routes accept `?token=` like the existing file routes [V `api-contract.md` §7.1]. The frontend
builds URLs with the existing `resolveApiUrl`.

### 4.7 Manifest: what the frontend requires

The frontend consumes a **subset** of `img3d.parts/1`. It tolerates both drafts from the research
(`backend_contract_map` §4.4 and `part_aware_3d_research` §6.3).

```jsonc
{
  "schema": "img3d.parts/1",
  "job_id": "job_…", "analysis_id": "pa_…",
  "parts": [{
    "id": "pt_hair",                 // or "part_id"
    "label": "Hair",                 // optional; frontend falls back to the category label
    "category": "hair",              // or "class"
    "kind": "physical",              // physical | region | physical_deferred (alias "deferred")
    "parent": "pt_body",             // nullable
    "attach_to": "pt_head",          // nullable; also accepted: "held_by"
    "side": "left",                  // left | right | center | null (character-relative)
    "node_name": "part:pt_hair",     // physical: glTF node name in the assembled GLB (also extras.img3d.part_id)
    "feature_id": null,              // region: value of _FEATURE_ID_0 on the host mesh
    "host_part": null,               // region: id of the mesh carrying it (usually the body)
    "faces": 210334, "confidence": 0.81,
    "files": { "mesh": "parts/pt_hair.glb", "mask": "masks/pt_hair.png" },  // relative to the manifest URL, or absolute /api/v1 URLs
    "refinement": { "eligible": true, "status": "untouched" }
  }],
  "regions": [ /* optional separate list (research §6.3 draft): {region_id, class, host_part, feature_id, symmetric_with} */ ],
  "relations": [{ "type": "attached_to", "a": "pt_hair", "b": "pt_body" }],
  "warnings": []
}
```

Normalization rules (`lib/partsModel.ts → normalizeManifest(raw: unknown, manifestUrl: string): NormalizeResult`):
1. `schema` must start with `img3d.parts/`. Otherwise the result is `{ok:false, error:'unsupported_manifest'}`.
2. Accept `id|part_id`, `category|class` and `kind ∈ {physical, region, physical_deferred, deferred}`. An entry
   with an unknown kind is **skipped** and recorded in `warnings`.
3. `regions[]` entries become `kind:'region'` parts (`region_id→id`). An id already present is not duplicated.
4. Ids must match `^[A-Za-z0-9_.:-]{1,64}$`. Invalid ids are skipped with a warning. There is a hard cap of 512
   parts.
5. File paths that are not absolute (`/api/…`) resolve against the directory of `manifestUrl` (string join, no
   `..`). An entry containing `..` is dropped.
6. A relation whose endpoint is unknown is dropped.
7. `refinement` is display-only. The frontend never acts on it this milestone.

### 4.8 WebSocket

```
{ "type": "part_analysis.updated", "ts": "…Z", "data": PartAnalysisOut }   // job-scoped; status changes never dropped
{ "type": "features.updated",      "ts": "…Z", "data": FeaturesOut }       // optional; frontend also refetches on hello
```

`api/ws.ts` adds both cases to `dispatch` and to `RealtimeEventMap`. A backend that never sends them changes
nothing.

### 4.9 Error → UI mapping

| Error | Surface | UI |
|---|---|---|
| 404 on `/features` | features | `status:'unsupported'`. Default hides the prompt and parts UI. Other modes show them disabled: "Needs a newer Img3D backend." |
| 422 `validation_error`, `loc` starts with `["body","source","prompt"]` | Create job | Inline error under the prompt field (`aria-describedby`) plus an error toast with the existing title "Could not start generation". |
| 422 or 503 `capability_unavailable` | Create job | Toast with the server message, then `featuresStore.refresh()`. |
| 503 `parts_unavailable` | Analyse parts | Toast with the server message, then `featuresStore.refresh()`. |
| 409 on the manifest | Part store | Keep `status:'running'`. Load the manifest on the terminal `part_analysis.updated`. |
| 429 `queue_full` | Job or analysis | Existing message [V `jobStore.ts:L81-85`]. |
| Manifest normalize error | Part store | Parts panel error state: "This part manifest could not be read (unsupported_manifest)." There is no viewer parts mode. |

### 4.10 Reconciliation with the two backend shapes

| Concern | Variant **runs** (`part_aware_3d_research` §6.5, `backend_generation_map` B4) | Variant **job** (`backend_contract_map` §4.3–4.7) | Frontend adapter (`api/parts.ts`) |
|---|---|---|---|
| Discovery | `features.parts.api = 'runs'` | `features.parts.api = 'job'` | switch on `api` |
| Request at generation | `settings.parts` auto-chains a run after the job completes | `settings.parts` runs inside the job (`job.steps`) | the same `PartOptionsIn` in both |
| Results for a job | `GET /jobs/{id}/part-analyses` | `job.result.parts` plus `GET /jobs/{id}/parts` (manifest) | `listPartAnalyses(jobId)` returns `PartAnalysisOut[]`. In the job variant it synthesises one item with `id = 'job:' + jobId` from `result.parts` and `job.steps`. |
| Re-run on an old job | `POST /jobs/{id}/part-analyses` | `POST /jobs/{id}/parts/rebuild` → a new derived job (`kind: 'part_decomposition'`) | `requestPartAnalysis(jobId, opts)` returns `{type:'run', run}` or `{type:'job', job}`. In the job case the store upserts it into `jobStore`. |
| Files | `/part-analyses/{id}/…` | `/jobs/{id}/parts/assembled.glb`, `/jobs/{id}/parts/{pt}/model.glb`, `…/mask`, `…/segmentation/overlay` | URLs come from the manifest and summary, never assembled by convention |
| Events | `part_analysis.updated` | `job.updated` only | the store handles both |
| Prompt flow alternative | – | – | If the backend picks the **two-step** text-to-image flow (`text_to_image_research` §7: `POST /t2i/generations` → candidates → use → `POST /jobs`), a `PromptReviewContent` (candidate picker) is added. The orchestration lives in `actions/generation.ts`. This is an open question (§21). |

---------------------------------------------------------------------------------------------------

## 5. Shared state and logic layer

### 5.1 Store inventory

| Store | Status | Persisted key | Notes |
|---|---|---|---|
| `authStore`, `systemStore`, `referenceStore`, `toastStore`, `viewerRuntime` | unchanged (`viewerRuntime` +parts fields, §7) | as today | |
| `settingsStore` | `toRequestSettings` and `SAMPLER_FIELDS` moved to `lib/settingsRequest.ts` and re-exported | `img3d.settings` v1 (unchanged) | No schema change. |
| `jobStore` | `submit(request?)` added; `retry` unchanged | `img3d.viewedJobId` | §5.2.7 |
| `uiStore` | **unchanged** | `img3d.ui` v1 | `mobileTab` and `historyOpen` stay as Default-shell state. |
| `meshTunerStore` | **untouched** | `img3d.meshTuner` | Not polled while gated (§13). |
| `preferencesStore` | N | `img3d.preferences` v1 | §5.2.1 |
| `navigationStore` | N | – | §5.2.2 |
| `featuresStore` | N | – | §5.2.3 |
| `inputStore` | N | `img3d.input` v1 | §5.2.4 |
| `partOptionsStore` | N | `img3d.partOptions` v1 | §5.2.5 |
| `partStore` | N | – | §5.2.6 |
| `workflowStore` | N (Node lazy chunk only) | `img3d.workflow` v1 | §12.4 |

### 5.2 New stores: exact interfaces

#### 5.2.1 `store/preferencesStore.ts`

```ts
import type { UiMode } from '../lib/uiModes'
export type DensityPreference = 'auto' | 'comfortable' | 'touch'
export type MotionPreference = 'system' | 'reduce'
export type NodeViewPreference = 'auto' | 'canvas' | 'list'
export type LinkStyle = 'bezier' | 'smoothstep' | 'straight'

export interface Preferences {
  uiMode: UiMode                       // default 'default'
  density: DensityPreference           // default 'auto'
  motion: MotionPreference             // default 'system'
  node: { view: NodeViewPreference; minimap: boolean; snapToGrid: boolean; linkStyle: LinkStyle }
                                       // default { view:'auto', minimap:true, snapToGrid:true, linkStyle:'bezier' }
  dismissedHints: string[]             // default []
}
interface PreferencesState extends Preferences {
  setUiMode: (mode: UiMode) => void
  setDensity: (density: DensityPreference) => void
  setMotion: (motion: MotionPreference) => void
  setNodePreference: <K extends keyof Preferences['node']>(key: K, value: Preferences['node'][K]) => void
  dismissHint: (id: string) => void
  reset: () => void
}
// create<PreferencesState>()(persist(…, { name: STORAGE_KEYS.preferences, version: 1,
//   storage: createJSONStorage(() => localStorage),
//   partialize: (s) => ({ uiMode, density, motion, node, dismissedHints }),
//   merge: (persisted, current) => ({ ...current, ...sanitizePreferences(persisted) }) }))
```

- `sanitizePreferences` (pure, in `lib/uiModes.ts`) drops any value that is not a member of its union. A corrupt
  or unknown persisted mode therefore falls back to `'default'`.
- Hydration is synchronous at store creation, so the first render uses the saved mode with no flash.
- Cross-tab sync: `GlobalOverlays` registers one `storage` listener. When `event.key === STORAGE_KEYS.preferences`,
  it calls `usePreferencesStore.persist.rehydrate()`.

#### 5.2.2 `store/navigationStore.ts` (not persisted)

```ts
export const REVEAL_TARGETS = ['create', 'prompt', 'settings', 'viewer', 'progress', 'parts', 'export', 'history'] as const
export type RevealTarget = (typeof REVEAL_TARGETS)[number]
export type MobileSheetId = 'create' | 'parts' | 'library' | 'progress' | 'blocked' | 'status'
export type TabletSection = 'create' | 'viewer' | 'parts' | 'library'
export type CreateTab = 'input' | 'settings'

interface NavigationState {
  reveal: { target: RevealTarget; seq: number } | null    // monotonic seq → same target can be re-requested
  appSettingsOpen: boolean
  sessionUiOverride: UiMode | null                        // from ?ui= or "use Default for this session"
  pipelineBlock: string | null                            // set ONLY by the Node shell while its graph is invalid
  mobileSheet: MobileSheetId | null
  createTab: CreateTab
  tabletSection: TabletSection
  tabletPanelOpen: boolean
  requestReveal: (target: RevealTarget) => void
  setAppSettingsOpen: (open: boolean) => void
  setSessionUiOverride: (mode: UiMode | null) => void
  setPipelineBlock: (reason: string | null) => void
  openMobileSheet: (id: MobileSheetId | null) => void
  setCreateTab: (tab: CreateTab) => void
  setTabletSection: (section: TabletSection) => void
  setTabletPanelOpen: (open: boolean) => void
}
```

**Reveal events** (the only cross-feature navigation signal):

| Emitted by | Target | Default shell | Mobile shell | Tablet shell | Node shell |
|---|---|---|---|---|---|
| `startGeneration()` success | `viewer` | mobile breakpoint: `setMobileTab('viewer')` | close sheets | rail: keep the panel, focus the viewer; bottom: collapse the sheet to peek | select the Image-to-3D node and expand the dock |
| `HistoryList.onOpened` | `viewer` | as above | close the Library sheet | as above | close the history drawer |
| `PartsEmpty` "Analyse parts" | `parts` | scroll `PartsPanel` into view | open the Parts sheet | section `parts` | select Part Extraction |
| Blocked FAB tap | `progress` or `create` | – | open the `blocked` sheet | – | – |
| Validation error on the prompt | `prompt` | `InputSourceCard` focus | open Create, `createTab='input'` | section `create` | select the Prompt node |

`AppShell`'s `useRevealProgress` and `useRevealTuning` [V `AppShell.tsx:L20-44`] stay. Their `scrollIntoView`
calls take `behavior: prefersReducedMotion() ? 'auto' : 'smooth'`.

#### 5.2.3 `store/featuresStore.ts`

```ts
interface FeaturesState {
  snapshot: FeaturesSnapshot                 // from lib/featureFlags.ts; initial EMPTY_FEATURES (status 'loading')
  refresh: () => Promise<boolean>            // GET /features → normalizeFeatures(raw, 200) | (null, 404) → unsupported
  apply: (raw: FeaturesOut) => void          // WS features.updated
}
```

```ts
// lib/featureFlags.ts (pure)
export type FeaturesStatus = 'loading' | 'ready' | 'unsupported' | 'error'
export interface CapabilityView { state: CapabilityState | 'unsupported' | 'loading'; reason: string | null }
export interface FeaturesSnapshot {
  status: FeaturesStatus
  prompt: CapabilityView & { defaultModelId: string | null; maxChars: number; presets: …; styles: … }
  parts: CapabilityView & { api: 'runs' | 'job'; categories: readonly PartCategoryOut[]; strategies: …;
                            defaultStrategy: string | null; segmentation: CapabilityView; rerun: boolean;
                            segmentationPreview: boolean }
  tuner: CapabilityView
  partRefinement: CapabilityView
  models: readonly ModelEntryOut[]
}
export function normalizeFeatures(raw: unknown, httpStatus: number | null): FeaturesSnapshot
export function isTunerVisible(buildFlag: boolean, f: FeaturesSnapshot): boolean      // buildFlag && f.tuner.state==='available'
export function textToImageModels(f: FeaturesSnapshot): readonly ModelEntryOut[]       // role === 'text_to_image'
export function selectableT2IModels(f: FeaturesSnapshot): readonly ModelEntryOut[]     // state installed|ready
```

`lib/buildFlags.ts` is not pure because it touches `import.meta.env`:
`export const BUILD_FLAGS = { meshTuner: import.meta.env.VITE_FEATURE_MESH_TUNER === 'true' } as const`.
`vite-env.d.ts` declares `readonly VITE_FEATURE_MESH_TUNER?: string`.

#### 5.2.4 `store/inputStore.ts`, persisted `img3d.input` v1

```ts
export type InputSource = 'image' | 'prompt'
export interface PromptForm {
  prompt: string                    // default ''
  negativePrompt: string            // default ''
  preset: 'character' | 'object' | 'raw'          // default 'character'
  style: 'realistic' | 'stylized' | 'anime' | null // default null (model default)
  partHint: boolean                 // default true
  modelId: string | null            // default null (backend default)
  imageSeed: number | null          // default null (random)
}
interface InputState {
  source: InputSource               // default 'image'  → today's behaviour
  prompt: PromptForm
  setSource: (source: InputSource) => void
  updatePrompt: (patch: Partial<PromptForm>) => void
  resetPrompt: () => void
  loadFromJob: (source: Extract<JobSourceOut, { type: 'prompt' }>) => void   // "Reuse prompt"
}
```

The `merge` function validates every field. A prompt longer than 20,000 characters is truncated on load as a
defence; the real limit comes from features.

#### 5.2.5 `store/partOptionsStore.ts`, persisted `img3d.partOptions` v1

```ts
export interface PartOptionsForm {
  enabled: boolean                 // default false → request byte-identical to today
  segmentation: 'auto' | 'off'     // default 'auto'
  decomposition: string | null     // null → features.parts.defaultStrategy
  separate: string[] | null        // null → every separable category whose default_kind !== 'region'
  subject: 'auto' | 'anime_character' | 'realistic_human' | 'object'   // default 'auto'
  partFiles: boolean               // default true
  assembledGlb: boolean            // default true
}
interface PartOptionsState { form: PartOptionsForm; update: (patch: Partial<PartOptionsForm>) => void; reset: () => void }
```

#### 5.2.6 `store/partStore.ts` (not persisted)

```ts
export type PartEntryStatus = 'idle' | 'loading' | 'ready' | 'error'
export interface ManifestState { status: 'idle' | 'loading' | 'ready' | 'error'; partSet: PartSet | null; error: string | null }
export interface PartJobEntry { status: PartEntryStatus; analyses: PartAnalysisOut[] /* newest first */; error: string | null }
export interface PartViewState {
  show: boolean                    // viewer shows the parts model instead of the original
  colorize: boolean                // categorical colours instead of textures
  hidden: string[]                 // part ids
  isolated: string | null
  selected: string | null
  hovered: string | null
}
interface PartState {
  byJob: Record<string, PartJobEntry>
  manifests: Record<string, ManifestState>          // key = analysis id
  view: PartViewState
  requestingJobIds: string[]
  loadForJob: (jobId: string, force?: boolean) => Promise<void>
  upsertAnalysis: (run: PartAnalysisOut) => void     // WS + REST; on terminal 'completed' → loadManifest
  loadManifest: (analysisId: string) => Promise<void>
  requestAnalysis: (jobId: string, options: PartOptionsIn) => Promise<boolean>
  cancelAnalysis: (analysisId: string) => Promise<void>
  removeAnalysis: (analysisId: string) => Promise<void>
  setShow: (show: boolean) => void
  setColorize: (on: boolean) => void
  toggleHidden: (partId: string) => void
  setHiddenSet: (ids: string[]) => void              // show all / physical only / regions only
  isolate: (partId: string | null) => void
  select: (partId: string | null) => void
  hover: (partId: string | null) => void
  resetView: () => void                              // called when the viewed job changes
}
export const selectLatestCompletedAnalysis = (s: PartState, jobId: string | null): PartAnalysisOut | null
export const selectActiveAnalysis = (s: PartState, jobId: string | null): PartAnalysisOut | null
export const selectManifest = (s: PartState, analysisId: string | null): ManifestState | null
```

Every selector returns a stored reference or a primitive, as zustand v5 requires.

#### 5.2.7 `jobStore` changes (small, backward compatible)

```ts
submit: (request?: CreateJobRequest) => Promise<boolean>
//   request === undefined → const built = buildRequestFromStores()   (store/requestFromStores.ts)
//   built.ok === false    → toast.warning(titleFor(built.code), built.reason); return false
//   image/no-references case keeps today's exact toast: 'Add a reference image' / 'Upload at least one image before generating.'
regenerateFromPrompt: (job: Job) => Promise<boolean>   // posts { source: job.source (as PromptSourceIn), settings: job.settings }
```

`store/requestFromStores.ts` (store layer, not pure) reads the following stores with `getState()` and calls
`buildCreateJobRequest`: `inputStore`, `referenceStore`, `settingsStore`, `systemStore`
(`defaults.reference_mode`), `partOptionsStore` and `featuresStore`.

### 5.3 Pure modules: exact signatures (tested under Node)

All of these use explicit `.ts` relative imports (`allowImportingTsExtensions: true`, `noEmit: true`
[V `tsconfig.app.json:L13,L16`]). They use only `import type` from non-pure files and value imports only from pure
files. `api/types.ts` has no imports, so it qualifies [V].

```ts
// domain/pipeline.ts
export interface PipelineConfig {
  source: 'image' | 'prompt'
  segmentation: boolean            // run 2D segmentation (only meaningful with partExtraction)
  partExtraction: boolean          // produce a part analysis
}
export function configFromForms(source: InputSource, parts: PartOptionsForm): PipelineConfig
// → { source, partExtraction: parts.enabled, segmentation: parts.enabled && parts.segmentation === 'auto' }

// lib/jobRequest.ts
export interface RequestInputs {
  config: PipelineConfig
  references: { id: string; primaryImageId: string | null; imageCount: number } | null
  prompt: PromptForm
  form: SettingsForm
  referenceMode: ReferenceMode
  partOptions: PartOptionsForm
  features: FeaturesSnapshot
}
export type BuildErrorCode = 'no_references' | 'empty_prompt' | 'prompt_too_long' | 'prompt_unavailable'
                           | 'parts_unavailable' | 'strategy_unavailable'
export type BuildResult = { ok: true; request: CreateJobRequest } | { ok: false; code: BuildErrorCode; reason: string }
export function buildCreateJobRequest(inputs: RequestInputs): BuildResult
export function toPartOptionsIn(form: PartOptionsForm, features: FeaturesSnapshot): PartOptionsIn | null
export function toPromptSourceIn(form: PromptForm, features: FeaturesSnapshot): PromptSourceIn

// lib/settingsRequest.ts   (moved verbatim from settingsStore.ts)
export const SAMPLER_FIELDS: readonly SamplerField[]
export function toRequestSettings(form: SettingsForm, referenceMode: ReferenceMode): GenerationSettings

// lib/generateAvailability.ts
export interface AvailabilityInputs {
  reachable: boolean | null; system: SystemStatus | null
  source: InputSource; imageCount: number; uploading: boolean
  promptLength: number; features: FeaturesSnapshot; partsEnabled: boolean
  activeJob: Job | null; submitting: boolean; pipelineBlock: string | null
}
export type AvailabilityCode = 'unreachable' | 'waiting_backend' | 'engine_problem' | 'pipeline_invalid'
  | 'features_loading' | 'prompt_unsupported' | 'prompt_unavailable' | 'empty_prompt' | 'prompt_too_long'
  | 'no_references' | 'uploading' | 'parts_unavailable' | 'busy' | 'queue_full' | 'submitting'
export interface GenerateAvailability {           // existing shape + code
  canGenerate: boolean; reason: string | null; note: string | null; busy: boolean
  engineProblem: EngineProblem | null; code: AvailabilityCode | null
}
export function computeGenerateAvailability(i: AvailabilityInputs): GenerateAvailability

// lib/uiModes.ts
export const UI_MODES = ['default', 'node', 'mobile', 'tablet'] as const
export type UiMode = (typeof UI_MODES)[number]
export const UI_MODE_META: Record<UiMode, { label: string; description: string }>
export function isUiMode(value: unknown): value is UiMode
export function parseUiOverride(search: string): UiMode | null          // '?ui=node' → 'node'
export function sanitizePreferences(raw: unknown): Partial<Preferences>
export function resolveUiMode(preference: UiMode, override: UiMode | null): UiMode
export function recommendUiMode(v: ViewportSnapshot): UiMode            // badge only; never auto-applied
export function resolveDensity(pref: DensityPreference, mode: UiMode, coarse: boolean): 'comfortable' | 'touch'

// lib/viewport.ts
export type WidthClass = 'compact' | 'medium' | 'expanded' | 'large'    // <600 · 600–839 · 840–1199 · ≥1200
export interface ViewportSnapshot {
  width: WidthClass; portrait: boolean; coarse: boolean; hover: boolean; short: boolean; lowMemory: boolean
}
export const VIEWPORT_QUERIES: Readonly<Record<'medium' | 'expanded' | 'large' | 'portrait' | 'coarse' | 'hover' | 'short', string>>
export function classifyViewport(matches: Readonly<Record<keyof typeof VIEWPORT_QUERIES, boolean>>, deviceMemory: number | null): ViewportSnapshot
export type NavPosture = 'rail' | 'bottom' | 'compact'
export function navPosture(v: ViewportSnapshot): NavPosture   // compact if width==='compact'; rail if width≥expanded && !portrait; else bottom

// lib/partsModel.ts
export type PartKind = 'physical' | 'region' | 'deferred'
export interface PartView {
  id: string; label: string; category: string; group: PartCategoryOut['group'] | 'other'; kind: PartKind
  parentId: string | null; attachToId: string | null; side: 'left' | 'right' | 'center' | null
  nodeName: string | null; featureId: number | null; hostPartId: string | null
  faces: number | null; confidence: number | null; meshUrl: string | null; maskUrl: string | null
  refinement: { eligible: boolean; status: string } | null
}
export interface RelationView { type: string; a: string; b: string }
export interface PartSet {
  schema: string; jobId: string | null; analysisId: string | null
  parts: readonly PartView[]; byId: Readonly<Record<string, PartView>>
  relations: readonly RelationView[]; warnings: readonly string[]
}
export type NormalizeResult = { ok: true; partSet: PartSet } | { ok: false; error: string }
export function normalizeManifest(raw: unknown, manifestUrl: string, categories: readonly PartCategoryOut[]): NormalizeResult
export function groupByCategory(set: PartSet): readonly { group: string; label: string; parts: readonly PartView[] }[]
export function hierarchy(set: PartSet): readonly { part: PartView; depth: number }[]   // depth-first by parentId
export function relationsOf(set: PartSet, id: string): readonly { type: string; otherId: string; direction: 'out' | 'in' }[]
export function toggleId(list: readonly string[], id: string): string[]
export function idsOfKind(set: PartSet, kinds: readonly PartKind[]): string[]
export type PartDisplayState = 'normal' | 'dimmed' | 'highlighted' | 'hidden'
export function displayStates(set: PartSet, view: Pick<PartViewState, 'hidden' | 'isolated' | 'selected' | 'hovered'>): Readonly<Record<string, PartDisplayState>>
export const PART_GROUP_ORDER: readonly string[]       // face, hair, clothing, accessory, object, appendage, body, other
export function categoryColor(index: number): readonly [number, number, number]   // 12-colour palette, ≥3:1 on the viewer bg

// lib/steps.ts
export interface StepView { id: string; label: string; status: PipelineStepStatus; message: string | null; percent: number | null }
export function stepsForRun(job: Job | null, analysis: PartAnalysisOut | null): readonly StepView[] | null   // null → legacy UI

// lib/featureMeta.ts — consistent labels across shells (WCAG 3.2.4)
export const FEATURE_META: Record<'create' | 'input' | 'prompt' | 'settings' | 'viewer' | 'progress' | 'parts' | 'export' | 'library',
                                  { title: string; shortTitle: string; description: string }>

// lib/motion.ts
export function prefersReducedMotion(): boolean    // matchMedia('(prefers-reduced-motion: reduce)') || <html data-motion='reduce'>
```

`computeGenerateAvailability` rule order is fixed (first match wins). Rules marked "unchanged" return today's
strings, so an image-source test pins them.
1. unreachable → `'The backend is unreachable.'` (unchanged)
2. `!system` → `'Waiting for the backend…'` (unchanged)
3. engine problem → summary (unchanged)
4. `pipelineBlock` → `` `Fix the workflow: ${pipelineBlock}` ``
5. Prompt source only, in this order:
   - features loading → `'Checking prompt-to-3D availability…'`
   - unsupported → `'Prompt-to-3D needs a newer Img3D backend.'`
   - state ≠ available → the backend `reason`, or `'Prompt-to-3D is not available.'`
   - empty → `'Describe what to generate.'`
   - too long → `` `The prompt is too long (max ${maxChars} characters).` ``
6. Image source only, in this order:
   - no images → `'Add at least one reference image.'` (unchanged)
   - uploading → `'Wait for uploads to finish.'` (unchanged)
7. `partsEnabled` with parts not available → `` `Part-aware output is unavailable: ${reason}. Turn it off to generate.` ``
8. busy, queue full and submitting → unchanged.

The note logic (engine loading and queue) is unchanged.

### 5.4 Actions (`src/actions/`)

```ts
// actions/generation.ts
export function startGeneration(): Promise<boolean>
//   1) if !computeAvailability(...).canGenerate → return false
//   2) if system.engine.state === 'not_loaded' → void systemStore.loadEngine()   (moved from GenerateCard.tsx:L36-38)
//   3) ok = await jobStore.submit()
//   4) if ok → navigationStore.requestReveal('viewer')
export function cancelActiveGeneration(): Promise<void>
export function regenerateFromPrompt(job: Job): Promise<boolean>
export function runGenerateShortcut(): void
//   blur focused input/textarea (commit numeric drafts, GenerateCard.tsx:L45-47), ignore while busy, startGeneration()

// actions/downloads.ts
export type ArtifactRef =
  | { kind: 'job_glb'; job: CompletedJob }
  | { kind: 'parts_glb'; url: string; filename: string; sizeHint: number | null }
  | { kind: 'part_glb'; url: string; filename: string; sizeHint: number | null }
  | { kind: 'manifest'; url: string; filename: string }
  | { kind: 'mask_png'; url: string; filename: string }
export function downloadArtifact(ref: ArtifactRef, onProgress?: (percent: number | null) => void, signal?: AbortSignal): Promise<boolean>
//   job_glb → api/jobs.downloadJobGlb (existing); others → api/files.downloadAuthorized(url, {fallbackName, onProgress, signal})
//   errors → toast.error('Download failed', message); returns false

// actions/parts.ts
export function analyzePartsForViewedJob(): Promise<boolean>         // builds PartOptionsIn via toPartOptionsIn; partStore.requestAnalysis
export function cancelPartAnalysis(analysisId: string): Promise<void>

// actions/pipeline.ts   (Node write-through)
export function applyPipelineConfig(config: PipelineConfig): void
//   inputStore.setSource(config.source); partOptionsStore.update({ enabled: config.partExtraction,
//   segmentation: config.segmentation ? 'auto' : 'off' })   — no-op writes are skipped
```

### 5.5 Hooks

| Hook | Returns | Implementation notes |
|---|---|---|
| `useGenerateAvailability()` | `GenerateAvailability` (+`code`) | One primitive or stored reference per selector, then `computeGenerateAvailability`. |
| `useGenerateAction()` | `{ phase: GenerateUiPhase; availability; activeJob; percent: number \| null; supportsCancel: boolean; cancelling: boolean; run(): void; cancel(): void }` | `GenerateUiPhase = 'blocked' \| 'ready' \| 'submitting' \| 'queued' \| 'running' \| 'cancelling'`. `run` is `() => void startGeneration()`. |
| `useViewport()` | `ViewportSnapshot` | `useSyncExternalStore` over `VIEWPORT_QUERIES`. Snapshots are cached per bit pattern (frozen objects, no allocation in `getSnapshot`). Server snapshot = large, landscape, fine pointer. |
| `useNavPosture()` | `NavPosture` | `navPosture(viewport)` plus a typing guard: the posture is not flipped while an editable element has focus (soft keyboard) [R `ui_modes_research` §7.3]. |
| `useResolvedUiMode()` | `UiMode` | `resolveUiMode(prefs.uiMode, nav.sessionUiOverride)` |
| `useReveal(handler)` | void | `useEffect` + `useNavigationStore.subscribe` on `reveal.seq`. It calls `handler(target)` through `useEffectEvent`. There is no `setState` in the effect body. |
| `useFeatures()` / `useTunerEnabled()` | snapshot / boolean | `isTunerVisible(BUILD_FLAGS.meshTuner, snapshot)` |
| `usePartsView()` | `{ entry, analysis, manifest, view }` | Several primitive or reference selectors keyed by `viewedJobId`. |
| `useSoftKeyboard()` | boolean | `focusin`/`focusout` on editable elements plus a `visualViewport` resize (height < 75% of layout height). |

### 5.6 Session wiring (`hooks/useBackendSession.ts` changes)

1. `syncSessionData` [V `L59-69`]:
   - add `useFeaturesStore.getState().refresh()`;
   - call `meshTuner.refreshRuntime()` and `loadRunsForJob()` only when `isTunerEnabledNow()`;
   - add `partStore.loadForJob(viewedJobId)` when `features.parts.state !== 'unsupported'`.
2. Realtime [V `L123-138`]: add `client.on('part_analysis.updated', partStore.upsertAnalysis)` and
   `client.on('features.updated', featuresStore.apply)`.
3. `pollBackend` [V `L47-56`]:
   - poll active (non-terminal) analyses, like `pollMeshTuner`;
   - call `pollMeshTuner` only when the tuner is enabled;
   - in `safety` mode, also `featuresStore.refresh()`.
4. Viewed-job subscription [V `L146-150`]: call `partStore.resetView()` and `partStore.loadForJob(id)`. The tuner
   `loadRunsForJob` runs only when enabled.
5. On re-hello [V `L154-157`]: refresh features and part entries. Resync the tuner only when it is enabled.
6. A `featuresStore` subscription calls `meshTuner.refreshRuntime()` when the tuner becomes enabled. This happens
   only if the build flag is on.

`isTunerEnabledNow()` is `isTunerVisible(BUILD_FLAGS.meshTuner, useFeaturesStore.getState().snapshot)`.

### 5.7 Singletons (exactly one owner each)

| Singleton | Owner after the change |
|---|---|
| `useBackendSession()` | `App.tsx` (root) |
| Ctrl/Cmd+Enter | `app/GlobalShortcuts.tsx`, which calls `runGenerateShortcut()`. It is removed from `GenerateCard` [V `L43-60`]. |
| Paste-to-upload listener | `ReferenceContent` while `active` [V `frontend_architecture_map` §5.1.10] |
| 3D viewer | whichever shell is mounted; one `ViewerPanel` at a time |
| `<Toaster/>`, App settings dialog, `BlenderSetupDialog` (gated) | `app/GlobalOverlays.tsx` |
| `storage` event listener (preferences) | `app/GlobalOverlays.tsx` |

### 5.8 Persistence keys (added to `lib/storage.ts → STORAGE_KEYS`)

```ts
preferences: 'img3d.preferences',   // preferencesStore v1
input: 'img3d.input',               // inputStore v1 (prompt form + source)
partOptions: 'img3d.partOptions',   // partOptionsStore v1
workflow: 'img3d.workflow',         // workflowStore v1 (Node layout/topology only, never parameter values)
```

Existing keys are unchanged. No migration is needed.

---------------------------------------------------------------------------------------------------

## 6. Feature content components (Content + Card)

### 6.1 Rules

1. **`XxxContent`** renders the feature body without `Panel`. It owns its store subscriptions, as today, and its
   root has `container-type: inline-size`. Props are limited to:
   - `active?: boolean` (paste listeners, render loops);
   - `headingLevel?: 2 | 3`;
   - `compact?: boolean` (node-body or pill summaries only, where defined).
2. **`XxxActions`** (optional) holds the header buttons. A card header, sheet header or inspector header renders it.
3. **`XxxPanel`** keeps its existing name, path and look:
   `<Panel id title icon subtitle actions={<XxxActions/>}><XxxContent/></Panel>`.
4. Titles and descriptions come from `lib/featureMeta.ts`, so every shell uses the same words. Icons come from
   `shells/shared/FeatureIcon.tsx`, a component that maps an id to a lucide icon.
5. Content never decides layout: no viewport queries, no `useBreakpoint`. Internal adaptation uses container
   queries only.

### 6.2 Split of existing panels (C2)

| Feature | New Content | New Actions | Existing Panel (kept) | Notes |
|---|---|---|---|---|
| References | `references/ReferenceContent.tsx` | `ReferenceActions` (same file, new set and menu) | `ReferencePanel.tsx` | The panel-level dropzone moves into the Content root. The `active` prop and paste gate are kept. |
| Generation settings | `settings/SettingsContent.tsx` (+ `PartOptionsSection`) | `SettingsActions` (Reset) | `SettingsPanel.tsx` | `PartOptionsSection` renders only when `features.parts.state !== 'unsupported'`. |
| Progress | `generation/ProgressContent.tsx` (+ `PipelineSteps`) | – (StatusBadge stays in the Panel `actions`) | `ProgressCard.tsx` | `PipelineSteps` renders only when `stepsForRun()` ≠ null. |
| Model stats | `viewer/ModelStatsContent.tsx` | `ModelStatsActions` (Reuse; plus "Reuse prompt" for prompt jobs) | `ModelStatsCard.tsx` | `DownloadButton` uses `downloadArtifact({kind:'job_glb'})`. `TunedStats` is untouched and still behind the gate. |
| History | `history/HistoryList.tsx` (already content) | – | Default drawer or tab | The loading spinner [V `HistoryList.tsx:L25`] becomes `Skeleton` rows. |

### 6.3 New feature content

| Component | Purpose | Store reads and writes | Key behaviour |
|---|---|---|---|
| `input/InputSourceSwitch.tsx` | `SegmentedControl` "Image \| Prompt" | `inputStore.source` | The Prompt segment is disabled with `title=reason` when features are unsupported or loading. It is never hidden in non-Default shells. |
| `input/InputSourceCard.tsx` | Slim card holding the switch (Default only) | – | Rendered only when `features.status !== 'unsupported'`. |
| `input/PromptContent.tsx` | Prompt form | `inputStore.prompt`, features | See the list after this table. |
| `input/PromptPanel.tsx` | `Panel` + PromptContent | – | Default, prompt source |
| `input/InputContent.tsx` | Switch plus ReferenceContent or PromptContent | – | Used by sheets, the rail panel and the Node inspector |
| `settings/PartOptionsSection.tsx` | Part-aware options | `partOptionsStore`, features | See the list after this table. |
| `parts/PartsContent.tsx` | Outliner | `partStore`, `jobStore.viewedJobId`, features | States: unsupported, no model, no analysis (CTA when `rerun`), running (steps and percent), error, ready (toolbar, grouped list, inspector). |
| `parts/PartsToolbar.tsx` | Controls | `partStore.view` | Toggle "Parts view" (`aria-pressed`), "Colorize", "Show all", "Physical only", "Regions only", "Group: Category \| Hierarchy". A filter input appears when there are more than 12 parts. |
| `parts/PartRow.tsx` | One row | `partStore` | Colour swatch (colorize on), label plus side, kind text chip ("Physical", "Region", "Deferred"), faces, visibility `<button aria-pressed>` ("Hide Hair" / "Show Hair"), isolate button. Hover and focus set `hovered`; click sets `selected`. |
| `parts/PartInspector.tsx` | Selected part details | `partStore`, manifest | Category, kind, parent, attached to or held by, side, faces, confidence, relations ("layered over Body", "symmetric with Eye (right)"). Refinement shows "Eligible · untouched — refinement is paused". Downloads: part GLB (physical), mask PNG. |
| `parts/PartsEmpty.tsx` | Empty and CTA states | features, `actions/parts` | "Analyse parts" (when `rerun` and the viewed job completed and no analysis is active) |
| `parts/PartsPanel.tsx` | `Panel` + PartsContent | – | Default |
| `export/ExportContent.tsx` | Every downloadable artifact of the viewed job | `jobStore`, `partStore` | Original GLB (existing), assembled parts GLB, per-part GLBs (disclosure list), manifest JSON, segmentation overlay. Each uses `downloadArtifact` with progress. |
| `export/ExportPanel.tsx` | `Panel` + ExportContent | – | Mobile and Tablet parts section, Node Export inspector |
| `generation/PipelineSteps.tsx` | Steps list (`job.steps` + `analysis.steps`) | – | Icon plus text per status (not colour alone). Live region announces only status changes. |
| `preferences/*` | App settings | `preferencesStore`, features | §8.4 |

`PromptContent` details:
- **Prompt field.** `<textarea>` with a visible `<label>`, `enterkeyhint="go"` and a character counter
  `n / max` (announced only when within 10% of the limit).
- **Shape controls.** Preset (Character / Object / Raw) and Style (Auto / Realistic / Stylized / Anime) as
  segmented controls.
- **Part hint.** A "Ask for clearly separated hair, clothes and accessories" switch, on by default. It is
  rendered only when parts are enabled.
- **Model picker.** It lists `selectableT2IModels`, each with a `LicenseBadge`. When none is selectable, it shows
  an honest notice: "No text-to-image model is installed. Model choice is pending approval — see App settings ▸
  Models."
- **Other fields.** Negative prompt (only when the model supports it); image seed (Random / fixed number).

`PartOptionsSection` details:
- **Master switch.** "Part-aware output (beta)". When it is disabled, `aria-describedby` carries the reason.
- **Options shown while enabled:**
  - Segmentation (Auto / Off), disabled with its reason when segmentation is unavailable;
  - Subject (Auto / Anime / Realistic / Object);
  - Strategy (`<select>`; unavailable options are disabled and show their reason);
  - "Separate as physical assets": a checkbox list of separable categories grouped Face / Hair / Clothing /
    Accessories / Objects / Appendages. Deferred categories carry "geometry later";
  - "Kept as regions on the continuous body: face, skin, limbs" (info text);
  - Export: assembled GLB, per-part GLBs.

### 6.4 Placement matrix

| Feature | Default (desktop / tablet / mobile tab) | Mobile shell | Tablet rail | Tablet bottom | Node shell |
|---|---|---|---|---|---|
| Input (switch + refs/prompt) | left col / left scroll / Inputs tab: `InputSourceCard` + `ReferencePanel` or `PromptPanel` | Create sheet › Input | panel › Create › Input | sheet › Create › Input | Input Image / Prompt node inspector |
| Generation settings (+ part options) | `SettingsPanel` (unchanged position) | Create sheet › Settings | panel › Create › Settings | sheet › Create › Settings | Image-to-3D inspector (`SettingsContent`); Segmentation and Part Extraction inspectors (`PartOptionsSection` subsets) |
| Generate / Cancel | `GenerateCard` (card or bar) | `GenerateFab` + sticky footer button in the Create sheet | `RailGenerateButton` (rail bottom group) | `GenerateFab` | toolbar Run / Cancel |
| Progress | `ProgressCard` | `ProgressPill` → Progress sheet | `ProgressPill` over the viewer | `ProgressPill` | node status, toolbar summary, inspector (no node selected) |
| Viewer | `ViewerPanel` | full-bleed home | fills the right area | top area | `ViewerDock` |
| Model stats + downloads | `ModelStatsCard` | Parts sheet › Model | panel › Parts › Model | sheet › Parts | Export inspector |
| Parts | `PartsPanel` (after `ModelStatsCard`) | Parts sheet (standard, non-modal) | panel › Parts | sheet › Parts | Part Extraction inspector |
| Export | ModelStats download + PartsPanel downloads | Parts sheet › Export | panel › Parts › Export | sheet › Parts › Export | Export inspector |
| History | drawer / mobile tab | Library sheet | panel › Library | sheet › Library | toolbar → `HistoryDrawer` |
| App settings | header gear | top-bar gear | rail bottom gear | top-bar gear | toolbar gear |

---------------------------------------------------------------------------------------------------

## 7. Viewer: part display (C3)

### 7.1 API additions

```ts
// viewer/viewerTypes.ts (still free of three.js imports)
export interface ViewerPartsBinding {
  physical: readonly { partId: string; nodeName: string | null }[]   // match by extras.img3d.part_id, then node name
  regions: readonly { partId: string; featureId: number; hostPartId: string | null }[]
}
export interface ViewerPartsProps {
  url: string                                   // token-aware assembled GLB
  show: boolean                                 // display instead of the original (after it has loaded)
  binding: ViewerPartsBinding
  states: Readonly<Record<string, PartDisplayState>>   // from lib/partsModel.displayStates
  colorize: boolean
  colors: Readonly<Record<string, readonly [number, number, number]>>
  onPick?: (partId: string | null) => void
}
export interface ModelViewerProps { /* existing … */ parts?: ViewerPartsProps | null }
export interface ViewerApi { resetCamera: () => void; screenshot: () => string | null; focusPart?: (partId: string) => void }
```

`viewerRuntime` gains `partsStatus`, `partsLoadedBytes`, `partsTotalBytes`, `partsError`, `startParts`,
`setPartsBytes`, `setPartsReady`, `setPartsError`, `resetParts` and `unmatchedPartIds: string[]`. These mirror the
existing secondary fields [V `viewerRuntime.ts`].

### 7.2 Scene behaviour (`viewer/partsScene.ts`, `ModelScene.tsx` gains a `PartsModel`)

- **Loading.**
  - `Model` gains `role: 'parts'`, with its own progress callback and error boundary role (the pattern of
    [V `ModelScene.tsx:L74-117`]).
  - It positions itself with the primary layout offset, as the secondary model does today.
  - The original stays visible until the parts model is ready, so there is no blank frame.
- **Index build.** One traversal maps each `Object3D` to a part id:
  - physical parts match `userData.img3d.part_id` (glTF `extras`) first, then `node_name`;
  - region-bearing meshes are detected by the geometry attribute `_feature_id_0`. three's GLTFLoader lowercases
    unknown attribute names [A: verify with the first real GLB].
  - Bindings that match nothing go to `unmatchedPartIds`. Their rows show "Not found in the 3D file", and their
    toggles are disabled.
- **State application.** A **256×1 RGBA `DataTexture`**:
  - index = region feature id, or a per-physical-part slot (0–255);
  - RGB = category colour (used when colorize is on);
  - A = state (0 normal, 96 dimmed, 160 highlighted, 255 hidden).

  An `onBeforeCompile` patch on each part material does three things:
  - samples the texture by `_feature_id_0` (regions) or by the uniform `uPartSlot` (physical meshes);
  - `discard`s hidden fragments;
  - mixes in highlight emissive, dim alpha (dither) or colorize albedo.

  Physical meshes also get `visible=false` when hidden, so they cost nothing. Every state change is a texture
  upload of 1 KB: there is no React re-render of the scene and no GLB reload.
- **Material cloning.** Materials of physical meshes are cloned once per part, sharing textures, so per-mesh
  uniforms work. The original `model.glb` materials are never patched.
- **View modes.** `applyViewMode` and the wireframe (clay, normals, wireframe) [V `sceneUtils.ts:L139,L162`] still
  apply. The patch is chained into the view materials, so highlight works in clay mode too.
- **Picking.** R3F `onClick` on the parts group, ignored if the pointer moved more than 4 px:
  - a physical hit maps the object to its part;
  - a region hit reads `_feature_id_0` at `face.a` and maps it to the part;
  - the result calls `onPick(partId)`, which runs `partStore.select`.

  Keyboard users select parts from the list; picking is an extra, so WCAG 2.1.1 is met through the list.
- **`focusPart`.** Fits the camera to the part's bounding box, computed once per part from geometry.
- **Memory policy.**
  - `useViewport().lowMemory` replaces the breakpoint check [V `ViewerPanel.tsx:L136,L148`].
  - When `lowMemory` is set, the parts model mounts only while `show` is true. While it is shown, the original
    is unmounted and the parts model owns the layout, since both are in the same unit-AABB frame.
  - Otherwise the parts model is preloaded once the analysis is complete, for an instant toggle.
- **Toolbar** (`ViewerToolbar.tsx`):
  - a "Parts" toggle (Layers icon) appears when `manifest.status==='ready'` and is bound to `partStore.view.show`;
  - a "Colorize" toggle appears while parts are shown;
  - a caption chip reads "Parts view · 14 parts".

  The Original/Tuned `VariantBar` stays under the tuner gate.

---------------------------------------------------------------------------------------------------

## 8. App root, mode switch and App settings

### 8.1 Boot and mode state machine

```
[module load: main.tsx]
  preferencesStore hydrates synchronously (localStorage or defaults; storage errors → defaults)
  override = parseUiOverride(location.search) → navigationStore.setSessionUiOverride(override)
  applyDocumentAttributes({ mode, density, motion, posture: null })   ← BEFORE createRoot: no flash
[App render]
  useBackendSession()                                   (once)
  phase ∈ {checking, unreachable, login} → <PreSession/> = today's Header + BackendBanner + splash | LoginScreen
                                            (identical in every mode; Header adapts by breakpoint)
  phase = ready → <ModeSwitch mode={resolved}/>
  always       → <GlobalOverlays/> (Toaster, AppSettingsDialog, gated BlenderSetupDialog, GlobalShortcuts)

ModeSwitch states (per mode change):
  LOADING  — Suspense fallback <ShellFallback mode/> (CSS skeleton of that shell, in the initial chunk)
  READY    — shell mounted; html attrs updated in useLayoutEffect; previous shell unmounted (domain state is in
             stores, so nothing is lost; the R3F canvas remounts; GLB re-fetch should hit the HTTP cache, see §20)
  FAILED   — ShellErrorBoundary (key = mode): "Couldn't load the <Mode> UI." [Retry] [Use Default UI for this
             session] → navigationStore.setSessionUiOverride('default'); the saved preference is NOT changed
```

Prefetch: focusing or hovering a mode card in App settings calls that shell's loader (`shellRegistry[mode]()`), so
the switch is instant.

### 8.2 `app/shellRegistry.ts` and `app/ModeSwitch.tsx`

```ts
// app/shellRegistry.ts (module scope; .ts file → no only-export-components warning)
export const SHELL_LOADERS = {
  node: () => import('../shells/node/index.ts'),
  mobile: () => import('../shells/mobile/index.ts'),
  tablet: () => import('../shells/tablet/index.ts'),
} as const
// each shells/<mode>/index.ts: export default <Mode>Shell; export const SHELL_READY: boolean
```

`ModeSwitch.tsx` declares, at module scope:
`const NodeShell = lazy(SHELL_LOADERS.node)` and the same for the others. `DefaultShell` is a static import.
Until a shell slice lands, its `index.ts` placeholder re-exports `DefaultShell` with `SHELL_READY = false`, and
the picker disables that card with "Coming in this milestone".

### 8.3 Document attributes and CSS scoping

`app/documentAttributes.ts → applyDocumentAttributes({mode, density, motion, posture})` writes these attributes
on `<html>`:
- `data-ui-mode` = `default`, `node`, `mobile` or `tablet`;
- `data-density` = `comfortable` or `touch`;
- `data-motion` = `system` or `reduce`;
- `data-posture` = `rail`, `bottom` or `compact` (Tablet only; removed otherwise).

`src/styles/modes.css` (new, imported by `global.css`) holds every per-mode override:

```css
:root[data-density='touch'] { --control-h-sm: 36px; --control-h-md: 44px; --control-h-lg: 52px; --hit-min: 44px; }
:root[data-ui-mode='mobile'], :root[data-ui-mode='tablet'] { --hit-min: 48px; }
:root[data-motion='reduce'] { --duration-fast: 0ms; --duration-normal: 0ms; --duration-slow: 0ms; }
:root { --shell-inset-bottom: 0px; --floating-nav-height: 64px; --floating-gap: var(--space-3); --fab-size: 60px; }
:root[data-ui-mode='mobile'],
:root[data-ui-mode='tablet'][data-posture='bottom'],
:root[data-ui-mode='tablet'][data-posture='compact'] {
  --shell-inset-bottom: calc(var(--floating-nav-height) + 2 * var(--floating-gap) + env(safe-area-inset-bottom, 0px));
}
/* mode-scoped equivalents of the max-width:767px rules for shared overlays */
:root[data-ui-mode='mobile'] .img3d-dialog-sheet { /* Dialog renders full-height sheet */ }
```

Dialog, Toaster and the EngineStatus popover keep their `@media (max-width: 767px)` rules for Default. A mode
selector is added (e.g. `:root[data-ui-mode='mobile'] .surface { … }`), so Mobile behaves as a phone at any
width. The Toaster offset uses `--shell-inset-bottom` [V `Toaster.module.css:L14-18`].

### 8.4 App settings dialog (`components/preferences/AppSettingsDialog.tsx`)

The dialog is built on `common/Dialog` (size `lg`; full-height sheet in Mobile). Its body is a lazy chunk,
loaded on first open, which keeps the initial bundle flat. It has these sections, each a `<section>` with an `h3`:

1. **Interface: UI style.**
   - A `RadioCardGroup` (`role="radiogroup"`, arrow keys, roving `tabIndex`) with four cards: Default, Node,
     Mobile and Tablet.
   - Each card has a CSS-only schematic, a label, a one-line description from `UI_MODE_META`, and a
     "Recommended for this device" badge from `recommendUiMode`.
   - Selecting a card applies immediately (`setUiMode`). The dialog stays open across the shell swap because it
     lives in `GlobalOverlays`.
   - When `?ui=` is active, the section shows "Temporarily overridden by the page address (Node). [Clear
     override]".
2. **Density.** Auto (touch on touch screens) / Comfortable / Touch.
3. **Motion.** Follow system / Reduce motion.
4. **Node editor.** Default view (Auto, Canvas, List), minimap, snap to grid, link style.
5. **Features and models** (read-only, honest):
   - `FeatureStatusList` shows Prompt-to-3D, Part-aware output, Segmentation, Auto Mesh Tuner and Part refinement,
     each with its state text and reason. The tuner reads "Paused — awaiting your approval".
   - `ModelRegistryList` shows `features.models` grouped by role: name, state, size, VRAM estimate, domains,
     `LicenseBadge` and a link.
   - There are no install buttons. Footer note: "Installing or changing models is a separate step that needs your
     approval."
   - The licence badge text is Permissive, Revenue-capped, Non-commercial, Research-only or Unknown, so it does
     not rely on colour alone.
6. **About.** App name and version (from `/system.app`) and the active UI mode.

Entry points:
- `AppSettingsButton` in `shells/shared` (IconButton `label="App settings"`, Settings2 icon) for Mobile, Tablet
  and Node;
- in Default, the same component is added to `Header.tsx` actions [V `Header.tsx:L47-68`] for every breakpoint.

---------------------------------------------------------------------------------------------------

## 9. Default shell: preserve, then polish

### 9.1 Composition (`shells/default/DefaultShell.tsx`)

```tsx
export interface ShellProps { nextRetryAt: number | null }      // the only prop any shell receives

export function DefaultShell({ nextRetryAt }: ShellProps) {    // eager; mirrors today's App.tsx:L19-41 'ready' branch
  const breakpoint = useBreakpoint()
  useReveal((target) => {
    // mobile breakpoint: 'viewer' | 'progress' | 'parts' | 'export' → setMobileTab('viewer');
    // 'create' | 'prompt' → setMobileTab('inputs'); 'settings' → setMobileTab('settings'); 'history' → setMobileTab('history')
    // tablet/desktop: 'history' → setHistoryOpen(true); 'parts' → scroll #parts into view; others → no-op
  })
  return (<>
    <Header breakpoint={breakpoint} />
    <BackendBanner nextRetryAt={nextRetryAt} />
    <div id="main-content" className={styles.content}><AppShell breakpoint={breakpoint} /></div>
    {breakpoint !== 'mobile' && <HistoryDrawer />}
  </>)
}
```

`nextRetryAt` comes from `useBackendSession()` in `App`. `ModeSwitch` passes it to every shell as its only prop
(`ShellProps`), which keeps `BackendBanner` working in every mode.

### 9.2 Default behaviour guarantees
- With a **legacy backend** (`/features` 404), the Default UI shows exactly today's structure plus one App
  settings gear and the token-level polish. The `ui_probe.js` fingerprint test checks this (§18).
- The Input card, the Parts panel and the part-options section appear only when `/features` exists.

### 9.3 Polish list (concrete; owners in §19)

| # | Item | Where | Acceptance |
|---|---|---|---|
| P1 | App settings gear | `Header.tsx` actions | Visible at every breakpoint; IconButton with a label; opens the dialog |
| P2 | Input source card + Prompt panel | `AppShell.tsx` desktop L51-53, tablet L80-83, mobile L122-124 | The switch toggles ReferencePanel and PromptPanel. Hidden on a legacy backend. |
| P3 | Parts panel | `AppShell.tsx` after `ModelStatsCard` (L63, L93, L133) | Panel states per §6.3. Hidden on a legacy backend. |
| P4 | Reveal mapping instead of direct tab writes | `GenerateCard.tsx:L40`, `AppShell.tsx:L139` | `requestReveal('viewer')` is used; the mobile Default tab still switches to Viewer |
| P5 | Tuner gate | `AppShell.tsx` (TuningCard ×3), `Header.tsx:L40`, `MobileTabBar.tsx:L31`, `HistoryItem.tsx:L54-60` | No tuner UI and no tuner requests with the flag off (network check in the mock) |
| P6 | **320 px toolbar clip** [V map §3.2 row 1] | `ViewerToolbar.module.css:L19` | `.modes{flex:1 1 auto;min-width:0}`. A container query (< 360 px) wraps the view-mode control onto its own row. `scrollWidth ≤ clientWidth` for all segments at 320 px. |
| P7 | **Phone landscape gets tablet layout** [row 3] | `hooks/useBreakpoint.ts` | `'mobile'` when `(max-height: 500px) and (pointer: coarse)`. At 812×375 coarse, the mobile tabs layout is used. Desktop short windows are unchanged. |
| P8 | **Short viewport chrome** [row 4] | `AppShell.module.css`, `GenerateCard.module.css`, `Header.module.css` | At `max-height: 500px`: generate bar ≤ 56 px (status text clamped to 1 line); header 48 px. Content area ≥ 220 px at 740×360. |
| P9 | **Tablet portrait** [row 2] | `AppShell.module.css:L51-65` | At `(orientation: portrait) and (min-width:768px) and (max-width:1279px)`: viewer on top, full width, `height: clamp(280px, 42dvh, 560px)`, then a 2-column card grid. Viewer width ≥ 700 px at 768×1024. |
| P10 | Viewer min-heights [rows 3, 6] | `ViewerPanel.module.css:L7`, `AppShell.module.css:L85-89` | `min-height: min(320px, 100%)`; the mobile viewer uses `min(300px, 55dvh)`. No inner overflow at 812×375. |
| P11 | Toast offset | `Toaster.module.css`, Default mobile | `--shell-inset-bottom` includes the generate bar on the inputs and settings tabs (set by `MobileLayout` through an html attribute `data-default-bar="on"`). Toasts never overlap the bar. |
| P12 | Touch targets under a coarse pointer | tokens + common css | `data-density='touch'` auto: ≥ 44 px for buttons, segments, switches (label row), slider hit area, menu items |
| P13 | Contrast | `tokens.css:L27,L31,L20` | `--color-text-subtle` → `#8a92a6` (≥ 4.6:1 on surface-4). Primary button background → `--color-accent-active` (4.62:1 with white). Add `--color-border-control: #5b6580` (3.17:1) for input boundaries [R `ui_modes_research` §9.1]. |
| P14 | Transitions | `AppShell.module.css`, `Panel.module.css`, `HistoryDrawer.module.css` | Mobile tab panels cross-fade (120 ms, `@starting-style`). Cards enter with fade plus a 4 px rise. Drawer exit animates (`allow-discrete`). Everything is zero under reduced motion. |
| P15 | Smooth scroll respects reduced motion | `AppShell.tsx:L25,L41` | `behavior: prefersReducedMotion() ? 'auto' : 'smooth'` |
| P16 | History drawer modality | `HistoryDrawer.tsx:L18-31` | `#main-content` and the header get `inert` while open. Focus cannot leave the drawer; Escape closes; focus returns. |
| P17 | Skeleton loading | `HistoryList.tsx`, `ReferenceTile.module.css` | Skeleton rows and thumbnail shimmer (static under reduced motion) instead of spinners |
| P18 | Spacing rhythm | `AppShell.module.css`, `Panel.module.css` | `scrollbar-gutter: stable` on scroll areas; panel padding `--space-4` (desktop) and `--space-3` (< 768); `scroll-padding-bottom` above the sticky generate card |
| P19 | Safe areas | `Header.module.css`, `AppShell.module.css` | Top, left and right `env(safe-area-inset-*)` on the header and mobile layout (landscape notch) |
| P20 | iOS background | `global.css:L25` | `background-attachment: fixed` is dropped under `(pointer: coarse)` |

---------------------------------------------------------------------------------------------------

## 10. Mobile shell (`shells/mobile`, lazy)

### 10.1 Structure

```
<MobileShell>  = <PhoneLayout/> (shells/shared, also used by Tablet 'compact')
  <CompactTopBar/>        logo · status dot (EngineStatus state + connection; tap → Status sheet) · App settings gear
  <BackendBanner/>        compact
  <main id="main-content" class="screen">        full-bleed, padding-bottom: var(--shell-inset-bottom)
    <ViewerPanel active={mobileSheet === null || sheetIsStandard}/>   (home; always mounted)
    <ProgressPill/>       floating top-centre while a job is queued/running or finished < 8 s ago; tap → Progress sheet
  </main>
  <FloatingNav label="Main" items=[Create, Viewer, Parts, Library] center=<GenerateFab/> hidden={softKeyboard}/>
  <Sheet id="create"   modal   snaps=['full']>  tabs Input | Settings  → InputContent / SettingsContent
                                                footer: Generate button (useGenerateAction)  ← always reachable
  <Sheet id="parts"    standard snaps=['peek','half','full']>  PartsContent + ModelStatsContent + ExportContent
  <Sheet id="library"  modal>   HistoryList onOpened → requestReveal('viewer')
  <Sheet id="progress" modal>   ProgressContent + Cancel
  <Sheet id="blocked"  modal>   GenerateBlockedContent (reason, engineProblem details, Retry loading model)
  <Sheet id="status"   modal>   StatusSheetContent (EngineStatus full, ConnectionIndicator, GpuMeter)
```

### 10.2 Floating navigation

- `<nav aria-label="Main">` contains buttons, not tabs, because the items open overlays.
- Create, Parts and Library are `aria-expanded` + `aria-controls`. Viewer is `aria-current="page"` when no sheet
  is open.
- Arrow keys give roving focus (Left/Right/Home/End), the pattern copied from
  `MobileTabBar.tsx` [V `L18-23`].
- Size: 64 px tall with 48×48 minimum items. Position: `bottom: calc(var(--floating-gap) +
  env(safe-area-inset-bottom))` with left and right safe-area insets and `max-width: 560px` (centred).
- Look: glass background (`color-mix` + `backdrop-filter`, with a solid fallback in `@supports not`).
- Badges: Create shows the reference image count; Parts shows the part count; Library shows an activity dot while
  a job is running.
- The nav is hidden while the soft keyboard is up (`useSoftKeyboard`). The Create sheet's sticky footer keeps
  Generate reachable then.

### 10.3 GenerateFab state machine (`shells/shared/GenerateFab.tsx`)

| `useGenerateAction().phase` | Visual | Tap | ARIA |
|---|---|---|---|
| `ready` | 60 px accent circle, Sparkles icon, label "Generate" under it | `run()`, then reveal viewer | `aria-label="Generate 3D model"` |
| `blocked` | dimmed, Info badge | opens the `blocked` sheet with the reason | `aria-disabled="true"`, `aria-describedby` → reason (still focusable) |
| `submitting` | spinner | – | `aria-busy="true"` |
| `queued` / `running` | `ProgressRing` (percent) around a Square icon | opens the Progress sheet (Cancel inside, with confirm) | `aria-label="Generation 42% — open progress"` |
| `cancelling` | spinner + "Cancelling" | – | `aria-busy` |

### 10.4 Sheets (`components/common/Sheet.tsx`)

```ts
export type SheetSnap = 'peek' | 'half' | 'full'              // 96px · 50dvh · calc(100dvh - top inset - 24px)
export interface SheetProps {
  id: string; title: string; open: boolean; onClose: () => void
  modal?: boolean                                             // true → <dialog>.showModal(); false → <section role="region">
  snaps?: readonly SheetSnap[]                                // default: modal ['full'], standard ['peek','half','full']
  snap?: SheetSnap; onSnapChange?: (snap: SheetSnap) => void
  headerActions?: ReactNode; footer?: ReactNode; children: ReactNode
}
```

Sheet state machine: `closed → opening → open(snap) ⇄ dragging → settling → open(snap') → closing → closed`.
- **Drag.** Only the handle and header drag, using Pointer Events with `setPointerCapture`. During the drag the
  `transform` is written to a ref (no per-frame React state). On release, velocity projection picks the nearest
  snap.
- **Handle.** A `<button>` with a 48×48 hit area. Its label is "Resize panel, currently half height".
  - Enter or Space cycles the snaps.
  - ArrowUp and ArrowDown move one snap.
  - Escape closes a modal sheet.

  This is the single-pointer alternative WCAG 2.5.7 requires.
- **Scrolling and motion.** The body has `overscroll-behavior: contain`. Entry and exit use `@starting-style` with
  `allow-discrete`. Everything is instant under reduced motion.
- **Width.** `max-width: 640px`, centred.
- **Light dismiss.** The modal sheet uses the same manual backdrop light-dismiss as `Dialog.tsx` (Safari lacks
  `closedby`) [R `ui_modes_research` §6.3].
- **Standard sheet.** No focus trap; the handle carries `aria-expanded`.

### 10.5 Mobile reveal mapping

| Target | Action |
|---|---|
| `viewer` | `openMobileSheet(null)` |
| `create` / `prompt` / `settings` | open `create`, with `createTab` = `input` (create, prompt) or `settings` |
| `progress` | open `progress` |
| `parts` / `export` | open `parts` at `half` |
| `history` | open `library` |

---------------------------------------------------------------------------------------------------

## 11. Tablet shell (`shells/tablet`, lazy)

### 11.1 Posture (automatic, no user action)

`useNavPosture()`:
- `compact` when width < 600 px;
- `rail` when width ≥ 840 px **and** landscape;
- `bottom` otherwise (portrait, or landscape below 840 px such as split screen or Stage Manager).

A typing guard holds the posture while a text field is focused, so the soft keyboard does not flip landscape and
portrait. Thresholds follow Material window classes [R `ui_modes_research` §7.1].

| Posture | Navigation | Content |
|---|---|---|
| `rail` | **Floating left `NavRail`**: 80 px wide, vertically centred glass card, `left: calc(var(--floating-gap) + env(safe-area-inset-left))`. Items: Create, Viewer, Parts, Library. Bottom group: `RailGenerateButton` (56 px, primary, ProgressRing while running) and the App settings gear. ArrowUp and ArrowDown give roving focus. | **Side panel** (`SectionHost`, `minmax(320px, 400px)`, collapsible; `container: panel`) shows the selected section. Selecting Viewer collapses the panel. The **viewer** fills the rest. `ProgressPill` floats at the top centre of the viewer. Library opens in the side panel. |
| `bottom` | **Floating bottom nav** (the same `FloatingNav` as Mobile, `max-width: 560px` centred) with `GenerateFab`. `CompactTopBar` at the top. | Viewer on top (`height: calc(100dvh - top bar - peek - nav)`), then a **standard Sheet** (`peek / half / full`) holding the selected section. A portrait tablet's half sheet still leaves a viewer ≥ 400 px tall at 768×1024. |
| `compact` | Renders `PhoneLayout`, identical to Mobile. | – |

The posture is written to `<html data-posture>` (§8.3), which drives `--shell-inset-bottom`, the toast offset and
the sheet heights.

### 11.2 Section content (shared by the rail panel and the bottom sheet)

| Section | Content |
|---|---|
| Create | Tabs Input \| Settings → `InputContent` / `SettingsContent`. A sticky footer holds a Generate button in `bottom` posture only; the rail has its own. |
| Viewer | rail: panel collapsed; bottom: sheet to `peek` |
| Parts | `PartsContent`, then the "Model" disclosure (`ModelStatsContent`), then the "Export" disclosure (`ExportContent`) |
| Library | `HistoryList` |

Reveal mapping: same targets as Mobile. In `rail`, set the section and open the panel. In `bottom`, set the section
and the sheet snap to `half`.

---------------------------------------------------------------------------------------------------

## 12. Node shell and workflow engine

### 12.1 Engine types (`src/workflow/engine/types.ts`, pure)

```ts
export const PORT_TYPES = ['image', 'masks', 'mesh', 'parts'] as const
export type PortType = (typeof PORT_TYPES)[number]
export const NODE_CATEGORIES = ['input', 'analyze', 'generate', 'parts', 'output', 'refine'] as const
export type NodeCategory = (typeof NODE_CATEGORIES)[number]
export type NodeMode = 'active' | 'bypass'

export interface InputPortDef { readonly id: string; readonly label: string; readonly accepts: readonly PortType[]; readonly required: boolean }
export interface OutputPortDef { readonly id: string; readonly label: string; readonly type: PortType }

export type CapabilityKey = 'prompt_to_3d' | 'parts' | 'segmentation' | 'part_refinement'
export interface CapabilityContext { readonly available: ReadonlySet<CapabilityKey>; readonly reasons: Readonly<Partial<Record<CapabilityKey, string>>> }

export interface NodeTypeDef {
  readonly type: string                       // namespaced, e.g. 'img3d.image_to_3d'
  readonly version: number
  readonly category: NodeCategory
  readonly title: string
  readonly description: string
  readonly inputs: readonly InputPortDef[]
  readonly outputs: readonly OutputPortDef[]
  readonly maxInstances: number               // 1 for every v1 node (one job per graph)
  readonly requires: readonly CapabilityKey[]
  readonly locked: boolean                    // refine nodes: visible as locked, never part of a run
  readonly bypass: 'none' | { readonly from: string; readonly to: string }   // pass-through mapping when bypassed
  readonly binds: 'references' | 'prompt' | 'part_options.segmentation' | 'settings' | 'part_options' | 'export' | 'none'
}

export interface GraphNode { readonly id: string; readonly type: string; readonly version: number; readonly mode: NodeMode }
export interface PortRef { readonly node: string; readonly port: string }
export interface GraphEdge { readonly id: string; readonly from: PortRef; readonly to: PortRef }
export interface Graph { readonly nodes: readonly GraphNode[]; readonly edges: readonly GraphEdge[] }
export interface Layout {
  readonly positions: Readonly<Record<string, { readonly x: number; readonly y: number }>>
  readonly collapsed: readonly string[]
  readonly viewport: { readonly x: number; readonly y: number; readonly zoom: number }
}
export interface WorkflowDoc { readonly graph: Graph; readonly layout: Layout }

export type IssueCode = 'unknown_node_type' | 'type_mismatch' | 'missing_required_input' | 'missing_required_node'
  | 'no_source' | 'multiple_sources' | 'cycle' | 'capability_missing' | 'node_locked' | 'too_many_instances'
  | 'no_sink' | 'disconnected'
export interface GraphIssue { readonly severity: 'error' | 'warning'; readonly code: IssueCode; readonly message: string
  readonly nodeId?: string; readonly portId?: string; readonly edgeId?: string }
```

**The graph holds no parameter values.** Every parameter lives in the shared stores (`binds`). The Default,
Mobile, Tablet and Node UIs therefore always edit the same form.

### 12.2 Node catalogue (`src/workflow/nodes/*.ts`)

| `type` | Title | Category | Inputs | Outputs | Binds to | Requires | Bypass | Inspector content | Node-body summary (pure `summary(ctx)`) |
|---|---|---|---|---|---|---|---|---|---|
| `img3d.input_image` | Input Image | input | – | `image: image` | referenceStore | – | none | `ReferenceContent` | "3 images · primary: front" / "No image yet" |
| `img3d.prompt` | Prompt | input | – | `image: image` (generated) | inputStore.prompt | `prompt_to_3d` | none | `PromptContent` | first 60 characters, or "Empty prompt" |
| `img3d.segmentation` | Segmentation | analyze | `image` (image, required) | `masks: masks` | partOptions (segmentation, subject) | `segmentation` | none (bypass = off) | `PartOptionsSection mode="segmentation"` + overlay image if any | "Auto · anime character" / "Off" |
| `img3d.image_to_3d` | Image-to-3D | generate | `image` (image, required) | `mesh: mesh` | settingsStore | – | none | `SettingsContent` (no part section) | "1024 · 500k tris · 2K tex" |
| `img3d.part_extraction` | Part Extraction | parts | `mesh` (mesh, required), `masks` (masks, optional) | `parts: parts` | partOptions (strategy, separate, export flags) | `parts` | `{from:'mesh', to:'parts'}` (mesh passes through) | `PartOptionsSection mode="parts"` + `PartsContent` (results) | "Regions + 6 physical · regions_only" |
| `img3d.export` | Export | output | `asset` (mesh or parts, required) | – | partOptions.export flags | – | none | `ExportContent` | "GLB + parts GLB + manifest" |
| *(refine slot)* | "Refinement — locked" palette group | refine | – | – | – | `part_refinement` | – | explanation | – |

`refineSlot.ts` registers **no node types**. It exports `REFINE_PALETTE_GROUP = { title: 'Refinement',
locked: true, reason: 'Tuning and refinement are paused until you approve them.' }`. Any future refine `NodeTypeDef`
(category `refine`, `locked: true`, `requires: ['part_refinement']`) is accepted by `createRegistry`, rendered
locked in the palette and on the canvas, rejected by validation as `node_locked` when active, and never part of a
run.

Port shapes and colours (WCAG 1.4.1: shape plus label, not colour alone): image ● cyan (`--port-image`),
masks ■ green (`--port-masks`), mesh ▲ violet (`--port-mesh`), parts ⬢ pink (`--port-parts`). Edges take the
source port colour.

### 12.3 Engine functions

```ts
// registry.ts
export interface NodeRegistry {
  get(type: string): NodeTypeDef | undefined
  list(): readonly NodeTypeDef[]
  palette(ctx: CapabilityContext): readonly { category: NodeCategory; entries: readonly { def: NodeTypeDef; enabled: boolean; reason: string | null }[] }[]
}
export function createRegistry(defs: readonly NodeTypeDef[]): NodeRegistry     // throws on duplicate type ids

// graph.ts — immutable ops (each returns a new Graph)
export function addNode(g: Graph, def: NodeTypeDef, id: string): Graph
export function removeNodes(g: Graph, ids: readonly string[]): Graph           // also removes incident edges
export function connect(g: Graph, from: PortRef, to: PortRef, id: string): Graph   // replaces an existing edge into `to`
export function disconnect(g: Graph, edgeId: string): Graph
export function setMode(g: Graph, nodeId: string, mode: NodeMode): Graph
export function nextId(g: Graph, prefix: 'n' | 'e'): string                    // deterministic: max numeric suffix + 1

// validate.ts
export type ConnectResult = { ok: true } | { ok: false; code: IssueCode; message: string }
export function canConnect(g: Graph, r: NodeRegistry, from: PortRef, to: PortRef): ConnectResult
//   output type ∈ accepts · no self-loop · no cycle (DFS from to.node must not reach from.node)
export function validateGraph(g: Graph, r: NodeRegistry, ctx: CapabilityContext): readonly GraphIssue[]

// plan.ts
export function topoOrder(g: Graph): { order: readonly string[]; cyclic: readonly string[] }   // Kahn, ties by id

// derive.ts — the ONLY place that turns topology into pipeline semantics
export function deriveConfig(g: Graph, r: NodeRegistry, ctx: CapabilityContext):
  { config: PipelineConfig | null; issues: readonly GraphIssue[] }

// template.ts
export function templateFromConfig(config: PipelineConfig, r: NodeRegistry): WorkflowDoc   // deterministic ids + positions
export function reconcile(doc: WorkflowDoc, config: PipelineConfig, r: NodeRegistry): WorkflowDoc
//   minimal edit so deriveConfig(result) === config (sets modes / reconnects source / inserts missing nodes)

// status.ts
export type NodeRunState = 'idle' | 'queued' | 'running' | 'done' | 'warning' | 'failed' | 'skipped' | 'bypassed' | 'locked' | 'unavailable'
export interface NodeStatusView { state: NodeRunState; percent: number | null; message: string | null }
export interface RunSnapshot { job: Job | null; analysis: PartAnalysisOut | null }
export function projectRunState(g: Graph, r: NodeRegistry, run: RunSnapshot, ctx: CapabilityContext): Readonly<Record<string, NodeStatusView>>

// serialize.ts
export const WORKFLOW_SCHEMA = 'img3d.workflow'
export const WORKFLOW_VERSION = 1
export function toJSON(doc: WorkflowDoc, appVersion: string): string
export function fromJSON(text: string, r: NodeRegistry): { doc: WorkflowDoc | null; issues: readonly GraphIssue[] }
//   validates shape; keeps unknown node types as-is (reported 'unknown_node_type'); no secrets; size cap 256 KB
```

**`deriveConfig` rules (v1: one job per graph):**
1. Image-to-3D: exactly one, active. Otherwise `missing_required_node` or `too_many_instances`.
2. Source: exactly one *active* source node (Input Image or Prompt) with a path to Image-to-3D's `image` input.
   - zero sources → `no_source`;
   - two connected sources → `multiple_sources`;
   - a disconnected source node gives a `disconnected` warning only, so both sources can sit on the canvas.
3. `segmentation = true` iff an active Segmentation node gets `image` from the chosen source **and** its `masks`
   reach an active Part Extraction.
4. `partExtraction = true` iff an active Part Extraction gets `mesh` from Image-to-3D and its output reaches Export.
5. Export: exactly one, connected (`no_sink` otherwise).
6. Capability gating: an active node whose `requires` are not available gives `capability_missing` (error) with
   the backend reason. A bypassed node is exempt.
7. Locked nodes: `node_locked` (error) when active. A cycle gives `cycle` and lists the node ids.

**`projectRunState` mapping.** It projects the active job if one is running, otherwise the viewed job.

| Node | Source of truth (first available) | Legacy fallback (no `steps`) |
|---|---|---|
| Input Image / Prompt | `job.steps['input_image' / 'prompt']` | queued → queued; running with `stage_index ≤ 1` → running (prompt) or done (image); `stage_index > 1` → done; failed at stage ≤ 1 → failed |
| Segmentation | `job.steps.segmentation` → `analysis.steps.segmentation` | `bypassed` if the config has it off; `idle` |
| Image-to-3D | `job.steps.image_to_3d` | queued or waiting → queued; stages 1–8 → running with `progress.percent`; completed → done; failed or cancelled → failed or skipped |
| Part Extraction | `job.steps.part_extraction` → `analysis.steps.part_extraction` (+ `analysis.progress.percent`) | `bypassed` / `idle` |
| Export | `job.steps.export` → analysis `export` step | done when the job is completed (and the analysis too, if requested) |
| any node whose capability is missing | – | `unavailable` |
| refine | – | `locked` |

### 12.4 `store/workflowStore.ts` (lazy, persisted `img3d.workflow` v1)

```ts
interface WorkflowState {
  doc: WorkflowDoc
  past: WorkflowDoc[]                 // max 50, not persisted
  future: WorkflowDoc[]               // not persisted
  selection: string | null
  issues: readonly GraphIssue[]       // last validateGraph result
  connect: (from: PortRef, to: PortRef) => ConnectResult
  disconnect: (edgeId: string) => void
  addNode: (type: string, position: { x: number; y: number }) => void
  removeNodes: (ids: readonly string[]) => void
  setNodeMode: (id: string, mode: NodeMode) => void
  moveNodes: (positions: Readonly<Record<string, { x: number; y: number }>>) => void   // layout only, no history entry
  setCollapsed: (id: string, collapsed: boolean) => void
  setViewport: (viewport: Layout['viewport']) => void
  select: (id: string | null) => void
  undo: () => void
  redo: () => void
  resetTemplate: () => void            // templateFromConfig(configFromForms(stores))
  importJson: (text: string) => GraphIssue[]
  exportJson: () => string
  syncFromStores: () => void           // on Node shell mount: reconcile(doc, configFromForms(stores))
}
```

**Write-through.** After every topology or mode mutation:

```
{config, issues} = deriveConfig(doc.graph, registry, capabilityContext(features))
if config     → applyPipelineConfig(config); navigationStore.setPipelineBlock(null)
else          → navigationStore.setPipelineBlock(firstError(issues).message)
```

The Node shell clears `pipelineBlock` on unmount, so Default, Mobile and Tablet are never blocked by a stale graph.

### 12.5 Node shell UI (`shells/node`, lazy chunk with `@xyflow/react@12.12.0`, exact pin)

**Layout by width.**

*Expanded or large (≥ 1024 px):*

```
┌──────────────────────── NodeToolbar ───────────────────────────────────────────────────────────┐
│ Run/Cancel (useGenerateAction) · status chip · + Add · Fit · Tidy · Undo · Redo · Canvas|List · │
│ Import · Export JSON · Reset · Library · App settings                                          │
├───────────────────────────────────────────┬────────────────────────────────────────────────────┤
│ NodeCanvas (React Flow)                   │ NodeInspector (360–420px, container: panel)         │
│   six pipeline nodes, typed handles,      │   selected node → its feature Content              │
│   MiniMap (pref), Controls (zoom buttons) │   none selected → ProgressContent + Problems        │
├───────────────────────────────────────────┤                                                    │
│ ViewerDock (resizable 30–60% height, collapsible; the ONE ViewerPanel)                          │
└───────────────────────────────────────────┴────────────────────────────────────────────────────┘
```

*Medium (600–1023 px):* the Inspector becomes a right-side overlay drawer. The ViewerDock becomes a toggle
(Canvas | Viewer).

*Compact (< 600 px), or a coarse pointer below 900 px:* the default view is **List** (`NodeListView`). Canvas
remains a toggle. The preference `node.view` (`auto`, `canvas`, `list`) overrides this.

**Adapter (`flowAdapter.ts`, `NodeCanvas.tsx`, `PipelineNode.tsx`, `PortHandle.tsx`). These are the only files that
import `@xyflow/react`.**
- `nodeTypes = { pipeline: PipelineNode }` at module scope.
- `toFlowNodes(doc, selection)` and `toFlowEdges(doc)` are pure mappers, recomputed per render (the compiler
  memoizes them). There are six nodes, so cost is trivial.
- `onNodesChange` → `moveNodes` and `select` (position and selection only). `onConnect` → `connect`.
  `isValidConnection` → `canConnect`. `onNodesDelete` → `removeNodes`. `onEdgesDelete` → `disconnect`.
- Settings: `connectOnClick` (tap-tap connect), `zoomOnPinch`, `panOnDrag`, `selectionOnDrag={false}`,
  `zoomOnDoubleClick={false}`, `minZoom 0.25`, `maxZoom 2`, and `connectionRadius` of 32 under a coarse pointer
  (20 otherwise).
- Theme: `@xyflow/react/dist/base.css` plus `flow.module.css`, which maps `--xy-*` variables to tokens
  [R `ui_modes_research` §5]. `colorMode="dark"`. The attribution is kept and restyled (open question §21).
- `PipelineNode`:
  - header: category stripe, title, mode chip (Active / Bypassed), status chip (icon plus text) and a collapse
    button;
  - body: `useNodeSummary(kind)` lines; an inline `ProgressBar` (`role="progressbar"`) while running; an issue
    chip;
  - 2D thumbnail only: the job preview for source nodes and the overlay PNG for Segmentation (no 3D in nodes,
    which protects the viewer singleton);
  - ARIA: `role="group"` with `aria-label` such as "Image-to-3D, running 45%".

  Node bodies hold **no** editable widgets. Editing happens in the Inspector, which avoids duplicate controls and
  `nodrag` hazards.
- `useNodeRunState(nodeId)` selects one primitive status string and one percent number, so progress ticks
  re-render only that node.

**Interactions and shortcuts** (`nodeShortcuts.ts`; canvas-scoped and ignored inside text inputs):

| Keys | Action |
|---|---|
| Ctrl/Cmd+Enter | Run (the global shortcut; one registration) |
| `/` or Ctrl+K | Add-node palette (`role="dialog"` + `combobox` / `listbox`; filtered by compatible types when opened from a dropped connection) |
| Alt+C | Collapse the node |
| Ctrl+B | Toggle bypass |
| Ctrl+Z, Ctrl+Y (or Ctrl+Shift+Z) | Undo, redo |
| `.` | Fit view |
| Delete / Backspace | Remove the selection |
| Enter on a node | Open the Inspector |

- Double-click on the pane opens the palette.
- On touch, a long-press (500 ms, cancelled by more than 8 px of movement) opens the palette or the node menu.
- A "+" button is always present in the toolbar.

**List view (`NodeListView`).**
- Nodes appear in topological order as cards: title, status, mode switch, summary, an "Edit" button that opens the
  inspector (a sheet on compact) and the issues.
- Each input port is a `<select>` of compatible upstream outputs, so connecting works without dragging
  (WCAG 2.5.7).
- An "Add node" menu is included.
- This is also the canonical screen-reader representation. The canvas has `aria-describedby` pointing to a graph
  summary: node count, issues and the running node.

**Problems panel.** A collapsible list of `issues`. Each item focuses its node. Run shows the issue count and is
`aria-disabled` while `pipelineBlock` is set.

**Template.** `templateFromConfig` lays out the default graph in columns:

| Column | Nodes |
|---|---|
| 0 | Input Image (top) and Prompt (bottom; disconnected when the source is image, with a "Use this source" button that rewires in one click) |
| 1 | Segmentation |
| 2 | Image-to-3D |
| 3 | Part Extraction |
| 4 | Export |

Part Extraction and Segmentation are `bypass` when `partOptions.enabled` is false. Exactly one source is ever
connected, so prompt and image input are both one click away.

**Partial re-run (optional).** On a completed viewed job, when `features.parts.rerun` is true, the Part Extraction
node's context menu offers "Run from here", which calls `analyzePartsForViewedJob()`. Nothing else re-runs
client-side; execution stays on the backend.

**Workflow files.** Export and Import produce or read `.img3d-workflow.json` (schema `img3d.workflow` v1, layout
plus topology, no parameter values or ids of user data). Import validates and reports issues. It never crashes on
unknown node types.

```json
{ "schema": "img3d.workflow", "version": 1, "app": "img3d-frontend@0.0.0",
  "graph": { "nodes": [{ "id": "n1", "type": "img3d.input_image", "version": 1, "mode": "active" },
                       { "id": "n3", "type": "img3d.image_to_3d", "version": 1, "mode": "active" }],
             "edges": [{ "id": "e1", "from": { "node": "n1", "port": "image" }, "to": { "node": "n3", "port": "image" } }] },
  "layout": { "positions": { "n1": { "x": 0, "y": 0 }, "n3": { "x": 640, "y": 0 } }, "collapsed": [],
              "viewport": { "x": 0, "y": 0, "zoom": 1 } } }
```

### 12.6 Segmentation preview (optional; only when `features.parts.segmentation_preview`)

The Segmentation inspector can offer "Preview masks" on the current primary image. This uses a backend endpoint
that is not specified here (`backend_contract_map` mentions
`POST /reference-sets/{set}/images/{img}/segmentation`). When the flag is false, no button is rendered. Open
question §21.

---------------------------------------------------------------------------------------------------

## 13. Tuner gating (UI only; no tuning code)

| Choke point | File | Behaviour when `useTunerEnabled()` is false |
|---|---|---|
| Blender setup dialog | `app/GlobalOverlays.tsx` | Not mounted, so no auto-open [V `frontend_architecture_map` §7 row 1] |
| Header Blender pill | `components/layout/Header.tsx:L40` | Not rendered |
| TuningCard in three layouts | `components/layout/AppShell.tsx` | Not rendered; `useRevealTuning` is not called (`useRevealTuning` is split into a gated child component so the hook order stays static) |
| Viewer Original/Tuned, secondary preload, TunedStats | `hooks/useViewedTuning.ts` | Returns `{latestRun:null, tunedRun:null, variant:'original'}` |
| History per-job tuning fetch and marker | `components/history/HistoryItem.tsx:L54-60` | The effect returns early; no marker |
| Viewer tab pulse | `components/layout/MobileTabBar.tsx:L31` | `tuning` is false |
| Session polling, WS-triggered loads, resync | `hooks/useBackendSession.ts` | Skipped (§5.6). WS handlers stay; they are harmless. |
| Node palette | `workflow/nodes/refineSlot.ts` | Locked group, no addable nodes |
| Mobile, Tablet and Node shells | – | Never import `components/meshTuner/*` |

Gate: `BUILD_FLAGS.meshTuner && features.tuning.mesh_tuner.state === 'available'`. Both are false in this
milestone, so the tuner is hidden even on an old backend without `/features`. Re-enabling later needs both the
build flag and the backend's approval switch. Nothing is deleted. `meshTuner/*` and `meshTunerStore` keep working
under the mock with `VITE_FEATURE_MESH_TUNER=true` plus a mock `--features full` where the tuner state is
`available`.

---------------------------------------------------------------------------------------------------

## 14. Responsiveness system

- **Width classes** (`lib/viewport.ts`): compact < 600, medium 600–839, expanded 840–1199, large ≥ 1200.
  - **short** = height ≤ 500;
  - **coarse** = `(pointer: coarse)`;
  - **lowMemory** = `navigator.deviceMemory ≤ 4`, or coarse with width < 1024.

  Default keeps `useBreakpoint` (768 / 1280), plus fix P7.
- **Containers, not viewports, inside features.** Every Content root declares `container-type: inline-size`, so
  one component works in a 360 px rail panel, a 640 px sheet or a desktop column.
- **No fixed heights above the available space.** Use `clamp()` with `dvh` and `min(…, 100%)`. The rule:
  `min-height` never exceeds the container (P10).
- **Safe areas** on every fixed element (top bar, nav, rail, FAB, sheets, toasts) with `env(…, 0px)` fallbacks.
- **Mode on any device.**
  - Mobile on a 1920 px screen: full-bleed viewer, centred nav (max 560 px), centred sheets (max 640 px).
  - Tablet on a desktop: rail posture.
  - Node on a phone: List view.
  - Default on a phone: today's mobile tabs.
  - Every combination passes the probe matrix (§18).
- **Viewport probe matrix:** 320×640, 375×812, 390×844, 740×360, 812×375, 768×1024, 1024×768, 1280×800 and
  1920×1080, each × 4 modes.

---------------------------------------------------------------------------------------------------

## 15. Accessibility checklist (acceptance)

1. Every function works by keyboard (2.1.1). The Node UI has the React Flow focus model plus List view. The only
   traps are modal dialogs and sheets, and Escape closes them (2.1.2).
2. A visible focus ring on every control. The `#8b6cff` ring is ≥ 3:1 against the surrounding colours (2.4.7,
   1.4.11).
3. Focus is never hidden by the floating nav, rail, FAB or a peek sheet (2.4.11). Use `scroll-padding-bottom:
   var(--shell-inset-bottom)` on scroll containers.
4. Target size: ≥ 24 px everywhere (2.5.8); 48 px in Mobile and Tablet; 44 px under a coarse pointer in Default.
   React Flow handles get a 44 px hit area through `::before`.
5. Every drag has a single-pointer alternative (2.5.7):
   - sheet handle: tap or arrow keys;
   - node connect: tap-tap or List view selects;
   - node move: arrow keys or "Tidy";
   - pinch zoom: zoom buttons (2.5.1).
6. Colour is never the only signal (1.4.1). Port shape plus label; status icon plus text; licence badge text; part
   kind text chip; validation messages in text.
7. Contrast tokens are fixed per P13. Text ≥ 4.5:1 (1.4.3). Components ≥ 3:1 (1.4.11).
8. Names, roles and values (4.1.2):
   - nav buttons with `aria-expanded` or `aria-current`;
   - rail `nav` with roving focus;
   - FAB with `aria-label` and `aria-disabled` plus `aria-describedby` when blocked;
   - sheets as `<dialog aria-labelledby>` or a region with the handle's `aria-expanded`;
   - nodes as `role="group"` with a status label;
   - progress bars with `aria-valuenow`;
   - part toggles with `aria-pressed` and explicit labels;
   - UI-style picker as `role="radiogroup"`.
9. Status messages (4.1.3) go through one polite live region per shell: job start, done or error; part analysis
   done; validation summary; part hidden or shown. Per-second timers are never announced (existing rule).
10. Reduced motion: CSS uses the `--duration-*` tokens (zeroed). JS smooth scroll, sheet spring, animated edges and
    the FAB ring respect `prefersReducedMotion()`. The `data-motion='reduce'` user override is honoured.
11. Orientation is never locked (1.3.4). Every mode reflows at 320 px without 2-D scrolling (1.4.10). The Node
    canvas is exempt as 2-D content; List view reflows.
12. Labels are consistent across shells (3.2.3, 3.2.4) through `FEATURE_META`: Create, Viewer, Parts, Library,
    App settings.
13. The prompt has a visible label. Errors explain how to fix them (3.3.1, 3.3.3).
14. Each shell has a skip link target: `#main-content`.

---------------------------------------------------------------------------------------------------

## 16. Performance budget

Baseline from `vite build` [R `frontend_architecture_map` §8.4]: initial JS about 180 kB gzip (`index` 110.0 +
`react` 68.4 + `vendor` 1.9); CSS 14.6 kB gzip; `three` 206.9 kB and `r3f` 79.9 kB lazy.

| Chunk | Budget (gzip) | Enforced by |
|---|---|---|
| Initial JS (index + react + vendor + Default shell + stores + pure libs) | **≤ 195 kB** (+15 kB) | `bundle_budget.mjs` |
| Initial CSS | ≤ 18 kB | `bundle_budget.mjs` |
| App settings dialog body (lazy) | ≤ 8 kB | `bundle_budget.mjs` |
| Mobile shell + `shells/shared` | ≤ 22 kB | `bundle_budget.mjs` |
| Tablet shell (excluding shared) | ≤ 12 kB | `bundle_budget.mjs` |
| Node shell own code + workflow engine + `workflowStore` | ≤ 35 kB | `bundle_budget.mjs` |
| `nodeui` vendor group (`@xyflow`, `d3-*`, `classcat`, nested zustand@4) | ≤ 65 kB, **never in initial** | `bundle_budget.mjs` (initial-chunk membership check) |
| `ModelViewer` chunk growth for parts | ≤ +6 kB | `bundle_budget.mjs` |
| `three` / `r3f` | unchanged (± 1 kB) | `bundle_budget.mjs` |

`vite.config.ts` gains a group at priority 35:

```ts
{ name: 'nodeui', test: /node_modules[\\/](@xyflow[\\/]|d3-(color|dispatch|drag|ease|interpolate|selection|timer|transition|zoom)[\\/]|classcat[\\/])/, priority: 35 }
```

The current `vendor` regex `/node_modules[\\/](zustand|use-sync-external-store)[\\/]/`
[V `vite.config.ts:L77`] also matches the nested `@xyflow/react/node_modules/zustand`, which would pull zustand@4
into the initial chunk. Anchor it on the top-level `node_modules` instead:
`/[\\/]frontend[\\/]node_modules[\\/](zustand|use-sync-external-store)[\\/]/`. Then verify with the budget script's
initial-chunk membership check.

Runtime budgets, checked manually in the browser pane with the Performance panel and `performance.now()` marks:

| Interaction | Budget |
|---|---|
| Mode switch with a warm chunk | ≤ 150 ms to the first shell paint (desktop) |
| Cold chunk | ≤ 600 ms on localhost |
| Sheet open | animation starts in the same frame; no layout thrash (transform/opacity only) |
| Part visibility, isolate or highlight | ≤ 1 frame (16 ms). One 1 KB texture upload; no React re-render of `ModelScene`; no GLB reload. |
| `job.updated` progress tick (≤ 5/s) | re-renders only progress consumers (ProgressContent, ProgressPill, FAB ring, the running node), never a whole shell. Check with React DevTools "highlight updates" during a mock job. |
| Node canvas, 6 nodes | first render ≤ 50 ms; drag at 60 fps |
| Memory | `lowMemory` devices never hold the original and parts GLBs at the same time (§7.2) |
| Persistence | preference writes are synchronous and tiny. Workflow layout writes are throttled to one per 500 ms during drags (write on `onNodeDragStop`, not per move). |

---------------------------------------------------------------------------------------------------

## 17. Quality gates (run after every slice; PowerShell)

```powershell
cd E:\14-OCT-2026\Img3D\frontend
npx oxlint --max-warnings 0                                  # 0 warnings / 0 errors
npx tsc -p tsconfig.app.json --noEmit                        # 0 errors
npx tsc -p tsconfig.node.json --noEmit                       # 0 errors
npx vite build --outDir $env:TEMP\img3d_build --emptyOutDir  # never touches frontend\dist during slices
cd E:\14-OCT-2026\Img3D
node test\frontend\review\compiler_check.mjs                 # [] and non-success events 0 (compiled count grows)
node test\frontend\review\import_boundaries.mjs              # 0 violations (allowlist printed)
node test\frontend\review\bundle_budget.mjs $env:TEMP\img3d_build   # all budgets green; nodeui not in initial
node --experimental-strip-types --test test\frontend\unit\    # all pure-module tests green
node test\frontend\mock_backend.mjs --port 8765 --engine-ready --seed-jobs 2 --features full --parts-scenario ok
node test\frontend\ui_modes_mock_smoke.mjs --base http://127.0.0.1:8765              # HTTP contract smoke
node test\frontend\mesh_tuner_mock_smoke.mjs                 # tuner contract still valid (mock unchanged for tuner)
# UI: vite preview against the mock, then run test\frontend\review\ui_probe.js in the browser pane (see §18)
```

Gate rules:
- No new bail-outs in `compiler_check`. If an xyflow-facing component has to opt out (`'use no memo'`), the reason
  is recorded in the file header and in the slice report.
- No `try/finally`, `?.`, `??` or `&&` inside `try` blocks in components.
- No new object or array from zustand selectors (reviewers grep for `useShallow`; it is allowed only with a
  comment).
- **No placeholder data**: a review grep forbids literal part names or model ids in `src/` outside type unions
  and `featureMeta`. The test is a negative check in `import_boundaries.mjs`.

---------------------------------------------------------------------------------------------------

## 18. Test plan

| File | Kind | Asserts |
|---|---|---|
| `test/frontend/unit/job_request.test.mjs` | node:test (strip-types) | (1) Image source with parts off gives an output **deep-equal and key-order-equal** to `{reference_set_id, primary_image_id, settings: toRequestSettings(form, mode)}`, with no `source` or `parts` key, for 6 form fixtures including advanced overrides. (2) A prompt request carries `source.type='prompt'` and no `reference_set_id` or `primary_image_id`. `negative_prompt` is omitted when unsupported or empty; `model_id` is omitted when null; `image_seed` is omitted when null. (3) Parts enabled produces `settings.parts` with `enabled:true` and the default strategy and separate list from features. It is never `null` or `enabled:false`. (4) The error codes `no_references`, `empty_prompt`, `prompt_too_long`, `prompt_unavailable`, `parts_unavailable` and `strategy_unavailable`. (5) A trimmed prompt. (6) Round trip: `job.settings` from a fixture produced by the builder passes again through `toRequestSettings` shape checks (unknown keys none). |
| `test/frontend/unit/generate_availability.test.mjs` | node:test | Image-source reasons and notes are **byte-identical** to the strings in today's `useGenerateAvailability.ts` for 9 scenarios. The prompt rules come in the documented order. `pipelineBlock` precedence. Parts unavailable blocks only when enabled. `code` values. |
| `test/frontend/unit/ui_modes.test.mjs` | node:test | `parseUiOverride` (valid, invalid, case, repeated param). `sanitizePreferences` drops unknown and corrupt values and keeps valid ones. `resolveUiMode` lets the override win. `recommendUiMode` table. `resolveDensity` table. `classifyViewport` and `navPosture` tables, including 812×375 coarse → bottom (Tablet) and 1024×768 → rail. |
| `test/frontend/unit/feature_flags.test.mjs` | node:test | `normalizeFeatures` for 200 (full, minimal), 404 → `unsupported`, a bad shape → `error`. `isTunerVisible` truth table (build × state). `selectableT2IModels` filters by state. |
| `test/frontend/unit/parts_model.test.mjs` | node:test | `normalizeManifest` on both research drafts gives the same `PartSet`. The `regions[]` merge. Unknown kind skipped plus a warning. Invalid ids and `..` paths dropped. The 512 cap. Relative URL resolution. `displayStates` for hidden, isolated, selected and hovered combinations. `hierarchy` depth-first order. `idsOfKind`. |
| `test/frontend/unit/steps.test.mjs` | node:test | `stepsForRun`: null for legacy jobs. Job steps merged with analysis steps in canonical order. Unknown step ids kept. |
| `test/frontend/unit/workflow_engine.test.mjs` | node:test | `createRegistry` rejects duplicates. The `canConnect` matrix (4 port types × 6 inputs). Cycle rejection. `validateGraph` issue codes (each code covered). `deriveConfig` for the 8 canonical topologies, including two sources with one disconnected (OK plus warning), bypassed parts (`partExtraction:false`) and a missing Export (`no_sink`). `templateFromConfig`→`deriveConfig` gives back the same config for all 2×2×2 configs. `reconcile` is minimal and idempotent. `projectRunState` from legacy-job fixtures (each stage), from `job.steps` and from analysis steps. A refine def is locked and never in the config. `toJSON`→`fromJSON` round trip. Unknown types are preserved. The size cap. Only `.ts` relative imports, with no React or zustand import (static scan). |
| `test/frontend/ui_modes_mock_smoke.mjs` | Node HTTP + WS client against the mock | `/features` shape per scenario. A prompt job is accepted by the mock with `source`; an unknown field gets 422 (the mock mirrors `extra=forbid`). An image job with `settings.parts` gets 202, followed by `part_analysis.updated` frames → completed. Manifest and assembled GLB fetch: the GLB parses, node names match the manifest, and the `_FEATURE_ID_0` accessor exists. `--features none` returns 404 for `/features`. The legacy flow is unchanged. Writes `test/outputs/frontend/ui_modes_smoke.json`. |
| `test/frontend/mock/part_aware_mock.mjs` | test double (not a test) | `registerPartAwareRoutes(ctx)`, `featuresFor(scenario)`, `buildPartsGlb()` (a multi-node GLB: body with a `_FEATURE_ID_0` attribute (3 regions) plus 4 physical child nodes with `extras.img3d.part_id`), `manifestFor(job)`. Flags: `--features none\|basic\|full`, `--t2i-scenario ready\|not_installed`, `--parts-scenario ok\|fail\|slow\|none`. |
| `test/frontend/review/import_boundaries.mjs` | static scan | The §3.2 rules. `@xyflow` appears only under `shells/node`. `three` only in the viewer lazy files. Pure modules use `.ts` relative imports only. No `components/meshTuner` import outside the allowlisted gated sites. The no-placeholder-data grep. |
| `test/frontend/review/bundle_budget.mjs` | build output scan | §16 budgets (gzip via `zlib`). The initial chunk set comes from `index.html` `<script>`/`modulepreload`. `nodeui` is absent from the initial set. |
| `test/frontend/review/ui_probe.js` | DOM probe pasted into the browser pane (`javascript_tool`) | For the current viewport: `scrollWidth === innerWidth`; count of interactive elements under 44 px (Mobile and Tablet must be 0, Default under coarse 0); focusable elements obscured by the nav (should be 0); presence of `#main-content`; html `data-*` attributes; `fingerprint()` = ordered list of landmark and section labels plus button names. The Default fingerprint on `--features none` must equal the stored baseline plus "App settings". |
| Manual matrix | browser pane | §14 viewport list × 4 modes, via `?ui=`. A screenshot per cell into the scratchpad, not the repo. Keyboard-only pass per shell. Reduced-motion pass. |

---------------------------------------------------------------------------------------------------

## 19. Implementation plan: slices with disjoint file ownership

**Prerequisite (the user's action):** commit or stash the uncommitted tuner edits in `frontend/src/api/types.ts`,
`lib/meshTuner.ts`, `store/meshTunerStore.ts` and `components/meshTuner/BlenderDownloadPanel.tsx`
[V `git status`]. Slice A1 edits `api/types.ts`. No slice touches the other three.

The backend contract (§4) must be reconciled and written into `api-contract.md` by the backend workstream before A1
merges. A1 can code against §4 and switch names in one place if they change.

**Rules for every slice:**
- Each wave lists slices that can run in parallel, and no two slices in one wave own the same file.
- A file may be owned again in a later wave only after the earlier slice is merged.
- Every slice ends with the §17 gates green.
- With a legacy backend (`--features none`), the Default UI must stay pixel-equivalent apart from the planned
  polish.

### Wave A: foundations (parallel)

| Slice | Owns (N = new, M = modified) | Acceptance |
|---|---|---|
| **A1 Contract types, API client, mock** | M `src/api/types.ts` (additive block only), M `src/api/ws.ts`; N `src/api/{featuresTypes,partsTypes,features,parts,files}.ts`; N `test/frontend/mock/part_aware_mock.mjs`; M `test/frontend/mock_backend.mjs` (import + flags + `job.kind/source/steps` echo + `extra=forbid` emulation for the new fields); N `test/frontend/ui_modes_mock_smoke.mjs` | tsc and lint green. No caller changes, so the app behaves identically. The smoke passes for `--features full/basic/none` × parts scenarios. `mesh_tuner_mock_smoke.mjs` still passes. The `api/parts.ts` adapter handles `api:'runs'` and `api:'job'` (job variant against a mock flag `--parts-api job`). |
| **A2 Tokens, modes CSS, primitives** | M `src/styles/{tokens,global}.css`; N `src/styles/modes.css`; M `src/components/common/{Button,SegmentedControl,Switch,RangeSlider,NumberField,Dialog,Toaster,Panel,Menu}.module.css`; N `src/components/common/{Sheet,FloatingNav,NavRail,Skeleton,RadioCardGroup,ProgressRing}.tsx` (+ `.module.css`); N `src/lib/motion.ts` | With no `data-density`, the desktop Default looks the same except the P13 contrast change. With `data-density=touch` set by hand, every control is ≥ 44 px. Sheet: keyboard snap cycling, Escape, backdrop dismiss, focus return; no React state per drag frame (code review). Compiler check is 0. |
| **A3 Workflow engine (pure) + domain** | N `src/domain/pipeline.ts`; N `src/workflow/engine/*.ts`, `src/workflow/nodes/*.ts`; N `test/frontend/unit/workflow_engine.test.mjs` | Unit tests are green. Only `.ts` relative imports. No react, zustand, axios or xyflow. The `deriveConfig` and `template` round trip covers all 8 configs. |
| **A4 Gate scripts** | N `test/frontend/review/{import_boundaries.mjs,bundle_budget.mjs,ui_probe.js}` | They run on the current tree. The boundaries pass with the documented allowlist. The budget passes on the baseline build. The probe returns the JSON report at 3 viewports on today's UI, and that output is stored as the baseline fingerprint in the probe file's header comment. |

### Wave B: shared logic and state

| Slice | Owns | Depends | Acceptance |
|---|---|---|---|
| **B1 Pure libraries + tests** | N `src/lib/{uiModes,viewport,featureFlags,buildFlags,jobRequest,settingsRequest,generateAvailability,partsModel,steps,featureMeta}.ts`; M `src/store/settingsStore.ts` (move `toRequestSettings` and `SAMPLER_FIELDS`, re-export); M `src/lib/storage.ts` (keys); M `src/vite-env.d.ts`; N `test/frontend/unit/{job_request,generate_availability,ui_modes,feature_flags,parts_model,steps}.test.mjs` | A1 (types), A3 (`PipelineConfig`) | All unit tests are green. `toRequestSettings` behaviour is unchanged (the existing imports still compile). The byte-identical request test passes. |
| **B2 Stores, actions, hooks, session** | N `src/store/{preferencesStore,navigationStore,featuresStore,inputStore,partOptionsStore,partStore,requestFromStores}.ts`; M `src/store/jobStore.ts`; N `src/actions/{generation,downloads,parts,pipeline}.ts`; N `src/hooks/{useGenerateAction,useViewport,useNavPosture,useResolvedUiMode,useReveal,useFeatures,useTunerEnabled,usePartsView,useSoftKeyboard}.ts`; M `src/hooks/{useGenerateAvailability,useBackendSession,useViewedTuning}.ts` | A1, B1 (B1 lands its exported signatures first) | The app renders today's Default UI unchanged. Against the mock (flag off), **no request** to `/blender/*` or `/tunings/*` shows in the browser pane network log. `/features` is fetched at sync and on hello. `part_analysis.updated` frames update `partStore` (dev tools check). The legacy mock (`--features none`) shows no errors and no toasts. |

### Wave C: composition (parallel)

| Slice | Owns | Depends | Acceptance |
|---|---|---|---|
| **C1 App root, mode switch, App settings, Default wiring** | M `src/main.tsx`, `src/App.tsx`; N `src/app/*`; N `src/shells/default/{DefaultShell.tsx,index.ts}`; N placeholder `src/shells/{node,mobile,tablet}/index.ts` (re-export DefaultShell, `SHELL_READY=false`); N `src/components/preferences/*`; M `src/components/layout/{Header,AppShell,MobileTabBar}.tsx` (P1, P4, P5, P15 only); M `src/components/generation/GenerateCard.tsx` (use `useGenerateAction`; shortcut removed); M `src/components/history/{HistoryItem,HistoryDrawer}.tsx` (P5 gate, P16, "Regenerate from prompt" menu item for prompt jobs) | B2 | The mode persists across reload. `?ui=` overrides without persisting. The App settings dialog: radiogroup keyboard behaviour; read-only features and models; the tuner shown as paused. Ctrl+Enter is registered once (one listener, verified with `getEventListeners` in the pane). A chunk-failure fallback works when one shell chunk is blocked in the network panel. The Default fingerprint is unchanged apart from the App settings button. |
| **C2 Feature content split + new features** | M `src/components/references/ReferencePanel.tsx`, `settings/SettingsPanel.tsx`, `generation/ProgressCard.tsx`, `viewer/ModelStatsCard.tsx`, `history/HistoryList.tsx`; N `references/ReferenceContent.tsx`, `settings/{SettingsContent,PartOptionsSection}.tsx`, `generation/{ProgressContent,PipelineSteps}.tsx`, `viewer/ModelStatsContent.tsx`, `input/*`, `parts/*` (except viewer integration), `export/*` (+ css modules) | B2 | Each `XxxPanel` renders the same DOM as before (fingerprint). Prompt form validation messages; character counter; honest "no model installed" notice. The Parts outliner works against the mock (list, toggles, isolate, inspector, downloads). Part options round-trip into the request (smoke). |
| **C3 Viewer parts** | M `src/components/viewer/{ModelScene,ModelViewer,ViewerPanel,ViewerToolbar}.tsx`, `viewer/{viewerTypes,viewerRuntime}.ts`, `viewer/{ViewerPanel,ViewerToolbar}.module.css`; N `viewer/partsScene.ts` | B2 (partStore), B1 (partsModel) | Against the mock parts GLB: toggling hides physical nodes and body regions; isolate dims the others; hover highlights; click-pick selects the row; colorize works in PBR and clay. A state change causes no GLB refetch (network). `unmatchedPartIds` shows for a manifest entry that is absent from the GLB. P6 (320 px toolbar) and P10 (viewer min-height) are done. The tuner VariantBar is unaffected when the flag is on. |
| **C4 Shared shell chrome** | N `src/shells/shared/*` (CompactTopBar, StatusSheetContent, GenerateFab, RailGenerateButton, ProgressPill, GenerateBlockedContent, AppSettingsButton, FeatureIcon, PhoneLayout, SectionHost, navItems.ts + css) | A2, B2; C2 signatures | The FAB state table (§10.3) is verified with the mock (`--tuning-seconds` style job timing). `PhoneLayout` works when temporarily mounted by a dev-only `?ui=mobile` once D1 lands. |

### Wave D: shells and Default integration (parallel)

| Slice | Owns | Acceptance |
|---|---|---|
| **D1 Mobile shell** | `src/shells/mobile/*` (replaces the placeholder `index.ts`; `SHELL_READY=true`) | Probe at 320×640, 375×812, 390×844 and 740×360: no horizontal scroll; 0 targets under 48 px; nothing obscured by the nav. The prompt and image flows are both possible end to end against the mock. Generate is reachable with the keyboard open (sticky sheet footer). Sheets: drag, keyboard snap and Escape. With reduced motion, everything is instant. |
| **D2 Tablet shell** | `src/shells/tablet/*` | Posture switches automatically: 1024×768 → rail; 768×1024 → bottom; 812×375 → bottom; 500×800 → compact; it does not flip while typing. Rail ArrowUp and ArrowDown roving focus. Side panel collapses on Viewer. The probe is green at all 9 viewports. |
| **D3 Node shell + React Flow** | M `frontend/package.json`, `package-lock.json` (`@xyflow/react` **12.12.0** exact); M `frontend/vite.config.ts` (`nodeui` group + tightened `vendor`); N `src/store/workflowStore.ts`; `src/shells/node/*`; M `THIRD_PARTY_NOTICES.md` (xyflow MIT, d3-* ISC, classcat MIT, nested zustand 4 MIT; licences verified from the installed `package.json` files) | The six nodes render from the template. Connections are validated (an invalid drag is refused with a reason). Bypass and mode toggles write through to the stores (switch to Default and see the same part toggle). Run uses the shared action. Per-node status follows a mock job, including the analysis steps. List view gives full keyboard parity. Import and Export JSON round trip. The refine group shows as locked. `nodeui` is not in the initial chunk (budget). The compiler check has 0 new bail-outs, or each one is documented. |
| **D4 Default integration + polish** | M `src/components/layout/AppShell.tsx` (P2, P3 placements), `layout/AppShell.module.css`, `layout/Header.module.css`, `src/App.module.css`, `generation/GenerateCard.module.css`, `history/HistoryDrawer.module.css`, `references/ReferenceTile.module.css`, `src/hooks/useBreakpoint.ts` (P7) | P2, P3 and P6–P20 acceptance as listed in §9.3. The Default fingerprint with `--features none` equals the baseline plus App settings. |

### Wave E: verification and documentation

| Slice | Owns | Acceptance |
|---|---|---|
| **E1 Full matrix and docs** | M `frontend/README.md` (modes, flags, keys, test commands); N `test/outputs/frontend/ui_modes_matrix.json` (generated, gitignored per the repo policy) | All §17 gates green. The 36-cell probe matrix is green. A manual keyboard and reduced-motion pass per shell. A real-backend smoke (image job with parts off) produces a request and `model.glb` identical to before: the request body is compared in the network panel, and the job's GLB size and stats are compared to a pre-change job with the same seed. |

Critical path: A1 → B1 → B2 → C1 → D1/D2/D3/D4 → E1. A2, A3 and A4 run alongside. Within a wave, the slices are
independent.

---------------------------------------------------------------------------------------------------

## 20. Risks and mitigations

| Risk | Mitigation |
|---|---|
| The backend chooses names or shapes different from §4 | All wire handling is isolated in `api/featuresTypes.ts`, `api/partsTypes.ts`, `api/parts.ts` and `lib/featureFlags.normalizeFeatures` / `lib/partsModel.normalizeManifest`. The stores and UI use normalized client models only. |
| `_FEATURE_ID_0` attribute naming or type differs in GLTFLoader output [A] | `partsScene.ts` probes `_feature_id_0`, then `_FEATURE_ID_0`, then `_part_id`. If none is found, regions fall back to list-only (toggles disabled, with a note). An early C3 spike runs against the mock GLB. |
| The React Compiler mis-memoizes xyflow interop | Keep xyflow in 4 files. Mutations go through store actions. Run the compiler check. As a last resort, `'use no memo'` per file, documented. |
| A nested zustand@4 leaks into the initial chunk | The `vendor` regex is tightened and the budget script checks initial membership. |
| Mode switching remounts the R3F canvas (cost and flicker) | The switch is rare and user-initiated. Fade in the new shell. The GLB re-fetch is expected to come from the HTTP cache [A: check the `model.glb` response cache headers; if they forbid caching, keep the R3F `useLoader` cache entry alive for 30 s after unmount instead of the current 250 ms disposal, `ModelScene.tsx` `scheduleDisposal`]. |
| Write-through from the Node graph surprises Default users (parts got enabled in Node) | Changes are visible in the shared settings section. A toast appears on the first write-through per session: "Part-aware output turned on from the Node workflow". |
| Soft keyboard flips the tablet posture | The typing guard in `useNavPosture`. Tested at 768×1024 with a focused textarea. |
| Token contrast change is perceived as a "redesign" | Only 3 tokens change. Listed for the user as open question 7. |
| Legacy-backend users see new chrome | Only the App settings gear. Prompt and parts stay hidden in Default when `/features` returns 404. |
| Large part counts (> 200) slow the list | Grouped collapsible sections and a filter. No virtualization until > 512 (the manifest cap). |

---------------------------------------------------------------------------------------------------

## 21. Open questions (for the user or the backend workstream)

1. **Part results shape.** Is the part analysis a run resource (`api:'runs'`, recommended by this proposal) or
   embedded in the job (`api:'job'`)? The frontend supports both through the adapter, but the backend should pick
   one for v1.
2. **Prompt flow.** Use a one-shot `POST /jobs {source}` (assumed here) or the two-step text-to-image flow with a
   candidate review? The review step is a UX gain: the user picks the image before spending 3–10 minutes on 3D.
   It needs `PromptReviewContent` and an extra Node node ("Text-to-Image" between Prompt and Image-to-3D).
3. **Model approvals.** Which text-to-image and segmentation models will be installed (Z-Image-Turbo /
   FLUX.2-klein-4B / Animagine XL / none; GDINO + SAM 2.1 + Florence-2 baseline; optional See-through, SAM 3,
   Sapiens2)? The UI renders whatever `features.models` reports. Nothing here changes per choice.
4. **New dependency.** May `@xyflow/react@12.12.0` (MIT, about 60 kB gz, lazy) be added? Keep its small
   attribution link (restyled) or remove it? The MIT licence allows removal; the authors request it be kept unless
   the user subscribes to React Flow Pro.
5. **Naming.** Is "App settings" (the gear) acceptable beside the existing generation "Settings" panel, or rename
   the generation panel to "Generation"? The second option is more churn in Default.
6. **Segmentation preview** before generation (§12.6): wanted in this milestone, or later?
7. **Contrast tokens** (P13) slightly darken the primary button and lighten subtle text. Is this acceptable as
   "polish, not redesign"?
8. **Device hint.** Should Default on a phone ever *suggest* the Mobile UI (a one-time dismissible hint)? The
   current proposal shows a "Recommended" badge only inside App settings and never auto-switches.
9. **Workflow files.** Are import and export of `.img3d-workflow.json` wanted now? They are cheap to include and
   carry no user data.
