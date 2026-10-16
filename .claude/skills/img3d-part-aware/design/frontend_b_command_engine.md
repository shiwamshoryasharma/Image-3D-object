# Frontend proposal B: command layer and workflow engine

Date: 2026-10-16. Status: **design proposal only**. No project code was changed; this file is the only output.
Milestone: part-aware backend and four UI modes. Tuning, refinement, eye/rig correction and Blender work stay
paused and hidden.

**Angle.** All four UI modes are thin renderers over two shared cores.

1. **A typed command layer** (`src/commands`). It is the only way a renderer or feature component changes state.
2. **A pure TypeScript workflow engine** (`src/workflow/engine`). It holds the graph model, node registry, typed
   ports, validation, compile-to-request, and the mapping from job stage or step to node status.

Every mode renders the same canonical workflow document:
- Node UI draws it as a canvas.
- Default, Mobile and Tablet render it as placed panels or a stepper.

So the prompt, image, segmentation, part and export options are defined once.

**Evidence used.** These were read in full or in their relevant sections:
- `research/frontend_architecture_map.md`
- `research/ui_modes_research.md`
- `research/backend_contract_map.md`
- `research/backend_generation_map.md` §6–7
- `research/segmentation_models_research.md` (taxonomy in §7)
- `research/text_to_image_research.md` §7–9
- `research/trellis2_internals.md` §4
- `research/part_aware_3d_research.md` §5–6
- `research/environment_audit.md` §8–9
- `.claude/skills/img3d-architecture/api-contract.md`
- `.claude/CLAUDE.md`
- Frontend sources: `App.tsx`, `main.tsx`, `store/{jobStore,settingsStore,uiStore}.ts`,
  `hooks/useGenerateAvailability.ts`, `lib/{stages,storage}.ts`, `api/{types,jobs,ws}.ts`,
  `components/generation/GenerateCard.tsx`, `components/viewer/{ViewerPanel,ModelScene,viewerTypes,viewerRuntime}.*`,
  `components/layout/AppShell.tsx`, `vite.config.ts`, `.oxlintrc.json`, `vite-env.d.ts`, `package.json`
- Tests: `test/frontend/mock_backend.mjs` (header), `test/frontend/review/compiler_check.mjs`

Tags used below:
- **[V]** a verified fact with a file and line.
- **[D]** a decision made by this proposal.
- **[A]** an assumption about the backend that must be reconciled with the backend design before the slice that
  depends on it merges.

---------------------------------------------------------------------------------------------------

## 0. Decisions on one page

1. **One canonical workflow document [D].**
   - It is persisted as `img3d.workflow`.
   - It contains the graph of six node types (Input Image, Prompt, Segmentation, Image-to-3D, Part Extraction,
     Export), the values the workflow owns, and the Node-UI layout.
   - It is the single definition of what a generation consists of, in every mode.
   - The Image-to-3D values stay in the existing `settingsStore` (`img3d.settings`). The Input Image values stay in
     the existing `referenceStore`. Both are bound, not copied, so nothing that works today moves.
2. **Pure engine [D].** `src/workflow/engine/*` imports no React, DOM, axios, zustand or `@xyflow`. It uses only
   erasable TypeScript with `.ts` import specifiers, so `node --experimental-strip-types --test` runs it directly
   (Node 22.17 [V], `erasableSyntaxOnly` + `allowImportingTsExtensions` [V, `tsconfig.app.json`]).
   - With the default template (image source, Segmentation and Part Extraction bypassed), `compile()` produces the
     **byte-identical JSON** of today's `jobStore.submit` request (`store/jobStore.ts:L208-212` [V]). A golden test
     pins this.
3. **Command layer [D].**
   - `useCommand(Spec)` returns `{ availability, pending, run }`, and `dispatch(Spec, payload)` runs a command.
   - Availability is a pure function of one `AppSnapshot`. The same function drives:
     - disabled reasons,
     - Ctrl/Cmd+Enter,
     - the Node-UI Run button,
     - the mobile Generate button (FAB),
     - the tablet rail Generate button.
   - Today's `useGenerateAvailability` (`hooks/useGenerateAvailability.ts:L31-75` [V]) becomes a compatibility
     adapter over it. The reason strings stay identical.
4. **Thin renderers [D].**
   - `src/renderers/{default,node,mobile,tablet}` contain layout, navigation and composition only.
   - They may import only `components/**`, `commands/bind/**`, `state/read/**`, `renderers/{shared,linear}/**`, and
     the pure `lib/**` and engine modules.
   - A grep test enforces this (`test/frontend/arch_boundaries.test.mjs`).
5. **Mode switch [D].**
   - `preferencesStore` (`img3d.preferences`, default `'default'`) holds the mode, chosen in App settings.
   - A `?ui=` query parameter overrides it for the session only.
   - The renderers are discovered with `import.meta.glob('../renderers/*/index.ts')`. Default is bundled eagerly; the
     others are lazy chunks.
   - The root element carries `<html data-ui-mode data-posture data-density data-motion>`.
6. **Parts [D].**
   - A `partsStore` plus a tolerant manifest parser accepts both manifest drafts in the research.
   - The single existing viewer (the `viewerRuntime` singleton, `components/viewer/viewerRuntime.ts:L32` [V]) gains
     part visibility, isolate, hover/select highlight and an explode slider.
   - Semantic regions on the continuous body mesh are highlighted through the `_PART_ID` vertex attribute when the
     backend provides it.
7. **Tuning stays hidden [D].**
   - The tuner UI renders only when both of these are true:
     - the build flag `VITE_FEATURE_MESH_TUNER === 'true'` (default off), and
     - the backend reports `features.mesh_tuning.enabled === true`.
   - A default build makes zero `/blender` or `/tunings` requests.
   - The engine has a `refine` category that can only hold `locked` nodes, and this milestone ships no refine nodes.
8. **The backend contract this design needs [A].** All of it is additive (§4):
   - `GET /api/v1/features`;
   - an optional `source` and an optional `settings.parts` on `POST /jobs`;
   - optional `Job.kind`, `Job.source`, `Job.steps` and `JobResult.parts`;
   - part file routes.

   A legacy backend (404 on `/features`) degrades to image-to-3D only, with honest reasons in the UI.
9. **Delivery [D].** 13 slices in four waves. Files are disjoint inside each wave, every slice leaves the app working,
   and gates run after each one (§16).

---------------------------------------------------------------------------------------------------

## 1. Scope, hard constraints, non-goals

**In scope:**
- prompt and image input in every mode;
- part-aware options;
- part results display;
- four renderers;
- the App settings dialog (UI style, density, models and features status);
- the command layer and workflow engine;
- Default UI polish;
- accessibility, performance budgets and quality gates;
- test-double extensions under `test/`.

**Hard constraints this design honours:**

| Constraint | How |
|---|---|
| Image-to-3D path unchanged when part features are off | The default template compiles to today's request (golden test G1, §5.6). The legacy `submit()` stays callable until every caller has migrated. |
| No `JobStage` additions; percent formula and `/system` keys unchanged | The frontend keeps the closed `JOB_STAGES` union (`api/types.ts:L34-47` [V]). Per-node status uses the optional new `job.steps`, and falls back to the legacy stage map (§5.7). Nothing is read from new `/system` keys; features come from a new endpoint. |
| Request bodies reject unknown fields | `compile` sends an optional key only when the backend advertises it in `features.*.request_keys` (§4.2), so a frontend never causes a 422 from `extra="forbid"`. |
| Single GPU worker; models load one at a time | The frontend never assumes concurrency. The Node UI allows at most one instance of each node type (`maxInstances: 1`), so one Run makes one job. |
| No new heavy native dependencies | The only new npm dependency is `@xyflow/react@12.12.0` (MIT, about 60 kB gz, lazy). Its install resolution was verified clean (`environment_audit.md` §8 [V]). |
| No fake generation data in the app | No placeholder parts, images or models. Unavailable features show the backend's reason. Test doubles live only in `test/frontend/mock_backend.mjs`. |
| Model choices pending | The UI lists only what `/features` reports, with installed/licence/approval states. Each model field is optional and defaults to the backend default (§4.2, §8.7). |
| Tuning disabled | Two-key gate (§10). No tuning commands, nodes or algorithms. |
| React Compiler, zustand 5, oxlint rules | §6.4 lists the rules: no `try/finally` in components or hooks, no new objects from selectors, constants in `.ts` files. Stores and the bus are not compiled and may use `try/finally`. |

**Non-goals:**
- Blender-side code.
- Editing the tuner components. `components/meshTuner/*` is paused work (committed as the WIP commit `8654b90`
  [V, `git log`]); this design only stops mounting it.
- A review-the-generated-image-first prompt flow. The backend contract is one-shot first.
- Multi-job graphs.
- Client-side execution of nodes.

---------------------------------------------------------------------------------------------------

## 2. Verified facts this design relies on

| Fact | Where [V] | Used by |
|---|---|---|
| `App.tsx` mixes session, auth gate, layout choice and Default chrome | `App.tsx:L14-42` | §8.7 AppRoot split |
| `jobStore.submit` builds the request from references and the settings form | `store/jobStore.ts:L197-223` (request L208-212) | G1 golden parity, S2 split |
| `jobStore.retry` resends `job.settings` verbatim with the reference ids | `store/jobStore.ts:L225-258` | Retry for prompt jobs (§6.5) |
| `toRequestSettings(form, referenceMode)` omits `advanced` without overrides | `store/settingsStore.ts:L112-135` | Bound Image-to-3D values |
| `useGenerateAvailability` reason order and strings | `hooks/useGenerateAvailability.ts:L55-73` | `generateAvailability` parity |
| `GenerateCard` registers Ctrl/Cmd+Enter, calls `submit`, then `setMobileTab('viewer')` | `components/generation/GenerateCard.tsx:L34-60` | ShortcutHost, `job.generate` reveal |
| Three places push mobile navigation: `GenerateCard:L40`, `AppShell.tsx:L139`, `BlenderSetupDialog.tsx:L89` | grep | `nav.reveal` |
| `PIPELINE_STAGES` hard-codes indices 1..8; `stageState` compares indices | `lib/stages.ts:L10-35` | Legacy stage map |
| `STORAGE_KEYS`: auth token, reference set, viewed job, settings, ui, meshTuner, blender dismissal | `lib/storage.ts:L6-16` | §7.2 |
| `uiStore` persists only `viewer`; `mobileTab` and `historyOpen` are not persisted | `store/uiStore.ts:L37-57` | Navigation moves to `navigationStore` |
| One viewer API: `resetCamera`, `screenshot`; `ModelViewerProps` | `components/viewer/viewerTypes.ts:L5-25` | §9 viewer extension |
| `ModelScene` loads with `useLoader(GLTFLoader)` and applies the view mode to the whole scene | `components/viewer/ModelScene.tsx:L74-114` | Part visibility and highlight |
| `ModelStatsCard` has "reuse settings" through `replaceForm(formFromJobSettings(...))` and a direct API download | `components/viewer/ModelStatsCard.tsx:L118,L171`; L20-50 | `job.reuseSettings`, `job.downloadGlb` |
| The WS dispatch ignores unknown types | `api/ws.ts` `dispatch` `default: break` | Optional `features.updated` |
| Rolldown chunk groups react / vendor / three / r3f | `vite.config.ts` `codeSplitting.groups` | xyflow group, budgets |
| oxlint: `react/only-export-components` warns on constant exports in component files | `.oxlintrc.json`; research §8.3 | Registries live in `.ts` files |
| No Playwright or Puppeteer installed | research §8.4 | Probe script plus manual browser-pane run |
| Baseline initial JS is about 178–180 kB gz; three (207 kB gz) and r3f (80 kB gz) are lazy | research §8.4 | §12 budgets |
| Breakages at 320 px (toolbar), 812×375 (tablet layout chosen), 740×360 (157 px content), 768×1024 (narrow viewer); 43 of 62 controls under 44 px at tablet size | `frontend_architecture_map.md` §3.2 | §8.3 polish |

---------------------------------------------------------------------------------------------------

## 3. Architecture

### 3.1 Layers and dependency direction

```
Backend /api/v1 (REST + WS)                                            contract: api-contract.md (+ §4 here)
  │
  ▼
src/api/*                typed axios client, RealtimeClient, + features.ts, parts.ts           (S0)
  │
  ▼
src/store/*              zustand domain stores = single source of truth
                         existing: auth, system, reference, settings, job, ui(viewer opts), toast, meshTuner
                         new: features, workflow, parts, preferences, navigation (+ transfer)
  │          ▲ written ONLY by command effects (commands/bind/*) and by session wiring (app/)
  │          │
  │   src/commands/bind/*   binds pure specs to real ports (stores, api, viewer runtime)
  │          │
  │   src/commands/specs/*  PURE: availability(snapshot, payload) + run(payload, env) via injected ports
  │          │        └── uses ──► src/workflow/engine/*  PURE: registry, ports, validate, compile, status,
  │          │                                            serialize, edit ops, linear plan
  │   src/commands/core/*   bus, registry, AppSnapshot store, React hooks, announcer, shortcuts
  │          ▲
  │   src/state/sync/*      store.subscribe → patchSnapshot(...)   (keeps AppSnapshot current)
  ▼
src/state/read/*         read-only hooks for UI (stable-reference selectors)
  │
  ▼
src/components/*         feature content + primitives, renderer-agnostic (read: state/read, write: commands)
src/renderers/linear/*   canonical graph → placed panels / stepper (used by Default, Mobile, Tablet)
src/renderers/shared/*   chrome shared by renderers (StatusCluster, GenerateAction, FloatingNav, NavRail…)
src/renderers/{default,node,mobile,tablet}/*   THIN: layout + navigation + composition
  ▲
src/app/*                AppRoot (session, auth gate), ModeSwitch, GlobalOverlays, ShortcutHost, Announcer,
                         bootstrap (glob-registers commands, syncers, reactions)
```

### 3.2 Import rules (enforced by `test/frontend/arch_boundaries.test.mjs`)

| Folder | May import | Must not import |
|---|---|---|
| `workflow/engine/**`, `workflow/nodes/**`, `commands/core/{types,registry,shortcuts}.ts`, `commands/specs/**`, `lib/{featureSet,partManifest,partGroups,uiModes}.ts` | each other (with `.ts` specifiers); `import type` from `api/types.ts` | `react`, `react-dom`, `axios`, `zustand` (except `zustand/vanilla` in `commands/core/snapshot.ts`), `@xyflow/*`, `three`, `../api/*` values, `../store/*`, `window`/`document` globals |
| `commands/bind/**`, `state/sync/**`, `state/reactions/**` | stores, api, specs, engine | `react` components, renderers |
| `state/read/**` | stores, engine, `zustand/react/shallow` | api values, command binds |
| `components/**` (after migration) | `state/read/**`, `commands/bind/**`, `commands/core/react.ts`, `lib/**`, other components | `store/**`, `api/*` values (`import type` and URL helpers in `api/jobs.ts`, `api/parts.ts`, `api/references.ts` are allowed) |
| `renderers/**` | the same as components, plus `renderers/{shared,linear}/**` | `store/**`, `api/**` values, `hooks/useBackendSession` |
| `renderers/node/**` only | `@xyflow/react` | – |
| `app/**` | everything | – |

`components/meshTuner/**` and files not yet migrated are listed in `test/frontend/arch_allowlist.json`. The test
fails when a file outside the allowlist breaks a rule. It also fails when an allowlisted file no longer breaks one, so
the list can only shrink.

### 3.3 "Generate", end to end (the same path in every mode)

1. A trigger runs `dispatch(JobCommands.generate, undefined, { source })`. The trigger is one of:
   - the Default `GenerateCard`,
   - the mobile FAB,
   - the tablet rail button,
   - the Node toolbar Run,
   - Ctrl/Cmd+Enter in `app/ShortcutHost`.
2. The bus reads `readSnapshot()` and evaluates `generateAvailability(snapshot)` (§6.5). A blocked result
   short-circuits. For keyboard and FAB triggers it also announces the reason.
3. `run`:
   1. If the engine state is `not_loaded`, call `ports.loadEngine()` without waiting (today's behaviour,
      `GenerateCard.tsx:L36-38` [V]).
   2. `ports.compileCurrent()` (`workflow/bridge.ts`) gathers the workflow doc, workflow values, the bound reference
      and settings values, features, and `system.defaults.reference_mode`. It then calls `engine.compile(...)`, which
      returns `{ request, stepMap }`.
   3. `ports.submitJob(request)` calls `jobStore.submitRequest(request)` (new; the old `submit()` delegates to it).
      That calls `api/jobs.createJob`.
   4. `ports.rememberRun(job.id, stepMap)` stores the step map in the session-only `workflowStore.runIndex`.
4. The bus marks the command pending (`job.generate`) while it runs. On success it announces "Generation queued" and
   sets `navigationStore.reveal = { target: 'run', seq: n+1 }`. Each renderer maps `run` to its own navigation.
5. `job.updated` WebSocket frames update `jobStore`. `state/read/workflow.ts → useNodeRunStates()` projects stage or
   steps onto nodes for the status chips (§5.7).

---------------------------------------------------------------------------------------------------

## 4. Backend contract assumed by the frontend [A]

Everything here is **additive**. The frontend adapter (`api/features.ts`, `api/parts.ts`, `lib/featureSet.ts`,
`lib/partManifest.ts`) is the only code that knows these wire shapes. If the backend design picks different names,
only these four files change. The reconciliation table is §4.4.

### 4.1 Endpoint table

| # | Method + path | Request | Success | Errors (code → frontend behaviour) | Used by |
|---|---|---|---|---|---|
| E1 | `GET /api/v1/features` (AuthGuard) | – | 200 `FeaturesOut` | 401 → session expiry flow (existing). **404 → legacy fallback** (§4.3). 5xx/network → keep the last known value, retry on the next safety poll | `featuresStore.refresh` at session sync, on `hello`, on `features.updated`, and on safety polls |
| E2 | `POST /api/v1/jobs` (extended) | `CreateJobRequest` with optional `source` and `settings.parts` (§4.2) | 202 `Job` | Existing: 404 set/image, 422 `validation_error`/`unsupported_by_engine`, 429 `queue_full`, 503 `engine_unavailable`. New: **422 `capability_unavailable`** or **503 `capability_unavailable`** → toast "This backend cannot run that pipeline right now: {message}" and `features.refresh()` | `job.generate`, `job.retry` |
| E3 | `GET /api/v1/jobs/{id}` and `GET /jobs` | – | `Job` with optional `kind`, `source`, `steps`, and `result.parts` | unchanged | jobStore |
| E4 | `GET /api/v1/jobs/{id}/parts` | – | 200 `PartManifest` JSON (schema `img3d.parts/1`) | 404 `not_found` (job or no manifest) → parts state `absent`; 409 `conflict` (not ready) → state `pending`, retried on the next `job.updated` | `parts.load` |
| E5 | `GET /api/v1/jobs/{id}/parts/assembled.glb` (FileAuthGuard, `?token=`) | – | `model/gltf-binary` inline | 404/409 → viewer error card with "Show original model" | viewer source "Parts" |
| E6 | `GET /api/v1/jobs/{id}/parts/{part_id}/model.glb` (FileAuthGuard) | – | glb attachment `img3d_<job>_<part>.glb` | 404 → toast | `parts.download` |
| E7 | `GET /api/v1/jobs/{id}/parts/{part_id}/mask` (FileAuthGuard) | – | `image/png` | 404 → row has no thumbnail | Parts list (optional thumbnails) |
| E8 | `GET /api/v1/jobs/{id}/parts/segmentation/overlay` (FileAuthGuard) | – | `image/png` | 404 → no overlay | Segmentation node preview, Parts sheet header |
| E9 | `POST /api/v1/jobs/{id}/parts/rebuild` | `PartOptionsIn` | 202 `Job` (kind `part_decomposition`) | 404, 409 (source job not completed), 422, 429, 503 `capability_unavailable` | `parts.rebuild` (only when `features.pipeline.part_extraction.rebuild === true`) |
| E10 | `POST /api/v1/models/{model_id}/install` | `{ "accept_license": true, "license_version": string }` | 202 `ModelEntryOut` | 403 `install_disabled`, 404, 409 `install_in_progress`, 422 `confirmation_required`, 502 `download_failed` | `models.install` (only when `entry.install.supported`) |
| WS1 | `features.updated` `{type, ts, data: FeaturesOut}` | – | – | optional; unknown to old clients | `featuresStore.setFeatures` |
| WS2 | `models.updated` `{type, ts, data: ModelEntryOut}` | – | – | optional | install progress |

The frontend **does not require** a `parts.updated` event. Rebuilds are Jobs, so `job.updated` covers them. If the
backend adds `parts.updated` `{data: {job_id}}`, the frontend refetches the manifest. `api/ws.ts` handles all three
optional types.

### 4.2 Wire shapes (TypeScript, snake_case as on the wire)

```ts
// ---- E1 GET /features ------------------------------------------------------------------------------
export interface CapabilityOut {
  available: boolean
  /** Short user-facing reason when unavailable (e.g. "No text-to-image model is installed"). */
  reason: string | null
  code: 'ok' | 'not_installed' | 'not_approved' | 'disabled' | 'gated' | 'license_not_accepted' | 'loading' | 'error'
  /** Optional request keys this backend accepts for this feature (compile omits keys not listed). */
  request_keys?: string[]
}
export interface FeaturesOut {
  schema: 'img3d.features/1'
  pipeline: {
    image_to_3d: CapabilityOut
    prompt_to_3d: CapabilityOut & {
      max_prompt_chars: number                       // e.g. 2000
      presets: ('character' | 'object' | 'raw')[]
      styles: ('realistic' | 'stylized' | 'anime')[]
      supports_negative_prompt: boolean              // false for distilled models (Z-Image-Turbo, klein)
      default_model_id: string | null
    }
    segmentation: CapabilityOut & {
      subjects: ('auto' | 'anime_character' | 'realistic_human' | 'object')[]
      default_segmenter_id: string | null
      max_extra_terms: number                        // e.g. 20
    }
    part_extraction: CapabilityOut & {
      strategies: { id: string; label: string; requires_segmentation: boolean; description: string }[]
      default_strategy: string | null
      separable_groups: PartGroupInfo[]              // physical-asset groups the user may toggle
      region_groups: PartGroupInfo[]                 // semantic regions kept on the continuous mesh
      rebuild: boolean                               // E9 available
    }
  }
  models: ModelEntryOut[]
  mesh_tuning: { enabled: boolean; reason: string | null }       // false until approval
  part_refinement: { enabled: boolean; reason: string | null }   // false until approval
}
export interface PartGroupInfo {
  id: 'eyes' | 'eyebrows' | 'eyelashes' | 'hair' | 'clothes' | 'accessories' | 'detachable' | 'face' | 'body' | string
  label: string
  default_kind: 'physical' | 'physical_deferred' | 'region'
  default_selected: boolean
  note: string | null      // e.g. "Eyelash geometry is deferred; mask + lid curve only"
}
export interface ModelEntryOut {
  id: string
  stage: 'text_to_image' | 'segmentation' | 'part_extraction'
  display_name: string
  state: 'not_installed' | 'installing' | 'installed' | 'loaded' | 'error' | 'unavailable'
  approved: boolean                     // the user's model decision (backend setting); unapproved = not selectable
  is_default: boolean
  reason: string | null
  size_bytes: number | null
  vram_estimate_gb: number | null
  license: {
    id: string; name: string; url: string | null
    commercial: 'allowed' | 'revenue_capped' | 'non_commercial' | 'research_only' | 'unknown'
    gated: boolean; requires_acceptance: boolean; accepted: boolean
  }
  install: { supported: boolean; hint: string | null; bytes_done: number | null; bytes_total: number | null }
}

// ---- E2 POST /jobs (additive) ---------------------------------------------------------------------
export interface PromptSourceIn {
  type: 'prompt'
  prompt: string                        // 1..max_prompt_chars after trim
  negative_prompt?: string              // only if supports_negative_prompt and non-empty
  preset?: 'character' | 'object' | 'raw'
  style?: 'realistic' | 'stylized' | 'anime'
  model_id?: string                     // only if user picked a non-default approved model
  image_seed?: number                   // only when randomize_image_seed === false
  randomize_image_seed?: boolean
  part_hint?: boolean                   // only when true ("parts clearly distinct" template clause)
}
export interface PartOptionsIn {
  enabled: true
  segmentation: 'auto' | 'off'
  decomposition: string                 // strategy id from features
  separate: string[]                    // PartGroupInfo ids of separable_groups the user kept on
  regions: string[]                     // PartGroupInfo ids of region_groups
  export: { assembled_glb: boolean; part_files: boolean; keep_base_model: true }
  subject?: 'auto' | 'anime_character' | 'realistic_human' | 'object'   // only if in request_keys
  segmenter_id?: string                                                   // only if in request_keys and non-default
  extra_terms?: string[]                                                  // only if in request_keys and non-empty
}
export interface CreateJobRequest {          // replaces today's interface (api/types.ts:L300) — compatible superset
  reference_set_id?: string                 // REQUIRED unless source.type === 'prompt' (then omitted)
  primary_image_id?: string | null          // present (possibly null) for image jobs, exactly as today
  source?: PromptSourceIn                   // omitted for image jobs
  settings: GenerationSettings & { parts?: PartOptionsIn }   // `parts` omitted when part features are off
}

// ---- E3 Job additions (all optional on the wire; absent on legacy jobs) ----------------------------
export type JobKind = 'image_to_3d' | 'prompt_to_3d' | 'part_decomposition'
export type JobSourceOut =
  | { type: 'image' }
  | { type: 'prompt'; prompt: string; negative_prompt: string | null; preset: string | null; style: string | null
      model_id: string | null; image_seed: number | null; generated_image_id: string | null }
export type PipelineStepId = 'input_image' | 'prompt' | 'segmentation' | 'image_to_3d' | 'part_extraction' | 'export'
export interface PipelineStepOut {
  id: PipelineStepId | string            // unknown ids are ignored by the projection
  label: string
  status: 'pending' | 'running' | 'done' | 'warning' | 'skipped' | 'failed'
  message: string | null
  progress: number | null                // 0..100 within the step
  duration_s: number | null
}
export interface JobPartsSummaryOut {
  status: 'completed' | 'partial' | 'failed' | 'skipped'
  manifest_url: string                   // e.g. /api/v1/jobs/job_…/parts
  assembled_url: string | null           // E5
  overlay_url: string | null             // E8
  part_count: number; physical_count: number; region_count: number
  warnings: string[]
  parts?: { id: string; label: string; kind: string; model_url: string | null; mask_url: string | null }[]
}
// Job gains: kind?: JobKind; source?: JobSourceOut | null; steps?: PipelineStepOut[] | null;
//            derived_from_job_id?: string | null
// JobResult gains: parts?: JobPartsSummaryOut | null
// Job.reference_set_id / primary_image_id stay non-null strings (backend materializes the generated image).
```

**The manifest (E4).** The frontend consumes the subset below. `lib/partManifest.ts → parsePartManifest(json)`
accepts both research drafts (`backend_contract_map.md` §4.4 and `part_aware_3d_research.md` §6.3) and ignores
unknown keys.

```ts
export interface PartManifestView {              // normalized by the parser (never the raw wire object)
  schema: 'img3d.parts/1'
  jobId: string
  regionAttribute: string | null                 // glTF attribute name, e.g. '_PART_ID' or '_FEATURE_ID_0'
  parts: PartView[]                              // physical + deferred + regions, one flat list
  relations: { type: string; a: string; b: string }[]
  warnings: string[]
}
export interface PartView {
  id: string                    // wire: parts[].id | parts[].part_id | regions[].region_id
  label: string                 // wire: label | class | id (title-cased)
  category: string              // wire: category | class   (fine-grained, e.g. 'eye.l', 'clothing.top', 'hair')
  group: string                 // wire: group | derived by lib/partGroups.ts prefix map ('eyes','hair',… 'other')
  kind: 'physical' | 'physical_deferred' | 'region'   // wire: kind ('physical'|'region'|'P'|'P*'|'R'); regions[] ⇒ region
  parent: string | null         // wire: parent | host_part
  attachTo: string | null       // wire: attach_to | attached_to
  side: 'left' | 'right' | 'center' | null
  symmetricWith: string | null  // wire: symmetric_with | symmetry_pair | symmetric_pair
  nodeName: string | null       // glTF node name in assembled.glb (wire: node_name)
  regionValue: number | null    // value of regionAttribute (wire: region.value | feature_id)
  faces: number | null          // wire: stats.faces | faces | region.face_count
  confidence: number | null
  modelUrl: string | null       // from JobPartsSummaryOut.parts[].model_url or manifest files.mesh (resolved)
  maskUrl: string | null
  refinement: { eligible: boolean; status: string } | null   // shown read-only ("untouched")
}
```

The parser **rejects** a manifest (`ManifestError`, shown as a parts error state) when any of these hold:
- the schema prefix is not `img3d.parts/`;
- `parts` is not an array;
- a part has no id;
- there are duplicate ids;
- an id contains `/` or `..` (defence in depth; the URLs come from the backend).

### 4.3 Legacy and partial backends

| Backend state | `FeatureSet` (normalized by `lib/featureSet.ts`) | UI effect |
|---|---|---|
| `/features` not fetched yet | `source:'loading'`. image_to_3d available. Others unavailable with code `loading` and reason "Checking backend features…" | The image path works immediately. The Prompt source and part toggles show a spinner badge. |
| 404 on `/features` (today's backend) | `source:'legacy'`. image_to_3d available. prompt/segmentation/part_extraction unavailable, reason "This backend version does not support {feature}." `mesh_tuning.enabled=false`, `part_refinement.enabled=false`, `models=[]` | Prompt stays selectable for editing, but Generate is blocked with the reason. Part toggles are disabled with the reason. |
| Feature reported with `available:false` | as reported | Honest reason plus a link to App settings → Models |
| A job has `steps == null` | – | Legacy stage projection (§5.7) |
| `result.parts == null` | – | Parts card hidden; the Node Export node shows "No parts in this result" |

### 4.4 Reconciliation table (frontend name → research proposals)

| Frontend expects | Backend research says | Resolution rule |
|---|---|---|
| `GET /features` | `GET /features` **or** `/pipeline/catalog` (`backend_contract_map.md` §4.7) | Only `api/features.ts` changes. Any name works. |
| `source: {type:'prompt', …}` at the top level | Same (§4.2 of the contract map). `prompt_source` in `text_to_image_research.md` §7 | Top-level `source` is preferred because retry resends only `settings` (contract map §4.2). |
| `settings.parts` keys `enabled, segmentation, decomposition, separate, regions, export{assembled_glb, part_files, keep_base_model}` | Identical (contract map §4.3) | – |
| Optional `subject`, `segmenter_id`, `extra_terms`, `model_id`, `part_hint`, `preset`, `style` | Partly in T2I §7 (`preset`, `style`, `model_id`). The others are new | Sent only when listed in `request_keys`. |
| `Job.steps[].id` equals the step ids above | Contract map §4.5: "stable ids equal to the Node-UI node types" | Node type = `img3d.` + step id. |
| `JobResult.parts` summary | Contract map: `{manifest_url, assembled_url, per-part {id,label,kind,model_url,mask_url}}` | The parser accepts either; counts are derived when absent. |
| Manifest field names | Two drafts (`id/category/kind/files/region` vs `part_id/class/node_name/regions[]/feature_id`) | Both are parsed (§4.2). |
| `assembled.glb` route | Contract map: `/jobs/{id}/parts/assembled.glb`. Part-aware research: `model_parts.glb` artifact | The frontend uses `result.parts.assembled_url` and never builds the path itself. |
| Model registry inside `/features` | T2I research proposes `GET /t2i/models` | Accepted either way: if a separate list exists, `api/features.ts` merges it into `models`. |

---------------------------------------------------------------------------------------------------

## 5. Workflow engine (`src/workflow/engine`, pure TypeScript)

### 5.1 Files

| File | Exports |
|---|---|
| `engine/types.ts` | `PORT_TYPES`, `PortType`, `NODE_CATEGORIES`, `NodeCategory`, `NodeMode`, `StepId`, `StepGroup`, `PanelKey`, `IconKey`, `InputPortDef`, `OutputPortDef`, `ParamDef`, `NodeTypeDef`, `GraphNode`, `GraphEdge`, `PortRef`, `Graph`, `WorkflowValues`, `NodeLayout`, `WorkflowDoc`, `GraphIssue`, `IssueCode`, `CompileInputs`, `CompileResult`, `StepMap`, `NodeRunState`, `RunStatus`, `LinearStep` |
| `engine/registry.ts` | `createRegistry(defs)` returning `Registry {get(type), all(), palette(caps, flags), byStep(stepId)}`. Throws on duplicate types, on a `refine` def without `locked: true`, and on a port id clash. |
| `engine/ports.ts` | `portAccepts(input, outType)`, `canConnect(doc, registry, from, to)` returning `{ok:true} \| {ok:false, code, message}` |
| `engine/graph.ts` | Pure edit ops that return a new doc: `addNode`, `removeNodes`, `connect`, `disconnect`, `setMode`, `setSource`, `setPartsEnabled`, `moveNodes`, `setViewport`, `setCollapsed`, `setValues`, `resetValues`, `autoLayout` (layered by topological depth; no dagre in v1) |
| `engine/plan.ts` | `topoOrder(graph)` (Kahn, stable by node id), `findCycles`, `activeSource(doc, registry)`, `buildPlan(doc, registry)` |
| `engine/validate.ts` | `validate(doc, registry, ctx: ValidationContext)` returning `GraphIssue[]` |
| `engine/compile.ts` | `compile(inputs: CompileInputs, registry)` returning `CompileResult` |
| `engine/status.ts` | `projectRunState(doc, registry, job: RunJobView \| null, stepMap: StepMap \| null)` returning `Record<nodeId, NodeRunState>` |
| `engine/linear.ts` | `linearSteps(doc, registry, caps)` returning `LinearStep[]` (what linear renderers draw) |
| `engine/serialize.ts` | `WORKFLOW_SCHEMA`, `toFile(doc, appVersion)`, `fromFile(text, registry)` returning `{ok, doc, warnings}`, `migrateDoc(raw)` |
| `engine/fromJob.ts` | `docFromJob(job: JobLikeForDoc, base: WorkflowDoc, registry, caps)` (the "Reuse settings" and "Open in workflow" mapping) |
| `engine/index.ts` | re-exports |
| `workflow/nodes/{inputImage,prompt,segmentation,imageTo3d,partExtraction,export}.ts` | one `NodeTypeDef` each (data plus pure `summarize`/`validateValues`/`contribute`) |
| `workflow/nodes/index.ts` | `NODE_DEFS` (array) and `REGISTRY = createRegistry(NODE_DEFS)` |
| `workflow/templates.ts` | `TEMPLATES` (`image`, `image_parts`, `prompt`, `prompt_parts`), `canonicalDoc()` |

Every relative import in these files uses an explicit `.ts` extension. Node's type stripping needs this, and it is
allowed by `allowImportingTsExtensions` [V]. Nothing here reads `import.meta.env`.

### 5.2 Core types

```ts
// workflow/engine/types.ts  (erasable syntax only; no enums, no parameter properties)
export const PORT_TYPES = ['image_set', 'masks', 'mesh', 'part_set'] as const
export type PortType = (typeof PORT_TYPES)[number]

export const NODE_CATEGORIES = ['input', 'analyze', 'generate', 'parts', 'output', 'refine'] as const
export type NodeCategory = (typeof NODE_CATEGORIES)[number]

export type NodeMode = 'active' | 'bypass'
export type StepId = 'input_image' | 'prompt' | 'segmentation' | 'image_to_3d' | 'part_extraction' | 'export'
export type StepGroup = 'source' | 'generate' | 'parts' | 'export'
export type CapabilityKey = 'image_to_3d' | 'prompt_to_3d' | 'segmentation' | 'part_extraction' | 'part_refinement'
export type PanelKey = 'references' | 'prompt' | 'segmentation' | 'generation_settings' | 'part_extraction' | 'export'
export type IconKey = 'image' | 'text' | 'scan' | 'box' | 'layers' | 'download' | 'lock'

export interface InputPortDef {
  readonly id: string; readonly label: string
  readonly accepts: readonly PortType[]; readonly required: boolean; readonly multiple: boolean
}
export interface OutputPortDef { readonly id: string; readonly label: string; readonly type: PortType }

/** Declarative parameter schema; renderers use it for summaries/generic widgets, panels may render richer UI. */
export type ParamDef =
  | { readonly kind: 'text'; readonly key: string; readonly label: string; readonly multiline: boolean
      readonly maxLength: number | 'prompt_max' }
  | { readonly kind: 'enum'; readonly key: string; readonly label: string
      readonly options: readonly { value: string; label: string }[] | CapabilityOptions; readonly nullable: boolean }
  | { readonly kind: 'multi_enum'; readonly key: string; readonly label: string; readonly options: CapabilityOptions }
  | { readonly kind: 'boolean'; readonly key: string; readonly label: string }
  | { readonly kind: 'seed'; readonly key: string; readonly label: string; readonly randomizeKey: string }
  | { readonly kind: 'string_list'; readonly key: string; readonly label: string
      readonly maxItems: number | 'extra_terms_max'; readonly maxLength: number }
  | { readonly kind: 'model'; readonly key: string; readonly label: string; readonly stage: 'text_to_image' | 'segmentation' }
  | { readonly kind: 'bound'; readonly key: string; readonly label: string; readonly store: 'references' | 'settings' }
export type CapabilityOptions = {
  readonly from: 'presets' | 'styles' | 'subjects' | 'strategies' | 'separable_groups' | 'region_groups'
}

export interface NodeTypeDef {
  readonly type: string                          // 'img3d.' + stepId for built-ins
  readonly version: number                       // bump + migrate() when values change shape
  readonly stepId: StepId | null                 // null for future non-backend nodes
  readonly category: NodeCategory
  readonly title: string
  readonly description: string
  readonly icon: IconKey
  readonly role: 'source' | 'stage' | 'sink'
  readonly stepGroup: StepGroup
  readonly inputs: readonly InputPortDef[]
  readonly outputs: readonly OutputPortDef[]
  readonly params: readonly ParamDef[]
  /** Where values live: in the workflow doc, or bound to an existing domain store. */
  readonly valueSource: 'workflow' | 'references' | 'settings'
  readonly defaults: NodeValues
  readonly requires: CapabilityKey
  readonly canBypass: boolean
  readonly maxInstances: number                  // 1 for every built-in (one job per Run)
  readonly locked: boolean                       // true only for 'refine' (never compiled)
  readonly panel: PanelKey
  readonly summarize: (values: NodeValues, caps: FeatureSet) => readonly SummaryItem[]
  readonly validateValues: (values: NodeValues, caps: FeatureSet) => readonly GraphIssue[]
  readonly contribute: (draft: RequestDraft, values: NodeValues, caps: FeatureSet) => void
  readonly migrate?: (values: unknown, fromVersion: number) => Record<string, unknown>
}
export type NodeValues = Readonly<Record<string, unknown>>
export interface SummaryItem { readonly label: string; readonly value: string }

export interface GraphNode { readonly id: string; readonly type: string; readonly version: number; readonly mode: NodeMode }
export interface PortRef { readonly node: string; readonly port: string }
export interface GraphEdge { readonly id: string; readonly from: PortRef; readonly to: PortRef }
export interface Graph { readonly nodes: readonly GraphNode[]; readonly edges: readonly GraphEdge[] }

/** Workflow-owned values, keyed by NODE TYPE (every built-in is a singleton). */
export type WorkflowValues = Readonly<Record<string, NodeValues>>

export interface NodeLayout {
  readonly positions: Readonly<Record<string, { readonly x: number; readonly y: number }>>
  readonly collapsed: readonly string[]
  readonly viewport: { readonly x: number; readonly y: number; readonly zoom: number }
}
export interface WorkflowDoc {
  readonly schema: 'img3d.workflow'
  readonly version: 1
  readonly graph: Graph
  readonly values: WorkflowValues
  readonly layout: NodeLayout                  // Node UI only; linear renderers ignore it
}

export type IssueCode =
  | 'unknown_node_type' | 'type_mismatch' | 'missing_required_input' | 'no_source' | 'multiple_sources'
  | 'cycle' | 'capability_missing' | 'node_locked' | 'too_many_instances' | 'no_sink'
  | 'invalid_value' | 'input_missing' | 'requires_segmentation' | 'unconnected_node'
export interface GraphIssue {
  readonly severity: 'error' | 'warning'; readonly code: IssueCode; readonly message: string
  readonly nodeId?: string; readonly portId?: string; readonly edgeId?: string
}

export type RunStatus = 'idle' | 'queued' | 'running' | 'done' | 'skipped' | 'failed' | 'bypassed' | 'unavailable' | 'locked'
export interface NodeRunState {
  readonly status: RunStatus; readonly progress: number | null; readonly message: string | null; readonly warning: boolean
}
export interface LinearStep {
  readonly group: StepGroup
  readonly title: string                               // 'Source' | 'Generation' | 'Parts' | 'Export'
  readonly nodeIds: readonly string[]                  // in plan order
  readonly activeSourceType: string | null             // only for group 'source'
  readonly enabled: boolean                            // false = bypassed group (parts)
  readonly toggleable: boolean                         // parts group
  readonly unavailable: { readonly reason: string } | null
}
```

`FeatureSet` comes from `lib/featureSet.ts` (pure; normalized from `FeaturesOut`). The engine imports it as a type
only. `RunJobView` is a structural subset of `Job` (`status`, `progress.stage`, `progress.stage_index`,
`progress.percent`, `progress.stage_label`, `queue_position`, `kind?`, `steps?`), so the engine has no dependency on
the full API type.

### 5.3 Node catalogue (the user's six stages)

Port glyph and colour tokens (§8.1):
- `image_set` ● `--port-image` (cyan, `--color-accent-2`)
- `masks` ■ `--port-masks` (green)
- `mesh` ▲ `--port-mesh` (violet, `--color-accent`)
- `part_set` ⬢ `--port-parts` (pink)

The wire takes the colour of the source port.

| Node type | Category / role / group | Inputs (accepts, req, multiple) | Outputs | Value source | Requires | Bypass | Panel |
|---|---|---|---|---|---|---|---|
| `img3d.input_image` "Input Image" | input / source / source | none | `images: image_set` | `references` (bound: `reference_set_id`, `primary_image_id`, `image_count`) | `image_to_3d` | no | `references` (existing ReferencePanel content) |
| `img3d.prompt` "Prompt" (text-to-image) | input / source / source | none | `images: image_set` ("Generated image") | `workflow` | `prompt_to_3d` | no | `prompt` |
| `img3d.segmentation` "Segmentation" (2D parts, before reconstruction) | analyze / stage / parts | `source: [image_set]` req | `masks: masks` | `workflow` | `segmentation` | **yes** | `segmentation` |
| `img3d.image_to_3d` "Image-to-3D" (TRELLIS.2) | generate / stage / generate | `source: [image_set]` req | `mesh: mesh` | `settings` (bound: today's settings form) | `image_to_3d` | no | `generation_settings` (existing SettingsPanel content) |
| `img3d.part_extraction` "Part Extraction" (3D, after reconstruction) | parts / stage / parts | `mesh: [mesh]` req; `masks: [masks]` optional | `parts: part_set` | `workflow` | `part_extraction` | **yes** | `part_extraction` |
| `img3d.export` "Export" | output / sink / export | `asset: [mesh, part_set]` req, multiple | none | `workflow` | `image_to_3d` | no | `export` |

**Why masks do not feed Image-to-3D.** TRELLIS.2 does not consume masks. They are used only to lift labels after
reconstruction (`backend_generation_map.md` §6(a)–(b); `trellis2_internals.md` §3). The graph shows the real data
flow: Segmentation.masks → Part Extraction.masks.

**Values per node.** These are the defaults. The capability-driven defaults come from `/features`.

| Node | Key | Type / bounds | Default |
|---|---|---|---|
| prompt | `prompt` | text, trimmed length 1..`max_prompt_chars` (fallback 2000) | `''` |
| prompt | `negative_prompt` | text ≤ 1000. Shown only if `supports_negative_prompt` | `''` |
| prompt | `preset` | enum from `presets` | `'character'` |
| prompt | `style` | enum from `styles`, nullable | `null` |
| prompt | `model_id` | model (stage `text_to_image`, approved + installed only), or `null` | `null` (backend default) |
| prompt | `randomize_image_seed` | boolean | `true` |
| prompt | `image_seed` | seed 0..2147483647 | `0` |
| prompt | `part_hint` | boolean | `false` |
| segmentation | `subject` | enum from `subjects` | `'auto'` |
| segmentation | `segmenter_id` | model (stage `segmentation`), or `null` | `null` |
| segmentation | `extra_terms` | string_list ≤ `max_extra_terms` (fallback 20) items, each ≤ 40 chars | `[]` |
| part_extraction | `strategy` | enum from `strategies` | `default_strategy`, else the first strategy |
| part_extraction | `separate` | multi_enum from `separable_groups` | ids with `default_selected` (normally all seven: eyes, eyebrows, eyelashes, hair, clothes, accessories, detachable) |
| part_extraction | `regions` | multi_enum from `region_groups` | ids with `default_selected` (face, body) |
| export | `assembled_glb` | boolean | `true` |
| export | `part_files` | boolean | `false` |

The base `model.glb` is always kept. That is a fixed `keep_base_model: true`, so the base model stays byte-for-byte.
Capability-driven defaults are resolved lazily: a stored value that is no longer offered is replaced by the current
default, with a `invalid_value` warning shown once.

**Contributions** (`contribute(draft, values, caps)`, called in plan order on active, non-bypassed nodes only):

| Node | Contribution to `RequestDraft` |
|---|---|
| input_image | `draft.reference_set_id = v.reference_set_id; draft.primary_image_id = v.primary_image_id` |
| prompt | `draft.source = buildPromptSource(v, caps)`. Optional keys are added only per the rules in §4.2 and `request_keys`. |
| segmentation | `draft.parts.segmentation = 'auto'`, plus optional `subject`, `segmenter_id` and `extra_terms` when advertised |
| image_to_3d | `draft.settings = v.settings` (already `toRequestSettings(form, referenceMode)` from the bridge) |
| part_extraction | `draft.parts.enabled = true; decomposition, separate, regions` |
| export | when `draft.parts` exists: `draft.parts.export = { assembled_glb, part_files, keep_base_model: true }` |

`compile` finalizes the draft (§5.6).
- If Part Extraction is bypassed but Segmentation is active, the masks have no consumer. Segmentation is then dropped
  from the request with an `unconnected_node`-style warning ("Segmentation has no effect without Part Extraction"), so
  `parts` is not emitted.
- If Part Extraction is active and Segmentation is bypassed, `segmentation: 'off'` is sent. If the chosen strategy has
  `requires_segmentation`, validation raises `requires_segmentation` (an error) with an auto-fix action in the UI
  ("Turn on Segmentation").

### 5.4 Canonical template (`canonicalDoc()`): stable ids that linear renderers rely on

```json
{
  "schema": "img3d.workflow", "version": 1,
  "graph": {
    "nodes": [
      {"id": "in_image",  "type": "img3d.input_image",     "version": 1, "mode": "active"},
      {"id": "in_prompt", "type": "img3d.prompt",          "version": 1, "mode": "active"},
      {"id": "seg",       "type": "img3d.segmentation",    "version": 1, "mode": "bypass"},
      {"id": "i23d",      "type": "img3d.image_to_3d",     "version": 1, "mode": "active"},
      {"id": "parts",     "type": "img3d.part_extraction", "version": 1, "mode": "bypass"},
      {"id": "export",    "type": "img3d.export",          "version": 1, "mode": "active"}
    ],
    "edges": [
      {"id": "e_in_image_i23d", "from": {"node": "in_image", "port": "images"}, "to": {"node": "i23d",   "port": "source"}},
      {"id": "e_in_image_seg",  "from": {"node": "in_image", "port": "images"}, "to": {"node": "seg",    "port": "source"}},
      {"id": "e_i23d_parts",    "from": {"node": "i23d",     "port": "mesh"},   "to": {"node": "parts",  "port": "mesh"}},
      {"id": "e_seg_parts",     "from": {"node": "seg",      "port": "masks"},  "to": {"node": "parts",  "port": "masks"}},
      {"id": "e_i23d_export",   "from": {"node": "i23d",     "port": "mesh"},   "to": {"node": "export", "port": "asset"}},
      {"id": "e_parts_export",  "from": {"node": "parts",    "port": "parts"},  "to": {"node": "export", "port": "asset"}}
    ]
  },
  "values": { "img3d.prompt": {"…": "defaults"}, "img3d.segmentation": {}, "img3d.part_extraction": {}, "img3d.export": {} },
  "layout": { "positions": { "in_image": {"x": 0, "y": 0}, "in_prompt": {"x": 0, "y": 260},
                             "seg": {"x": 340, "y": 0}, "i23d": {"x": 340, "y": 260},
                             "parts": {"x": 680, "y": 130}, "export": {"x": 1020, "y": 260} },
              "collapsed": [], "viewport": {"x": 40, "y": 40, "zoom": 0.9} }
}
```

- An unconnected source node is legal and is not "the source". Today `in_prompt` stays in the graph but unconnected,
  and the Node UI shows it dimmed ("not connected"). This keeps the prompt text when the user switches back.
- **`setSource('prompt')`** is a pure op. It rewires the `source` edges of `i23d` and `seg` from `in_image` to
  `in_prompt`. Switching back does the reverse. Edge ids are recomputed as `e_<fromNode>_<toNode>` (deterministic;
  suffixed `_2…` on collision).
- **`setPartsEnabled(on)`** sets `seg` and `parts` to `active` or `bypass` together. It is the linear "Separate
  parts" toggle.
- **Templates:**
  - `image`: the canonical doc.
  - `image_parts`: seg and parts active.
  - `prompt`: source rewired.
  - `prompt_parts`: both changes.
- **Graphs edited in the Node UI and linear modes.** Linear modes find nodes **by type** (singletons), not by id.
  They therefore keep working after Node-UI edits that re-create nodes. If a required built-in type is missing, for
  example after the user deleted Image-to-3D, the linear renderers show the validation banner with a single "Restore
  default workflow" action (`workflow.loadTemplate`, values kept).

### 5.5 Validation (`validate`)

`ValidationContext` = `{ caps: FeatureSet, bound: { input_image: {image_count, reference_set_id} } }`.
Validation is cheap: it runs on every snapshot change and never touches stores.

| Code | Severity | Rule | Message (exact) |
|---|---|---|---|
| `unknown_node_type` | error if active, warning if bypassed | type not in the registry (kept, never dropped) | `Unknown node type "{type}". It will be ignored.` |
| `too_many_instances` | error | more than `maxInstances` nodes of one type | `Only one {title} node is supported.` |
| `cycle` | error | Kahn leftovers | `The workflow contains a cycle ({ids}).` |
| `type_mismatch` | error | edge `from` output type not in the `to.accepts` | `{fromTitle} → {toTitle}: {outType} cannot connect to {inLabel}.` |
| `no_source` | error | `image_to_3d.source` not connected | `Connect an Input Image or Prompt node to Image-to-3D.` |
| `multiple_sources` | error | `image_to_3d.source` and `segmentation.source` come from different source nodes | `Segmentation and Image-to-3D must use the same source.` |
| `missing_required_input` | error | required input unconnected on an active node | `{title}: connect "{portLabel}".` |
| `no_sink` | error | no active Export reachable from Image-to-3D | `Add an Export node.` |
| `capability_missing` | error (active node) | `caps[requires].available === false` | `{title} is unavailable: {reason}` |
| `node_locked` | error | an active `locked` node | `{title} is locked until refinement is approved.` |
| `input_missing` | error | The active source is input_image and `image_count === 0` → `Add at least one reference image.` The active source is prompt and the trimmed prompt is empty → `Enter a prompt to generate from.` | (as given) |
| `invalid_value` | error | `validateValues`: prompt too long, unknown strategy, an empty `separate` and empty `regions` while parts are active, an unapproved or uninstalled `model_id` | per node, e.g. `Prompt is longer than {n} characters.` |
| `requires_segmentation` | error | the strategy needs masks while Segmentation is bypassed | `{strategyLabel} needs Segmentation. Turn on Segmentation or choose another method.` |
| `unconnected_node` | warning | an active non-source node is unreachable from the source, or Segmentation is active without Part Extraction | `{title} is not connected and will be skipped.` / `Segmentation has no effect without Part Extraction.` |

### 5.6 Compile (`compile`)

```ts
export interface CompileInputs {
  readonly doc: WorkflowDoc
  readonly caps: FeatureSet
  /** Bound values supplied by workflow/bridge.ts (the only impure gatherer). */
  readonly bound: {
    readonly input_image: { reference_set_id: string | null; primary_image_id: string | null; image_count: number }
    readonly image_to_3d: { settings: GenerationSettings }       // = toRequestSettings(form, referenceMode)
  }
}
export type StepMap = readonly { readonly stepId: StepId; readonly nodeId: string }[]
export type CompileResult =
  | { readonly ok: true; readonly request: CreateJobRequest; readonly stepMap: StepMap
      readonly summary: { readonly source: 'image' | 'prompt'; readonly partsEnabled: boolean }
      readonly warnings: readonly GraphIssue[] }
  | { readonly ok: false; readonly issues: readonly GraphIssue[] }
```

Algorithm:
1. Run `validate`. Any error returns `{ok:false}`.
2. `plan = topoOrder(active nodes reachable from the active source to Export)`.
3. Start a draft `{settings: undefined, parts: undefined}`.
4. Call each planned node's `contribute`.
5. Finalize with a **fixed key order** so the JSON is byte-stable:
   - Image source: `{ reference_set_id, primary_image_id, settings }`.
   - Prompt source: `{ source, settings }`.
   - Inside `settings`, keep `toRequestSettings`'s order and append `parts` last only when the draft has parts.
   - Inside `parts`, the key order is `enabled, segmentation, decomposition, separate, regions, export`, then
     `subject, segmenter_id, extra_terms` when present.
6. `stepMap` = the planned nodes that have a `stepId`, in plan order.

**Golden outputs** (`test/frontend/workflow/compile.test.mjs`):
- **G1 (parity, hard constraint).** Canonical doc, image source,
  `bound.input_image = {rs_0123456789ab, img_0123456789ab, 1}`, settings =
  `toRequestSettings(DEFAULT_FORM, 'primary_only')`. The test asserts that
  `JSON.stringify(result.request) === JSON.stringify(legacyRequest)`, where `legacyRequest` is built exactly like
  `jobStore.ts:L208-212`:
  ```json
  {"reference_set_id":"rs_0123456789ab","primary_image_id":"img_0123456789ab","settings":{"resolution":1024,"seed":0,"randomize_seed":true,"polygon_target":500000,"texture_size":2048,"remove_background":true,"reference_mode":"primary_only"}}
  ```
  The same assertion runs with advanced overrides present, with `primary_image_id: null`, and for the legacy
  `FeatureSet` (G1 must not depend on `/features`).
- **G2.** Image plus parts (template `image_parts`, features `full` fixture with every `request_keys` entry):
  ```json
  {"reference_set_id":"rs_0123456789ab","primary_image_id":"img_0123456789ab","settings":{"resolution":1024,"seed":0,"randomize_seed":true,"polygon_target":500000,"texture_size":2048,"remove_background":true,"reference_mode":"primary_only","parts":{"enabled":true,"segmentation":"auto","decomposition":"image_masks","separate":["eyes","eyebrows","eyelashes","hair","clothes","accessories","detachable"],"regions":["face","body"],"export":{"assembled_glb":true,"part_files":false,"keep_base_model":true},"subject":"auto"}}}
  ```
- **G3.** Prompt, no parts:
  `{"source":{"type":"prompt","prompt":"a knight in silver armor","preset":"character","randomize_image_seed":true},"settings":{…}}`.
  It has **no** `reference_set_id` or `primary_image_id` key, and no `style` because it is null.
- **G4.** Prompt plus parts with `request_keys` lacking `subject`: `parts` has no `subject` key.
- **G5.** The legacy fallback `FeatureSet` with template `image_parts` gives `{ok:false}` and `capability_missing` on
  `seg` and `parts`.

### 5.7 Run-state projection (`projectRunState`)

**Which job is shown:** the active job if it is not terminal; otherwise the viewed job; otherwise none. With no job,
every node is `idle`, except bypassed nodes (`bypassed`), unavailable nodes (`unavailable`) and locked nodes
(`locked`).

**Restricting to the compiled plan.** When the shown job was submitted from this session,
`workflowStore.runIndex[job.id]` (the `StepMap` saved by `job.generate`) limits the projection to the node ids that
were actually compiled. A node added or re-created after Run therefore shows `idle`, not the old job's state. For jobs
from other sessions or tabs, mapping is by node type.

**Path A: `job.steps` present** (new backend). For each step, `nodeType = 'img3d.' + step.id`. Find the node of that
type (singleton) and map:

| `step.status` | `RunStatus` |
|---|---|
| `pending` | `queued` |
| `running` | `running` (`progress = step.progress`) |
| `done` | `done` |
| `warning` | `done` (`warning: true`) |
| `skipped` | `skipped` |
| `failed` | `failed` |

`message = step.message`. Nodes whose type has no step stay `idle`. Unknown step ids are ignored.

**Path B: legacy job (`steps == null`).** `job.kind` is absent, so it is image_to_3d.

| `job.status` / `progress.stage` | input_image | image_to_3d | export | prompt / segmentation / part_extraction |
|---|---|---|---|---|
| `queued` (any stage) | done | queued ("Queued, position {n}") | queued | idle |
| `running` + `waiting_for_model` | done | queued ("Waiting for model") | queued | idle |
| `running` + `preprocessing`…`exporting` | done | running (`progress = percent`, message = `stage_label`) | queued | idle |
| `running` + `finalizing` | done | done | running | idle |
| `completed` | done | done | done | idle |
| `failed` at stage S | done | failed if `stage_index(S) ≤ 7`, else done | failed if S = finalizing, else skipped | idle |
| `cancelled` | done | skipped | skipped | idle |

The stage index comparison reuses the indices in `lib/stages.ts` (1..8) [V]. The legacy `ProgressCard` stage list
remains the detailed view; nodes show only the summary.

### 5.8 Serialization (`serialize.ts`)

- **File format** (`.img3d-workflow.json`):
  `{ "schema": "img3d.workflow", "version": 1, "app": "img3d-frontend@<version>", "doc": WorkflowDoc }`.
- **What is never exported:**
  - bound values (reference set ids, settings form);
  - tokens;
  - job ids.

  A workflow file is a recipe, not a run record.
- **`fromFile`:**
  1. Parse the JSON. A `SyntaxError` returns `{ok:false, message}`.
  2. Check `schema`.
  3. Use `migrateDoc` per version.
  4. Run each node's `migrate(values, fromVersion)`.
  5. Keep unknown node types with mode forced to `bypass`, and add a warning.
  6. Clamp layout numbers to finite values in ±100000.
  7. Reject more than 64 nodes or more than 256 edges.
- **The persisted store** uses the same doc shape (§7.2). If an unknown newer version is found in localStorage, the
  store moves the raw string to `img3d.workflow.backup`, loads `canonicalDoc()`, and the app shows the info toast
  "Saved workflow was created by a newer version; the default workflow was restored."

### 5.9 Edit operations and undo

- All ops in `graph.ts` are pure and return a new `WorkflowDoc`. Unchanged parts are structurally shared, so React
  Compiler memoization holds.
- **Undo history** lives in `workflowStore`: `past: WorkflowDoc[]` and `future: WorkflowDoc[]`, capped at 50.
  - Recorded: structure ops (add, remove, connect, disconnect, mode, source, parts toggle, template, import,
    auto-layout) and `moveNodes` (only on drag stop).
  - Not recorded: `setValues`. Text fields keep their native undo, and keystrokes must not flood history.
  - Not recorded: `setViewport` and `setCollapsed`.

### 5.10 `docFromJob` ("Reuse settings" and "Open in workflow")

Input: `job.kind`, `job.source`, and `job.settings.parts` (both may be absent).
- **Source:** prompt if `kind === 'prompt_to_3d'`, else image.
- **Prompt values** come from `job.source`.
- **Parts:** enabled iff `settings.parts?.enabled`. Values come from it, and unknown ids are dropped against the
  current caps with a warning.
- **Layout:** the current one is kept.

The image-to-3D settings continue to use the existing `formFromJobSettings` path (`settingsStore.ts:L138-157` [V]).
The `job.reuseSettings` command applies both.

### 5.11 Locked refinement slot (future tuning nodes)

- `registry.ts` accepts `category: 'refine'` only together with `locked: true` and `requires: 'part_refinement'`.
  Anything else throws at registry creation.
- `compile` never calls `contribute` on locked nodes, and `validate` raises `node_locked` if one is active.
- `palette(caps, flags)` shows a "Refinement — awaiting approval" group only when the build flag
  `FEATURES.partRefinementPreview` is true (default **false**). This milestone ships **no** refine node definitions.
- A unit test proves that the guard rejects an unlocked refine def. This is the "registry slot" with no algorithm
  behind it.

### 5.12 Linear plan (`linearSteps`)

`linearSteps(doc, registry, caps)` returns, in this fixed order:

| Step | Nodes | Title | Notes |
|---|---|---|---|
| `source` | both source types | "Source" | `activeSourceType` = the type connected to Image-to-3D |
| `generate` | image_to_3d | "Generation" | – |
| `parts` | segmentation + part_extraction | "Parts" | `toggleable: true`; `enabled` = both active; `unavailable` when both capabilities are missing (reason of part_extraction) |
| `export` | export | "Export" | – |

Steps whose node types are missing from the doc are omitted, and a banner is shown (§5.4). This is the **only**
function linear renderers use to decide what to show. That is how a Node-UI edit (for example, bypassing
Segmentation) is reflected in Default, Mobile and Tablet without separate logic.

---------------------------------------------------------------------------------------------------

## 6. Command layer (`src/commands`)

### 6.1 Core types (`commands/core/types.ts`)

```ts
export type CommandSource = 'ui' | 'shortcut' | 'palette' | 'system'
export type BlockCode =
  | 'offline' | 'not_ready' | 'engine' | 'input_missing' | 'capability' | 'busy'
  | 'queue_full' | 'invalid_workflow' | 'not_allowed' | 'feature_off'
export type Availability =
  | { readonly enabled: true; readonly note: string | null }
  | { readonly enabled: false; readonly code: BlockCode; readonly reason: string }
export const ENABLED: Availability = Object.freeze({ enabled: true, note: null })

export interface CommandError {
  readonly code: 'blocked' | 'busy' | 'api' | 'invalid' | 'cancelled' | 'unexpected'
  readonly message: string
  readonly status?: number          // HTTP status for 'api'
  readonly apiCode?: string         // backend error.code for 'api'
}
export type Outcome<R> = { readonly ok: true; readonly value: R } | { readonly ok: false; readonly error: CommandError }

export type RevealTarget =
  | 'inputs' | 'prompt' | 'settings' | 'parts_options' | 'run' | 'viewer' | 'parts' | 'history' | 'export' | 'app_settings'

export interface RunEnv { readonly snapshot: () => AppSnapshot; readonly source: CommandSource }

export interface CommandSpec<P, R> {
  readonly id: string                                   // 'domain.verb', unique (registry throws on duplicates)
  readonly title: string                                // shown in shortcut help / palette / error toasts
  readonly availability: (snap: AppSnapshot, payload: P) => Availability
  readonly run: (payload: P, env: RunEnv) => Promise<Outcome<R>>
  readonly key?: (payload: P) => string                 // pending key, default = id (e.g. 'job.cancel:job_…')
  readonly exclusive?: boolean                          // default true: reject while the same key is pending
  readonly shortcut?: ShortcutDef
  readonly announce?: { readonly start?: string; readonly success?: string | ((value: R) => string); readonly failure?: boolean }
  readonly reveal?: RevealTarget | ((value: R) => RevealTarget | null)
  readonly toastOnError?: boolean                       // default true for 'api' / 'unexpected' / 'invalid'
  readonly trace?: boolean                              // default true; false for high-frequency (hover)
}
export const defineCommand = <P = void, R = void>(spec: CommandSpec<P, R>): CommandSpec<P, R> => spec
```

**Spec factories and binding.**
- Each domain exports a pure factory from `commands/specs/<domain>.ts`, for example
  `createJobCommands(ports: JobPorts)`. It returns an object of specs such as `{ generate, cancel, retry, … }`.
- `commands/bind/<domain>.ts` imports the real stores and API. It builds the ports, then exports
  `export const JobCommands = registerCommands(createJobCommands(jobPorts))`.
- Components import `JobCommands` and call `useCommand(JobCommands.generate, undefined)`.
- Unit tests call the factory with fake ports. They never touch stores, axios or `import.meta.env`.
- No central command map exists, so slices stay independent. `registerCommands` only feeds the runtime registry used
  for shortcut help and the duplicate-id check.

### 6.2 Bus (`commands/core/bus.ts`, not compiled by React Compiler, so `try/finally` is fine)

```ts
export interface BusPorts {
  notify(tone: 'info' | 'success' | 'warning' | 'error', title: string, message?: string): void   // → toast
  announce(message: string, politeness: 'polite' | 'assertive'): void                            // → Announcer
  reveal(target: RevealTarget): void                                                              // → navigationStore
  trace(event: BusEvent): void                                                                    // dev logger (?debug=commands)
}
export function configureBus(ports: BusPorts): void            // called once in app/bootstrap.ts
export async function dispatch<P, R>(spec: CommandSpec<P, R>, payload: P, opts?: { source?: CommandSource }): Promise<Outcome<R>>
export type BusEvent =
  | { type: 'command.blocked'; id: string; source: CommandSource; code: BlockCode }
  | { type: 'command.started'; id: string; key: string; source: CommandSource }
  | { type: 'command.succeeded'; id: string; key: string; ms: number }
  | { type: 'command.failed'; id: string; key: string; ms: number; error: CommandError }
```

Pipeline:
1. `snap = readSnapshot()`.
2. `av = spec.availability(snap, payload)`. If it is blocked:
   - trace `command.blocked`;
   - if the source is `shortcut`, announce `av.reason` (assertive);
   - return `{ok:false, error:{code:'blocked', message: av.reason}}`.
3. If `exclusive !== false` and the key is pending, return `busy`.
4. Add the key to `pendingStore`, trace `command.started`, and announce `start`.
5. `await spec.run(payload, {snapshot: readSnapshot, source})`. Thrown exceptions are caught and become `unexpected`;
   the stack goes to `console.error` in dev only.
6. In `finally`, remove the pending key.
7. On success: trace, announce `success` (polite), and call `ports.reveal(...)` when `reveal` is set.
8. On failure:
   - trace;
   - when `toastOnError !== false` and the code is `api`/`unexpected`/`invalid`, call
     `notify('error', spec.title + ' failed', message)`;
   - when `announce.failure`, announce the message (assertive).

A bus that has not been configured yet (tests, or before bootstrap) uses no-op ports.

### 6.3 `AppSnapshot` (`commands/core/snapshot.ts`, built on a `zustand/vanilla` store)

Every field is a primitive or a stored reference. `patchSnapshot(partial)` changes the snapshot object only when some
field changes by `Object.is`. Re-renders therefore follow availability-relevant changes only. Job progress percent,
for example, is **not** in the snapshot, so progress frames (up to 5/s) do not re-render command consumers.

| Field | Type | Kept current by (slice) |
|---|---|---|
| `authPhase` | `'checking' \| 'unreachable' \| 'login' \| 'ready'` | `state/sync/system.ts` (S2) |
| `reachable` | `boolean \| null` | same |
| `system` | `SystemStatus \| null` (stored reference) | same |
| `engineLoadPending` | `boolean` | same |
| `features` | `FeatureSet` (never null; `source: 'loading' \| 'legacy' \| 'backend'`) | `state/sync/features.ts` (S2) |
| `referenceSetId` | `string \| null` | `state/sync/references.ts` (S2) |
| `primaryImageId` | `string \| null` | same |
| `referenceImageCount` | `number` | same |
| `uploadsPending` | `number` | same |
| `activeJobId`, `activeJobStatus` | `string \| null`, `JobStatus \| null` | `state/sync/jobs.ts` (S2) |
| `activeJobKind` | `JobKind \| null` | same |
| `viewedJobId`, `viewedJobStatus` | `string \| null`, `JobStatus \| null` | same |
| `viewedJobHasParts` | `boolean` | same |
| `submitting` | `boolean` | same |
| `historyStatus` | `'idle' \| 'loading' \| 'ready' \| 'error'` | same |
| `workflowDoc` | `WorkflowDoc` | `state/sync/workflow.ts` (S1) |
| `canUndo`, `canRedo` | `boolean` | same |
| `uiMode`, `resolvedUiMode`, `availableUiModes` | `UiMode`, `UiMode`, `readonly UiMode[]` | `state/sync/prefs.ts` (S3) |
| `appSettingsOpen` | `boolean` | `state/sync/nav.ts` (S3) |
| `viewerReady` | `boolean` | `state/sync/viewer.ts` (S4) |
| `partsStatus` | `'absent' \| 'idle' \| 'pending' \| 'loading' \| 'ready' \| 'error'` (for the viewed job) | `state/sync/parts.ts` (S4) |
| `viewerSource` | `'model' \| 'parts'` | same |
| `hasAssembledModel` | `boolean` | same |

`DEFAULT_SNAPSHOT` declares every field, so a slice that has not landed yet still type-checks and behaves
conservatively. For example, `features.source = 'loading'` and `viewerReady = false`.

### 6.4 React bindings (`commands/core/react.ts`) and compiler and zustand rules

```ts
export function useSnapshot(): AppSnapshot                       // useStore(snapshotStore, (s) => s.snapshot)
export function useCommand<P, R>(spec: CommandSpec<P, R>, payload: P): {
  readonly availability: Availability
  readonly pending: boolean
  readonly run: (override?: P) => Promise<Outcome<R>>
}
export function useCommandPending(key: string): boolean           // selector returns a boolean primitive
export { dispatch } from './bus.ts'
```

`useCommand` computes `spec.availability(useSnapshot(), payload)` during render. Any new object is created in the
render body, not in a selector, so it is safe under zustand v5.

Rules for all new UI code:
- No `try/finally`, and no `?.`/`??`/`&&` inside `try`, in components or hooks. Async handlers call `dispatch` and use
  the returned `Outcome`.
- No manual `useMemo` or `useCallback`.
- No `ref.current` reads during render.
- No `setState` in effects (`react/set-state-in-effect`). Use derived state or a `key` remount.
- Command tables, placement maps, nav item lists and node registries live in `.ts` files, never in `.tsx`
  (`react/only-export-components`).
- Fire-and-forget calls use `void dispatch(...)` (`typescript/no-floating-promises`).

### 6.5 Command catalogue (ids are final; payload and result types are exact)

Legend: **Blocks** = availability conditions in order (the first match wins). **Fx** = effect through the ports.
**Ann** = live-region announcement.

**Job and session (S2: `specs/job.ts`, `specs/system.ts`, `specs/auth.ts`)**

| Id | Payload → Result | Blocks | Fx | Ann / reveal / shortcut |
|---|---|---|---|---|
| `job.generate` | `void → {jobId: string}` | `generateAvailability` (below) | `loadEngine` if `not_loaded`; `compileCurrent`; `submitJob`; `rememberRun` | start "Submitting generation"; success "Generation queued"; reveal `run`; **Ctrl/Cmd+Enter** (global) |
| `job.cancel` | `{jobId?: string} → void` (default active job) | no target → `not_allowed` "Nothing to cancel."; `supports_cancel` false → `not_allowed` "This engine does not support cancellation." | `jobStore.cancel(id)` (key `job.cancel:<id>`) | success "Cancellation requested" |
| `job.retry` | `{jobId: string, from?: 'image' \| 'prompt'} → {jobId: string}` | the job is not terminal → `not_allowed`; the same busy, queue and engine checks as generate; `from:'prompt'` while prompt_to_3d is unavailable → capability | `from:'image'` (default): `jobStore.retry(job)` (today's behaviour, uses the materialized reference); `from:'prompt'`: resubmit `{source: toPromptSourceIn(job.source), settings: job.settings}` | reveal `run` |
| `job.remove` | `{jobId} → void` | not terminal → `not_allowed` "Cancel the job before deleting it." | `jobStore.remove` | – |
| `job.view` | `{jobId: string \| null} → void` | – | `jobStore.view` | reveal `viewer` when not null |
| `job.reuseSettings` | `{jobId} → void` | not completed → `not_allowed` | `settings.replaceForm(formFromJobSettings(...))` plus `workflowStore.commit(docFromJob(...))` | Ann "Settings restored from job {short}" |
| `job.downloadGlb` | `{jobId} → void` | not completed → `not_allowed` | `downloadJobGlb` + `saveBlob`; progress goes to `transferStore['glb:'+jobId]` | – |
| `job.loadHistory` | `void → void` | offline | `jobStore.loadHistory` | – |
| `system.loadEngine` | `void → void` | pending, or state ∈ {loading, ready} → `not_allowed` | `systemStore.loadEngine` | – |
| `system.retryConnection` | `void → void` | – | `systemStore.requestRetry` | – |
| `auth.login` | `{username, password} → boolean` | phase ≠ login | `authStore.login` | – |
| `auth.logout` | `void → void` | phase ≠ ready | `authStore.logout` | – |

`generateAvailability(snap)` (exported for the parity test) evaluates the following in order:
1. `reachable === false` → offline "The backend is unreachable."
2. `!system` → not_ready "Waiting for the backend…"
3. The engine state is `unavailable` or `error` → engine, with the summary text exactly as today
   (`useGenerateAvailability.ts:L26-29`, `L49-53`). `useEngineProblem()` in `state/read/system.ts` exposes the summary and
   details.
4. Workflow issues from `validate`, source-specific first:
   - "Add at least one reference image." / "Enter a prompt to generate from." / "Prompt-to-3D is unavailable: {reason}";
   - then capability issues for active part nodes;
   - then "Fix the workflow: {first error message}".
5. The source is image and `uploadsPending > 0` → busy "Wait for uploads to finish."
6. `activeJobStatus` is non-terminal → busy "A generation is already in progress."
7. The queue is full → queue_full "The generation queue is full."
8. `submitting` → busy "Submitting…"
9. The `note` field is unchanged from today: the engine-loading and queue-ahead texts (L64-72).

For the image source with default settings, this order and these strings equal today's (`L55-73`). The compatibility
adapter `useGenerateAvailability()` returns the same `{canGenerate, reason, note, busy, engineProblem}` interface, so
unmigrated callers keep working.

**Error mapping for `job.generate`, `job.retry` and `parts.rebuild`** (`describeSubmitError`, extended from
`jobStore.ts:L81-85`):

| Error | Message |
|---|---|
| 429 | "The generation queue is full. Wait for running jobs to finish." |
| unreachable | "The backend is unreachable." |
| 422 or 503 with code `capability_unavailable` | "This backend cannot run that pipeline right now: {message}" (also triggers `features.refresh()`) |
| 422 whose `details[].loc` contains `"parts"` | "Part options were rejected by the backend: {message}" |
| 422 whose loc contains `"source"` | "The prompt was rejected by the backend: {message}" |
| otherwise | `message` |

**Input, references and settings (S2: `specs/references.ts`, `specs/settings.ts`)**

| Id | Payload | Blocks | Fx |
|---|---|---|---|
| `references.upload` | `{files: File[], slot?: ReferenceSlot}` | offline | `referenceStore.uploadFiles` |
| `references.replace` | `{image: ReferenceImage, file: File}` | – | `replaceImage` |
| `references.remove` | `{imageId}` | – | `removeImage` |
| `references.setPrimary` | `{imageId}` | already primary → not_allowed | `setPrimary` |
| `references.move` | `{imageId, slot}` | – | `moveImage` |
| `references.newSet` | `void` | uploads pending → busy | `newSet` |
| `references.clearTileError` | `{slot}` | – | `clearTileError` |
| `references.reload` | `void` | – | `refresh` |
| `settings.update` | `{patch: Partial<Omit<SettingsForm,'advanced'>>}` | – | `settingsStore.update` |
| `settings.updateSampler` | `{key: SamplerKey, field: SamplerField, value: number \| null}` | – | `updateSampler` |
| `settings.resetSampler` | `{key: SamplerKey}` | – | `resetSampler` |
| `settings.resetAdvanced` | `void` | – | `resetAdvanced` |
| `settings.resetAll` | `void` | – | `resetAll(defaults, capabilities)` |
| `settings.setAdvancedOpen` | `{open: boolean}` | – | `setAdvancedOpen` |

**Workflow (S1: `specs/workflow.ts`)**

| Id | Payload | Blocks | Fx (pure op, then `workflowStore.commit`) | History |
|---|---|---|---|---|
| `workflow.setSource` | `{source: 'image' \| 'prompt'}` | – | `setSource` | yes |
| `workflow.setPartsEnabled` | `{enabled: boolean}` | `enabled` while `segmentation` and `part_extraction` are both unavailable → capability "{reason}" | `setPartsEnabled` | yes |
| `workflow.setParam` | `{nodeType: string, key: string, value: unknown}` | `valueSource ≠ 'workflow'` → not_allowed "Edit this value in its own panel."; the key is not in `params` → not_allowed | `setValues` (validated and clamped per ParamDef) | no |
| `workflow.resetParams` | `{nodeType}` | – | `resetValues` | no |
| `workflow.setNodeMode` | `{nodeId, mode: NodeMode}` | `!canBypass` and mode is bypass → not_allowed "{title} cannot be bypassed." | `setMode` | yes |
| `workflow.addNode` | `{type: string, position?: {x,y}}` | unknown type; `maxInstances` reached → not_allowed "Only one {title} node is supported."; locked → feature_off | `addNode` (id `n_` + 6 base36) | yes |
| `workflow.removeNodes` | `{nodeIds: string[]}` | – | `removeNodes` (plus attached edges) | yes |
| `workflow.connect` | `{from: PortRef, to: PortRef}` | `canConnect` failure → `not_allowed` with its message | `connect` (replaces the existing edge on a non-`multiple` input) | yes |
| `workflow.disconnect` | `{edgeIds: string[]}` | – | `disconnect` | yes |
| `workflow.moveNodes` | `{positions: Record<string,{x,y}>}` | – | `moveNodes` | yes (drag stop only) |
| `workflow.setViewport` | `{viewport: {x,y,zoom}}` | – | `setViewport` | no |
| `workflow.setCollapsed` | `{nodeId, collapsed: boolean}` | – | `setCollapsed` | no |
| `workflow.autoLayout` | `void` | – | `autoLayout` | yes |
| `workflow.undo` / `workflow.redo` | `void` | `!canUndo` / `!canRedo` → not_allowed | store history | – |
| `workflow.loadTemplate` | `{templateId: 'image' \| 'image_parts' \| 'prompt' \| 'prompt_parts'}` | – | template doc; **values kept** | yes |
| `workflow.importFile` | `{text: string}` | – | `fromFile` (warnings toasted) | yes |
| `workflow.exportFile` | `void → {filename: string}` | – | `toFile`, then a `saveBlob` of `img3d-workflow-<yyyymmdd-hhmm>.img3d-workflow.json` | – |

**Parts and viewer (S4: `specs/parts.ts`, `specs/viewer.ts`)**

| Id | Payload | Blocks | Fx |
|---|---|---|---|
| `parts.load` | `{jobId: string, force?: boolean}` | the job has no `result.parts` → not_allowed | `GET manifest` → `parsePartManifest` → `partsStore.setManifest` |
| `parts.setVisibility` | `{partIds: string[], visible: boolean}` | no manifest → not_allowed | `partsStore.view.hidden` |
| `parts.toggle` | `{partId}` | – | toggle hidden |
| `parts.showAll` | `void` | – | clear hidden and isolate |
| `parts.isolate` | `{partId: string \| null}` | – | `view.isolated` |
| `parts.select` | `{partId: string \| null}` | – | `view.selected` (the list scrolls the row into view) |
| `parts.hover` | `{partId: string \| null}` | – | `view.hovered` (`trace:false`, `exclusive:false`) |
| `parts.setViewerSource` | `{source: 'model' \| 'parts'}` | `parts` and no `assembled_url` → not_allowed "This result has no assembled parts model." | `view.source` |
| `parts.setExplode` | `{amount: number}` (0..1) | source ≠ parts → not_allowed | `view.explode` |
| `parts.download` | `{jobId, target: {partId: string} \| 'assembled' \| 'manifest'}` | URL missing → not_allowed | axios blob plus `saveBlob` (auth header; progress in `transferStore`) |
| `parts.rebuild` | `{jobId, options: PartOptionsIn} → {jobId}` | `features.part_extraction.rebuild !== true` → feature_off; the generate busy, queue and engine checks | `POST /jobs/{id}/parts/rebuild` |
| `viewer.setOption` | `{key: keyof ViewerOptions, value}` | – | `uiStore.setViewerOption` |
| `viewer.resetCamera` | `void` | `!viewerReady` | `viewerRuntime.api.resetCamera()` |
| `viewer.screenshot` | `void` | `!viewerReady` | today's `ViewerPanel.screenshot` logic (`ViewerPanel.tsx:L151-159` [V]), moved here |

**Navigation, app and preferences (S3: `specs/nav.ts`, `specs/app.ts`, `specs/prefs.ts`)**

| Id | Payload | Fx |
|---|---|---|
| `nav.reveal` | `{target: RevealTarget}` | `navigationStore.reveal = {target, seq+1}` |
| `nav.setDefaultTab` | `{tab: 'inputs' \| 'settings' \| 'viewer' \| 'history'}` | Default mobile tab |
| `nav.setHistoryOpen` | `{open: boolean}` | Default history drawer |
| `nav.openSheet` | `{sheet: SheetId, snap?: SnapId}` | Mobile and tablet-bottom sheets |
| `nav.closeSheet` | `void` | – |
| `nav.setSheetSnap` | `{snap: SnapId}` | – |
| `nav.setSection` | `{section: SectionId}` | Mobile and Tablet active destination |
| `nav.setSidePanelOpen` | `{open: boolean}` | Tablet rail side panel |
| `nav.selectNodes` | `{nodeIds: string[]}` | Node selection (inspector) |
| `nav.setNodeView` | `{view: 'canvas' \| 'list'}` | Node UI presentation (session; the default comes from prefs) |
| `nav.setInspectorOpen` | `{open: boolean}` | – |
| `app.openSettings` | `{section?: 'interface' \| 'node' \| 'models' \| 'about'}` | **Ctrl/Cmd+,** |
| `app.closeSettings` | `void` | – |
| `app.openShortcutHelp` | `void` | **?** (outside text fields) |
| `prefs.setUiMode` | `{mode: UiMode}` | Persist and switch. Announces "Switched to {label} interface". Blocked with `not_allowed` "This interface is not included in this build." when the mode is not in `availableUiModes`. |
| `prefs.setDensity` | `{density: 'auto' \| 'comfortable' \| 'touch'}` | – |
| `prefs.setMotion` | `{motion: 'system' \| 'reduce'}` | – |
| `prefs.setNodeOptions` | `{patch: Partial<NodePrefs>}` | – |

`SheetId` = `'create' | 'parts' | 'library' | 'progress' | 'status' | 'blocked'`.
`SnapId` = `'peek' | 'half' | 'full'`.
`SectionId` = `'create' | 'viewer' | 'parts' | 'library'`.

**Models (S2: `specs/models.ts`)**

| Id | Payload | Blocks | Fx |
|---|---|---|---|
| `models.refresh` | `void` | – | `featuresStore.refresh` |
| `models.install` | `{modelId: string, licenseVersion: string, acceptLicense: true}` | `!entry.install.supported` → feature_off with `entry.install.hint`; `entry.license.requires_acceptance && acceptLicense !== true` → not_allowed; `!entry.approved` → not_allowed "This model has not been approved for this installation."; state ∈ {installing, installed, loaded} → not_allowed | E10. Progress arrives through `models.updated` or a 2 s poll of `/features` while any entry is `installing`. |

`models.install` is only reachable from the licence dialog (§8.7). The dialog needs an explicit checkbox
("I accept the {licence name} licence") and shows the licence URL. Model **approval** is a backend setting reflecting
the user's decision. The UI never approves or picks a model by itself.

### 6.6 Shortcuts (`commands/core/shortcuts.ts` pure parser plus `app/ShortcutHost.tsx`, the single global listener)

| Scope | Keys | Command | Notes |
|---|---|---|---|
| global | Ctrl/Cmd+Enter | `job.generate` | Blurs a focused input or textarea first, so a numeric draft commits (today's behaviour, `GenerateCard.tsx:L45-47` [V]). Ignores repeats. Disabled while a modal `<dialog>` other than App settings is open. |
| global | Ctrl/Cmd+, | `app.openSettings` | `preventDefault` |
| global (not typing) | `?` | `app.openShortcutHelp` | – |
| node canvas focus | Ctrl/Cmd+Z; Ctrl/Cmd+Shift+Z or Ctrl+Y | `workflow.undo` / `workflow.redo` | Ignored in text fields. |
| node canvas focus | Delete / Backspace | `workflow.removeNodes` (selected) | React Flow `deleteKeyCode={null}`; the command handles deletion. |
| node canvas focus | `/` or Ctrl/Cmd+K | open the Add-node palette | – |
| node canvas focus | Alt+C | `workflow.setCollapsed` | – |
| node canvas focus | Ctrl/Cmd+B | `workflow.setNodeMode` (toggle bypass) | – |
| node canvas focus | `.` | fit view (React Flow instance, local) | – |
| node canvas focus | Ctrl/Cmd+S | `workflow.exportFile` | Only while the canvas has focus; never hijacked globally. |

`ShortcutHost` is mounted once in `AppRoot`. It replaces the `shortcut` prop of `GenerateCard`
(`GenerateCard.tsx:L51-60`), and that prop becomes a no-op that is kept for compatibility. Canvas-scoped shortcuts are
registered by `renderers/node/flow/useCanvasShortcuts.ts` on the canvas element only.

### 6.7 Announcements and errors

- `app/Announcer.tsx` renders two visually hidden live regions: `role="status" aria-live="polite"` and
  `role="alert" aria-live="assertive"`.
- The bus port `announce` pushes into `announcerStore`. A message is cleared after 4 s, and an identical message within
  1 s is deduped.
- Toasts stay as they are (`toastStore`, max 5, deduped).
- Job transitions (completed, failed, cancelled) keep their existing toasts in `jobStore.notifyTransition`
  (`L69-75` [V]), and also announce politely through `state/reactions/jobAnnouncements.ts`.

---------------------------------------------------------------------------------------------------

## 7. State: stores, persistence, state machines

### 7.1 New and changed stores

| Store | File (slice) | Persisted | Owns |
|---|---|---|---|
| `featuresStore` (new) | `store/featuresStore.ts` (S2) | no | `raw: FeaturesOut \| null`, `status: 'idle' \| 'loading' \| 'ready' \| 'legacy' \| 'error'`, `set: FeatureSet` (normalized), `refresh()`, `setFeatures(raw)`, `applyModelUpdate(entry)` |
| `workflowStore` (new) | `store/workflowStore.ts` (S1) | `img3d.workflow` v1 (doc only) | `doc`, `past`, `future`, `commit(doc, {history})`, `undo()`, `redo()`, `runIndex: Record<jobId, StepMap>` (session only) |
| `partsStore` (new) | `store/partsStore.ts` (S4) | no | `byJob: Record<jobId, {status, manifest: PartManifestView \| null, error: string \| null, loadedAt}>`, `view: {jobId, hidden: string[], isolated, selected, hovered, source: 'model' \| 'parts', explode}`; actions only called from `bind/parts.ts` |
| `preferencesStore` (new) | `store/preferencesStore.ts` (S3) | `img3d.preferences` v1 | `uiMode`, `density`, `motion`, `node` options |
| `navigationStore` (new) | `store/navigationStore.ts` (S3) | no | `reveal`, `defaultTab` (moved from `uiStore.mobileTab`), `historyOpen` (moved), `sheet`, `snap`, `section`, `sidePanelOpen`, `nodeSelection`, `nodeView`, `inspectorOpen`, `appSettingsOpen`, `appSettingsSection`, `shortcutHelpOpen` |
| `transferStore` (new) | `store/transferStore.ts` (S2) | no | download progress keyed `glb:<job>` / `part:<job>:<part>` |
| `announcerStore`, `pendingStore`, `snapshotStore` (new, vanilla) | `commands/core/*` (S0b) | no | live-region queue; pending command keys; `AppSnapshot` |
| `jobStore` (changed) | S2 | unchanged (`img3d.viewedJobId`) | adds `submitRequest(request)`; `submit()` delegates to it with the legacy builder; `retry(job, {from})`. `Job` type gains optional fields. |
| `uiStore` (unchanged) | – | `img3d.ui` v1 | `viewer` options. `mobileTab` and `historyOpen` stay only for the hidden tuner's `BlenderSetupDialog`; new code uses `navigationStore`. |
| `settingsStore`, `referenceStore`, `systemStore`, `authStore`, `toastStore`, `meshTunerStore` | unchanged | unchanged | – |

### 7.2 localStorage keys

The existing keys keep their names and shapes: `img3d.auth.token`, `img3d.referenceSetId`, `img3d.viewedJobId`,
`img3d.settings` v1, `img3d.ui` v1, `img3d.meshTuner` v1, and `img3d.blenderSetup.dismissedMessage` (`lib/storage.ts`).

New keys added to `STORAGE_KEYS` (S0):

| Key | Writer | Version | Shape (inside zustand `persist` `{state, version}`) | Merge / validation |
|---|---|---|---|---|
| `img3d.preferences` | preferencesStore | 1 | `{ uiMode: 'default' \| 'node' \| 'mobile' \| 'tablet', density: 'auto' \| 'comfortable' \| 'touch', motion: 'system' \| 'reduce', node: { linkStyle: 'bezier' \| 'smoothstep' \| 'straight', minimap: boolean, snapToGrid: boolean, view: 'auto' \| 'canvas' \| 'list' } }` | Each field is checked against its `as const` tuple. Invalid or missing values fall back to `{uiMode:'default', density:'auto', motion:'system', node:{linkStyle:'bezier', minimap:true, snapToGrid:false, view:'auto'}}`. |
| `img3d.workflow` | workflowStore | 1 | `{ doc: WorkflowDoc }` | `migrateDoc`. On failure the raw value goes to `img3d.workflow.backup` and `canonicalDoc()` is used. Values are revalidated against caps lazily (§5.3). |
| `img3d.workflow.backup` | workflowStore | – | the raw string | written only on an incompatible load |

Cross-tab: `preferencesStore` listens to `storage` events for its own key and calls `persist.rehydrate()`. The
workflow is **not** synced across tabs; the last writer wins on reload, which is documented in App settings → About.

### 7.3 State machines

**Generation as observed by the client.** Derived from `jobStore` and the snapshot; nothing new is stored.

```
          job.generate (enabled)
 idle ───────────────────────────► submitting ──202──► queued ──job.updated(running)──► running
  ▲                                     │                 │                              │
  │◄──────── error (toast, Ann) ────────┘                 │ job.cancel                   │ job.cancel
  │                                                       ▼                              ▼
  │                                                  cancelling ───────────────────► cancelled
  │◄──────────────────────── completed │ failed │ cancelled ◄────────────────────────────┘
```

**Parts state per viewed job** (`partsStore.byJob[jobId].status`):

```
absent   (result == null or result.parts == null)            ── never fetched
idle ── parts.load ──► loading ──200──► ready ── job.updated with changed parts summary ──► idle (refetch)
                         ├──409──► pending ── next job.updated ──► loading
                         ├──404──► absent
                         └──other/parse error──► error ── parts.load(force) ──► loading
```

`state/reactions/partsAutoload.ts` (S4) dispatches `parts.load` when the viewed job becomes completed with
`result.parts`.

**Model install** (`ModelEntryOut.state`):

```
not_installed ──models.install(accept)──► installing(bytes) ──► installed ──(first use)──► loaded
      ▲                                       └─► error ──models.install──► installing
      └── unavailable (gated without token / not approved): no transition from the UI; shows reason + hint
```

**Sheets** (Mobile and Tablet-bottom; `navigationStore.sheet` / `snap`):

```
standard sheets (create, parts):  closed ⇄ peek ⇄ half ⇄ full     (drag, handle tap cycles, ArrowUp/Down)
modal sheets (library, progress, status, blocked):  closed ──open──► half|full ──Esc/backdrop/drag-down──► closed
```

**UI mode and posture:**

```
uiMode = override(?ui=, session) ?? preferences.uiMode ; resolved = available(uiMode) ? uiMode : 'default'
tablet/mobile posture = rail | bottom | compact     (§8.5; frozen while a text field has focus)
```

**Node run state:** `RunStatus` (§5.7), recomputed from the shown job on each `job.updated`.

---------------------------------------------------------------------------------------------------

## 8. Renderers

### 8.1 Shared kit (`components/common/*` and `renderers/shared/*`, slices S5 and S6)

**New primitives** (`components/common/`, S5). They have no store access; parents pass `onX` callbacks that dispatch
commands.

| Component | Contract |
|---|---|
| `Sheet.tsx` | `variant: 'standard' \| 'modal'`, `snaps: SnapId[]`, `snap`, `onSnapChange`, `onClose`, `labelledBy`, `handleLabel`.<br>- Modal = native `<dialog>.showModal()` (top layer, inert background, Esc), with manual backdrop dismiss as in `Dialog.tsx`.<br>- Standard = `<section role="region">`.<br>- Drag works only on the handle and header: Pointer Events, `setPointerCapture`, and `transform` written to a ref'd element inside handlers (compiler-safe). The handle is a `<button>` with a 48×48 hit area and a 36×4 visual pill.<br>- Keyboard: Enter/Space cycles snaps, ArrowUp/ArrowDown step between snaps.<br>- The body has `overscroll-behavior: contain` and `padding-bottom: env(safe-area-inset-bottom)`.<br>- `@starting-style` enter/exit uses `--duration-slow` and `--ease-out`, which are zero under reduced motion. |
| `FloatingNav.tsx` | WAI-ARIA tablist with roving focus (ArrowLeft/Right, Home, End), copied from `MobileTabBar`. Props `items: NavItem[]`, `value`, `onChange`, `center?: ReactNode` (the FAB slot, outside the tablist). 64 px tall, `max-width: 560px`, centred, `bottom: calc(12px + env(safe-area-inset-bottom))`, glass background with a `@supports` fallback. |
| `NavRail.tsx` | The same semantics, vertical (ArrowUp/Down). 80 px wide, vertically centred, left `calc(12px + env(safe-area-inset-left))`. Props `items`, `value`, `onChange`, `footer?: ReactNode`. |
| `ProgressRing.tsx` | SVG ring, `role="progressbar"`, `aria-valuenow`, indeterminate state (static under reduced motion) |
| `Skeleton.tsx` | shimmer block using `--color-surface-3/4`; static under reduced motion |
| `RadioCards.tsx` (owned by S3, which uses it first) | `role="radiogroup"`, roving focus; used by the UI-style picker |
| `VisuallyHidden.tsx` | – |

**Shared renderer pieces** (`renderers/shared/`, S5, plus panels from S6):

| Component | Notes |
|---|---|
| `StatusCluster.tsx` | `EngineStatus` + `GpuMeter` (only when `variant='full'`) + `ConnectionIndicator`. The tuner pill is rendered only when `isMeshTunerEnabled(snap.features)`. |
| `GenerateAction.tsx` | `variant: 'card' \| 'bar' \| 'fab' \| 'rail' \| 'toolbar'`. One component over `useCommand(JobCommands.generate)` and `useCommand(JobCommands.cancel)`, with the progress ring from `useActiveJobProgress()`. A blocked FAB/rail/toolbar button uses `aria-disabled="true"` (it stays focusable); activating it opens the `blocked` sheet or popover with reason, note and engine problem plus "Retry loading model". The existing `GenerateCard` becomes `<GenerateAction variant="card"/>` with an identical look (S6). |
| `AppSettingsButton.tsx`, `HistoryButton.tsx`, `AccountButton.tsx` | dispatch `app.openSettings`, `nav.setHistoryOpen`, `auth.logout` |
| `icons.ts` | `IconKey` → lucide component map (a `.ts` file) |
| `useRevealEffect.ts` | `useRevealEffect(handler)` subscribes to `navigationStore.reveal.seq` and calls the renderer's mapping (an effect that runs commands; no setState in the effect) |
| `useSoftKeyboard.ts` | `visualViewport` resize plus `focusin`/`focusout`; returns `keyboardOpen` (used to hide floating navigation) |

**Tokens added** (`styles/tokens.css`, S5): every new value is a semantic token, and no literal colours are used in
new modules.

```css
:root {
  --control-h-sm: 28px; --control-h-md: 34px; --control-h-lg: 44px; --hit-min: 24px;
  --floating-nav-height: 64px; --fab-size: 60px; --floating-gap: var(--space-3); --rail-width: 80px;
  --surface-glass: color-mix(in srgb, var(--color-surface-2) 86%, transparent);
  --port-image: var(--color-accent-2); --port-masks: #34d399; --port-mesh: var(--color-accent); --port-parts: #f472b6;
  --cat-input: var(--color-accent-2); --cat-analyze: var(--color-info); --cat-generate: var(--color-accent);
  --cat-parts: #f472b6; --cat-output: var(--color-success); --cat-refine: var(--color-warning);
  --part-highlight: #fbbf24; --part-select: var(--color-accent-2);
  --shell-inset-bottom: 0px; --shell-inset-left: 0px;          /* set per renderer for toasts / scroll padding */
}
:root[data-density='touch'] { --control-h-sm: 40px; --control-h-md: 44px; --control-h-lg: 52px; --hit-min: 44px; }
```

`Button`, `IconButton`, `SegmentedControl`, `Switch` (label row), `RangeSlider` (thumb `::before` hit area) and
`NumberField` read `--control-h-*` and `--hit-min` (S5 edits their CSS modules only; no markup change).

### 8.2 Linear renderer (`renderers/linear/*`, S6): the canonical graph as forms and a stepper

| File | Export |
|---|---|
| `placement.ts` | `type SlotId`; `DEFAULT_PLACEMENT`, `MOBILE_PLACEMENT`, `TABLET_PLACEMENT` (`Record<StepGroup, SlotId>`) |
| `WorkflowSlot.tsx` | `<WorkflowSlot placement slot density />`. It renders every `LinearStep` assigned to `slot`, in `linearSteps` order. |
| `StepFrame.tsx` | Step chrome: title, run-status chip (from `useNodeRunStates`), the "Separate parts" switch for toggleable steps (`workflow.setPartsEnabled`), an unavailable banner (reason plus "App settings → Models" link), and validation messages for the step's nodes. |
| `SourceStep.tsx` | `SegmentedControl` "Image \| Prompt" (`workflow.setSource`), then the active source's panel. When Prompt is selected and unavailable, the prompt panel stays editable and the reason is shown under it. |
| `Stepper.tsx` | Vertical accordion of steps for Mobile and Tablet `create`. Numbered headers (`<h3><button aria-expanded>`), a collapsed summary from `def.summarize`, and a sticky footer `<GenerateAction variant="bar"/>`. |
| `WorkflowBanner.tsx` | Graph-level errors that are not tied to a step, plus "Restore default workflow" |

**Panel registry** (`components/workflow/panels/registry.ts`, S6; a `.ts` file):
`NODE_PANELS: Record<PanelKey, ComponentType<NodePanelProps>>`, with
`NodePanelProps = { nodeId: string; context: 'linear' | 'inspector'; density: 'regular' | 'touch' }`.

| PanelKey | Component | Built from |
|---|---|---|
| `references` | `components/references/ReferenceContent.tsx` | today's `ReferencePanel` body (split; `ReferencePanel` = `Panel` + content) |
| `prompt` | `components/prompt/PromptContent.tsx` (new) | – |
| `segmentation` | `components/workflow/panels/SegmentationPanel.tsx` (new) | – |
| `generation_settings` | `components/settings/SettingsContent.tsx` | today's `SettingsPanel` body (split) |
| `part_extraction` | `components/workflow/panels/PartExtractionPanel.tsx` (new) | – |
| `export` | `components/workflow/panels/ExportPanel.tsx` (new) | – |

**Placement maps:**

```ts
export const DEFAULT_PLACEMENT = { source: 'left.source', generate: 'right.generate', parts: 'right.parts', export: 'right.export' } as const
export const MOBILE_PLACEMENT  = { source: 'create', generate: 'create', parts: 'create', export: 'create' } as const
export const TABLET_PLACEMENT  = { source: 'create', generate: 'create', parts: 'create', export: 'create' } as const
```

**New panel contents** (S6):
- **PromptContent:**
  - a `<label>` "Describe the character or object" with a `<textarea>` (`maxLength` from caps,
    `enterkeyhint="go"`, live counter "123 / 2000", `aria-describedby` on the counter and hint);
  - Preset `SegmentedControl` (Character | Object | Raw);
  - Style select (None | Realistic | Stylized | Anime);
  - a collapsible "Advanced" section: negative prompt (only if `supports_negative_prompt`), image seed and randomize
    (reusing `SeedControl`), Model picker (`ModelSelect`: approved and installed models of the stage, licence badge,
    "Backend default" first), and a "Part-friendly prompt" switch.

  Values change through `workflow.setParam` (draft text is local `useState` and commits on change; no debounce is
  needed because history is not recorded).
- **SegmentationPanel:**
  - Subject select;
  - Segmenter `ModelSelect`;
  - "Also look for" chips input (`extra_terms`, Enter adds, Backspace removes, max count from caps);
  - after a run, the overlay thumbnail (E8) when `result.parts.overlay_url` exists.
- **PartExtractionPanel:**
  - Method radio list from `strategies` (label and description);
  - "Separate as physical parts" checkbox grid from `separable_groups`, each with a kind badge (Physical / Deferred)
    and its `note` (for example, eyelashes "geometry deferred");
  - "Keep as regions on the body" checkbox list from `region_groups`;
  - a footer note: "Face and body topology stays continuous; regions are labels, not cuts."
- **ExportPanel:** a fixed row "Base model (model.glb) — always kept", switches "Assembled parts GLB" and "Separate
  part files". After completion it shows download buttons (`job.downloadGlb`, `parts.download`).

### 8.3 Default renderer (`renderers/default/*`, S7): preserved and polished

**Structure.**
- The structure is today's, moved verbatim first: `components/layout/{AppShell, Header, MobileTabBar, mobileTabs}` →
  `renderers/default/{DefaultLayouts.tsx, DefaultHeader.tsx, DefaultTabBar.tsx, tabs.ts}`.
- `components/layout/{EngineStatus, GpuMeter, ConnectionIndicator, BackendBanner, Logo}` stay in place as shared
  feature pieces.
- Desktop (≥ 1280): 3 columns. Tablet (768–1279): 2 columns. Mobile (< 768): 4 tabs. These are unchanged.

**Additions.**
- Left column: `<WorkflowSlot slot="left.source"/>` replaces the bare `<ReferencePanel/>` (`AppShell.tsx:L51-53` [V]).
  With the Image source it renders the same ReferencePanel as today, with a 2-segment source switch above it.
- Right column: `right.generate` (SettingsPanel, retitled "Generation settings"), `right.parts` (a "Parts" card,
  collapsed by default, with a "Separate parts" switch in its header), then Progress, Stats (with `right.export`
  folded into its download section), **PartsCard** (results, §9), and TuningCard **only when the tuner is enabled**.
- Header: a gear (`AppSettingsButton`) next to History. On mobile, the "Settings" tab gets an "App settings…" row at
  the top.
- Reveal mapping (`useRevealEffect`):

  | Target | Desktop/tablet | Mobile |
  |---|---|---|
  | `run` | scroll the progress card into view (today's `useRevealProgress`) | tab `viewer` (today's behaviour) |
  | `viewer` | – | tab `viewer` |
  | `parts` | – | tab `viewer` |
  | `inputs` / `prompt` | – | tab `inputs` |
  | `settings` / `parts_options` | – | tab `settings` |
  | `history` | – | tab `history` |
  | `app_settings` | opens the dialog | opens the dialog |

**Polish list.** Each item is concrete and testable; no redesign.

| # | Change | Where | Acceptance |
|---|---|---|---|
| P1 | The layout chooser uses `useViewport()`. **mobile** when width < 768, or when `(pointer: coarse)` and height < 500. **tablet** when width < 1280. Otherwise desktop. | `DefaultLayouts.tsx` | At 812×375 the mobile tab layout is used, with no overflow of the 340 px viewer (fixes §3.2 row 3). |
| P2 | Viewer heights become `clamp(240px, 58dvh, 640px)` (tablet) and `clamp(200px, calc(100dvh - var(--chrome-h)), 520px)` (mobile). `.viewer { min-height: 0 }` with `min-height: 200px` only inside the desktop column. | `AppShell.module.css:L61-65,L85-89`, `ViewerPanel.module.css:L7` | At 740×360 the viewer tab content is ≥ 200 px with no inner double scroll (rows 4 and 6). |
| P3 | Tablet portrait (768–1279 and portrait): the viewer goes first (full width, `55dvh`), then the panels below in two columns `minmax(0,1fr) minmax(0,1fr)`. | `DefaultLayouts.tsx` + CSS | At 768×1024 the viewer is ≥ 700 px wide (row 2). |
| P4 | Toolbar: the view-mode `SegmentedControl` wraps to its own row at ≤ 420 px container width (container query), and segments use `min-width: max-content`. | `ViewerToolbar.module.css:L19` (S4 owns viewer CSS) | At 320 px there is no clipped "Normals" (row 1). |
| P5 | Contrast tokens: `--color-text-subtle: #8a92a6`; the primary button background is `--color-accent-active #7a58f5` (4.62:1), active `#6d4ae8`; input boundaries use `--color-border-strong`, which becomes ≥ 3:1 (`#5b6580`). | `tokens.css` (S5) | A contrast check in `ui_modes_probe.mjs` shows text ≥ 4.5:1 and controls ≥ 3:1 (ui_modes §9.1). |
| P6 | Touch density: `:root[data-pointer='coarse'][data-ui-mode='default']` sets `data-density='touch'` unless the preference is `comfortable`. | `app/documentFlags.ts` (S3) + tokens | 0 controls under 44 px at 768×1024 coarse (was 43/62). |
| P7 | The History drawer becomes a modal side sheet (`<dialog>` variant): focus trap, inert background, Esc, focus restore. | `components/history/HistoryDrawer.tsx` (S6) | Tab cannot leave the open drawer. |
| P8 | `scrollIntoView` uses `behavior: reducedMotion ? 'auto' : 'smooth'`. | `useRevealEffect.ts` | With reduced motion, no smooth scroll. |
| P9 | Skeletons instead of spinners for history items, reference thumbnails and the parts list first load. | S6/S4 | No layout shift > 0.05 CLS when history loads (probe). |
| P10 | Mobile tab panels cross-fade (opacity 120 ms, `--duration-fast`). Panels and cards use a 4 px rise and fade enter via `@starting-style`. Drawer, sheet and dialog exits use `allow-discrete`. | CSS | Zero durations under reduced motion. |
| P11 | `scrollbar-gutter: stable` on `.scrollArea` and `.scrollColumn`; `scroll-padding-bottom` equal to the GenerateCard bar height on mobile tabs. | `AppShell.module.css` | No horizontal jump when a scrollbar appears; the focused field is not hidden behind the bar (WCAG 2.4.11). |
| P12 | Toast offset = `--shell-inset-bottom` (set by each renderer: mobile tab bar + generate bar). | `Toaster.module.css` (S5) | Toasts never overlap the Generate bar (row 12). |
| P13 | Safe areas: the header gets `padding-top: env(safe-area-inset-top)` and left/right insets in landscape. | `DefaultHeader.module.css` | No notch overlap in landscape on an iPhone viewport. |
| P14 | `background-attachment: fixed` is dropped under `(pointer: coarse)`. | `global.css:L25` (S5) | – |
| P15 | Header crowding at 1280–1366: `GpuMeter` collapses to an icon with a popover below 1440 px. | `DefaultHeader` | No header overflow at 1280. |
| P16 | Loading states for new features: the Prompt source shows a "Checking backend features…" inline status while `features.source === 'loading'`; the PartsCard shows a skeleton while loading and an error with Retry. | S6/S4 | – |
| P17 | Unified panel padding: `--space-4` (desktop) and `--space-3` (< 768) across `Panel`. | `Panel.module.css` (S5) | – |

### 8.4 Mobile renderer (`renderers/mobile/*`, S8): a dedicated app-style phone UI

```
┌──────────────── top bar (48px + safe-top) ────────────────┐
│ ◆ Img3D            [● Engine ready ▾]           [⚙]       │   status pill opens the 'status' modal sheet
├────────────────────────────────────────────────────────────┤
│                                                            │
│                 full-bleed ViewerPanel (home)              │   empty state: "Create your first model" → create
│                                                            │
│      ╭───────────────────────────────────────────────╮     │
│      │ Create   Viewer   ( ✦ GENERATE )   Parts  Library │     │   floating nav, 64px, bottom 12px + safe-bottom
│      ╰───────────────────────────────────────────────╯     │
└────────────────────────────────────────────────────────────┘
```

| Destination (`SectionId`) | Opens | Sheet / snap |
|---|---|---|
| `create` | `Stepper` (Source → Generation → Parts → Export) + a sticky footer `GenerateAction variant="bar"` | standard sheet, opens at `full`, snaps peek/half/full |
| `viewer` | closes any sheet; the viewer is the home screen | – |
| `parts` | `PartsList` (§9) plus viewer-source toggle and explode slider | standard sheet at `half`, so the viewer stays visible |
| `library` | `HistoryList`. Opening a job dispatches `job.view` → reveal `viewer` → the sheet closes. | modal sheet `full` |
| FAB | `GenerateAction variant="fab"`. Ready: accent, label "Generate". Submitting: spinner. Running: progress ring; a tap opens the `progress` modal sheet (ProgressContent + Cancel). Blocked: `aria-disabled`; a tap opens the `blocked` sheet. | – |
| ⚙ | `app.openSettings`. The App settings dialog renders as a full-height modal sheet under `data-ui-mode='mobile'`. | – |

- **Touch.** `data-density='touch'` is always set in Mobile mode. Targets are ≥ 48 px. No hover-only affordances:
  reference tile menus are always visible.
- **Landscape** (`height < 520`): the floating nav becomes a left `NavRail` with the FAB in its footer, and sheets
  open from the right as side sheets (`max-width: 420px`). The viewer fills the rest.
- **Soft keyboard:** while `keyboardOpen`, the floating nav and FAB are hidden. The sheet's sticky Generate bar stays
  above the keyboard (`visualViewport` offset).
- **Reveal mapping:**

  | Target | Effect |
  |---|---|
  | `run` | close the create sheet, set section `viewer` |
  | `viewer` | section `viewer` |
  | `parts` | open the parts sheet at half |
  | `inputs` / `prompt` / `settings` / `parts_options` | open create, expand that step |
  | `history` | library |
  | `app_settings` | the dialog |

- **Wide screens in Mobile mode** (the user may choose Mobile on desktop): the shell renders a centred column
  `max-width: 480px` with the same layout, and the sides use `--color-bg`. It is not stretched.

### 8.5 Tablet renderer (`renderers/tablet/*`, S9): automatic posture

`hooks/useNavPosture.ts` (S3) is built on `useSyncExternalStore`, the same pattern as `useBreakpoint.ts`. It returns
a cached string snapshot and freezes while a text field has focus (the soft-keyboard guard from ui_modes §7.3).

| Posture | Rule (first match) | Navigation | Workspace |
|---|---|---|---|
| `compact` | width < 600 | the Mobile layout (same components) | as §8.4 |
| `rail` | landscape **and** (width ≥ 840 **or** (height < 520 **and** width ≥ 640)) | floating left `NavRail`: Create, Viewer, Parts, Library, separator, ⚙ App settings; footer `GenerateAction variant="rail"` (56 px round with a progress ring) | a side panel `clamp(320px, 34vw, 420px)` showing the selected section (Create = `Stepper`, Parts = `PartsList`, Library = `HistoryList`). Re-tapping the active rail item collapses the panel. The viewer fills the rest. Progress appears as a compact card at the top of the side panel while running. |
| `bottom` | otherwise (portrait, split screen, Stage Manager) | `FloatingNav` (the same as mobile, centred, max 560 px) with the FAB | viewer on top (`55dvh`); the selected section in a **standard** sheet below with snaps peek/half/full (default half) |

- **Switching** happens automatically on `(orientation)`, width, height or `screen.orientation` change. The selected
  `section` survives a posture change because it lives in `navigationStore`.
- **Panels** use container queries (`container: panel / inline-size`). The same `ReferenceContent`, `SettingsContent`
  and `PartsList` adapt to 320–420 px side panels or full-width sheets.
- **Reveal mapping:** as on Mobile, except `run` keeps the side panel open in `rail` posture and focuses the viewer
  (`viewer.resetCamera` is not called).

### 8.6 Node renderer (`renderers/node/*`, S11): ComfyUI-style over the same document

```
┌ NodeToolbar (48px) ────────────────────────────────────────────────────────────────────────────────┐
│ [▶ Run | ■ Cancel] status text │ [+ Add] [↶] [↷] [Tidy] [Fit] │ [Canvas|List] [Problems 2] │ [Template▾] [Import] [Export] │ StatusCluster [History] [⚙] │
├────────────────────────────────────────────────────┬───────────────────────────────────────────────┤
│ FlowCanvas (React Flow) or ListView                │ DockedViewer (the single ViewerPanel, 45% h)   │
│                                                    ├───────────────────────────────────────────────┤
│                                                    │ Inspector: NODE_PANELS[def.panel] for selection│
└────────────────────────────────────────────────────┴───────────────────────────────────────────────┘
grid-template-columns: minmax(0,1fr) clamp(320px, 30vw, 440px)
```

| File | Responsibility |
|---|---|
| `index.ts` | `export default` the lazy entry (`NodeRenderer`) |
| `NodeRenderer.tsx` | Grid. Below 1024 px wide: the inspector becomes a standard sheet and the viewer a floating PiP card (`240×180`, expandable). The PiP stays mounted with `active=false` when collapsed, so the GLB is not reloaded. Below 600 px, or with `prefs.node.view==='list'`, it renders `ListView`. |
| `NodeToolbar.tsx` | `GenerateAction variant="toolbar"` (Run/Cancel, the same command); undo/redo; add; tidy (`workflow.autoLayout`); fit; view toggle; a Problems popover (issues → `nav.selectNodes`); a Template menu (`workflow.loadTemplate`); Import (file input → `workflow.importFile`); Export (`workflow.exportFile`) |
| `flow/FlowCanvas.tsx` | The only file rendering `<ReactFlow>`. Props: `colorMode="dark"`, `nodeTypes` (module scope), `minZoom 0.25`, `maxZoom 2`, `fitView` on first mount, `zoomOnDoubleClick={false}`, `deleteKeyCode={null}`, `selectionOnDrag={false}`, `connectOnClick`, `connectionRadius` 30 under a coarse pointer, `isValidConnection` → `canConnect`, `onConnect` → `workflow.connect`, `onEdgesDelete` → `workflow.disconnect`, `onNodeDragStop` → `workflow.moveNodes`, `onMoveEnd` → `workflow.setViewport`, `onSelectionChange` → `nav.selectNodes`, a pane double-click or a connection dropped on the pane → palette pre-filtered by compatible port type. `<Background variant="dots"/>`, `<Controls showInteractive={false}/>` (the single-pointer zoom alternative), `<MiniMap pannable zoomable/>` when `prefs.node.minimap` and width ≥ 840. |
| `flow/adapter.ts` | Pure functions: `toFlowNodes(doc, registry, runStates, selection)` and `toFlowEdges(doc, runStates)` (an edge is `animated` only while its source node is running and motion is not reduced; it takes the source-port colour). Drag positions live in a local `useState` mirror during the drag (presentation-only state), committed on drag stop. |
| `flow/PipelineNode.tsx` | Header: category stripe, icon, title, status chip (icon + text), lock or unavailable badge, collapse. Body: `def.summarize(values)` rows; for a source node, a "Connected / not connected" note; the inline `ProgressBar` while running; for Export after completion, download buttons (commands). Handles: `PortHandle` per port with `data-port-type`, a colour token, a glyph (●■▲⬢) and a visible label. Interactive children carry `nodrag nopan` (and `nowheel` on scroll areas). Nodes are `role="group"` with `aria-label` "Image-to-3D, running 45%". |
| `flow/nodeTypes.ts` | `export const nodeTypes = { pipeline: PipelineNode }` (module scope) |
| `flow/useCanvasShortcuts.ts` | the canvas-scoped shortcuts of §6.6 |
| `flow/flow.css` | `@xyflow/react/dist/base.css` import plus the token mapping (`--xy-*` → tokens, as in ui_modes §5), and handle hit areas ≥ 44 px under `(pointer: coarse)` through `::before` |
| `ListView.tsx` | The accessible canonical representation: an ordered list (topological order) of node cards. Each card has the title, status, a mode switch, and for each input port a `<select>` of compatible upstream outputs (`workflow.connect` / `disconnect`, no dragging; WCAG 2.5.7), plus the node's panel inline. This is the default below 600 px. |
| `Inspector.tsx` | `NODE_PANELS[def.panel]` for the single selected node with `context="inspector"`. For multiple selections: the count and bulk Bypass / Delete. For none: workflow summary and issues. |
| `AddNodePalette.tsx` | `<dialog>` + combobox/listbox. Entries come from `registry.palette(caps, FEATURES)`, grouped by category, with availability badges. The refine group is shown only with `partRefinementPreview`. Disabled entries carry their reason (for example, "Only one Image-to-3D node is supported"). |
| `DockedViewer.tsx` | renders `<ViewerPanel active/>`, the **only** viewer in the app (singleton rule, `viewerRuntime.ts:L32` [V]) |

**No duplicated logic.**
- Every edit is a `workflow.*` command.
- Run is `job.generate`; it compiles the same document the Default UI compiles.
- Node bodies use `summarize` (pure).
- The Inspector uses the same panels as the linear renderers.

### 8.7 App root, mode switching, App settings dialog (S3)

**`app/` files:**

| File | Responsibility |
|---|---|
| `AppRoot.tsx` | `useBackendSession()` (exactly once), the auth gate (splash / LoginScreen, unchanged), `<ModeSwitch/>`, `<GlobalOverlays/>`, `<ShortcutHost/>`, `<Announcer/>`. `main.tsx` renders `AppRoot`; `App.tsx` remains as `export { AppRoot as default }` for compatibility. |
| `bootstrap.ts` | Imported first in `main.tsx`. `configureBus(realPorts)`; `import.meta.glob(['../commands/bind/*.ts', '../state/sync/*.ts', '../state/reactions/*.ts'], { eager: true })` registers every domain without a central list, so slices stay file-disjoint; `applyDocumentFlags()` before `createRoot` (no flash). |
| `ModeSwitch.tsx` | `const RENDERERS = import.meta.glob(['../renderers/*/index.ts', '!../renderers/default/index.ts'])` (lazy). The `default` renderer is imported statically, so it is in the initial bundle and Default users see no added latency. `availableUiModes` = the discovered folders ∩ `UI_MODES`. Wraps the active renderer in `RendererErrorBoundary` + `Suspense` (a skeleton shell). On mode change it moves focus to the new renderer's `<main tabIndex={-1}>` heading. |
| `RendererErrorBoundary.tsx` | A class boundary. Fallback: "The {mode} interface failed to load." with [Retry] (remount via `key`) and [Switch to Default] (`prefs.setUiMode('default')`). |
| `documentFlags.ts` | Sets `<html data-ui-mode data-density data-motion data-pointer>` from prefs and the viewport (the renderers set `data-posture`). |
| `GlobalOverlays.tsx` | `<Toaster/>`, `<AppSettingsDialog/>`, `<ShortcutHelpDialog/>`; `isMeshTunerEnabled(features) && <BlenderSetupDialog/>` |
| `realtime.ts` | `registerRealtimeHandler(type, fn)`. Called by `useBackendSession` for `features.updated`, `models.updated` and `parts.updated`, so domains register without editing the session hook. |

**`lib/uiModes.ts` (pure):**

```ts
export const UI_MODES = ['default', 'node', 'mobile', 'tablet'] as const
export type UiMode = (typeof UI_MODES)[number]
export const UI_MODE_LABELS: Record<UiMode, { label: string; description: string }> = {
  default: { label: 'Default', description: 'The classic three-column workspace. Adapts to any screen.' },
  node:    { label: 'Node editor', description: 'Build the pipeline as connected nodes, ComfyUI-style.' },
  mobile:  { label: 'Mobile', description: 'App-style phone layout with bottom navigation and sheets.' },
  tablet:  { label: 'Tablet', description: 'Side rail in landscape, bottom navigation in portrait.' },
}
export function parseUiModeParam(search: string): UiMode | null            // '?ui=node' → 'node'
export function resolveUiMode(pref: UiMode, override: UiMode | null, available: readonly UiMode[]): UiMode
export function suggestUiMode(v: { width: number; height: number; coarse: boolean }): UiMode   // hint only
export function resolvePosture(v: { width: number; height: number; landscape: boolean }): 'rail' | 'bottom' | 'compact'
```

- `suggestUiMode`:
  - width < 600, or (coarse and min(width, height) < 520) → mobile;
  - coarse and width < 1366 → tablet;
  - otherwise default.
- The suggestion is only shown in Settings as "Suggested for this screen: Tablet". It is never applied automatically.
  Default stays the default, as the user requires.

**App settings dialog** (`components/app-settings/AppSettingsDialog.tsx` on `common/Dialog`; a full-height modal
sheet in Mobile mode):

| Section | Controls (all commands) |
|---|---|
| Interface | **UI style**: `RadioCards` with four cards (schematic SVG preview, label, description, "Current" badge, "Suggested" chip), dispatching `prefs.setUiMode`. Focusing or hovering a card prefetches the chunk (`import()` warm-up). Then Density (Auto / Comfortable / Touch) and Motion (Follow system / Reduce). "Saved on this device" note. |
| Node editor | Link style (Bezier / Smooth step / Straight), Minimap, Snap to grid, Default view (Auto / Canvas / List) |
| Models & features | Read-only status from `features`. For each pipeline feature: available state and reason. For each model: name, stage, licence badge (green allowed / amber revenue-capped / red non-commercial or research), size, VRAM, state, "Approved"/"Not approved". An **Install** button appears only when `install.supported && approved`; it opens the licence confirmation sub-dialog (licence name, URL, a checkbox "I accept…", then `models.install`). Also "Mesh tuning & refinement: Disabled until approved" (from `mesh_tuning` and `part_refinement`). |
| About | App and backend versions, a keyboard-shortcut list generated from the command registry, storage note |

---------------------------------------------------------------------------------------------------

## 9. Part display (S4)

**Data.**
- `lib/partManifest.ts` holds `parsePartManifest` (§4.2).
- `lib/partGroups.ts` holds `groupOf(category)` (prefix map), `GROUP_ORDER` (eyes, eyebrows, eyelashes, hair, clothes,
  accessories, detachable, face, body, other) and `KIND_LABEL` (Physical / Deferred / Region).
- The prefix map, matching segmentation research §7.1:
  - `eye.`/`eye_` → eyes;
  - `eyebrow` → eyebrows;
  - `eyelash` → eyelashes;
  - `hair` → hair;
  - `clothing.`/`clothing_` → clothes;
  - `accessory` → accessories;
  - `object.`/`object_`/`appendage` → detachable;
  - `body.head.`/`face`/`nose`/`lips`/`mouth`/`ear` → face;
  - `body` → body;
  - otherwise other.

**`PartsList.tsx`** (`components/parts/`): one component used by the Default PartsCard, the Mobile parts sheet, the
Tablet parts section and the Node Export inspector.
- Header: counts ("12 parts · 7 physical · 5 regions"), a viewer source toggle "Original | Parts" (`parts.setViewerSource`;
  disabled with a reason when there is no assembled GLB), "Show all", and an explode slider (only for the Parts
  source).
- Groups follow `GROUP_ORDER` as collapsible `<section>`s. Each row is a `<li>` with:
  - a colour swatch (deterministic hue from the part id; a `--part-*` palette of 10 tokens, AA against the surface);
  - the label and side ("Eye (L)");
  - a kind badge;
  - a visibility toggle (`aria-pressed`, label "Hide Eye (L)");
  - Isolate (`aria-pressed`);
  - an overflow `Menu`: "Download part GLB" (when `modelUrl`), "Copy part id", and read-only "Refinement: untouched".
- Hover (pointer) → `parts.hover`. Click or Enter → `parts.select`. The selected row has `aria-selected` in a
  `role="listbox"` group with roving focus.
- Rows are at least 44 px under touch density.
- Warnings from the manifest render as an `InlineAlert` list at the top.
- No virtualization: fewer than 200 parts is expected (budget §12).

**Viewer extension** (`components/viewer/*`, S4). All `three` code stays inside the lazy viewer chunk.

```ts
// viewerTypes.ts (type-only, no three import)
export interface PartViewProps {
  readonly nodeToPart: Readonly<Record<string, string>>   // glTF node name → part id (from manifest nodeName)
  readonly hidden: readonly string[]
  readonly isolated: string | null
  readonly selected: string | null
  readonly hovered: string | null
  readonly regionAttribute: string | null                  // manifest name, lower-cased by GLTFLoader ('_part_id') [A]
  readonly regionValues: Readonly<Record<string, number>>  // region part id → attribute value
  readonly explode: number                                 // 0..1
  readonly onHover: (partId: string | null) => void        // → dispatch(parts.hover)
  readonly onSelect: (partId: string | null) => void       // → dispatch(parts.select)
}
// ModelViewerProps gains: parts?: PartViewProps | null
// ViewerApi gains: focusPart(partId: string): void        // frames the part's bbox (used by "Zoom to part" menu item)
```

- **Source.** `ViewerPanel` passes `url = viewerSource === 'parts' ? resolveApiUrl(result.parts.assembled_url) : jobModelUrl(result)`.
  This is one viewer and one model at a time. The tuned secondary model stays tuner-gated.
  - Under `(pointer: coarse)` or `navigator.deviceMemory <= 4`, the original is not preloaded while Parts is shown
    (today's mobile memory rule, `ViewerPanel.tsx:L146-148` [V], now driven by a capability signal rather than by the
    breakpoint).
- **Index** (`components/viewer/partsScene.ts`). On load, traverse the scene once. Map `object.name` and
  `object.userData.img3d_part_id` (glTF node `extras` land in `userData` [A, the GLTFLoader extras behaviour]) to part
  ids, and cache each node's original position and bbox.
- **Visibility.** `object.visible = !(hidden.includes(id)) && (isolated === null || id === isolated || isAncestorOf(id, isolated))`.
  This applies to physical nodes; regions are toggled by the overlay (below).
- **Highlight (physical).**
  - An overlay mesh shares the part geometry and uses a `MeshBasicMaterial` (`--part-select` for selected,
    `--part-highlight` for hovered; opacity 0.35; `depthTest` true; `polygonOffset -1`) plus an `EdgesGeometry`
    outline with a 30° threshold.
  - It is computed lazily and cached per part.
- **Highlight (region).** When `regionAttribute` exists on the geometry, build an index subset where
  `attr[v0]===attr[v1]===attr[v2]===value`. This is one pass over the index buffer, cached per value. A ≈2M-face mesh
  needs about 6M index reads, budget ≤ 50 ms. The subset renders with the same overlay material.
  - When the attribute is missing, region rows show "Highlight unavailable for this region" and the visibility toggle
    is disabled, because regions are not separate nodes. Nothing is faked.
- **Explode.** For each physical node, `position = original + (partCenter − assetCenter) × explode × 0.6`. Regions
  never move. `explode = 0` restores the originals exactly (the reassembly view).
- **Picking.** `onPointerMove` on the model group, throttled to one call per animation frame, resolves the nearest
  ancestor with a part id → `onHover`. A click, or a tap under `(hover: none)`, → `onSelect`. Picking is disabled while
  the orbit drag is active, so it does not fire during rotation.
- **View modes** (`applyViewMode` clay/normals) keep working: overlays are added after the view materials and use
  their own material.
- **Disposal** follows `scheduleDisposal` (`ModelScene.tsx:L41-60` [V]); overlay geometries and materials are disposed
  with the scene.

---------------------------------------------------------------------------------------------------

## 10. Feature flags and tuner gating

| Flag | Source | Default | Effect |
|---|---|---|---|
| `FEATURES.meshTuner` | build env `VITE_FEATURE_MESH_TUNER === 'true'` (`lib/features.ts`) | **false** | Necessary for any tuner UI or request. |
| `features.mesh_tuning.enabled` | backend `/features` | false (or absent → false) | Necessary too. `isMeshTunerEnabled(fs) = FEATURES.meshTuner && fs.meshTuning.enabled === true`. |
| `FEATURES.partRefinementPreview` | build env `VITE_FEATURE_REFINEMENT_PREVIEW === 'true'` | **false** | Shows the locked "Refinement — awaiting approval" palette group. It never enables compile. |
| `features.part_refinement.enabled` | backend | false | Informational in App settings. Locked nodes stay locked in this milestone even when true. |
| `features.pipeline.*.available` | backend | legacy fallback | Gates Prompt, Segmentation and Part Extraction (validation and UI). |
| `?ui=<mode>` | URL | none | Session-only UI mode override (not persisted). |
| `?debug=commands` | URL | off | The bus traces to the console (dev builds only; stripped in production by an `import.meta.env.DEV` check). |

**Tuner choke points.** All seven are gated by `isMeshTunerEnabled`, **without editing any `components/meshTuner/*`
or `store/meshTunerStore.ts` file**:

| # | Mount or request | Owner (slice) | Gate |
|---|---|---|---|
| T1 | `<BlenderSetupDialog/>` auto-open | `app/GlobalOverlays.tsx` (S3) | not mounted |
| T2 | `BlenderRuntimeStatus` header pill | `renderers/shared/StatusCluster.tsx` (S5) | not rendered |
| T3 | `TuningCard` in Default layouts | `renderers/default/DefaultLayouts.tsx` (S7) | not rendered; `useRevealTuning` is not called |
| T4 | Viewer `VariantBar`, tuned secondary GLB, `TunedStats` | `hooks/useViewedTuning.ts` (S4) | returns `{latestRun:null, tunedRun:null, variant:'original'}` |
| T5 | `HistoryItem` `loadRunsForJob` effect and the "tuned" marker | `components/history/HistoryItem.tsx` (S6) | effect skipped |
| T6 | Mobile tab pulse while tuning | `renderers/default/DefaultTabBar.tsx` (S7) | – |
| T7 | Session: `refreshRuntime`, `pollMeshTuner`, `loadRunsForJob`, the viewed-job subscription, resync on hello | `hooks/useBackendSession.ts` (S3) | skipped (WS handlers stay; they are harmless) |

**Acceptance (S10 smoke).** With a default build against the mock, the mock's request log
(`GET /api/__test/requests`, test-only) contains **zero** paths matching `/blender` or `/tunings` after 30 s of idle
plus one generation. This also holds when the mock reports `mesh_tuning.enabled=true`, because the build flag is off.

---------------------------------------------------------------------------------------------------

## 11. Accessibility (acceptance checklist; WCAG 2.2 AA unless noted)

1. **Keyboard (2.1.1, 2.1.2).**
   - Every command is reachable without a pointer, including Node editing through React Flow's focus model plus the
     ListView.
   - Modal dialogs and sheets trap focus by design (native `<dialog>`) and close on Esc. Focus returns to the opener.
2. **Focus visible and not obscured (2.4.7, 2.4.11).**
   - The global `:focus-visible` ring is kept.
   - Floating nav, rail, FAB and peek sheets never cover the focused element. Scroll containers get
     `scroll-padding-bottom: calc(var(--floating-nav-height) + 2*var(--floating-gap) + env(safe-area-inset-bottom))`
     (and `-left` for the rail).
3. **Target size (2.5.8; plus 44/48 px guidance).**
   - ≥ 24 px everywhere.
   - ≥ 48 px in Mobile and Tablet modes.
   - ≥ 44 px under a coarse pointer in Default and Node modes.
   - React Flow handle hit areas ≥ 44 px under coarse pointers.
4. **Dragging movements (2.5.7) and pointer gestures (2.5.1).**

   | Gesture | Single-pointer alternative |
   |---|---|
   | sheet drag | handle tap and arrow keys |
   | connect | tap-tap (`connectOnClick`) and ListView selects |
   | node move | arrow keys (React Flow) and Tidy |
   | pinch | zoom buttons |
   | explode | slider with keyboard |

5. **Use of colour (1.4.1).**
   - Ports: colour plus glyph plus text label.
   - Node status: icon plus text.
   - Part swatches are always paired with a text label and a kind badge.
6. **Contrast (1.4.3, 1.4.11).** The token fixes in P5. Port and category colours are checked against
   `--color-surface-2` at ≥ 3:1.
7. **Name, role, value (4.1.2).**
   - Navs: `<nav aria-label>` with tablist/tab and `aria-selected`.
   - FAB: `aria-label="Generate 3D model"`, plus `aria-disabled` and `aria-describedby` when blocked.
   - Sheets: `<dialog aria-labelledby>`, or a region with a handle using `aria-expanded` and `aria-controls`.
   - Nodes: `role="group"` and `aria-label`.
   - Progress: `role="progressbar"` with `aria-valuenow`.
   - Parts: listbox with `aria-selected`; toggles use `aria-pressed`.
8. **Status messages (4.1.3).** These go through the Announcer only: job queued, completed, failed and cancelled;
   mode switched; workflow issues after Run is blocked by keyboard; parts loaded ("12 parts loaded"). Never per-second
   timers or progress frames.
9. **Reduced motion (2.3.3 AAA and the media query).**
   - All motion uses the `--duration-*` tokens (zeroed).
   - JS smooth scroll, edge animation, the FAB ring spin, sheet springs, View Transitions and the explode transition
     respect `data-motion='reduce'`.
10. **Orientation (1.3.4).** Never locked. Tablet supports both orientations, and Mobile supports landscape (rail).
11. **Reflow (1.4.10).**
    - Default, Mobile, Tablet and Node ListView work at 320 CSS px with no two-dimensional scroll.
    - The Node canvas is exempt as two-dimensional content, and ListView is its reflow alternative.
12. **Consistent navigation and identification (3.2.3, 3.2.4).** The same labels and icons are used across modes:
    Create, Viewer, Parts, Library, Settings, Generate.
13. **Labels and errors (3.3.1, 3.3.2, 3.3.3).**
    - The prompt textarea has a visible label, a counter and an error text.
    - Blocked reasons are explicit and include how to fix ("Enter a prompt to generate from.").
14. **Screen-reader parity for the Node UI.**
    - The canvas `aria-describedby` points to a "Graph summary" ("6 nodes, 2 bypassed, 0 problems, Image-to-3D
      running 45%").
    - ListView is the canonical accessible view.
    - React Flow's `ariaLabelConfig` strings are set.

---------------------------------------------------------------------------------------------------

## 12. Performance budget

Measured by `test/frontend/budgets.mjs` (`vite build --manifest` into a temp dir, gzip of each chunk). The build
fails the gate when a budget is exceeded.

| Asset | Baseline [V] | Budget |
|---|---|---|
| Initial JS (entry + static imports; includes Default renderer, commands, engine, prefs) | ≈ 178–180 kB gz | **≤ 195 kB gz** (commands + engine + new stores + linear renderer ≤ 15 kB gz) |
| Initial CSS | 14.6 kB gz | ≤ 18 kB gz |
| Mobile renderer chunk | – | ≤ 18 kB gz |
| Tablet renderer chunk (shares the kit with mobile through a `renderer-kit` chunk) | – | ≤ 12 kB gz |
| Node renderer app chunk | – | ≤ 30 kB gz |
| `xyflow` vendor chunk (lazy; new Rolldown group, priority 35, regex `@xyflow\|d3-(drag\|zoom\|selection\|interpolate\|dispatch\|color\|ease\|timer\|transition)\|classcat`) | – | ≤ 64 kB gz. Must **not** appear in initial imports, and the nested zustand 4 must be in this chunk, not in `vendor`. |
| three / r3f / ModelViewer (lazy) | 207 / 80 / 5.7 kB gz | unchanged ± 3 kB (the parts scene adds ≤ 3 kB) |

Runtime budgets (checked in the probe or by manual trace on the mock):

| Interaction | Budget |
|---|---|
| Mode switch with the chunk cached, until the new renderer's first paint | ≤ 150 ms |
| `dispatch` → store update (non-API commands) | ≤ 2 ms |
| `validate` + `compile` (≤ 20 nodes) | ≤ 1 ms |
| Re-renders caused by `job.updated` progress frames | only progress components, node status chips and the FAB ring; renderer shells and panels 0 (React DevTools profiler check) |
| Node canvas pan/zoom with ≤ 20 nodes | 60 fps on the dev machine; `onlyRenderVisibleElements` off (not needed) |
| PartsList render with 200 parts | ≤ 16 ms |
| Region overlay build at 2M faces | ≤ 50 ms first, cached afterwards |
| Hover picking | ≤ 1 raycast per animation frame |
| Memory on coarse/low-memory devices | at most one GLB mounted (original **or** assembled) |

---------------------------------------------------------------------------------------------------

## 13. File plan (final tree; owner slice in brackets; "~" = edited existing file)

```
frontend/
  package.json, package-lock.json ~ [S11]            + "@xyflow/react": "12.12.0" (exact pin)
  vite.config.ts ~ [S11]                             + codeSplitting group 'xyflow' (priority 35) + 'renderer-kit'
  src/
    main.tsx ~ [S3]                                  import './app/bootstrap'; render <AppRoot/>
    App.tsx ~ [S3]                                   re-export AppRoot (compat)
    vite-env.d.ts ~ [S0]                             VITE_FEATURE_MESH_TUNER?, VITE_FEATURE_REFINEMENT_PREVIEW?
    api/
      types.ts ~ [S0]                                APPEND-ONLY section "Part-aware & prompt (§4)"; CreateJobRequest superset
      features.ts [S0]                               getFeatures() → FeaturesOut | null (404 ⇒ null), installModel()
      parts.ts [S0]                                  getPartManifest(), partFileUrl(), downloadPartBlob(), rebuildParts()
      ws.ts ~ [S0]                                   + 'features.updated' | 'models.updated' | 'parts.updated' cases
    lib/
      features.ts [S0]                               FEATURES (build flags), isMeshTunerEnabled(fs)
      featureSet.ts [S0]                             FeatureSet type + normalizeFeatures(raw, status) (pure)
      storage.ts ~ [S0]                              + preferences, workflow, workflowBackup keys
      uiModes.ts [S3]                                UI_MODES, labels, parse/resolve/suggest/posture (pure)
      partManifest.ts [S4]                           parsePartManifest (pure)
      partGroups.ts [S4]                             groupOf, GROUP_ORDER, KIND_LABEL, swatch hue (pure)
    commands/
      core/{types,bus,registry,snapshot,pending,announcer,shortcuts,react}.ts [S0b]
      specs/job.ts, system.ts, auth.ts, references.ts, settings.ts, models.ts [S2]
      specs/workflow.ts [S1]
      specs/nav.ts, app.ts, prefs.ts [S3]
      specs/parts.ts, viewer.ts [S4]
      bind/<same names>.ts [same owners]             real ports + registerCommands(...)
    state/
      sync/{system,features,references,jobs}.ts [S2]; sync/workflow.ts [S1]; sync/{prefs,nav}.ts [S3];
      sync/{viewer,parts}.ts [S4]
      read/{system,features,references,jobs,settings}.ts [S2]; read/workflow.ts [S1]; read/{prefs,nav}.ts [S3];
      read/parts.ts [S4]
      reactions/jobAnnouncements.ts [S2]; reactions/partsAutoload.ts [S4]
    workflow/
      engine/{types,registry,ports,graph,plan,validate,compile,status,linear,serialize,fromJob,index}.ts [S1]
      nodes/{inputImage,prompt,segmentation,imageTo3d,partExtraction,export,index}.ts [S1]
      templates.ts [S1]
      bridge.ts [S1]                                 compileCurrent(): reads stores → engine.compile (impure glue)
    store/
      workflowStore.ts [S1]; featuresStore.ts, transferStore.ts [S2]; jobStore.ts ~ [S2];
      preferencesStore.ts, navigationStore.ts [S3]; partsStore.ts [S4]
    hooks/
      useBackendSession.ts ~ [S3]; useViewport.ts, useNavPosture.ts [S3]; useBreakpoint.ts ~ [S3] (wrapper)
      useGenerateAvailability.ts ~ [S2] (adapter); useViewedTuning.ts ~ [S4] (gate)
    app/{AppRoot,ModeSwitch,RendererErrorBoundary,GlobalOverlays,ShortcutHost,Announcer,ShortcutHelpDialog}.tsx,
        {bootstrap,documentFlags,realtime}.ts [S3]
    components/
      common/{Sheet,FloatingNav,NavRail,ProgressRing,Skeleton,VisuallyHidden}.tsx (+css) [S5]
      common/RadioCards.tsx (+css) [S3]
      common/{Button,SegmentedControl,Switch,RangeSlider,NumberField,Panel,Toaster}.module.css ~ [S5] (tokens only)
      app-settings/{AppSettingsDialog,InterfaceSection,NodeSection,ModelsSection,AboutSection,LicenseDialog}.tsx [S3]
      references/{ReferenceContent.tsx, ReferencePanel.tsx ~} [S6]
      settings/{SettingsContent.tsx, SettingsPanel.tsx ~, AdvancedSettings ~, SeedControl ~, PolygonTargetControl ~} [S6]
      generation/{GenerateCard.tsx ~, ProgressContent.tsx, ProgressCard.tsx ~, StepList.tsx} [S6]
      history/{HistoryList ~, HistoryItem ~, HistoryDrawer ~} [S6]
      prompt/{PromptContent.tsx, ModelSelect.tsx} [S6]
      workflow/panels/{registry.ts, SegmentationPanel, PartExtractionPanel, ExportPanel}.tsx [S6]
      parts/{PartsList, PartsCard, PartRow, ExplodeSlider}.tsx [S4]
      viewer/{ViewerPanel ~, ModelViewer ~, ModelScene ~, ViewerToolbar ~, ModelStatsCard ~, viewerTypes ~,
              viewerRuntime ~, partsScene.ts, sceneUtils ~} [S4]
      layout/{EngineStatus,GpuMeter,ConnectionIndicator,BackendBanner,Logo} (unchanged; CSS media → mode-aware ~ [S7])
      layout/{AppShell,Header,MobileTabBar,mobileTabs} → MOVED to renderers/default [S7]
      meshTuner/** (untouched)
    renderers/
      shared/{StatusCluster,GenerateAction,AppSettingsButton,HistoryButton,AccountButton}.tsx,
             {icons,useRevealEffect,useSoftKeyboard}.ts [S5]
      shared/phone/{PhoneWorkspace,PhoneTopBar,SheetHost}.tsx [S8]      (used by Mobile and Tablet-compact)
      linear/{placement.ts, WorkflowSlot, StepFrame, SourceStep, Stepper, WorkflowBanner}.tsx [S6]
      default/{index.ts, DefaultRenderer, DefaultLayouts, DefaultHeader, DefaultTabBar}.tsx, tabs.ts [S7]
      mobile/{index.ts, MobileRenderer.tsx, sections.ts} [S8]
      tablet/{index.ts, TabletRenderer.tsx, RailWorkspace.tsx, BottomWorkspace.tsx, sections.ts} [S9]
      node/{index.ts, NodeRenderer, NodeToolbar, ListView, Inspector, AddNodePalette, DockedViewer}.tsx,
           flow/{FlowCanvas.tsx, PipelineNode.tsx, PortHandle.tsx, adapter.ts, nodeTypes.ts, useCanvasShortcuts.ts, flow.css} [S11]
    styles/
      tokens.css ~, global.css ~ [S5]; modes.css [S3] (per-[data-ui-mode]/[data-density] overrides)
test/frontend/
  mock_backend.mjs ~ [S10]                           + flags §14.2, request log, strict request validation
  part_aware_mock_smoke.mjs, ui_modes_probe.mjs, budgets.mjs, arch_boundaries.test.mjs [S10]
  arch_allowlist/{s6_components,s7_default,s4_viewer,meshtuner}.json [S10 creates; each named slice shrinks its own]
  fixtures/{features_full,features_unavailable,manifest_draft_a,manifest_draft_b,job_legacy,job_prompt_steps}.json [S10]
  commands/core.test.mjs [S0b]; commands/{generate,references,settings}.test.mjs [S2];
  commands/workflow.test.mjs [S1]; commands/{prefs_nav}.test.mjs [S3]; commands/parts.test.mjs [S4]
  workflow/{registry,ports,graph_ops,validate,compile,status,linear,serialize,from_job}.test.mjs [S1]
  lib/featureSet.test.mjs [S0]; lib/uiModes.test.mjs [S3]; lib/partManifest.test.mjs [S4]
THIRD_PARTY_NOTICES.md ~ [S11]                        + @xyflow/react, @xyflow/system, d3-* (ISC), classcat, zustand@4 (MIT)
```

The existing paths that other code imports are kept. `components/layout/AppShell.tsx` and `Header.tsx` remain for one
release as one-line re-exports from `renderers/default`. They are deleted only after the allowlist shows zero
importers. No working functionality is removed.

---------------------------------------------------------------------------------------------------

## 14. Test plan

Project rule: tests live under `test/` only. The frontend has no unit-test runner today, and this design adds none.
Pure modules run on **Node's built-in runner with type stripping**:

```powershell
cd E:\14-OCT-2026\Img3D
node --experimental-strip-types --test "test/frontend/**/*.test.mjs"
```

Test files import `../../../frontend/src/**.ts` directly. Bare specifiers resolve from `frontend/node_modules`; only
`zustand/vanilla` is needed, in `commands/core/snapshot.ts`.

### 14.1 Unit tests (Node runner)

| File | Slice | Asserts |
|---|---|---|
| `workflow/registry.test.mjs` | S1 | The six built-ins register. A duplicate type throws. A `refine` def without `locked` throws. `palette()` hides refine unless `partRefinementPreview`. `maxInstances` is reported. |
| `workflow/ports.test.mjs` | S1 | The full `canConnect` matrix (4 output types × every input), self-loop rejection, cycle rejection (DFS), replacement on single inputs, multiple inputs on Export. |
| `workflow/graph_ops.test.mjs` | S1 | Every op is pure: input deep-frozen, no mutation, untouched subtrees `===` (structural sharing). `setSource` rewires both edges deterministically. `setPartsEnabled`. `autoLayout` is deterministic. |
| `workflow/validate.test.mjs` | S1 | Each `IssueCode` has at least one positive and one negative case, with the **exact message strings** of §5.5. Legacy features → `capability_missing` only on active part and prompt nodes. |
| `workflow/compile.test.mjs` | S1 | **G1–G5** (§5.6), including byte-identical G1 against a re-implementation of `jobStore.ts:L208-212`. Key order. `request_keys` gating. Optional prompt keys are omitted when null or default. Segmentation without Part Extraction is dropped with a warning. Part Extraction without Segmentation sends `segmentation:'off'`. A strategy that requires masks without Segmentation fails. |
| `workflow/status.test.mjs` | S1 | Every row of the Path A and Path B tables (§5.7). Unknown step ids are ignored. No job → idle/bypassed/unavailable. |
| `workflow/linear.test.mjs` | S1 | `linearSteps` for the four templates, after deleting Image-to-3D (banner case), and with features unavailable. |
| `workflow/serialize.test.mjs` | S1 | Round-trip equality. An unknown node is kept and forced to bypass. A newer version is rejected. The 64-node and 256-edge limits. Exported files contain no bound values or ids. |
| `workflow/from_job.test.mjs` | S1 | A legacy job → image template, parts off. A prompt job with `settings.parts` → prompt_parts with values. Unknown part groups are dropped with a warning. |
| `commands/core.test.mjs` | S0b | Blocked path (no run; announce only for `shortcut`). `exclusive` → busy. The pending key is cleared when `run` throws. The order of trace events. `patchSnapshot` is a no-op on equal values (the same object identity). `registerCommands` throws on duplicate ids. Unconfigured ports are no-ops. |
| `commands/generate.test.mjs` | S2 | A **parity table**: 12 snapshots × the expected `{enabled, reason, note}` taken verbatim from `useGenerateAvailability.ts:L55-73`, plus the new prompt and parts rows. The `run` port order: `loadEngine` only when `not_loaded`, then compile, then submit, then `rememberRun`. The error-mapping table (§6.5). `retry from:'prompt'` builds `{source, settings}`. |
| `commands/references.test.mjs`, `settings.test.mjs` | S2 | Ports are called with the exact payloads; availability rules hold. |
| `commands/workflow.test.mjs` | S1 | `setParam` rejects bound node types and unknown keys. History is recorded or not per §5.9. Undo/redo availability. |
| `commands/prefs_nav.test.mjs` | S3 | `setUiMode` is blocked for an unavailable mode. Reveal sequence increments. Sheet snap cycling. |
| `commands/parts.test.mjs` | S4 | Visibility, isolate and select reducers. `setViewerSource('parts')` is blocked without `assembled_url`. `rebuild` gating. |
| `lib/featureSet.test.mjs` | S0 | Normalization of the `full`, `unavailable`, legacy (null) and loading inputs; `request_keys` passthrough; `isMeshTunerEnabled` truth table (build flag × backend flag). |
| `lib/uiModes.test.mjs` | S3 | `parseUiModeParam`, `resolveUiMode` (override, pref, availability), and `suggestUiMode` / `resolvePosture` tables at 320×640, 375×812, 812×375, 740×360, 768×1024, 1024×768, 1180×820, 1280×800, 1920×1080. |
| `lib/partManifest.test.mjs` | S4 | Draft A and draft B fixtures normalize to equal `PartManifestView`s. Rejections: bad schema, duplicate ids, `../` in an id. `groupOf` table for every taxonomy id in segmentation research §7.1. |
| `arch_boundaries.test.mjs` | S10 | The import rules of §3.2 (regex over import specifiers). Engine and spec files contain no `window.`, `document.` or `import.meta.env`. `@xyflow` appears only under `renderers/node`. `three` is imported only by the lazy viewer files. Every `commands/bind/*.ts` calls `registerCommands`. Every new store has a `state/sync/*` file. Allowlist entries still violate, so the allowlist can only shrink. |

### 14.2 Contract and UI smoke against the test double

**`mock_backend.mjs` extensions (S10).** These are test-only data, never shipped.

| Flag | Values | Behaviour |
|---|---|---|
| `--features` | `full` (default), `legacy`, `unavailable`, `slow` | `legacy`: 404 on `/features`. `unavailable`: every new pipeline feature unavailable, with reasons. `slow`: 3 s latency. |
| `--parts-scenario` | `none`, `ok` (default with `full`), `partial`, `fail`, `no-attribute` | `ok`: completed jobs with `settings.parts` get a manifest (draft A) plus an assembled GLB of boxes named `part:<id>` with a `_PART_ID` attribute on the body. `no-attribute`: the attribute is omitted. `fail`: steps report `part_extraction` failed. |
| `--prompt-scenario` | `ok`, `fail` | Prompt jobs stream steps `prompt` → `image_to_3d` → `export`. |
| `--strict-requests` | on by default | `POST /jobs` rejects unknown keys with 422 `validation_error` and a `loc`, mirroring `extra="forbid"`. |

The mock also adds a request log at `GET /api/__test/requests` (path, method, body hash).

**`part_aware_mock_smoke.mjs` (S10)** (Node ≥ 22, HTTP only). It asserts:
- the `/features` shapes for every `--features` mode;
- that the frontend golden requests G1–G4, read from `test/frontend/fixtures`, are accepted (202);
- that a G2 body with an extra unknown key gets 422;
- that the manifest and assembled GLB routes return 200 with the right content types;
- that the request log contains no `/blender` or `/tunings` path after the scripted flow when the frontend build has
  the tuner flag off (run with the UI open in the browser pane).

**`ui_modes_probe.mjs` (S10).**
- It exports a probe function source string. An agent runs it in the desktop app's browser pane (`javascript_tool`)
  for each mode × viewport: 320×640, 375×812, 812×375, 740×360, 768×1024, 1024×768, 1280×800, 1920×1080.
- It returns JSON with:
  - `docScrollWidth === innerWidth` (no page horizontal scroll);
  - the count of interactive elements under 44 px (Default with a coarse pointer, and Node) and under 48 px (Mobile,
    Tablet);
  - focus-obscured checks: the focused element's rect does not intersect the floating nav, rail or FAB;
  - the contrast of a sampled set of tokens;
  - the presence of the Announcer regions.
- Pass criteria:
  - no horizontal scroll in any mode, at any size (the Node canvas is excluded);
  - Mobile and Tablet have 0 targets under 48 px;
  - Default under a coarse pointer has 0 under 44 px;
  - prompt and image inputs are reachable in every mode (selectors `[data-testid="source-switch"]`,
    `textarea#prompt-input`, `[data-testid="reference-dropzone"]`).
- No browser automation library is installed [V], so this runs agent-driven or manually. Adding Playwright is an open
  question (§18).

**Request-body check (S10).** In the browser pane against the mock, trigger Generate with defaults in each of the
four modes. Read the POST body with `read_network_requests`. It must equal G1 byte-for-byte in all four modes. This
proves "same API, no duplicated logic".

### 14.3 Existing checks that must stay green

- `node test\frontend\review\compiler_check.mjs`: prints `[]` and 0 non-success events. The compiled-function count
  grows from 91.
- `node test\frontend\mesh_tuner_mock_smoke.mjs`: the mock keeps the tuner routes, so this contract smoke still
  passes even though the UI hides the tuner.
- Backend suites are untouched by frontend slices. When S0's types are reconciled, the backend workstream's own
  contract tests are the reference.

---------------------------------------------------------------------------------------------------

## 15. Quality gates (run after every slice; all must pass)

```powershell
cd E:\14-OCT-2026\Img3D\frontend
npx oxlint --max-warnings 0                                  # baseline 0/0
npx tsc -p tsconfig.app.json --noEmit
npx tsc -p tsconfig.node.json --noEmit
npx vite build --outDir "$env:TEMP\img3d_fe_build" --emptyOutDir --manifest     # never touches frontend\dist
cd E:\14-OCT-2026\Img3D
node --experimental-strip-types --test "test/frontend/**/*.test.mjs"
node test\frontend\review\compiler_check.mjs                 # [] ; non-success events: 0
node test\frontend\budgets.mjs --dir "$env:TEMP\img3d_fe_build"   # §12 budgets
# with the mock running: node test\frontend\mock_backend.mjs --port 8765 --engine-ready --seed-jobs 2
node test\frontend\part_aware_mock_smoke.mjs --port 8765
node test\frontend\mesh_tuner_mock_smoke.mjs
```

Additional gates for UI slices (S6–S11): the ui_modes probe at the listed viewports, the request-body check, and a
manual keyboard-only pass (Tab through each mode: generate, switch source, open settings, switch mode, toggle a part).

**Real backend (user-approved GPU run only, after S7).**
1. Start the real backend and the frontend build.
2. Run one image job with defaults from the Default UI.
3. The captured POST body must equal G1.
4. The completed job must display exactly as before: viewer, stats, download.

---------------------------------------------------------------------------------------------------

## 16. Implementation slices

Rules:
- Within a wave, file sets are disjoint (§13 owners).
- Cross-slice dependencies are only on names fixed in this document.
- Merges follow the stated order.
- Every slice keeps the app working and passes §15 before merging.
- No slice edits `components/meshTuner/**` or `store/meshTunerStore.ts`.
- **Precondition:** a clean `frontend/` working tree. At writing time the paused tuner work is committed (`8654b90`)
  and `git status` shows no frontend changes [V]. If tuner edits reappear before S0 starts, commit or stash them
  first, because S0 appends to `api/types.ts`.

### Wave 0 (parallel)

| Slice | Owns | Depends on | Acceptance |
|---|---|---|---|
| **S0 Contract types and API adapters** | `api/types.ts` (append-only), `api/features.ts`, `api/parts.ts`, `api/ws.ts`, `lib/features.ts`, `lib/featureSet.ts`, `lib/storage.ts`, `vite-env.d.ts`, `test/frontend/lib/featureSet.test.mjs` | the §4 shapes, reconciled with the backend design | Gates green. No runtime behaviour change; nothing calls the new functions yet. `CreateJobRequest` is a superset (existing call sites still type-check). The `featureSet` tests pass. |
| **S0b Command core** | `commands/core/*`, `test/frontend/commands/core.test.mjs` | – | Core tests pass. No imports of stores or api (arch test). Not wired into the app yet. |
| **S10 Test infrastructure** | everything under `test/frontend/` listed for S10 in §13 | §4 shapes | The mock supports every §14.2 flag. `part_aware_mock_smoke.mjs` passes against the mock alone. `arch_boundaries.test.mjs` passes, with the allowlist seeded from the current tree (`components/**` store importers, `meshTuner/**`). `budgets.mjs` runs against a baseline build and prints sizes; budgets are enforced once S11 lands. |

### Wave 1 (parallel development; merge order S1 → S2 → {S3, S4, S5})

| Slice | Owns | Depends on | Acceptance |
|---|---|---|---|
| **S1 Workflow engine and state** | `workflow/**`, `store/workflowStore.ts`, `commands/{specs,bind}/workflow.ts`, `state/{sync,read}/workflow.ts`, `test/frontend/workflow/**`, `test/frontend/commands/workflow.test.mjs` | S0, S0b | All §14.1 S1 tests pass, including **G1 parity**. The engine imports nothing forbidden. `img3d.workflow` persists and migrates. No UI uses it yet, so nothing changes visually. |
| **S2 Job, session and input commands** | `store/{jobStore~,featuresStore,transferStore}.ts`, `commands/{specs,bind}/{job,system,auth,references,settings,models}.ts`, `state/sync/{system,features,references,jobs}.ts`, `state/read/{system,features,references,jobs,settings}.ts`, `state/reactions/jobAnnouncements.ts`, `hooks/useGenerateAvailability.ts~`, `test/frontend/commands/{generate,references,settings}.test.mjs` | S0, S0b, S1 (bridge) | The parity table passes. `jobStore.submit()` still works for unmigrated callers and now delegates to `submitRequest(legacyBuild())`. `useGenerateAvailability` returns identical values. Features load at session sync (by S3's hook; until then, `models.refresh` is callable). The request body from the existing Default UI equals G1. |
| **S3 App root, preferences, navigation, mode switch, App settings** | `main.tsx~`, `App.tsx~`, `app/**`, `hooks/{useBackendSession~,useViewport,useNavPosture,useBreakpoint~}.ts`, `lib/uiModes.ts`, `store/{preferencesStore,navigationStore}.ts`, `commands/{specs,bind}/{nav,app,prefs}.ts`, `state/{sync,read}/{prefs,nav}.ts`, `components/app-settings/**`, `components/common/RadioCards.tsx`, `styles/modes.css`, tests `lib/uiModes`, `commands/prefs_nav` | S0b, S2 | The app boots through `AppRoot`. `ModeSwitch` renders the **existing** Default layout through a temporary `renderers/default/index.ts` that re-exports today's App body; S7 takes ownership of that folder in Wave 2. The gear opens App settings, and modes not yet built show "Not included in this build". `?ui=` works. `<html data-*>` flags are set before first paint. Tuner gates T1 and T7 are applied, so a default build makes zero tuner requests (smoke). Ctrl/Cmd+Enter works through `ShortcutHost` exactly as before. |
| **S4 Parts data and viewer** | `lib/{partManifest,partGroups}.ts`, `store/partsStore.ts`, `commands/{specs,bind}/{parts,viewer}.ts`, `state/{sync,read}/{parts,viewer}.ts`, `state/reactions/partsAutoload.ts`, `components/parts/**`, `components/viewer/**~`, `hooks/useViewedTuning.ts~`, tests `lib/partManifest`, `commands/parts` | S0, S0b, S2 | With mock `--parts-scenario ok`, a completed parts job auto-loads its manifest. `PartsCard` (not yet placed) renders in a test harness route `?ui=default&debug=parts`, which is dev only. Viewer source toggle, visibility, isolate, hover/select highlight and explode work. `no-attribute` shows honest "Highlight unavailable" rows. T4 is gated. P4 (toolbar wrap) is fixed. Viewer chunk budget holds. |
| **S5 Shared kit and tokens** | `components/common/{Sheet,FloatingNav,NavRail,ProgressRing,Skeleton,VisuallyHidden}.tsx`, the common CSS modules (tokens only), `renderers/shared/**` (except `phone/`), `styles/{tokens,global}.css` | S0b, S2 (`GenerateAction`), S0 (`isMeshTunerEnabled`) | Density tokens apply under `[data-density='touch']`. P5, P12, P14 and P17 are done. `GenerateAction` variants work in a dev harness. `Sheet` meets the keyboard and drag a11y of §8.1. Gate T2 is in `StatusCluster`. The compiler check stays at 0 events. |

### Wave 2 (merge order S6 → S7)

| Slice | Owns | Depends on | Acceptance |
|---|---|---|---|
| **S6 Feature panels and linear renderer** | `components/{references,settings,generation,history,prompt,workflow}/**`, `renderers/linear/**`, `arch_allowlist/s6_components.json` (shrinks) | S1, S2, S4, S5 | Content and Panel are split with zero visual diff in Default (screenshot compare at 1280×800 before and after). `GenerateCard` = `GenerateAction variant="card"`, pixel-identical apart from the contrast token. `PromptContent`, Segmentation, Part Extraction and Export panels render from caps; unavailable states show backend reasons. `ProgressCard` renders `job.steps` when present and the legacy stages otherwise. `HistoryItem` gets kind and parts badges plus T5. `HistoryDrawer` becomes modal (P7). Components no longer import stores (allowlist file empty except deferred items, each listed with a reason). |
| **S7 Default renderer and polish** | `renderers/default/**` (takes over from S3's temporary entry), `components/layout/*` CSS (mode-aware media), `arch_allowlist/s7_default.json` | S3, S5, S6, S4 (`PartsCard`) | Default looks the same at 1280×800 except for: the source switch, the Parts options card, the PartsCard, and the gear. P1–P3, P6, P8–P11, P13, P15 and P16 are done, and the probe passes at every viewport. Gates T3 and T6 are in place. Prompt and image both generate against the mock. G1 is the body for a defaults run. |

### Wave 3 (parallel; merge order S8 → S9; S11 independent)

| Slice | Owns | Depends on | Acceptance |
|---|---|---|---|
| **S8 Mobile renderer** | `renderers/mobile/**`, `renderers/shared/phone/**` | S5, S6, S4 | Everything in §8.4. In the probe: no horizontal scroll at 320–430 px widths, 0 targets under 48 px, the nav is hidden while typing, the landscape rail at 812×375, and Generate is reachable in ≤ 1 tap from every screen. Prompt and image generation both work. Parts sheet with viewer interaction. Request body equals G1 for defaults. |
| **S9 Tablet renderer** | `renderers/tablet/**` | S8 (`shared/phone`), S5, S6, S4 | `resolvePosture` table behaviour in the browser: rail at 1024×768, 1180×820 and 812×375; bottom at 768×1024 and 820×1180; compact below 600. The section survives an orientation flip. Container-query panels look right at 320–420 px. Probe passes. |
| **S11 Node renderer** | `renderers/node/**`, `package.json`, `package-lock.json`, `vite.config.ts`, `THIRD_PARTY_NOTICES.md` | S1, S2, S5, S6, S4 | `@xyflow/react@12.12.0` is installed with an exact pin. The `xyflow` chunk is lazy (budgets pass; it is not in the initial imports; nested zustand 4 is inside it). Everything in §8.6: connect validation through `canConnect`, a cycle is impossible, maxInstances is enforced with a reason, Run compiles the same doc (body equals G1 for the canonical doc and G2 after "Separate parts"), and live node status comes from mock steps. ListView works keyboard-only and is the default below 600 px. The compiler check has 0 events, or documented `'use no memo'` with a reason. Import and export round-trip. |

**Definition of done for the milestone.**
- All slices are merged and the §15 gates are green.
- All four modes are selectable and persisted.
- Prompt and image work in every mode.
- Parts are shown when the backend provides them.
- The tuner is invisible with zero requests.
- The G1 body is unchanged.
- The user approves the milestone before any tuning work starts.

---------------------------------------------------------------------------------------------------

## 17. Risks and mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | The backend picks different names or shapes | Four adapter files (§4). `request_keys` gating. The strict mock mirrors `extra="forbid"`. Reconcile §4.4 before S0 merges. |
| R2 | The glob-based registration (`import.meta.glob`) silently misses a module | The arch test requires `registerCommands` in every bind file and a sync file per store. `?debug=commands` lists registered ids in dev. `DEFAULT_SNAPSHOT` is conservative, so a missing syncer blocks commands instead of enabling wrong ones. |
| R3 | `AppSnapshot` lags a store | Syncers subscribe synchronously (zustand `subscribe` fires inside `set`). The bus always reads a fresh snapshot. A unit test sets a store and reads the snapshot in the same tick. |
| R4 | React Flow 12 and React Compiler interplay is unverified [A] | It is isolated in `renderers/node/flow/*`. The compiler check runs on those files. `'use no memo'` is the documented escape hatch. The engine and adapter are pure, so a library swap (Rete) touches only `flow/`. |
| R5 | `vendor` chunk regex captures the nested zustand 4 into the initial bundle | The `xyflow` group gets a higher priority than `vendor`, and `budgets.mjs` asserts initial-import membership. |
| R6 | GLTFLoader lower-cases custom attributes (`_PART_ID` → `_part_id`) and maps node `extras` to `userData` [A] | S4 verifies both with the mock GLB. `regionAttribute` is matched case-insensitively. When the attribute is missing, the UI degrades honestly. |
| R7 | One canonical doc means Node-UI edits change the Default UI's pipeline | This is intended (single source of truth). Linear renderers show a banner with "Restore default workflow" when the doc diverges from what they can render. Open question Q5. |
| R8 | Mode switch remounts the viewer and could reload the GLB | `ModelScene`'s deferred disposal (250 ms, `ModelScene.tsx:L41-60` [V]) plus a ≤ 150 ms switch budget keeps the `useLoader` cache. Verified in S3/S4 by watching network requests during a switch. |
| R9 | New tuner edits made before S0 conflict with S0's append to `api/types.ts` | The precondition in §16 (a clean tree). S0 is append-only. Tuner files are never edited. |
| R10 | iPadOS Safari `<dialog>` focus restore and `visualViewport` quirks [A] | A manual check on the Tablet slice. The sheet keeps a manual focus-restore fallback (stores the opener and focuses it on close). |
| R11 | Shortcut conflicts (React Flow Backspace, browser Ctrl+S) | React Flow `deleteKeyCode={null}`. Canvas-scoped shortcuts only. Ctrl+S only with canvas focus. |
| R12 | Prompt jobs' retry semantics are unclear to users | `job.retry` offers "Retry from generated image" (default; deterministic) and "Regenerate from prompt" in the History menu for prompt jobs. |

---------------------------------------------------------------------------------------------------

## 18. Open questions (genuinely undecided; everything else above is decided)

1. **Feature endpoint and model registry location.** `/features` (assumed), `/pipeline/catalog`, or a separate
   `/t2i/models`? Only `api/features.ts` changes, but the backend design must pick one.
2. **Optional request keys.** Will the backend accept `subject`, `segmenter_id`, `extra_terms` (segmentation) and
   `preset`, `style`, `model_id`, `part_hint`, `image_seed`/`randomize_image_seed` (prompt)? The frontend sends them
   only when they appear in `request_keys`.
3. **Model decisions (user).** The UI works with any subset, including none.
   - Text-to-image: Z-Image-Turbo, FLUX.2-klein-4B, Animagine XL (already on disk in a sibling project), or none yet.
   - Segmentation: the Grounding DINO + SAM 2.1 + Florence-2 baseline, the optional See-through anime parser, the
     optional gated SAM 3, the optional Sapiens2.

   Should the App settings page offer an **Install** button at all (E10), or should installation stay script-only,
   with the UI showing `install.hint`?
4. **Review step for prompt jobs.** Should a prompt job pause after text-to-image so the user can review or pick the
   image before the 3D stage? v1 is one-shot (contract map §4.2). A review flow needs a non-GLB result type.
5. **One shared workflow doc for all modes.** It is proposed and drives "no duplicated logic". The alternative is one
   doc per mode, where Node edits never affect Default.
6. **React Flow attribution.** Keep the themed attribution (default), or subscribe to Pro to remove it?
7. **Automated browser tests.** Add Playwright as a dev-only dependency under `test/` for CI-style UI-mode checks, or
   keep the agent-driven probe in the browser pane?
8. **Region highlighting.** Will `assembled.glb` carry the continuous body mesh with a `_PART_ID` (or
   `_FEATURE_ID_0`) vertex attribute, and will the manifest name it (`region_attribute`)? Without it, regions are
   listed but cannot be highlighted.
9. **Derived `part_decomposition` jobs** (E9). Should they appear as their own History entries (proposed: yes, with a
   "Parts of job X" badge through `derived_from_job_id`) or be grouped under the source job?
10. **An "Auto" UI mode.** It is not offered: the user asked for four modes with Default as the default. Settings only
    shows a "Suggested for this screen" hint. Confirm.
