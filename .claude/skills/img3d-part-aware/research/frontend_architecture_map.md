# Frontend architecture map — part-aware milestone + four UI modes

Date: 2026-10-16. Phase: read-only. Scope: `frontend/src/**`, the frontend configs, and `test/frontend/**`.
Author role: frontend architecture mapper.

**Legend.** **[V]** means verified: I read the file or ran the command. **[A]** means assumption or proposal.
`file:Lnn` refers to the line in the current working tree. That tree includes uncommitted 2026-10-16 tuner edits in
`api/types.ts`, `components/meshTuner/BlenderDownloadPanel.tsx`, `lib/meshTuner.ts` and `store/meshTunerStore.ts`
(`git status`) **[V]**.

**Read-only compliance.** I modified no project file except this report. Lint, `tsc --noEmit` and the compiler
check only read files. The production build went to my scratchpad (`--outDir …\scratchpad\vite_build_probe`), so
`frontend/dist` and `node_modules/.tmp` were not touched. To measure layouts I ran the in-memory test double
`test/frontend/mock_backend.mjs` and `vite preview` of the scratchpad build on 127.0.0.1 for a few minutes. Both
are stopped. The real backend, GPU and model were not used. The excluded jobs were not touched.

---------------------------------------------------------------------------------------------------

## 0. TL;DR

1. **The state and API layer is already cleanly separated from the visuals.** The layers are `api/` (axios, types,
   WebSocket client), `store/` (8 zustand stores plus `viewerRuntime`), `hooks/useBackendSession.ts` (the only
   session/realtime wiring) and `hooks/useGenerateAvailability.ts` (the single source of truth for "can
   generate"). Components almost never call the API. The only exceptions are the two GLB download buttons
   **[V]**. Four renderers can share this layer without duplicating logic.
2. **Layout is hard-wired in `App.tsx` and `components/layout/AppShell.tsx`.** One width-only
   `useBreakpoint()` hook (mobile < 768 ≤ tablet < 1280 ≤ desktop) drives three layouts. It ignores orientation,
   height and pointer type. Eleven CSS modules repeat `@media (max-width: 767px)`. Three feature components push
   mobile-tab navigation directly (`setMobileTab('viewer')`) **[V]**. These are the main refactor points.
3. **Verified breakages at small sizes:**
   - At 320 px the view-mode control clips "Normals".
   - On a phone in landscape (812×375) the app picks the tablet layout. The 340 px-min viewer then overflows a
     295 px column, and the left column is mostly the Generate card.
   - At 740×360, header, generate bar and tab bar leave 157 px for content.
   - At 768×1024 portrait, the tablet grid gives the viewer only 340 px of width but 635 px of height.
   - Touch targets are mostly 26–34 px: 43 of 62 controls are under 44 px at tablet size.

   **[V]** (browser measurements, §3.3).
4. **Proposal [A]:** add `src/app/` (root and mode switch), `src/shells/{default,node,mobile,tablet}/` (thin,
   lazy-loaded renderers), `src/nodeGraph/` (a pure-TypeScript graph model with no React) and a
   `navigationStore` built on "reveal" intents. Split each feature panel into `XxxContent` (no card chrome) and
   `XxxPanel`. Store the mode in a new persisted `preferencesStore` under `STORAGE_KEYS.preferences =
   'img3d.preferences'`, default `'default'`, and switch it from a new App-settings dialog. Expose the active
   mode as `<html data-ui-mode data-orientation data-pointer>`.
5. **Prompt-to-3D** goes at the top of the inputs column or tab as an "Image | Prompt" source switch. It needs:
   - a new `inputStore`;
   - a branch in `jobStore.submit` (`store/jobStore.ts:L197`) and in `useGenerateAvailability`
     (`hooks/useGenerateAvailability.ts:L58`);
   - backend contract work. No prompt endpoint or type exists today.
6. **The Auto Mesh Tuner UI is mounted in nine places** (§7). With no flag, a missing Blender auto-opens the setup
   dialog on startup (`store/meshTunerStore.ts:L264-272`) **[V]**. Proposed fix: a
   `lib/features.ts → FEATURES.meshTuner` flag, default **off**, applied at six choke points.
7. **Gates are green today [V]:**
   - oxlint 1.88.0: 0 warnings and 0 errors, 91 files, 116 rules.
   - `tsc`: 0 errors on both projects.
   - Compiler check: `[]`, 91 compiled functions, 0 non-success events.
   - `vite build`: OK in 2.65 s. Initial JS is about 550 kB min (about 180 kB gzip).

---------------------------------------------------------------------------------------------------

## 1. Inventory and toolchain [V]

- `frontend/src`: 91 source files (oxlint count), 15,864 lines including CSS. Entry is `index.html` →
  `src/main.tsx` (StrictMode → `App`).
- Installed versions (read from `node_modules/*/package.json`):

  | Group | Packages |
  |---|---|
  | React | react / react-dom 19.3.0 |
  | State | zustand 5.0.15 |
  | Build | vite 8.3.4 (Rolldown), @vitejs/plugin-react 6.1.2, @rolldown/plugin-babel 0.2.4, babel-plugin-react-compiler 1.0.0 |
  | Types and lint | typescript 6.0.3, oxlint 1.88.0 (package.json says `^1.81.0`) |
  | 3D | three 0.186.1, @react-three/fiber 9.8.1, @react-three/drei 10.7.9 |
  | Other | lucide-react 1.57.0, axios 1.20.0, react-dropzone 20.1.3 |

  Node is v22.17.0.
- `vite.config.ts`:
  - Plugins are `react()` plus `babel({ presets: [reactCompilerPreset()] })`.
  - The dev and preview proxies send `/api` (including the WebSocket) to `IMG3D_BACKEND_URL ?? http://127.0.0.1:8000`.
  - The server listens on `0.0.0.0` with `strictPort`, and the allowed host names include this machine's name.
  - Rolldown `codeSplitting.groups`: react(50), vendor = zustand + use-sync-external-store (40), three (30),
    r3f (20).

  `@rolldown/plugin-babel` excludes `node_modules` by default (`DEFAULT_EXCLUDE`, dist/index.mjs L78). The React
  Compiler therefore only compiles app code, never libraries.
- `tsconfig.app.json`:
  - Target is ES2023 with `moduleResolution: bundler`.
  - Also on: `verbatimModuleSyntax`, `erasableSyntaxOnly` (no TS enums or parameter properties),
    `noUnusedLocals/Parameters`, `noFallthroughCasesInSwitch` and `allowImportingTsExtensions`.

  `strict` is **not** set explicitly. TypeScript 6 makes strict the default; tsc reports 0 errors.
  `tsconfig.node.json` covers only `vite.config.ts`.
- `.oxlintrc.json`:
  - Plugins: react, typescript and oxc.
  - Explicit rules: `react/rules-of-hooks: error` and `react/only-export-components: warn
    (allowConstantExport)`.
  - The effective config (`oxlint --print-config`) also enables the default correctness set. That includes the
    React-Compiler-style rules `react/set-state-in-effect`, `react/set-state-in-render`, `react/refs`,
    `react/purity`, `react/immutability`, `react/static-components`, `react/preserve-manual-memoization`,
    `react/exhaustive-deps`, `react/use-memo` and `react/incompatible-library`, plus
    `typescript/no-floating-promises`. This is why the code has `void promise()` everywhere.
- `frontend/README.md` documents the scripts, the layout, the persisted keys and how to use the mock. It is
  accurate for today's code.

---------------------------------------------------------------------------------------------------

## 2. Component tree and classification

### 2.1 Runtime tree [V]

```
main.tsx ── <StrictMode><App/>  (imports styles/global.css → tokens.css)
App.tsx
├─ useBackendSession()                                L15   STATE/API bootstrap (exactly once)
├─ useBreakpoint()                                    L17   LAYOUT decision (width only)
├─ <a skipLink>
├─ Header(breakpoint)                                 L24   LAYOUT chrome
│   ├─ Logo
│   ├─ EngineStatus(compact)      FEATURE (system store, load engine, popover)
│   ├─ BlenderRuntimeStatus       FEATURE (meshTuner)  Header.tsx:L40
│   ├─ GpuMeter                   FEATURE (desktop only)  L41
│   ├─ ConnectionIndicator        FEATURE
│   └─ History button / Sign-out  LAYOUT actions (uiStore.historyOpen, authStore.logout)
├─ BackendBanner(nextRetryAt)                         L25   FEATURE (system + auth stores)
├─ #main-content
│   ├─ phase 'checking'  → splash (Spinner)           L28
│   ├─ phase 'login'     → LoginScreen                L33   FEATURE (authStore)
│   └─ phase 'ready'     → AppShell(breakpoint)       L35   LAYOUT
│        ├─ desktop: [ReferencePanel] | [ViewerPanel] | [SettingsPanel, ProgressCard, ModelStatsCard, TuningCard] + GenerateCard(shortcut)
│        ├─ tablet:  [ReferencePanel, SettingsPanel] + GenerateCard | [ViewerPanel, ProgressCard, ModelStatsCard, TuningCard]
│        └─ mobile:  tabpanels inputs|settings|viewer|history + GenerateCard(variant=bar) + MobileTabBar
├─ HistoryDrawer  (only when breakpoint ≠ mobile)     L38   LAYOUT chrome around FEATURE HistoryList
├─ BlenderSetupDialog (phase ready)                   L39   FEATURE (meshTuner)
└─ Toaster                                            L40   FEATURE (toastStore)
```

### 2.2 Classification

| Kind | Modules | Notes |
|---|---|---|
| **LAYOUT / shell** (mode-specific, rewrite per renderer) | `App.tsx`, `App.module.css`, `components/layout/AppShell.tsx` (+css), `Header.tsx` (+css), `MobileTabBar.tsx` (+css), `mobileTabs.ts`, `history/HistoryDrawer.tsx` (+css), `hooks/useBreakpoint.ts` | AppShell holds three layouts plus `useRevealProgress` (L20-28) and `useRevealTuning` (L31-44) scroll helpers. The drawer is chrome; `HistoryList` is the feature. |
| **FEATURE panels** (reusable by all renderers) | `references/ReferencePanel` + `ReferenceTile`; `settings/SettingsPanel` (+ `AdvancedSettings`, `SeedControl`, `PolygonTargetControl`, `Field`); `generation/GenerateCard`, `ProgressCard`; `viewer/ViewerPanel` (+ lazy `ModelViewer` → `ModelScene`, `ViewerToolbar`, `ViewerErrorBoundary`), `ModelStatsCard`; `history/HistoryList` + `HistoryItem`; `layout/EngineStatus`, `GpuMeter`, `ConnectionIndicator`, `BackendBanner`, `Logo`; `auth/LoginScreen`; `meshTuner/*` (11 components) | Each subscribes to stores itself, with no prop drilling. Most render a `Panel` card, so the chrome and the content are fused (see §5.2 step 4). |
| **Primitives** | `components/common/*`: Button/IconButton, Dialog (native `<dialog>`), Panel, EmptyState, Menu (portal), SegmentedControl, Switch, NumberField, RangeSlider, ProgressBar, Spinner, StatusBadge, Tag, Toaster | No store access except `Toaster` (toastStore). Missing primitives: Sheet (bottom), Drawer (generic), Tabs, Popover, Tooltip. |
| **STATE** | `store/*.ts`, `components/viewer/viewerRuntime.ts` | See §2.3. |
| **API** | `api/client.ts`, `types.ts`, `ws.ts`, `auth.ts`, `system.ts`, `references.ts`, `jobs.ts`, `blender.ts`, `tunings.ts` | `client.ts` has no store imports; stores register hooks through `configureClientHooks`. |
| **Session/logic hooks** | `useBackendSession`, `useGenerateAvailability`, `useTuneAvailability`, `useViewedTuning`, `useNow`, `useFullscreen` | `useBackendSession` must stay at the root (exactly once). |
| **lib (pure)** | `storage.ts` (STORAGE_KEYS + safe localStorage), `format.ts`, `slots.ts`, `stages.ts` (PIPELINE_STAGES), `imageValidation.ts`, `download.ts`, `meshTuner.ts` | |

Components that call the API directly instead of going through a store or hook **[V]**:
- `ModelStatsCard.tsx` `DownloadButton` → `downloadJobGlb` (L20-50).
- `meshTuner/TunedDownloadButton.tsx` → `downloadTunedGlb`.

Everything else in components imports only URL helpers (`jobModelUrl`, `referenceThumbnailUrl`, `jobPreviewUrl`)
or `API_BASE_URL`. A Node-UI Export node would duplicate the download logic, so it should move into a hook or
store action (§5.2).

### 2.3 Stores (zustand 5, `create<T>()(…)`) [V]

| Store | Persisted (localStorage key) | Owns | Depends on |
|---|---|---|---|
| `authStore` | token only, through `api/client.ts` (`img3d.auth.token`) | `phase` checking/unreachable/login/ready; login/logout/expire | api/auth, client, toast |
| `systemStore` | – | `/system` snapshot, `reachable`, `lastError`, realtime status, `engineLoadPending`, `retrySignal`; `refresh`, `loadEngine` | api/system, toast |
| `referenceStore` | set id via `writeString` (`img3d.referenceSetId`) | current ReferenceSet, upload queue (serialized `uploadChain`), tile errors, busy ids; upload/replace/move/primary/newSet | api/references, systemStore (limits), toast |
| `settingsStore` | `persist` → `img3d.settings` v1 | generation form, `advancedOpen`; `toRequestSettings`, `clampToCapabilities`, `formFromJobSettings` | api/types |
| `jobStore` | viewed id via `writeString` (`img3d.viewedJobId`) | jobs cache, `order`, active/viewed ids, submit/retry/cancel/remove/view, stale-update guards | api/jobs, referenceStore, settingsStore, systemStore, toast |
| `uiStore` | `persist` → `img3d.ui` v1 (only `viewer` is partialized) | `mobileTab`, `historyOpen` (shell state) **mixed with** `viewer` options (shared feature state) | – |
| `meshTunerStore` | `persist` → `img3d.meshTuner` v1 (preset/overrides/advancedOpen); `img3d.blenderSetup.dismissedMessage` | Blender runtime, setup dialog, runs per job, variant original/tuned | api/blender, api/tunings, jobStore, toast |
| `toastStore` | – | toast queue (max 5, dedupe) + imperative `toast.*` | – |
| `viewerRuntime` (in `components/viewer/`) | – | download progress and status of the **single** mounted viewer (primary plus secondary) | – |

The cross-store graph is acyclic. `jobStore → reference/settings/system`; `meshTunerStore → jobStore`.

**`viewerRuntime` is a global singleton** (`viewerRuntime.ts:L32`). At most one `ViewerPanel`/`ModelViewer` may be
mounted at a time in the whole app. The Node UI must not render a second viewer, for example a node preview plus a
docked viewer **[V/A]**.

### 2.4 Session wiring (`hooks/useBackendSession.ts`) [V]

1. Registers client hooks: reachability → `systemStore.setReachable`, 401 → `authStore.expire`.
2. Calls `authStore.check()`. While the phase is `unreachable` it retries with backoff
   `[1.5, 2.5, 4, 6, 8] s` and exposes `nextRetryAt`.
3. When the phase is `ready`:
   - Runs `syncSessionData()`, which loads system, references, history and `meshTuner.refreshRuntime` (L64),
     then the runs of the viewed job (L68).
   - Creates a `RealtimeClient`. Its events `hello`, `system.updated`, `job.updated`, `blender.updated` (L129)
     and `tuning.updated` (L130) update the stores.
   - REST polling acts as fallback and as a safety net (`pollBackend` → `pollMeshTuner`, L36/L53).
   - Subscribes to `viewedJobId` changes to load tuning runs (L146).
   - Resyncs the tuner on each re-`hello` (L155).
4. After an outage, re-checks auth and resyncs.
5. Keeps the settings form aligned with `/system.defaults` and capabilities (L184).

This is the shared realtime and API layer the user asked for. It must stay in the root, above every renderer.

---------------------------------------------------------------------------------------------------

## 3. Responsiveness today

### 3.1 Mechanism [V]

- **JavaScript breakpoint.** `hooks/useBreakpoint.ts` uses `useSyncExternalStore` on two `matchMedia` queries:
  `(min-width: 768px)` (L6) and `(min-width: 1280px)` (L7). The server snapshot is `'desktop'`. There is no
  orientation, height, `pointer: coarse` or `hover` input.
- **Consumers.** `App.tsx` (L17, L24, L35, L38), `Header.tsx` (compact pills, GpuMeter on desktop only, History
  button hidden on mobile) and `ViewerPanel.tsx:L136/L148` (on mobile it does not preload the tuned GLB).
- **Layouts** (`AppShell.module.css`):
  - Desktop `.desktop` (L33): a grid of `var(--column-left) minmax(0,1fr) var(--column-right)` =
    `minmax(300px,360px) | 1fr | minmax(320px,388px)`, `100dvh`. The body never scrolls; columns scroll
    internally.
  - Tablet `.tablet` (L51): `minmax(300px, 380px) minmax(0, 1fr)` (L53). The left column holds references,
    settings and the Generate card. The right column holds the viewer at a fixed `height: min(62vh, 640px);
    min-height: 340px` (L61-65) plus progress, stats and tuning.
  - Mobile `.mobile` (L68): one `role=tabpanel` at a time (hidden attribute). Viewer `height: min(58vh, 520px);
    min-height: 300px` (L85-89). The Generate bar (`variant="bar"`) shows only on the inputs and settings tabs.
    The bottom `MobileTabBar` is not floating: it is in normal flow, `grid repeat(4, 1fr)`,
    `height: var(--tabbar-height)=60px`, with `safe-area-inset-bottom` padding. It is a WAI-ARIA tablist with
    roving focus, arrow, Home and End keys.
- **CSS media queries.** Eleven files use `@media (max-width: 767px)`: Header, BackendBanner, EngineStatus
  popover (fixed full-width), Dialog (full-screen sheet), Toaster (offset by `--tabbar-height`), and the meshTuner
  modules BlenderDownloadPanel, BlenderInstallTable, BlenderRuntimeStatus, BlenderSetupDialog and
  SelectedRuntimeCard. These follow the viewport width, **not** a UI mode.
- **Container queries.**
  - ReferencePanel grid (`.slots`): 3 columns, 2 at ≤ 260 px, 6 at ≥ 520 px. The detail grid is 4 columns, 8 at
    ≥ 520 px.
  - ViewerPanel hides the caption at ≤ 640 px.

  These are the only truly mode-agnostic responsive parts.
- **Hover media.** `@media (hover: none)` hides the keyboard hint on the Generate button and shows the tile
  star and menu permanently.
- **Viewport.** `index.html` sets `viewport-fit=cover`, `color-scheme dark` and `theme-color #0b0d12`. `#root` and
  `.app` use `100dvh`. `safe-area-inset-*` is used only at the bottom (tab bar, Dialog, Toaster). There is no
  top, left or right inset handling, so a notch in landscape is not handled.

### 3.2 Fixed sizes and layouts that break or degrade at small sizes [V file:line]

| # | Where | What | Effect |
|---|---|---|---|
| 1 | `viewer/ViewerToolbar.module.css:L19` `.modes { flex: 0 1 236px }` + `SegmentedControl` sm segments | View-mode control squeezed beside three 34 px buttons | **Verified at 320 px:** the "Normals" segment overflows, `scrollWidth` 58 > `clientWidth` 49, and is clipped. |
| 2 | `layout/AppShell.module.css:L53` tablet grid `minmax(300px,380px) 1fr` | Portrait tablets | **Verified at 768×1024:** the columns are 380 px and 352 px. The viewer is 340×635: narrow and tall, and narrower than the form column. |
| 3 | `AppShell.module.css:L61-65` `.tabletViewer height:min(62vh,640px); min-height:340px` + width-only breakpoint | Phone landscape ≥ 768 px wide (e.g. 812×375) gets the **tablet** layout | **Verified:** main is 319 px tall and each column 295 px. The viewer (340 px) overflows its column, so its bottom toolbar needs scrolling. The left column's scroll area is about 100 px because the Generate card (L84) takes the rest. |
| 4 | Mobile chrome: header `--header-height:56px` + GenerateCard bar (≈ 86 px) + tab bar 61 px | Short viewports | **Verified at 740×360:** content area is 157 px (44 % of the height). The `.mobileViewer min-height:300px` (L88) then scrolls inside a 157 px panel. |
| 5 | `viewer/ViewerPanel.module.css:L148` `.variantControl { width:168px }`; `L137` `top: calc(var(--space-3) + 40px)` | Original/Tuned bar is hard-coupled to the toolbar height | Overlaps if the toolbar wraps or changes height in other modes. |
| 6 | `viewer/ViewerPanel.module.css:L7` `.viewer { min-height:320px }` | Viewer in tight containers (Node inspector, sheets) | Forces overflow in any panel under 320 px tall. |
| 7 | `settings/PolygonTargetControl.module.css:L3,L9` fixed 104 px number column | At 320 px the slider shrinks to 154 px (verified) | Still usable but cramped; the slider thumb is 16 px. |
| 8 | `common/Dialog.module.css:L24/L28` `.md 560px`, `.lg 820px` (`max-width: calc(100vw - 32px)`) | 768–860 px | `.lg` collapses to the viewport width minus 32 px. Acceptable. Below 768 it is a full-screen sheet (L105). |
| 9 | `history/HistoryDrawer.module.css:L24` `width:min(420px,92vw)` | – | OK. Note that `App.tsx:L38` does not mount the drawer on mobile at all; history is a tab there. |
| 10 | `layout/GpuMeter.module.css:L6` `max-width:280px`, `L52` track `min-width:120px` | Desktop header only | Not a break, but with five pills the header can crowd at 1280 px. |
| 11 | `layout/EngineStatus.module.css:L86` popover `min(360px, 100vw-24px)`; at ≤ 767 px `position:fixed` | – | OK. |
| 12 | `common/Toaster.module.css:L14-20` mobile offset = `--tabbar-height` only | Mobile | Toasts can overlap the Generate bar on the inputs and settings tabs (it sits above the tab bar). **[A, from code]** |
| 13 | Touch targets: Button sm 28 / md 34 / lg 44 px (`Button.module.css:L40,46,52`), IconButton 28/34 px (L133-139), SegmentedControl 30/26 px, Switch track 38×22 / 32×18, RangeSlider thumb 16 px, NumberField 32 px, header pills 30 px, Menu items `min-height:32px` | Mobile and tablet touch | **Verified:** 21 of the visible controls on the 320 px viewer tab and 43 of 62 at tablet sizes are under 44 px. There is no `(pointer: coarse)` sizing anywhere. |
| 14 | `ReferenceTile.module.css` star and menu buttons 26 px, `top/left 6px` | Phones | Small; they overlap the thumbnail. Revealed by `hover:none`. |
| 15 | `layout/Header.tsx:L31` tagline hidden on mobile; pills compact | 320 px | **Verified:** no horizontal overflow at 320 (`docScrollWidth` = 320). Logo, two pills and a dot fit. |

No page-level horizontal scroll was found at 320, 740, 768, 812, 1024 or 1280 px (`scrollWidth == innerWidth`) **[V]**.

### 3.3 Measurement method [V]

I ran the mock (`--engine-ready --seed-jobs 2`) and the scratchpad build in the desktop app's browser pane with
emulated viewports 1280×800, 320×640, 768×1024, 1024×768, 812×375 and 740×360. A DOM probe checked the
bounding rects of all elements, `scrollWidth > clientWidth` and the sizes of interactive controls. With a model
opened from history, I measured the viewer and inputs tabs and the settings tab. The tablet layout at 1024×768 is
fine: 380 | 608 columns, viewer 596×476.

---------------------------------------------------------------------------------------------------

## 4. Design tokens, theming, motion, states [V]

- **Tokens** (`styles/tokens.css`, imported by `global.css`). Dark-only by design ("never hard-code colors in
  modules", L1-4).

  | Group | Tokens |
  |---|---|
  | Surfaces | `--color-bg`, `--color-surface-1…4`, `--color-overlay`, `--color-viewer-bg` |
  | Borders | `--color-border`, `-strong`, `-focus` |
  | Text | `--color-text`, `-muted`, `-subtle`, `-inverse` |
  | Brand | accent `#8b6cff`, `-hover`, `-active`, `-soft`, `-border`; `--color-accent-2 #22d3ee`; `--gradient-brand` |
  | Status | success, warning, danger, info, each with `-soft`; `--color-primary-star` |
  | Fonts | system stacks only, so the app works offline |
  | Type scale | `--text-2xs` 11px … `--text-2xl` 22px |
  | Weights and leading | weights 400–700, two leadings |
  | Spacing | 4 px grid `--space-1…10` (4–40 px) |
  | Radii | `--radius-xs 4 … lg 14`, pill |
  | Shadows | `--shadow-sm/md/lg`, `--focus-ring` |
  | Motion | `--ease-out cubic-bezier(.22,1,.36,1)`, `--duration-fast 120ms`, `normal 200ms`, `slow 320ms` |
  | Layout | `--header-height 56px`, `--tabbar-height 60px`, `--layout-gap 12px`, `--column-left/right` |
  | Z-index | sticky 10, header 20, dropdown 40, drawer 60, toast 80, modal 90 |

  `prefers-reduced-motion` sets all durations to 0 (L113-119).
- **Theming gaps.**
  - There are **141 hard-coded color literals in 36 CSS modules** (hex or `rgb(…)`), for example
    `rgb(11 13 18 / 0.94)` for the translucent bars (`MobileTabBar.module.css`, `GenerateCard .bar`, Header 0.86)
    and `#ffc2cd` / `#fda4b4` for error text.
  - There is no `[data-theme]` or mode scoping and no token for touch-target size, safe-area insets, nav height
    per mode or elevation of floating chrome.
  - A theme or per-mode density layer needs new semantic tokens: `--surface-glass`, `--text-on-danger`,
    `--control-h-sm/md/lg`, `--touch-target`, `--shell-inset-bottom/left`.
- **Motion.**
  - Global keyframes: `img3d-spin`, `img3d-fade-in`, `img3d-pulse` (global.css). Local: ProgressBar `slide`,
    Toaster `enter`.
  - There are 41 `transition`/`animation` declarations. Real transitions exist for the HistoryDrawer slide
    (transform + visibility), the Dialog fade-in, the Menu and popover fade-ins, button and tile hover, the
    progress fill width and the Switch thumb.
  - **Missing:** mobile tab panels switch instantly (the `hidden` attribute), there are no panel enter/leave or
    layout transitions, no skeletons and no View Transitions.
  - Spin and pulse keyframes with fixed durations ignore the reduced-motion tokens; only `Spinner` slows down
    (`Spinner.module.css:L10`).
- **Loading, empty and error states (good coverage, reuse them):**
  - The splash in `App.tsx:L28` and the login screen.
  - `BackendBanner`: unreachable or backend error with a retry countdown (the countdown is not a live region).
  - `ConnectionIndicator` (live, polling or offline) and the `EngineStatus` pill and popover (load progress,
    "Retry loading model").
  - `GenerateCard`: disabled reason, info note, `engineProblem` with collapsible details and retry.
  - `ProgressCard`: stage list, ETA and an error box.
  - Viewer: empty state with steps and live progress (`ViewerEmpty`), chunk-loading fallback, byte-level
    download overlay, error card with retry, `ViewerErrorBoundary` (chunk or WebGL failure).
  - `ReferencePanel`: loading, offline, error and retry; per-tile upload progress and errors.
  - `HistoryList`: loading, error and empty.
  - Toasts (deduped, max 5, errors 8 s).
  - Accessibility: live regions announce only status changes, never per-second timers.

---------------------------------------------------------------------------------------------------

## 5. Four renderers over one state/API layer — what must change

### 5.1 Coupling points that block multiple renderers [V file:line]

1. **`App.tsx` mixes three jobs:** root concerns (`useBackendSession` L15, auth gate L28-35, global overlays
   L39-40), the layout decision (`useBreakpoint` L17) and Default-mode chrome (Header L24, HistoryDrawer L38).
2. **`AppShell.tsx` holds three layouts plus reveal hooks.** Desktop L46-72, Tablet L74-100, Mobile L116-149,
   switch L152-156. These are effectively the Default mode's responsive behaviour.
3. **`uiStore` mixes shell state with feature state.** Shell state: `mobileTab`, `historyOpen` (L29-30). Feature
   state: `viewer` options. Only `viewer` is persisted (L51).
4. **Feature components drive Default-mobile navigation directly:**
   - `GenerateCard.tsx:L40` `if (ok && focusViewerOnSubmit) setMobileTab('viewer')`.
   - `BlenderSetupDialog.tsx:L89-97` `setMobileTab('viewer')`.
   - `AppShell.tsx` passes `HistoryList onOpened={() => setMobileTab('viewer')}`.
5. **The generate action lives inside a component.** `GenerateCard.generate()` (L34-41) starts the engine load if
   it is `not_loaded`, then calls `submit`, then navigates. The global Ctrl/Cmd+Enter shortcut is registered by
   whichever `GenerateCard` gets `shortcut` (L43-60). A Node-UI "Run" button or a mobile floating action button
   would duplicate it.
6. **`Header` takes `breakpoint`** and decides which feature pills appear (L39-42, L48, L60).
7. **`ViewerPanel` uses `useBreakpoint()`** (L136) for a memory decision (L148) instead of a device-capability
   signal.
8. **The CSS media queries in the 11 modules** follow the viewport width, not the selected mode. If a user picks
   Mobile UI on a 1280 px browser, the Dialog would still be centered, the toast offset would be wrong, and so on.
9. **Feature panels hard-wire `Panel` card chrome.** ReferencePanel, SettingsPanel, ProgressCard, ModelStatsCard
   and TuningCard all wrap `Panel`. Nodes and sheets need the body without the card header.
10. **Singletons that must not be duplicated across shells:**
    - `useBackendSession`.
    - The paste listener in `ReferencePanel` (L93; gated by `active`).
    - The Ctrl+Enter shortcut.
    - `viewerRuntime` (one viewer).
    - `HistoryDrawer` focus management.
    - `Dialog` `showModal`.
11. **The download logic is in components** (§2.2).
12. **`lib/stages.ts:L10` `PIPELINE_STAGES` is a hard-coded list of 8 stages.** `api/types.ts:L34` `JOB_STAGES`
    is a closed union. Part-aware stages (segmentation, part extraction, text-to-image) will shift
    `stage_index`, and `stageState()` (L24) compares indices.

### 5.2 Target architecture and folder structure [A]

```
Backend API (/api/v1 REST + WS)                          ← contract: api-contract.md
  → src/api/            typed client, types, RealtimeClient            (unchanged; extended for parts/prompt)
  → src/store/          zustand domain stores (single source of truth) (+ inputStore, partStore, preferencesStore,
                                                                         navigationStore, nodeGraphStore)
  → src/hooks/          session + derived logic (useBackendSession, useGenerateAvailability, useGenerateAction,
                        useGlbDownload, useViewport, useResolvedUiMode, useReveal)
  → src/nodeGraph/      PURE TS graph model: node/port types, registry, default template, validation,
                        compile(graph, stores) → CreateJobRequest, stage→node status mapping (no React)
  → src/components/     FEATURE content + primitives (renderer-agnostic, no layout decisions)
  → src/shells/         RENDERERS: default/ node/ mobile/ tablet/ (+ shared/ chrome pieces)
  → src/app/            AppRoot (session, auth gate, overlays) + ModeSwitch (lazy shells)
```

Proposed tree, incremental and keeping existing paths where possible:

```
src/
  main.tsx                         → renders <AppRoot/> (or keep App.tsx as the root to avoid churn)
  app/
    AppRoot.tsx                    useBackendSession(); phase gate (splash/login); <ModeSwitch/>; <GlobalOverlays/>
    ModeSwitch.tsx                 useResolvedUiMode() → React.lazy(shells/<mode>/…Shell); sets <html data-ui-mode …>
    GlobalOverlays.tsx             <Toaster/>, <AppSettingsDialog/>, FEATURES.meshTuner && <BlenderSetupDialog/>
    useGlobalShortcuts.ts          Ctrl/Cmd+Enter → useGenerateAction().run (registered once, here)
  shells/
    shared/                        StatusCluster (EngineStatus+GpuMeter+ConnectionIndicator[+Blender pill]),
                                   AccountButton, HistoryButton, SettingsButton, Sheet, SideDrawer, FloatingNav
    default/                       DefaultShell.tsx (= today's Header + AppShell desktop/tablet/mobile + HistoryDrawer,
                                   moved verbatim then polished), DefaultHeader.tsx, MobileTabBar.tsx, mobileTabs.ts
    mobile/                        MobileShell.tsx, MobileTopBar.tsx, FloatingBottomNav.tsx, sheets/{Inputs,Settings,
                                   History,Parts,Export}Sheet.tsx, GenerateFab.tsx
    tablet/                        TabletShell.tsx (landscape: FloatingRail left; portrait: FloatingBottomNav),
                                   TabletWorkspace.tsx
    node/                          NodeShell.tsx (lazy chunk), NodeCanvas.tsx (library adapter), NodeInspector.tsx,
                                   DockedViewer.tsx, nodes/{InputImage,Prompt,Segmentation,ImageTo3D,
                                   PartExtraction,Export}Node.tsx (thin bindings to feature Content components)
  components/                      (existing folders stay)
    references/ReferenceContent.tsx + ReferencePanel.tsx (Panel wrapper)
    settings/SettingsContent.tsx    + SettingsPanel.tsx
    generation/ProgressContent.tsx  + ProgressCard.tsx; GenerateButton.tsx (uses useGenerateAction)
    viewer/ModelStatsContent.tsx    + ModelStatsCard.tsx; viewer API gains part visibility/highlight
    prompt/PromptContent.tsx        (new)        parts/PartsContent.tsx (new: outliner, physical vs region)
    app-settings/AppSettingsDialog.tsx (new: "Interface" section = UI-mode picker)
    common/Sheet.tsx, Drawer.tsx    (new primitives; generalize HistoryDrawer/Dialog patterns)
  nodeGraph/  types.ts registry.ts templates.ts validate.ts compile.ts status.ts
  store/      preferencesStore.ts navigationStore.ts inputStore.ts partStore.ts nodeGraphStore.ts (+ existing)
  hooks/      useViewport.ts useResolvedUiMode.ts useGenerateAction.ts useGlbDownload.ts useReveal.ts (+ existing)
  lib/        features.ts uiModes.ts (+ existing)
  styles/     tokens.css (+ semantic tokens) global.css modes.css (per-[data-ui-mode] token overrides)
```

Rules that prevent duplicated logic **[A]**:
- Shells contain only layout, navigation and composition. They never import `api/*` and never call store
  *actions*, except navigation, preference and graph-layout stores.
- Feature `XxxContent` components own their store subscriptions (as today). Shells pass only presentation props
  such as `density`, `compact` and `active`.
- Every cross-feature action goes through a hook or store action: `useGenerateAction`, `useGlbDownload`,
  `navigationStore.reveal(target)`.
- Shell-specific navigation state lives in `navigationStore`, namespaced by shell, or in shell-local `useState`.
  It never lives in feature stores.

### 5.3 Navigation abstraction (replaces `setMobileTab` calls) [A]

```ts
// store/navigationStore.ts
export type RevealTarget = 'inputs' | 'prompt' | 'settings' | 'viewer' | 'progress' | 'parts' | 'export' | 'history'
interface NavigationState {
  /** Monotonic request so the same target can be re-requested. Shells subscribe and map it to their own nav. */
  reveal: { target: RevealTarget; seq: number } | null
  requestReveal: (target: RevealTarget) => void
  // per-shell transient state (not persisted except where noted)
  defaultMobileTab: 'inputs' | 'settings' | 'viewer' | 'history'   // moved from uiStore.mobileTab
  historyOpen: boolean                                               // moved from uiStore.historyOpen
  mobileSheet: RevealTarget | null
  tabletSection: RevealTarget
  nodeSelection: string | null
}
```

How each shell maps a reveal:
- `GenerateCard` / `useGenerateAction` on success → `requestReveal('viewer')`.
- `BlenderSetupDialog` → `requestReveal('viewer')`.
- `HistoryList.onOpened` → `requestReveal('viewer')`.
- Default mobile switches the tab. Mobile closes the sheet and shows the viewer. Tablet focuses the viewer.
  Node selects the Export or viewer dock.
- `useRevealProgress` / `useRevealTuning` (AppShell L20-44) become `useReveal(target)` helpers in
  `shells/shared`.

Keep `uiStore` for `viewer` options only, with persist `version: 2`. The `merge` already ignores unknown or missing
keys, so dropping `mobileTab`/`historyOpen` needs no migration (they were never persisted).

### 5.4 UI-mode preference: storage, resolution, switching [A]

```ts
// lib/storage.ts — add
  /** Interface preferences (UI mode). Persisted by store/preferencesStore.ts. */
  preferences: 'img3d.preferences',
  /** Node UI graph layout (node positions, edges, viewport) — layout only, never parameter values. */
  nodeGraph: 'img3d.nodeGraph',
  /** Prompt-to-3D form (prompt, negative prompt, input source). */
  input: 'img3d.input',

// lib/uiModes.ts
export const UI_MODES = ['default', 'node', 'mobile', 'tablet'] as const
export type UiMode = (typeof UI_MODES)[number]
export type UiModePreference = UiMode | 'auto'   // 'auto' optional extra; product default stays 'default'

// store/preferencesStore.ts — zustand persist, name STORAGE_KEYS.preferences, version 1
{ uiMode: UiModePreference /* default 'default' */, setUiMode(mode) }
```

- **Default is `'default'`,** as the user requires. The Default mode keeps today's desktop, tablet and mobile
  responsive behaviour, so nothing changes for existing users.
- **Persistence.** Zustand `persist` + `createJSONStorage(() => localStorage)` hydrates synchronously when the store
  is created, so the first render already uses the saved mode and there is no flash. In zustand 5,
  `createJSONStorage` tolerates a throwing `getStorage`, so a blocked localStorage falls back to the default.
  Validate the saved value with `UI_MODES.includes` inside `merge`, as `meshTunerStore` does at L501-509.
  The preference is per browser, which is effectively per device. That is right: phone = Mobile,
  workstation = Node.
- **Optional dev override [A]:** a `?ui=node|mobile|tablet|default` query parameter read once at startup and
  **not** persisted. It is useful for screenshots and smoke tests.
- **Resolution** (`hooks/useResolvedUiMode.ts`): `preference === 'auto'` → phone width (< 600 px or a short
  coarse-pointer viewport) → `mobile`; coarse pointer and < 1280 px → `tablet`; otherwise → `default`. An explicit
  mode always wins.
- **Viewport signal** (`hooks/useViewport.ts`, replacing and generalizing `useBreakpoint`):
  `useSyncExternalStore` over `matchMedia` for width classes, `(orientation: portrait)`, `(pointer: coarse)`,
  `(hover: none)` and `(max-height: 500px)`. Return a cached frozen object per key combination; never allocate in
  `getSnapshot`. Keep `useBreakpoint` as a thin wrapper for the Default shell to preserve behaviour.
- **DOM flags.** `ModeSwitch` writes `document.documentElement.dataset.uiMode`, `.orientation` and `.pointer` in
  an effect. CSS then scopes overrides: `:root[data-ui-mode='mobile'] { --control-h-md: 44px; --touch-target:
  44px }`. Convert the 11 `@media (max-width: 767px)` blocks to
  `:root[data-ui-mode='mobile'] .x, :root[data-ui-mode='default'][data-width='narrow'] .x`. An alternative for
  component-internal decisions is container queries.
- **Switching UI.** A new **App settings** dialog (`components/app-settings/AppSettingsDialog.tsx`, built on
  `common/Dialog`) with an "Interface" section: four radio cards (Default, Node, Mobile, Tablet) with
  descriptions, and optionally "Auto". Store `appSettingsOpen` in `navigationStore`. Open it from a gear
  `IconButton` in every shell:
  - Default: the header `actions` (`Header.tsx:L47-68`).
  - Mobile: a "More" tab or the top-bar overflow.
  - Tablet: the bottom of the rail.
  - Node: the toolbar.

  Naming: today "Settings" means *generation* settings (SettingsPanel title and the mobile tab "Settings",
  `MobileTabBar.tsx:L11-16`). Rename the app-level one to "App settings" or "Preferences", or relabel the
  generation panel "Generation", to avoid confusion.
- **Lazy shells.** `ModeSwitch` uses `React.lazy` per shell so the Node UI library (§5.6) loads only in Node mode.
  Add a Rolldown group such as `{ name: 'nodeui', test: /node_modules[\\/](@xyflow|d3-(drag|zoom|selection|
  interpolate|dispatch|color|ease|timer|transition)|classcat)[\\/]/, priority: 25 }`. Make sure the `vendor`
  group does not pull a nested zustand@4 that @xyflow/react depends on into the initial chunk; check with
  `npm ls zustand` after install **[A]**.
- **Transition between modes.** Unmount the old shell and mount the new one. All domain state survives because it
  lives in the stores. The R3F canvas is remounted (GLB cached by `useLoader`), and the viewer singleton allows
  this because only one shell is mounted.

### 5.5 Per-mode notes [A]

- **Default (today's UI, polished, not redesigned).**
  - Move `AppShell` + `Header` + `MobileTabBar` + `HistoryDrawer` into `shells/default` unchanged first, then
    polish.
  - Tablet portrait: viewer first, then the form, or make the left column `minmax(280px, 40%)`, to fix row 2 of
    §3.2.
  - Use height-aware viewers: `clamp()` with `dvh` and no fixed `min-height` above the available height (§3.2
    rows 3, 4, 6). Wrap or abbreviate the toolbar segments (row 1).
  - Add tab-panel cross-fades and panel enter animations behind `--duration-*`. Replace raw colors with semantic
    tokens. Add a `pointer: coarse` sizing layer. Offset toasts by `--shell-inset-bottom`. Add skeletons for
    history and references.
- **Mobile (dedicated app-style shell).**
  - Compact top bar: logo, one status dot that opens a sheet with the status cluster, and a gear.
  - A full-bleed viewer is the home screen. A **floating** bottom navigation pill (Inputs/Prompt, Settings,
    Parts, History, More) sits at `bottom: calc(12px + env(safe-area-inset-bottom))`.
  - A large primary **Generate** floating action button or bar is always reachable. It uses `useGenerateAction`
    and turns into Cancel while busy.
  - Panels open as bottom sheets: a new `common/Sheet` on native `<dialog>` with a grab handle, snap heights of
    50 % and 90 %, `overscroll-behavior: contain` and drag-to-dismiss via pointer events. This follows the same
    focus-trap approach as `Dialog`, so no new dependency is needed.
  - Controls are at least 44×44 px. Keep the WAI-ARIA tablist semantics from `MobileTabBar`.
- **Tablet.**
  - `useViewport().orientation`: landscape uses a **floating left rail** (icons plus labels, 72–88 px) with the
    workspace as viewer plus a docked side panel. Portrait uses the mobile **floating bottom nav** with the
    viewer on top and the panel as a sheet below.
  - Switches automatically on `(orientation: portrait)` and on available width. Use a width threshold too, so
    split-screen windows behave: portrait-like if width < 900 px **[A]**.
- **Node (ComfyUI-style).**
  - A canvas with nodes Input Image, Prompt, Segmentation, Image-to-3D, Part Extraction and Export, plus typed
    edges.
  - Node bodies render compact `XxxContent` controls. A right **Inspector** shows the full content of the
    selected node: the full ReferenceContent, SettingsContent, PartsContent, etc.
  - A **docked viewer** shows the result (not inside a node, because of the viewer singleton). A toolbar holds
    Run/Cancel (`useGenerateAction`), fit view, auto-layout, reset template and the gear.
  - Node status comes from the active job (`progress.stage` → node via `nodeGraph/status.ts`).

### 5.6 Node-graph engine: keep it pure, choose the renderer library carefully [A + V metadata]

**`src/nodeGraph/` (pure TypeScript, no React, unit-testable):**
- `types.ts`: `PortType = 'image' | 'prompt' | 'mask' | 'mesh' | 'parts' | 'file'` (closed union with `as const`;
  TS enums are not allowed by `erasableSyntaxOnly`). Also `NodeKind`, `GraphNode {id, kind, position}` and
  `GraphEdge {from:{node,port}, to:{node,port}}`.
- `registry.ts`: a `NodeDefinition` per kind with input and output ports, a label, an icon key, a
  `bindsTo: 'references' | 'prompt' | 'settings' | 'segmentation' | 'parts' | 'export'` and an optional
  `featureFlag`. Future tuning nodes register with `featureFlag: 'meshTuner'` and stay hidden while it is off.
- `templates.ts`: the default image→3D and prompt→3D graphs.
- `validate.ts`: type-compatible connections, no cycles, exactly one source (image or prompt), and Image-to-3D
  required.
- `compile.ts`: graph + store snapshots → the API request. This is the **only** place that knows how a graph maps
  to `CreateJobRequest`. It reuses `toRequestSettings` (`settingsStore.ts`). There is no client-side execution;
  the backend runs the pipeline.
- `status.ts`: `job.progress.stage` → per-node state (pending/active/done/failed), reusing `stageState`.
- **Node parameter values are not stored in the graph.** Nodes bind to the existing stores, so the Node UI and
  the Default UI edit the same form. `nodeGraphStore` (persisted `img3d.nodeGraph`) holds only layout,
  topology, selection and viewport.

**Renderer library options** (npm registry metadata fetched 2026-10-16 **[V]**):

| Option | Version / license | Notes |
|---|---|---|
| **@xyflow/react** (React Flow) | 12.12.0 (published 2026-09-24), **MIT** — https://github.com/xyflow/xyflow/blob/main/LICENSE; peer `react >=17`; deps `zustand ^4.4.0`, `classcat`, `@xyflow/system 0.0.83` (d3-drag/zoom/selection/interpolate) | Bundlephobia: **187.9 kB min / 60.0 kB gzip**. Nodes are React components, so feature `Content` components can be reused inside nodes. Touch pan and pinch, minimap, controls. Brings a second zustand (v4); lazy-chunk it. **Recommended**, pinned exactly, Node mode only. |
| rete 2.0.6 | MIT, 226 kB unpacked core (+ separate react/area plugins) | More plumbing for typed sockets and rendering; smaller ecosystem. |
| litegraph.js 0.7.18 / @comfyorg/litegraph 0.17.2 | MIT, 3.2–3.8 MB unpacked | Canvas-rendered, the real ComfyUI look, but React feature components **cannot** live inside nodes. That would duplicate controls, against the "no duplicated logic" requirement. |
| In-house SVG/HTML canvas | – | Feasible for a fixed six-node DAG, but pan, zoom, connection dragging, touch and accessibility add up to weeks of work. |

The React Compiler does not compile `node_modules`, so the library is unaffected **[V]**. Custom node components
written in the app are compiled. Do not add `memo` or `useCallback` by hand, because
`react/preserve-manual-memoization` is enabled. Keep the library behind `shells/node/NodeCanvas.tsx` so it can be
swapped. Add it to `THIRD_PARTY_NOTICES.md` (repo is GPL-3.0, MIT-compatible).

### 5.7 Incremental implementation order (each step leaves the app working; run the gates in §8 after each) [A]

1. **No-visual-change extraction.**
   - Add `lib/uiModes.ts`, `lib/features.ts`, `store/preferencesStore.ts`, `store/navigationStore.ts`,
     `hooks/useViewport.ts` and `hooks/useGenerateAction.ts`.
   - Move the logic of `GenerateCard.generate` and the Ctrl+Enter shortcut to `app/useGlobalShortcuts.ts` and the
     hook.
   - Replace the three `setMobileTab('viewer')` calls with `requestReveal('viewer')`. The Default shell maps it to
     the tab.
   - Move `DownloadButton` logic to `useGlbDownload`.
2. **Split `App.tsx` into AppRoot + ModeSwitch + DefaultShell.** Move Header, AppShell, MobileTabBar and
   HistoryDrawer into `shells/default` verbatim. Keep `components/layout/*` re-exports temporarily if needed.
3. **Add the App settings dialog** with the mode picker. Only Default exists; the other modes are disabled.
4. **Split `XxxContent` / `XxxPanel`** for references, settings, progress, stats, history and later parts.
5. **Mobile shell + `common/Sheet`.** Then the Tablet shell (rail + bottom nav). Then the Node shell (lazy,
   library added).
6. **Polish the Default mode** (§5.5) behind no flag.
7. **Prompt input and the parts panel** wired to the backend contract once it exists. Mock first in
   `test/frontend/mock_backend.mjs`.

---------------------------------------------------------------------------------------------------

## 6. Where prompt-to-3D input goes [V current state, A proposal]

**Current state.**
- Generation is image-only end to end:
  - `jobStore.submit` (L197) refuses without reference images (L198-201) and sends
    `reference_set_id/primary_image_id` (L209).
  - `useGenerateAvailability` blocks with "Add at least one reference image." (L58).
  - `CreateJobRequest` (`api/types.ts:L300`) has no prompt field.
  - `ViewerEmpty` instructs the user to add an image (`ViewerPanel.tsx:L63`).
  - The contract §7.4 has no prompt endpoint, and TRELLIS.2-4B is image-conditioned. Prompt-to-3D therefore needs
    a backend text→image (or text→3D) stage. That is the backend team's decision.

**Placement.**
- Default desktop: the top of the left column, which today holds only `<ReferencePanel/>`
  (`AppShell.tsx:L51-53`).
- Default tablet: the top of the left `scrollArea` (L80-83).
- Default mobile: the "Inputs" tab (L122-124).

In all three, add an **"Image | Prompt" SegmentedControl** (`InputSourceSwitch`) above either
`ReferenceContent` or a new `PromptContent`. `PromptContent` holds a prompt textarea, an optional negative prompt,
optional style or subject presets (character vs object, which drives part decomposition hints) and an optional
"show generated reference image" preview with "use as reference". In the Mobile shell the switch lives in the
Inputs sheet. In the Tablet shell it lives in the Inputs section. In the Node shell the **Prompt** node is a source
node alternative to **Input Image**; `validate.ts` enforces exactly one source.

**State.** A new `store/inputStore.ts` persisted at `img3d.input`: `{ source: 'image' | 'prompt', prompt,
negativePrompt, … }`. It must not go in `settingsStore`, so the settings form stays engine-only.
`useGenerateAvailability` branches on `source`. `jobStore.submit` (or `nodeGraph/compile.ts`) builds the request.

**Proposed contract shape [A], to be decided in `api-contract.md` first.** Keep `CreateJobRequest` backward
compatible, with `reference_set_id` still accepted and a new optional
`input: {kind:'image', reference_set_id, primary_image_id} | {kind:'prompt', prompt, negative_prompt}`, plus
`settings.parts: {mode: 'off'|'semantic'|'physical', categories?: [...]}`. `Job.progress` should carry the stage
list (`stages: [{id,label,index}]`) so `lib/stages.ts` becomes data-driven, and `JobResult.parts` should carry a
part manifest: id, name, category (eyes/eyebrows/eyelashes/hair/clothes/accessory/detachable/body-region),
`physical: boolean`, parent/attachment, `node_name` in the GLB, bbox, faces, material, optional per-part
`download_url`. The viewer then needs `ViewerApi.setPartVisibility(ids)` / `highlightPart(id)` traversing named
GLB nodes in `ModelScene`. Today `computeLayout` and `applyViewMode` operate on the whole scene.

---------------------------------------------------------------------------------------------------

## 7. Auto Mesh Tuner UI: every mount point and how to keep it hidden [V]

| # | Mount or coupling | Location |
|---|---|---|
| 1 | `<BlenderSetupDialog />` mounted when phase is ready. It **auto-opens once per session** when runtime is `needs_download` (`meshTunerStore.applyRuntime`, L264-272) unless the message was dismissed. | `App.tsx:L8, L39` |
| 2 | `<BlenderRuntimeStatus compact />` header pill (popover, detect, setup) | `components/layout/Header.tsx:L8, L40` |
| 3 | `<TuningCard />` in all three layouts, plus `useRevealTuning` scroll | `components/layout/AppShell.tsx:L5, L10, L31-44, L64-66, L94-96, L134` |
| 4 | Viewer Original/Tuned `VariantBar` and tuned GLB preload as the secondary model | `components/viewer/ViewerPanel.tsx:L5, L8, L12, L84-129, L135-149, L199` |
| 5 | `TunedStats` + `TunedDownloadButton` when variant is tuned | `components/viewer/ModelStatsCard.tsx:L5, L9, L17, L61-112, L120, L136` |
| 6 | Per-completed-job `loadRunsForJob` (GET `/jobs/{id}/tunings` for every listed completed job) and the "tuned" marker | `components/history/HistoryItem.tsx:L8, L54-60, L93-97` |
| 7 | Viewer-tab pulse while tuning | `components/layout/MobileTabBar.tsx:L5, L31` |
| 8 | Session: `pollMeshTuner`, `refreshRuntime` at sync, `loadRunsForJob(viewed)`, WS `blender.updated`/`tuning.updated`, viewed-job subscription, resync on hello | `hooks/useBackendSession.ts:L15, L36-45, L53, L64, L68, L129-130, L146-150, L155` |
| 9 | Store, hooks, lib, API: `store/meshTunerStore.ts` (persist `img3d.meshTuner`), `hooks/useTuneAvailability.ts`, `hooks/useViewedTuning.ts`, `lib/meshTuner.ts`, `api/blender.ts`, `api/tunings.ts`, `api/types.ts` (Blender/Tuning types, WS messages), `api/ws.ts` (events) | – |

**Proposed flag [A].**

```ts
// lib/features.ts
export const FEATURES = {
  /** Auto Mesh Tuner (Blender). Paused: enable only with VITE_FEATURE_MESH_TUNER=true (later also gated by a
   *  backend capability, e.g. system.features.mesh_tuner, once the backend exposes one). */
  meshTuner: import.meta.env.VITE_FEATURE_MESH_TUNER === 'true',
} as const
```

Declare `VITE_FEATURE_MESH_TUNER?: string` in `vite-env.d.ts`. Gate at these choke points, so no tuner UI renders
and **no `/blender` or `/tunings` request is made** while the flag is off:
- (a) `GlobalOverlays`, for the dialog.
- (b) `StatusCluster`, for the pill.
- (c) Shells, for `TuningCard`.
- (d) `useViewedTuning`: return `{latestRun:null, tunedRun:null, variant:'original'}` when off. That disables the
  viewer `VariantBar`, the secondary GLB and `TunedStats` in one place.
- (e) `HistoryItem` effect and marker.
- (f) `useBackendSession`: skip `refreshRuntime`, `pollMeshTuner`, `loadRunsForJob` and resync. The WS handlers
  can stay because they are harmless.

Keep all tuner files in place; delete nothing. Do not move `components/meshTuner/*` now, because they have
uncommitted edits (`git status`). The mock keeps the tuner scenarios; `mesh_tuner_mock_smoke.mjs` stays valid.

---------------------------------------------------------------------------------------------------

## 8. Constraints for new code and quality gates

### 8.1 React Compiler (babel-plugin-react-compiler 1.0.0, default options, app code only) [V docs + probes from the tuner research]

A compiler bailout **does not fail the build**; the component is just not memoized. `compiler_check.mjs` is the
gate.

Patterns to avoid in components and hooks:
- `try { await … } finally {}` bails out. Use Promise chains, as in `ModelStatsCard.tsx:L24-33` and
  `useFullscreen.ts`.
- `?:`, `&&`, `||`, `??` or `?.` inside a `try` block bail out.
- Reading or writing `ref.current` during render bails out.
- An unconditional `setState` during render bails out.
- Manual `useMemo`/`useCallback` with wrong dependencies bails out. Do not add manual memoization;
  `react/preserve-manual-memoization` and `react/use-memo` are on. The existing manual memoization is only in
  `ModelScene.tsx` (L77-78, L169), inside the R3F canvas.

Safe patterns: derived-state reset `if (prev !== x) { setPrev(x); … }` (used in `ViewerPanel.tsx:L147`),
`useEffectEvent` (`GenerateCard.tsx:L43`), document-listener effects, class error boundaries (not compiled).

Put async error handling in **store actions**. Stores are not compiled, so `try/finally` is fine there; every
store uses it.

### 8.2 zustand v5 [V]

- One primitive or one *existing* reference per `useStore((s) => …)` call. **Never return a new object or array
  from a selector:** in v5 that is an infinite re-render. If you must, use `useShallow` from
  `zustand/react/shallow` (present in 5.0.15, unused today).
- Put fallbacks *outside* the selector: `useSystemStore((s) => s.system?.limits) ?? FALLBACK_LIMITS`
  (`ReferencePanel.tsx`).
- Named selectors returning stored references are fine: `selectActiveJob`, `selectViewedJob`, `selectLatestRun`,
  `selectTunedRun`, `selectActionError`.
- Use `create<T>()(…)` with `persist` + `createJSONStorage(() => localStorage)`, a `version` and a validating
  `merge`. Use `useXStore.getState()` in effects and handlers for imperative reads (49 uses in components and
  hooks).
- `useSyncExternalStore` hooks (`useBreakpoint`, the future `useViewport`) must return cached values from
  `getSnapshot`.

### 8.3 oxlint and TypeScript [V]

- `react/only-export-components` (warn) fires even on `export const X = [...] as const` in a component file.
  Constants, hooks and selectors go in `lib/`, `store/` or `hooks/`; `mobileTabs.ts` exists for this reason.
  Node definitions, the mode list and nav item tables must live in `.ts` files.
- `react/set-state-in-effect` (warn): no `setState` directly in effects. Use derived state or `key` remounts.
- `typescript/no-floating-promises`: prefix fire-and-forget calls with `void`.
- `erasableSyntaxOnly`: no `enum`, `namespace` or constructor parameter properties. Use `as const` tuples plus
  unions (see `api/types.ts`).
- `verbatimModuleSyntax`: use `import type` for types.
- Never import `three` or `@react-three/*` from non-lazy modules (keep `viewerTypes.ts`-style type-only files).
  Likewise, never import the node-graph library outside the lazy Node shell.
- Accessibility conventions to keep:
  - `IconButton` requires `label`.
  - Live regions only for status changes.
  - Tablists and radiogroups with roving focus.
  - Native `<dialog>` for modals.

### 8.4 Gate commands (PowerShell) and baseline [V = I ran it today]

```powershell
cd E:\14-OCT-2026\Img3D\frontend
npm run lint                                   # oxlint; warnings do not fail
npx oxlint --max-warnings 0                    # strict gate  [V] "Found 0 warnings and 0 errors. 91 files, 116 rules"
npx tsc -p tsconfig.app.json --noEmit          # [V] exit 0  (writes nothing)
npx tsc -p tsconfig.node.json --noEmit         # [V] exit 0
npm run build                                  # tsc -b + vite build → frontend\dist (served by backend at /)
npx vite build --outDir <scratch dir> --emptyOutDir   # [V] build without touching dist: OK in 2.65 s
cd E:\14-OCT-2026\Img3D
node test\frontend\review\compiler_check.mjs   # [V] prints [] and "compiled functions: 91, non-success events: 0"
node test\frontend\mesh_tuner_mock_smoke.mjs   # tuner contract smoke vs mock (Node >= 22); writes
                                               # test\outputs\mesh_tuner\frontend\mock_smoke.json (not run today: read-only)
# UI against the test double (no GPU):
node test\frontend\mock_backend.mjs --port 8765 --engine-ready --seed-jobs 2
#   flags: --auth --blender-scenario ready|old-only|none|downloading --no-download --download-seconds N
#          --download-fail none|checksum|network --tuning-scenario ok|warnings|fail --tuning-seconds N
$env:IMG3D_BACKEND_URL="http://127.0.0.1:8765"; cd frontend; npm run dev     # or: npx vite preview --port 4174
```

Baseline bundle (`vite build`, minified / gzip) **[V]**:

| Chunk | Minified | Gzip | Loaded |
|---|---|---|---|
| `index` | 326.6 kB | 110.0 kB | initial |
| `react` | 218.9 kB | 68.4 kB | initial |
| `vendor` | 4.4 kB | 1.9 kB | initial |
| `index.css` | 84.3 kB | 14.6 kB | initial |
| `three` | 808.2 kB | 206.9 kB | lazy |
| `r3f` | 249.3 kB | 79.9 kB | lazy |
| `ModelViewer` | 13.2 kB | 5.7 kB | lazy |

- **New gates to add [A].**
  - Mock scenarios for prompt jobs and the part manifest (`--parts-scenario`).
  - A `ui_modes` check that loads each mode at 320×640, 375×812, 768×1024, 1024×768, 812×375 and 1280×800 and
    asserts `scrollWidth == innerWidth` plus a ≥ 44 px touch-target count for the mobile and tablet modes. The DOM
    probe used in §3.3 can be reused.
  - No browser automation library is installed in `frontend/node_modules`: no Playwright or Puppeteer
    **[V]**.
  - Keep the compiler check at 0 events; the compiled-function count should grow, not shrink.

---------------------------------------------------------------------------------------------------

## 9. Risks and open questions

1. **The backend contract comes first.** The prompt input, the parts manifest, segmentation outputs and
   data-driven stages all need `api-contract.md` updates before frontend types (`api/types.ts` mirrors the
   contract). New `JobStage` values would otherwise break `stageState()` indices (`lib/stages.ts:L24`).
2. **The viewer singleton.** The Node UI must use one docked viewer. Node previews should be 2D thumbnails such as
   the job preview PNG or part-mask images.
3. **Media-query CSS versus mode.** Until the 11 `max-width:767px` blocks are mode-scoped, choosing Mobile on a
   wide screen or Node on a phone renders inconsistent dialogs and toasts.
4. **Memory on phones.** `ViewerPanel` skips the tuned preload on mobile via the breakpoint (L148). With part-split
   GLBs (several meshes), phones need lazy per-part loading or a merged GLB with named nodes. The
   `useViewport().pointer/lowMemory` signal should drive this, not the UI mode.
5. **A second zustand copy** (v4 inside @xyflow/react) is harmless but must be kept out of the initial chunk
   **[A]**.
6. **Uncommitted tuner edits** are in four frontend files. Coordinate before touching them, and keep the moves in
   §5.7 away from `components/meshTuner/*`.

---------------------------------------------------------------------------------------------------

## Appendix — what I read and ran

**Read in full or in relevant part [V]:**
- Root and layout: `main.tsx`, `App.tsx`/css, `vite-env.d.ts`, `components/layout/*` (all 15 files).
- Features: `references/*`, `generation/*`, `settings/*` (tsx + css sizes), `viewer/*` (ViewerPanel,
  ModelViewer, ViewerToolbar, ViewerErrorBoundary, ModelStatsCard, viewerRuntime, viewerTypes, ModelScene head),
  `history/*`, `meshTuner/TuningCard`, `BlenderSetupDialog`, `BlenderRuntimeStatus` (head), `auth/LoginScreen`
  (head).
- `common/*` (Panel, Button, Dialog, Toaster, EmptyState, ProgressBar, Spinner, SegmentedControl, Menu head; CSS
  size scan).
- All `store/*`, all `api/*`, all `hooks/*`, `lib/storage.ts`, `stages.ts`, `slots.ts`, `download.ts`,
  `format.ts` (head), `imageValidation.ts` (head).
- Styles: `styles/tokens.css`, `global.css`.
- Config: `vite.config.ts`, the three tsconfigs, `.oxlintrc.json`, `package.json`, `index.html`,
  `frontend/README.md`.
- Tests: `test/frontend/mock_backend.mjs` (header, flags, routes), `mesh_tuner_mock_smoke.mjs` (header, checks),
  `review/compiler_check.mjs`.
- Project docs: `.claude/CLAUDE.md`, the `img3d-run-and-test` skill, the `img3d-architecture` skill +
  `api-contract.md` §7.4/§8/§10, and the tuner research `frontend_integration.md` (constraints).

**Commands run [V]:**
- Lint and types: `oxlint` (default and `--max-warnings 0`, `--print-config`), `tsc --noEmit` (both projects).
- Checks and build: `compiler_check.mjs`, `vite build --outDir <scratchpad>`.
- Measurements: mock + `vite preview` (127.0.0.1), browser-pane viewport probes (§3.3).
- Lookups: `git status` (no-optional-locks), npm registry and bundlephobia metadata lookups.

The servers were stopped afterwards.
