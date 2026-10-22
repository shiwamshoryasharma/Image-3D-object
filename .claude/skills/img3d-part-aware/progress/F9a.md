# F9a Shared shell chrome (Wave C → D) — done 2026-10-22

**Built.** `src/shells/shared/*` over F4 primitives, F5 stores/hooks, F6 `GenerateAction` and F7 content. Nothing mounts it yet: `SHELL_READY` and the placeholder `index.ts` files are untouched, so it adds 0 bytes to the bundle.

**Files (all N, `frontend/src/shells/shared/`).** `PhoneLayout.tsx`, `CompactTopBar.tsx` (+ `StatusPill`), `StatusSheetContent.tsx`, `ProgressPill.tsx`, `GenerateBlockedContent.tsx`, `AppSettingsButton.tsx`, `FeatureIcon.tsx`, `SectionHost.tsx`, `SheetHost.tsx`, `navItems.ts`, `sheets.ts`, `shellText.ts`, `Shell.module.css`.

**Exports for F9/F10/F11.**
- `PhoneLayout({nextRetryAt})`: the whole Mobile portrait layout, also Tablet `compact`. It renders `CompactTopBar`, `BackendBanner`, `<main id="main-content" tabIndex={-1}>` (ViewerPanel + ProgressPill), `FloatingNav` (FAB centre, `hidden` while the soft keyboard is up) and `SheetHost`. It calls `useReveal(applySheetReveal)` itself.
- `CompactTopBar()` (logo, `StatusPill` → `status` sheet, gear) · `AppSettingsButton({className?})` · `FeatureIcon({id: FeatureId, size?})`.
- `SheetHost({side?: 'bottom'|'right', sections?: boolean = true})`: one `Sheet` per `SheetId`. Element ids come from `sheetElementId(id)`; the stored snap is coerced to the sheet's allowed snaps; `create` gets the sticky `GenerateAction variant="bar"` footer. With `sections={false}`, only review/progress/blocked/status mount.
- `SectionHost({section, active? = true, headingLevel? = 3})`. Create = Input|Settings tablist over `createTab`. Parts = LazyPartsContent + "Model"/"Export" `<details>`. Library = HistoryList (opening an item reveals the viewer). Viewer → null. No footer: the host adds it.
- `ProgressPill()`: positions itself absolutely at the top centre of the nearest positioned ancestor (`--progress-pill-top`). Shown while a run is queued/running and for 8 s after it ends; tap → `progress`.
- `GenerateBlockedContent()`: reason/note/engine problem + "Retry loading model". "Open Create" closes the sheet, then `requestReveal('create')`, so it works in any posture. `StatusSheetContent()`: full EngineStatus, ConnectionIndicator (label), GpuMeter.
- `useShellNav({kind:'sheets'} | {kind:'panel', panelId})` → `{items, value, onSelect}` for `FloatingNav`/`NavRail`. It sets badges, `aria-expanded`/`aria-controls`/`aria-current`, and the §4.4 / §4.5.2 selection rules. Also exported: `selectSheetSection`, `selectPanelSection`, `SHELL_SECTIONS`, `SECTION_SHEET`.
- `sheets.ts`: `SHEET_SPECS: Record<SheetId, {modal, snaps, defaultSnap, title}>` (§4.4 table), `SHEET_IDS`, `SECTION_SHEETS`, `sheetElementId`, `resolveSheetSnap`, `openShellSheet(id|null, snap?)` (default snap per sheet), `applySheetReveal(target)` (the §4.4 reveal table).

**Acceptance review.**
- `SheetHost` covers every `SheetId`: both `SHEET_SPECS` and `SHEET_BODIES` are `Record<SheetId,…>`, so a new id fails `tsc`.
- FAB/rail table (§4.1) vs `useGenerateAction` phases, in F6's `RoundAction`: every row matches. ready → run. blocked → aria-disabled + Info badge, opens `blocked`. submitting/cancelling → spinner + aria-busy. queued → indeterminate ring; running → percent ring; both open `progress`. Cancel + confirm live in the progress sheet (`ProgressSheetContent`).
- F6 calls `openSheet` with no snap. `resolveSheetSnap` covers that: for example, `blocked` after a `full` Create opens at `half`.

**Gates.** tsc -b 0 · oxlint clean · compiler_check 0 events (192 fns) · import_boundaries 0 violations / 0 stale · protected 27/27 · unit 525/525 · ui_modes_mock_smoke 198/198 · mesh_tuner_mock_smoke 36/36 · build to `test/.runtime/frontend/f9a/dist` ok. Browser checks belong to F9/F10 (§11).

**Deviations.**
1. Added `sheets.ts`, `shellText.ts` and one `Shell.module.css`, so tables and words live in `.ts` files.
2. The status pill is its own button: engine dot + text, with `ConnectionIndicator` overlaid and `pointer-events:none`. It does not embed `EngineStatus compact`, which already renders its own popover button (that would nest interactive elements). The engine words are copied from EngineStatus `STATE_LABEL`, which is not exported.
3. No separate banner component: §4.4 uses the unchanged `BackendBanner`. `PhoneLayout` includes it; rail and Node layouts render `<BackendBanner nextRetryAt/>` themselves. Shells work while `phase==='unreachable'` (the pill says "Backend offline" and Generate is blocked).
4. Sheet titles not in FEATURE_META: "Review images", "Generation progress", "Generate is unavailable", "System status". The ended pill reads "Generation finished/failed/cancelled".
5. Progress Cancel confirm is an inline expanded group ("Stop this run? Its progress is lost.", then Cancel / Keep running), not a nested dialog.

**Requests.**
- **F9:** set `index.ts` to `posture === 'portrait' ? <PhoneLayout/>`, and set `SHELL_READY = true`. For landscape: `NavRail` with `useShellNav({kind:'sheets'})`, footer `GenerateAction variant="rail"` + `AppSettingsButton`; `BackendBanner`; `main#main-content` (position:relative) with ViewerPanel + ProgressPill; `<SheetHost side="right"/>`; `useReveal(applySheetReveal)`.
- **F10:** rail = `NavRail` with `useShellNav({kind:'panel', panelId})`, `SectionHost` in the side panel, `<SheetHost side="right" sections={false}/>`, its own reveal map. bottom = `CompactTopBar` + `FloatingNav` (FAB) + its own **standard** section `Sheet` (`SectionHost`, footer `GenerateAction bar` for Create) driven by `useShellNav({kind:'panel'})` + `<SheetHost sections={false}/>`. compact = `PhoneLayout`.
- **F11:** `AppSettingsButton` and `BackendBanner` are available. `FeatureIcon` too if wanted.
- **F8:** make `ViewerPanel` fill `.phoneMain > :first-child` (flex:1) full-bleed. If the viewer toolbar sits at the top, set `--progress-pill-top`.
- **F6/F12 (EngineStatus owner):** export `STATE_LABEL`, so `shellText.ENGINE_STATE_TEXT` can re-use it.
- **F4:** `NavItemButton` gives no screen-reader text for a dot badge (`badge: ''`), so the Library activity dot is visual only. Consider a `badgeLabel`.

**Open issues.** None blocking. A tap on Viewer while a modal sheet is open cannot happen (the nav is inert), so re-selection toggles only the standard `parts` sheet.
