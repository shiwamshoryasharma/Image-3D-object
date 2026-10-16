I built the full Blender runtime setup UI and Auto Tune UI and checked them visually at desktop, tablet and mobile sizes. All quality gates pass: oxlint, tsc, `npm run build`, and the compiler check (`[]`, 0 non-success events, 91 compiled functions, up from 60). Everything was tested against the extended mock backend only; the real backend endpoints are still being written, so nothing has run against them yet.

## Files
All paths are under `E:/14-OCT-2026/Img3D/`.

**New, frontend (`frontend/src/`)**
- API: `api/blender.ts`, `api/tunings.ts`
- Logic: `lib/meshTuner.ts`, `store/meshTunerStore.ts`, `hooks/useViewedTuning.ts`, `hooks/useTuneAvailability.ts`
- Shared components: `components/common/Dialog.tsx` and `Tag.tsx`, each with a `.module.css`
- `components/meshTuner/`, each with a `.module.css`: `BlenderRuntimeStatus`, `BlenderSetupDialog`, `SelectedRuntimeCard`, `BlenderDownloadPanel`, `BlenderInstallTable`, `InlineAlert`, `TuningCard`, `TuningOptionsForm`, `TuningSteps`, `TuningReport`, `TunedDownloadButton`

**Edited, frontend (`frontend/src/`)**
- `api/types.ts`, `api/ws.ts` (new types and the two WS events), `lib/storage.ts` (two new keys), `hooks/useBackendSession.ts`, `App.tsx`
- `components/layout/Header.tsx`, `AppShell.tsx`, `MobileTabBar.tsx`
- `components/viewer/ViewerPanel.tsx` (+css), `ModelViewer.tsx`, `ModelScene.tsx`, `viewerTypes.ts`, `viewerRuntime.ts`, `ModelStatsCard.tsx`
- `components/history/HistoryItem.tsx` (+css)
- `components/common/Button.module.css`: I fixed a bug that was already there. Button labels used line-height 1 with overflow hidden, so letters like g, p, y were cut off at the bottom. This file is shared by every button in the app.

**Tests**
- `test/frontend/mock_backend.mjs`, extended with switches: `--blender-scenario ready|old-only|none|downloading`, `--no-download`, `--download-seconds`, `--download-fail none|checksum|network`, `--tuning-scenario ok|warnings|fail`, `--tuning-seconds`, `--seed-jobs N`, `--engine-ready`. The default tuning scenario is `warnings`.
- New `test/frontend/mesh_tuner_mock_smoke.mjs`: checks the mock against the DESIGN §6 contract, including the WebSocket events.
- Outputs in `test/outputs/mesh_tuner/frontend/`: `mock_smoke.json`, 28 screenshots in `screens/`, and `visual_check_notes.txt`.

## What it does
- **Store** (`useMeshTunerStore`): runtime actions are refresh, detect, select (mode, optional executable), resolve release, start download (always sends `confirm: true`) and cancel download. Run actions are load runs per job, refresh, start, cancel and resync. It also holds the preset and options form (saved in localStorage) and the viewer's Original/Tuned choice.
- **Stale updates**: a run's status never moves backwards, older progress is ignored, and older download frames or scans are dropped.
- **Auto-open**: the setup dialog opens once per session when state is `needs_download`. Closing it saves that exact message, so the same message never auto-opens again in that browser.
- **Request body**: Auto Tune sends `{preset}` plus only the options that differ from the preset's defaults.
- **WebSocket**: `blender.updated` and `tuning.updated` are handled. On every reconnect the frontend re-fetches the runtime, the viewed job's runs and any active runs over REST. The fallback poll also refreshes active runs and the runtime.
- **Viewer**: both models are loaded into one canvas and the tuned one uses the original's position offset. Toggling Original/Tuned doesn't reset the camera. The original stays visible until the tuned model has loaded. On phones the tuned model only loads once it is first shown.

## Testing
- Gate commands (frontend): `npx oxlint --max-warnings 0 src` and `npx tsc -p tsconfig.app.json --noEmit` both exit 0. `npm run build` succeeds in about 2.6 s.
- Gate command (repo root): `node test/frontend/review/compiler_check.mjs` printed `[]` with 0 non-success events.
- Mock smoke test: `node test/frontend/mesh_tuner_mock_smoke.mjs` gave **36 passed, 0 failed** in about 17 s. It covers the exact prompt texts, 422/404/409/503 errors, all download phases with speed and ETA, cancel then resume, the 8 steps streaming in order, and a separate tuned GLB file.
- **Visual check** with the mock plus the Vite dev server:
  - ready: 5.2.2 selected and 4.2.17 shown as incompatible.
  - old-only: the exact message, release details, confirm button, live progress, then embedded 5.2.2 selected automatically.
  - Also checked: the checksum-failure state, the Auto Tune flow (steps, report, Original/Tuned toggle, tuned stats, history "tuned" marker), the failed and cancelled run states, and the per-message dismissal.

Problems I found and fixed during the visual check:
- Action errors were hidden: toasts sit under the modal dialog. Errors now show next to the control that failed.
- A failed download showed a moving bar. It now shows a static bar.
- The preview layout was too large, so I made it a compact grid.
- When Auto Tune starts, the step list now scrolls into view.
- The mobile dialog header and footer, and how the popover shows long paths, were cleaned up.
- A cancelled download now offers "Resume download".

## Known limitations
- The browser pane was hidden for part of the session, so some mobile interactions were driven with DOM `click()` calls and confirmed by reading the page text.
- The main JS bundle grew from 232 kB to 326 kB minified, about 57 kB of it the new tuner components. The setup dialog could be loaded on demand if that matters.
- The history "tuned" marker costs one `GET /jobs/{id}/tunings` per completed job shown in the list, fetched lazily and once per job.
- On desktop and tablet both GLBs stay in memory to make the toggle instant.
- The symmetry control offers Auto/Off/Repair; the API's `symmetrize` value isn't exposed.

## Deviations from DESIGN.md
None to the contract. Implementation choices:
- A mode-only selection sends just `{mode}`. Choosing a specific install sends `{mode: <that install's kind>, executable}`, and a typed custom path sends `mode: "system"`.
- When the download prompt is shown, the UI calls `POST /blender/runtime/release` automatically. This only reads the release info and never downloads.
- The viewer's "Tuned" option shows the newest completed run, while the card shows the newest run of any status.
- I followed DESIGN's file names (`api/blender.ts`, `api/tunings.ts`) rather than the research doc's single `api/meshTuner.ts`.

## Requests for other areas (backend)
1. **Auth on report/log links (likely breaks when login is enabled):** the UI opens `/tunings/{id}/report` and `/tunings/{id}/log` as links with `?token=`. Please serve both behind FileAuthGuard; otherwise the report link fails when auth is on, because the report is JSON and DESIGN puts JSON routes behind AuthGuard.
2. Include all 8 steps (as `pending`) in `TuningRunOut.steps` from the moment a run is created. The UI falls back to the fixed plan if the list is empty.
3. Keep `last_scan_at` and the download's `updated_at` from going backwards. The UI drops older snapshots, including download frames from the same download (same `started_at`).
4. The 503 `blender_unavailable` message is shown to the user exactly as sent. A run's `error.message` is shown as-is too.
5. Optional contract change: a `tuned` or `tuning_count` field on Job would remove the per-job history requests.