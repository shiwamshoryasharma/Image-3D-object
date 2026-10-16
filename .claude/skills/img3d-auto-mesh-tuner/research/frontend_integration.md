# Img3D frontend: integration guide for Blender Runtime and the Auto Mesh Tuner UI

The current frontend builds, lints and passes the React Compiler check cleanly. I changed no project source files. My probe scripts are in `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\frontend\` and their outputs are in `E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\`. Everything new below is a design proposal. The backend owns the API contract, so `api-contract.md` §7/§8 should be updated before the frontend types are written.

## 0. Baseline I ran (no project files modified)

| Check | Command | Result |
|---|---|---|
| Lint | `E:\14-OCT-2026\Img3D\frontend\node_modules\.bin\oxlint.cmd src` | 0 warnings, 0 errors. 71 files, 116 rules, oxlint **1.88.0** |
| Type check (writes nothing) | `.\node_modules\.bin\tsc.cmd -p tsconfig.app.json --noEmit` | exit 0, about 5 s. `node_modules\.tmp\*.tsbuildinfo` was not touched |
| Production build (separate output folder) | `.\node_modules\.bin\vite.cmd build --outDir E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\frontend_build_probe --emptyOutDir` | OK, about 5 s. three 808 kB, r3f 249 kB, index 232 kB, react 219 kB |
| React Compiler check | `node test\frontend\review\compiler_check.mjs` (run from repo root) | `[]`, "compiled functions: 60, non-success events: 0" |

Installed versions: react 19.3.0, zustand 5.0.15, lucide-react 1.57.0, three 0.186.1, @react-three/fiber 9.8.1, drei 10.7.9, axios 1.20.0, vite 8.3.4, TypeScript 6.0.3, babel-plugin-react-compiler 1.0.0, @vitejs/plugin-react 6.1.2.

## 1. Build and tooling facts

- **`package.json` scripts:**
  - `build` = `tsc -b && vite build`. It writes `frontend\dist`, which the backend serves at `/`. Running it changes what a live backend serves.
  - `lint` = `oxlint`. It has no `--max-warnings`, so warnings do not fail it.
- **`vite.config.ts`:**
  - Plugins are `react()` plus `babel({ presets: [reactCompilerPreset()] })` (L46–49).
  - The compiler gets default options. My probe `compiler_default_threshold.mjs` confirmed that a bailout **does not fail the build**: the function is just left unmemoized. So `compiler_check.mjs` is the only real gate.
  - The `/api` proxy has `ws: true` (L37–43). Manual chunks keep three/r3f out of the initial bundle (L60–71). Any new non-lazy import of `three` or `@react-three/*` would pull about 800 kB into the initial bundle. `viewerRuntime.ts` and `viewerTypes.ts` are safe to import from the main bundle.
- **TypeScript (`tsconfig.app.json`):**
  - `erasableSyntaxOnly`: no `enum`, no `namespace`, no parameter properties. Use `as const` tuples plus unions, as `api/types.ts` L4–56 does.
  - `verbatimModuleSyntax`: type imports must use `import type` or `type X`.
  - Also on: `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch`, `allowImportingTsExtensions`.
- **`.oxlintrc.json`:** plugins `react`, `typescript`, `oxc`; `react/rules-of-hooks` is an error; `react/only-export-components` warns, with `allowConstantExport`. A probe in `test\outputs\mesh_tuner\frontend\lint_probe\` (needs `--no-ignore`, because `test/outputs` is gitignored) showed these default rules firing too:
  - `only-export-components` warns even on `export const X = [...] as const` and on exported hooks or helpers in a component file. Put constants, selectors and hooks in `lib/`, `store/` or `hooks/`. `components/layout/mobileTabs.ts` exists for exactly this reason.
  - `react(set-state-in-effect)` warns on a synchronous `setState` inside `useEffect`.
  - `react-hooks(exhaustive-deps)` warns on missing effect dependencies.
  - `react(jsx-key)` warns on list items without a key.

## 2. Conventions

### State (zustand v5)
- **Store shape:** `create<State>()((set, get) => ({...}))`.
  - Async actions live in the store and own their `try/catch/finally`, `toApiError` and toasts. Example: `store/jobStore.ts` L197–223. Components call `void action()`.
  - Cross-store reads inside actions use `useXStore.getState()` (jobStore L198–205).
- **Selectors:** one primitive or stable reference per `useStore((s) => …)` call.
  - Named selectors: `selectActiveJob` / `selectViewedJob` (jobStore L306–310), `selectCapabilities` / `selectLimits` / `selectEngineState` (systemStore L80–82).
  - **Never return a newly allocated object or array from a selector.** Under v5 that causes an infinite re-render. If one is unavoidable, use `useShallow` from `zustand/react/shallow` (present in 5.0.15).
- **Persistence:** `persist` with `createJSONStorage(() => localStorage)`, `version`, `partialize`, and a defensive `merge` (settingsStore L215–239, uiStore L47–56).
  - Storage keys live in `lib/storage.ts` `STORAGE_KEYS` (L6–12). Add `meshTuner: 'img3d.meshTuner'` there.
  - `uiStore` persists only `viewer` (L51), so new UI flags such as `tunerSetupOpen` or `modelVariant` are ephemeral automatically.
- **Out-of-order REST vs WebSocket updates:** guard against stale data as `jobStore.isStale` (L58–63) and `referenceStore.applySet` (L76–81) do. Status never moves backwards, and a smaller `progress.updated_at` is ignored.
- **Transition toasts:** `notifyTransition` (jobStore L69–75) plus `toast.info/success/warning/error(title, message?)` (toastStore L40–45). The error toast lasts 8 s, others 4.5 s. Identical toasts collapse.

### API layer (`api/`)
- **No store imports in `api/`.** The client reports back through hooks registered with `configureClientHooks` (client.ts L186–197). The interceptor already handles reachability and 401 (L221–239), so new code must not handle 401 again.
- **Errors:** wrap everything in `toApiError(e)` (L152). `ApiError` exposes `{status, code, message, details, requestId, isUnreachable, isCancelled}` (L72–95). `errorMessage()` is at L177.
- **Timeouts:** default 30 s (L212). Override per call: `{ timeout: 120_000 }` (references.ts L63), or `timeout: 0` for blob downloads (jobs.ts L67).
- **URLs from the backend** are absolute paths (`/api/v1/...`):
  - For GLTFLoader, `<img>` and `<a>`, use `resolveApiUrl(path)`, which appends `?token=` (L42–49).
  - For axios on those paths, use `absolutePathConfig` (L242), as `downloadJobGlb` does (jobs.ts L60–78).
- **Types:** snake_case, mirroring the contract. Enums are `as const` tuples (types.ts).

### CSS
- One `X.module.css` next to each `X.tsx`, with camelCase class names.
- State classes are named after enum values and indexed as `styles[engine.state]` (EngineStatus L75), `styles[status]` (StatusBadge L18) or `styles[state]` (ProgressCard L158).
- Use only `tokens.css` variables. Their header says never hard-code colours, although existing modules do use a few fixed tints (`#86efac`, `#fda4b4`, `#d6ccff`). Copy those, don't invent new ones.
- **Tokens you will need:**
  - Surfaces `--color-surface-1..4`, `--color-overlay`
  - Status colours `--color-{success,warning,danger,info}` and their `-soft` variants
  - Accent `--color-accent`, `-soft`, `-border`
  - Fonts `--font-mono`, sizes `--text-2xs..2xl`, spacing `--space-1..10`, radii `--radius-*`, shadows `--shadow-*`
  - z-index: `--z-dropdown:40`, `--z-drawer:60`, `--z-toast:80`, `--z-modal:90`
  - Durations `--duration-*` (zeroed under prefers-reduced-motion, L113–119)
- Mobile breakpoint is `@media (max-width: 767px)`. The viewer uses `@container (max-width: 640px)` (ViewerPanel.module.css L134).
- Global helpers: `.visually-hidden`, `.mono`, and keyframes `img3d-spin`, `img3d-fade-in`, `img3d-pulse` (global.css L130–170). The UI is dark-only.

### Icons
- `lucide-react` 1.57.0. Use `size={14–16}` in panels and `size={20}` in the tab bar, and always pass `aria-hidden` when the icon is decorative.
- I checked these names exist: `WandSparkles`, `Wand2`, `Wrench`, `Hammer`, `ScanSearch`, `Download`, `FolderOpen`, `HardDrive`, `Package`, `PackageCheck`, `ShieldCheck`, `ShieldAlert`, `CircleCheck`, `CircleAlert`, `TriangleAlert`, `GitCompareArrows`, `ArrowLeftRight`, `Columns2`, `SquareSplitHorizontal`, `Layers`, `Blend`, `ListChecks`, `ClipboardCheck`, `Settings2`, `SquareTerminal`, `Gauge`, `Eye`, `EyeOff`, `RefreshCw`, `Undo2`, `CirclePlay`, `Pause`.

### Accessibility patterns to copy
- **Popover:** a `button` with `aria-expanded` and `aria-controls`, a `role="dialog"` panel, and close on outside `pointerdown` or Escape (EngineStatus.tsx L32–50, L72–93). On mobile it becomes `position: fixed` (EngineStatus.module.css L99–107).
- **Drawer:** `aside` with `inert={!open}` and `aria-hidden`, focus moved to the panel on open and restored on close, Escape and backdrop close it (HistoryDrawer.tsx L18–44).
- **Menu:** rendered in a portal; `role=menu` / `menuitem`; arrow keys, Home/End, Escape and Tab (Menu.tsx).
- **Tabs:** WAI-ARIA roving tabindex, arrows/Home/End (MobileTabBar.tsx L17–39). Panels use `role=tabpanel` plus `hidden` (AppShell.tsx L76–88).
- **Live regions:** announce only status or stage changes through a visually-hidden `role="status"`, never per-second timers (ProgressCard.tsx L95–98, ModelViewer.tsx L37–40, BackendBanner L35–36). Errors use `role="alert"`. The Toaster is `aria-live="polite"`.
- **Controls:** `ProgressBar` is `role=progressbar`, `Switch` is `role=switch`, `SegmentedControl` is `radiogroup`/`radio`. `IconButton` requires a `label` (used for `aria-label` and `title`).
- **React 19 features in use:** ref-as-prop (Menu passes `ref` into `IconButton`, L109), `inert`, and `useEffectEvent` (GenerateCard L43).

### React Compiler constraints (measured with probes)
Source: `test\mesh_tuner\probe\frontend\compiler_patterns.mjs`, results in `test\outputs\mesh_tuner\frontend\compiler_patterns.json`.

| Pattern in a component or hook | Result |
|---|---|
| `try { await … } finally { … }` | **Bails out**: "Handle TryStatement without a catch clause". The repo's workaround is Promise chaining (ModelStatsCard L24–33, useFullscreen L21–27). |
| `try/catch` whose `try` body contains `?:`, `&&`, `\|\|`, `??` or `?.` | **Bails out**: "Support value blocks … within a try/catch" |
| Plain `try/catch` with `await` | OK |
| Reading or writing `ref.current` during render | **Bails out**: "Cannot access refs during render". Only touch refs in effects and handlers. |
| Unconditional `setState` during render | **Bails out** |
| Derived-state reset: `if (prev !== x) { setPrev(x); setY(…) }` | OK. Use this instead of `useEffect(() => setY(…), [x])`, which oxlint flags as set-state-in-effect. |
| `useCallback` or `useMemo` with missing dependencies | **Bails out**: "Existing memoization could not be preserved". Prefer no manual memoization; the compiler does it. |
| `useEffectEvent`, `for…of` with `break`, document-listener popover effect, `switch` returns | OK |
| Class components (`ViewerErrorBoundary`, `ModelErrorBoundary`) | Not compiled; this is expected |

Rule of thumb: put all async error handling in store actions. Stores are not compiled, so `try/finally` is fine there.

`test\mesh_tuner\probe\frontend\proposed_components.mjs` compiled sketches of a native `<dialog>` wrapper, the runtime status pill, the variant hook and the report table with no bailouts (results in `…\frontend\proposed_components.json`).

## 3. Reusable component prop signatures

```ts
// common/Button.tsx L6-16, L43-50
Button: ComponentProps<'button'> & { variant?: 'primary'|'secondary'|'ghost'|'danger'|'subtle'; size?: 'sm'|'md'|'lg'; icon?: ReactNode; iconRight?: ReactNode; loading?: boolean; fullWidth?: boolean }  // loading ⇒ disabled + aria-busy + Spinner
IconButton: ComponentProps<'button'> & { label: string; icon: ReactNode; active?: boolean; size?: 'sm'|'md'; variant?: 'ghost'|'solid'|'danger' }
// common/Panel.tsx L5-18 — renders <section aria-labelledby={`${id}-title`}>
Panel: { title?: ReactNode; icon?: ReactNode; actions?: ReactNode; subtitle?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string; id?: string; flush?: boolean }
// common/ProgressBar.tsx L3-10 — value null = indeterminate
ProgressBar: { value: number|null; tone?: 'accent'|'success'|'warning'|'danger'|'muted'; size?: 'xs'|'sm'|'md'; label?: string; className?: string }
// common/SegmentedControl.tsx L5-21 (generic)
SegmentedControl<T extends string|number>: { value: T; options: readonly { value: T; label: ReactNode; title?: string; disabled?: boolean }[]; onChange: (v: T) => void; label: string; size?: 'sm'|'md'; disabled?: boolean }
// common/StatusBadge.tsx L15 — only JobStatus
StatusBadge: { status: 'queued'|'running'|'completed'|'failed'|'cancelled'; label?: string }
// common/Menu.tsx L7-30
Menu: { label: string; icon: ReactNode; sections: { title?: string; items: { id: string; label: ReactNode; icon?: ReactNode; hint?: string; disabled?: boolean; danger?: boolean; onSelect: () => void }[] }[]; disabled?: boolean; triggerSize?: 'sm'|'md'; triggerVariant?: 'ghost'|'solid' }
// common/Switch.tsx L5-12
Switch: { checked: boolean; onChange: (c: boolean) => void; label: ReactNode; description?: ReactNode; disabled?: boolean; size?: 'sm'|'md' }
// common/EmptyState.tsx L5-12
EmptyState: { icon?: ReactNode; title: ReactNode; description?: ReactNode; action?: ReactNode; compact?: boolean; tone?: 'neutral'|'danger' }
// common/NumberField.tsx L6-22 (draft text, commits clamped value on blur/Enter; parses 1.5k/2m)
NumberField: { value: number|null; onChange; min; max; step?; nullable?; placeholder?; disabled?; id?; ariaLabel?; className?; integer?; suffix? }
// common/RangeSlider.tsx L5-15
RangeSlider: { value; min; max; step?; onChange; ariaLabel: string; ariaValueText?; disabled?; id? }
// common/Spinner.tsx: { size?: number; label?: string }
// settings/Field.tsx L5-13: { label; htmlFor?; labelId?; hint?; aside?; children }
// viewer/ViewerToolbar.tsx L14-21
ViewerToolbarProps: { disabled; isFullscreen; fullscreenSupported; onResetCamera; onScreenshot; onToggleFullscreen }
```

**Missing building blocks you will have to create:** a Modal/Dialog, a text input, a select, a table, a generic badge, a reusable step list, and a tooltip (the codebase uses `title`).
- `ProgressCard`'s `StageIcon` (L16–29) and the `.stage*` styles (ProgressCard.module.css L81–165) are not exported. Either copy them into a new `common/StepList.tsx` or extract them.
- Text input styling can be copied from `auth/LoginScreen.module.css` (`.inputWrap`, `.input`).

## 4. Files to add (proposal)

### 4.1 `api/types.ts`: new "Mesh tuner" section
`TuningRun.status` reuses `JobStatus`. That way `StatusBadge`, `TERMINAL_STATUSES` and `isTerminal(x: Pick<Job,'status'>)` (jobStore L44) work unchanged.

```ts
export const BLENDER_SOURCES = ['system', 'embedded', 'custom'] as const
export type BlenderSource = (typeof BLENDER_SOURCES)[number]
export interface BlenderInstall { id: string; path: string; version: string; version_tuple: [number, number, number]; is_lts: boolean; source: BlenderSource; compatible: boolean; incompatible_reason: string | null }
export type BlenderRuntimeState = 'not_configured' | 'detecting' | 'ready' | 'incompatible' | 'downloading' | 'error'
export type BlenderDownloadState = 'idle' | 'resolving' | 'downloading' | 'verifying' | 'extracting' | 'completed' | 'failed' | 'cancelled'
export interface BlenderDownload { state: BlenderDownloadState; version: string | null; bytes_done: number; bytes_total: number | null; percent: number | null; error: string | null; updated_at: IsoDateTime }
export interface BlenderRuntimeStatus { state: BlenderRuntimeState; min_version: string; selected: BlenderInstall | null; installs: BlenderInstall[]; message: string | null; error: string | null; download: BlenderDownload | null }
export type TuningStepStatus = 'pending' | 'running' | 'done' | 'warning' | 'skipped' | 'failed'
export interface TuningStep { id: string; label: string; status: TuningStepStatus; message: string | null; duration_seconds: number | null }
/** Data-driven so new Blender steps/metrics need no frontend change. */
export interface TuningMetric { key: string; label: string; unit: string | null; before: number | null; after: number | null; better: 'lower' | 'higher' | 'neutral' }
export interface TuningOptions { subdivision: boolean; quads: boolean; symmetry: 'auto' | 'x' | 'off'; refine_face: boolean; clean_rig_weights: boolean; smoothing: number; min_island_ratio: number }
export interface TuningProgress { percent: number; step_id: string | null; step_index: number; step_count: number; message: string | null; updated_at: IsoDateTime }
export interface TuningRun { id: string; job_id: string; status: JobStatus; created_at: IsoDateTime; started_at: IsoDateTime | null; finished_at: IsoDateTime | null; options: TuningOptions; progress: TuningProgress; steps: TuningStep[]; metrics: TuningMetric[]; warnings: string[]; blender_version: string | null; result: { model_url: string; download_url: string; stats: MeshStats } | null; error: JobError | null }
// WS (§8) — extend the union at L420
export interface WsBlenderRuntimeMessage { type: 'blender.runtime'; ts: IsoDateTime; data: BlenderRuntimeStatus }
export interface WsBlenderDownloadMessage { type: 'blender.download'; ts: IsoDateTime; data: BlenderDownload }
export interface WsTuningProgressMessage { type: 'tuning.progress'; ts: IsoDateTime; data: TuningRun }  // full snapshot, like job.updated
```

`tuning.progress` should carry the **full `TuningRun` snapshot**, not a delta, so that a dropped progress frame is harmless. On the backend, `core/events.py` `Event(droppable=True)` should be used for progress frames only, never for status transitions.

**Tuned-model URLs must be unique per run**, for example `/api/v1/tunings/{run_id}/model.glb`. drei's `useLoader` caches by URL string.

### 4.2 `api/meshTuner.ts`
Same style as `api/jobs.ts`. Endpoints are proposals:
- `getBlenderRuntime()` → `GET /mesh-tuner/runtime`
- `detectBlender()` → `POST …/runtime/detect`, with `{ timeout: 60_000 }`. Blender `--version` takes about 0.13 s per install according to another agent's `outputs\mesh_tuner\detect\cli_probe.json`.
- `selectBlender({install_id}|{path})` → `PUT …/runtime/selection`
- `startBlenderDownload({version?})` → `POST …/runtime/download` (202). The ZIP must download on the backend; never through axios in the browser.
- `cancelBlenderDownload()`
- `startTuning(jobId, options)` → `POST /jobs/{id}/tunings` (202)
- `listTunings(jobId)`
- `getTuning(id)`
- `cancelTuning(id)`
- `tunedModelUrl(run) = resolveApiUrl(run.result.model_url)`
- `downloadTunedGlb`: copy of `downloadJobGlb` (jobs.ts L60–78) with `absolutePathConfig` and `timeout: 0`.

### 4.3 `store/meshTunerStore.ts`
- **State:**
  - `runtime: BlenderRuntimeStatus|null`, `runtimeStatus: LoadStatus`, `runtimeError: ApiError|null`
  - `download: BlenderDownload|null`, `detecting`, `selecting`
  - `runs: Record<string, TuningRun>`, `latestRunByJob: Record<string, string>`, `activeRunId: string|null`, `startingJobIds: string[]`
  - `options: TuningOptions`, persisted via `partialize`
- **Actions:**
  - `refreshRuntime`, `detect`, `select`, `startDownload`, `cancelDownload`
  - `applyRuntime(r)` and `applyDownload(d)`, called from the WebSocket; ignore a download whose `updated_at` is older than the one held
  - `upsertRun(run)`, using an `isStale` copy of jobStore L58–63
  - `loadRunsForJob(jobId)`, `startTuning(jobId)`, `cancelTuning(id)`
  - `resync()` = `refreshRuntime` + refresh of the active run
- Transition toasts follow the `notifyTransition` pattern.

### 4.4 Hooks
- **`hooks/useTuneAvailability.ts`:** same shape as `useGenerateAvailability` (L5–16): `{ canTune, reason, note, busy }`. Reasons, in order:
  1. backend unreachable
  2. no completed viewed job
  3. runtime not `ready` (no compatible Blender ≥ 5.1, or a download is in progress)
  4. a tuning run for this job is active
  5. starting…
- **`hooks/useViewedTuning.ts`:** `const jobId = useJobStore((s) => s.viewedJobId)`, then `useMeshTunerStore((s) => (jobId ? s.runs[s.latestRunByJob[jobId]] ?? null : null))`. This returns a stable store reference.

### 4.5 Components (`components/meshTuner/`)
- **`BlenderRuntimeStatus.tsx` (+ `.module.css`):** header pill and popover cloned from `EngineStatus.tsx`.
  - Pill text: `Blender 5.2.2` or `Blender: set up`. A spinner plus `ProgressBar size="xs"` while downloading.
  - Popover contents: selected runtime in a `dl.grid` (version, source, path in mono with `overflow-wrap:anywhere`, compatibility), download progress, and buttons "Re-detect", "Change runtime…" (opens the dialog) and "Download Blender".
  - If `runtime === null`, render nothing or a muted pill, like EngineStatus L52–59 does for `!system`.
- **`common/Dialog.tsx` (new):** a native `<dialog>` whose effect calls `showModal()` or `close()` based on `open`, with `onCancel` (preventDefault, then `onClose`) and a backdrop click to close. It gives you the top layer, a focus trap and inert background natively, so no z-index is needed. Style `::backdrop` with `var(--color-overlay)`. On mobile, make it a full-height sheet under `@media (max-width:767px)`. This compiled cleanly in the probe.
- **`BlenderSetupDialog.tsx`:**
  - `SegmentedControl<'system'|'embedded'>` labelled "Runtime source".
  - A `fieldset` of native radio buttons, one row per detected install: version, LTS tag, source tag, mono path, and a compatible/incompatible pill copied from `EngineStatus.module.css .stateTag`. Disable or annotate incompatible rows (4.2.17 < 5.1).
  - A custom-path text input.
  - Download block: `ProgressBar` showing `percent`, or `null` (indeterminate) while verifying or extracting, plus `formatBytes(done)/formatBytes(total)`.
  - Buttons "Use selected" (primary), "Download portable" and "Cancel download" (danger).
- **`TuningCard.tsx`:** a `<Panel id="tuning" title="Mesh tuner" icon={<WandSparkles size={16}/>} actions={<StatusBadge status={run.status}/>}>`.
  - Runtime line plus a "Set up Blender" button.
  - Options as `Switch` and `RangeSlider` controls.
  - "Auto Tune" as `<Button variant="primary" size="lg" fullWidth icon={<WandSparkles size={18}/>}>`, with the disabled reason shown as GenerateCard L119–125 does.
  - Live overall `ProgressBar` plus a step list, with the visually-hidden `role=status` announcement pattern.
  - A Cancel button.
- **`TuningReport.tsx`:**
  - Step checklist: an `ol` with per-step icon, label, status (visually hidden), duration and message, reusing the ProgressCard `.stage/.done/.active/.failed/.skipped` styles; add a `.warning` style.
  - Metrics: a `<table>` with `<th scope="col">` columns Metric / Before / After / Change. The change cell is coloured by `better` (success / danger / subtle). Use `formatInteger`, `formatBytes` and `formatDuration` from `lib/format.ts`.

## 5. Adding the WebSocket events (exact edit points)

The flow is: server frame → `parseMessage` (ws.ts L77–88, which only checks that `type` is a string) → `dispatch` switch (L248–265). The switch's `default: break` **silently ignores unknown types**, so the backend can start emitting new events before the frontend handles them.

1. **`api/types.ts` L420:** add the three message interfaces to `WsServerMessage`.
2. **`api/ws.ts` `RealtimeEventMap` L31–37:** add `'blender.runtime': BlenderRuntimeStatus; 'blender.download': BlenderDownload; 'tuning.progress': TuningRun`.
3. **`api/ws.ts` `dispatch` L248–265:** add `case 'blender.runtime': this.emit('blender.runtime', message.data); break` and the same for the other two. The union narrows the type; every case needs `break` because of `noFallthroughCasesInSwitch`.
4. **`hooks/useBackendSession.ts` L107–119:** add `client.on('blender.runtime', (r) => useMeshTunerStore.getState().applyRuntime(r))`, likewise for download and tuning. Also add `client.on('hello', () => void useMeshTunerStore.getState().resync())`: `hello` fires on every (re)connect, so it catches up on events missed while the socket was down.
5. **`pollBackend` L35–43** (fallback while the socket is down, safety poll every 20 s while it is up):
   - If the active run is not terminal, refresh it.
   - If a download is in progress, or `mode === 'safety'`, run `refreshRuntime()`.
6. **`syncSessionData` L46–52:** add `useMeshTunerStore.getState().refreshRuntime()`. If the viewed job is set, also `loadRunsForJob(viewedJobId)`.
7. **Backend side (for reference):**
   - Add the event names to `backend/schemas/events.py` `EventType` (L13–17).
   - Publish via `EventBus.publish_threadsafe(Event(type=…, data=…, job_id=…, droppable=…))` (`core/events.py` L28–36), as `services/job_events.py` L25–30 does.
   - `api/websocket_hub.py` `_job_filter` (L227–237) lets every non-`job.updated` event through.
   - `hello` carries only `{system}`, so the runtime state must be fetched over REST, or an optional field added to `SystemStatus` (a contract change).

## 6. Where to mount the UI

| UI | File / location | Change |
|---|---|---|
| Runtime pill | `components/layout/Header.tsx` L36–42, inside the `(phase==='ready'\|\|'unreachable')` block, after `<EngineStatus>` | `<BlenderRuntimeStatus compact={isMobile} />`. `.status` is `flex:1; min-width:0` (Header.module.css L44–50), so it fits. |
| Setup dialog | `App.tsx` L37, next to `<HistoryDrawer/>` | `{phase === 'ready' && <BlenderSetupDialog />}` on all breakpoints. Open/close via a new `uiStore` flag `tunerSetupOpen` (not persisted). |
| TuningCard, desktop | `AppShell.tsx` `DesktopLayout` L44 | After `<ModelStatsCard />` inside the right `.scrollArea`. |
| TuningCard, tablet | `AppShell.tsx` `TabletLayout` L70 | After `<ModelStatsCard />`. |
| TuningCard, mobile (recommended) | `AppShell.tsx` `MobileLayout` L106–107, viewer tab | After `<ModelStatsCard />`. This keeps the Original/Tuned toggle and the report on the same tab. |
| Optional 5th mobile tab | `uiStore.ts` L7 `MobileTab` adds `'tune'`; `MobileTabBar.tsx` L10–15 adds `{ id:'tune', label:'Tune', icon:<WandSparkles size={20}/> }`; `MobileTabBar.module.css` L12 changes `repeat(4…)` to `repeat(5…)`; `AppShell` L95–115 adds `<MobilePanel tab="tune">` | Add a pulse dot while tuning, like L67–71. `mobileTabs.ts` ids work for any tab. At 375 px each tab is 75 px, and `text-2xs` nowrap labels fit. `mobileTab` is not persisted, so no migration is needed. |
| Scroll-into-view | `AppShell.tsx` `useRevealProgress` L18–26 | Clone as `useRevealTuning` keyed on the active run id. |
| History marker | `history/HistoryItem.tsx` L80–85 `.meta` | `<span>tuned</span>` when `latestRunByJob[job.id]` has completed. |
| Stats for the tuned model | `viewer/ModelStatsCard.tsx` L71–92 rows, L123 `DownloadButton` | When the variant is `tuned`, show `run.result.stats` and a tuned-GLB download. |

## 7. Viewer: switching between the original and tuned models

**How it works today:**
- `ViewerPanel.tsx` L79–81 computes `url = jobModelUrl(result)`. L96 and L105 key **both** `ViewerErrorBoundary` and `ModelViewer` by `url`.
- So a URL change remounts the whole `<Canvas>` (a new WebGL context), and `ModelViewer` L56–58 restarts `viewerRuntime`.
- `ModelScene.tsx` `Model` L83–94 schedules disposal 250 ms after unmount, including `useLoader.clear` (L91). Switching back therefore **downloads the GLB again**.
- `ViewerController` L148–155 refits the camera whenever the layout changes, so the camera resets too.
- Simply swapping the URL works, but each toggle costs a context rebuild, a re-download and a camera reset. Acceptable only as a v1 fallback.

**Recommended: mount both models in one canvas and toggle visibility.**
1. **ViewerPanel:**
   - `originalUrl = jobModelUrl(result)`.
   - `tunedUrl = run?.status === 'completed' && run.result ? resolveApiUrl(run.result.model_url) : null`.
   - Track the variant with the derived-reset pattern, which compiled in the probe: `if (prevJob !== job?.id) { setPrevJob(job?.id); setVariant('tuned') }`. Or keep it in `uiStore`.
   - Keep `key={originalUrl}` on the boundary and on `ModelViewer`, so the canvas stays alive while toggling.
   - Pass `tunedUrl` and `variant` down. The caption (L122–127) and the screenshot filename (L89) should include the variant.
2. **`viewerTypes.ts` `ModelViewerProps` (L11–18):** add `secondaryUrl?: string | null; showSecondary?: boolean`. `ModelViewer` forwards them to `ModelScene`. `retry` (L60–63) must clear both URLs.
3. **ModelScene:**
   - Render the original `<Model>` as now; it owns `onLayout`, so the camera fits only when the original changes.
   - When `secondaryUrl` is set, render a second `<ModelErrorBoundary><Suspense fallback={null}><Model url={secondaryUrl} …/></Suspense></ModelErrorBoundary>`, mounted immediately so it preloads.
   - `Model` gets a `visible` prop: `<group position={offset} visible={visible}>`.
   - **Position the tuned model with the original's `layout.offset`.** `computeLayout` (sceneUtils.ts L28–39) recentres each model on its own bounding box. Island removal or smoothing changes the bounding box, so without this the two models would not line up.
   - The backend must keep the coordinate frame: no re-centring or applied transforms in Blender, and consistent glTF Y-up export.
   - Keep the original visible until the tuned model reports ready, to avoid a blank frame.
4. **`viewerRuntime.ts`:** it is one global store, and `onLoadProgress` (ModelScene L32–34) is module-level, so the two loaders would mix their byte counts. Either pass a per-URL callback into `useLoader(GLTFLoader, url, configureLoader, onProgress)` and ignore non-primary URLs, or add `secondaryStatus` / `secondaryBytes` fields for a small "Loading tuned model…" chip.
5. **Toggle control:** `SegmentedControl<'original'|'tuned'> label="Model version" size="sm"`, with tuned disabled when no tuned model exists (`SegmentOption.disabled`, L10).
   - **Do not put it in `.top`.** On a 375 px phone that row is already full: `.modes` is 236 px plus three 34 px IconButtons (ViewerToolbar.module.css L1–33).
   - Add a new absolutely positioned `.variantBar` at `top: calc(var(--space-3) + 40px)`, left side, `z-index: 2`, with the backdrop styling of `.modes > div`.
   - Pass it through new optional `ViewerToolbarProps`.
   - An optional `T` hotkey can be added via a window `keydown` effect like GenerateCard L51–60, ignoring key presses while focus is in an input.
6. **Memory:** keeping both GLBs (around 500 k faces and 2K textures each) mounted is fine on desktop. On mobile, consider mounting the secondary only when tuned is selected.

## 8. Developing the UI without Blender
- **Mock backend:** `test\frontend\mock_backend.mjs` is a dependency-free contract mock. Run `node ..\test\frontend\mock_backend.mjs --port 8765 [--auth]`, then `$env:IMG3D_BACKEND_URL='http://127.0.0.1:8765'; npm run dev` (it uses `MOCK_USER` / `MOCK_PASSWORD` env vars when `--auth` is on).
- **Extending it (test-only):**
  - Add routes in the router (L279–408).
  - `broadcast(type, data)` (L424–427) only wraps `job.updated`; other types pass through raw.
  - Simulate download progress and tuning steps with `setInterval`, the way the engine load is simulated (L32–41).
  - Serve the tuned GLB with `buildSphereGlb()` (L187+), giving it a distinct URL.

## 9. Commands (PowerShell)
```powershell
cd E:\14-OCT-2026\Img3D\frontend
npm run lint                                   # oxlint (warnings don't fail; for a strict gate: npx oxlint --max-warnings 0)
npx tsc -p tsconfig.app.json --noEmit          # type-check, writes nothing
npm run build                                  # tsc -b + vite build → frontend\dist (served by backend at /)
npx vite build --outDir ..\test\outputs\mesh_tuner\frontend_build_probe --emptyOutDir   # build without touching dist
cd E:\14-OCT-2026\Img3D
node test\frontend\review\compiler_check.mjs   # must print [] and "non-success events: 0" (baseline 60 compiled fns)
node test\mesh_tuner\probe\frontend\compiler_patterns.mjs test\outputs\mesh_tuner\frontend\compiler_patterns.json
node test\mesh_tuner\probe\frontend\proposed_components.mjs test\outputs\mesh_tuner\frontend\proposed_components.json
```

## 10. Gotchas checklist
- **Compiler gate:** a bailout never fails `vite build`. Always run `compiler_check.mjs`.
- **No `try/finally`, and no `?:` / `&&` / `??` / `?.` inside a `try`, in components or hooks.** Keep that logic in store actions or use Promise chains.
- **zustand v5 selectors** must return primitives or existing references.
- **`only-export-components`:** keep constants, hooks and selectors out of component files. Even `as const` arrays trigger the warning.
- **No `setState` directly in effects.** Use derived-state reset or `key` remounting.
- **Long-running backend operations** (detect, download, tuning) should return 202 and report progress over the WebSocket, mirrored by a REST snapshot for the polling fallback.
- **Tuned URLs must be unique per run.** Use `resolveApiUrl` for the viewer and axios with `absolutePathConfig` for downloads.
- **Never import three.js from non-lazy modules.**

## Files I created (probes and outputs only)
- `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\frontend\compiler_patterns.mjs`
- `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\frontend\proposed_components.mjs`
- `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\frontend\compiler_default_threshold.mjs`
- `E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\frontend\compiler_patterns.json`
- `E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\frontend\proposed_components.json`
- `E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\frontend\lint_probe\` (`MixedExports.tsx`, `ConditionalHook.tsx`, `EffectDeps.tsx`)
- `E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\frontend_build_probe\` (vite build output)