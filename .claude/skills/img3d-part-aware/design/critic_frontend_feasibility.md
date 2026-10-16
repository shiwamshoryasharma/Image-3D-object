# Critic: frontend feasibility of UI_DESIGN.md

Date: 2026-10-16. Lens: can the frontend half be built as written against the code that exists today
(`frontend/src` at `8654b90`, clean tree), the installed toolchain (React 19.2, React Compiler 1.0, zustand 5.0.15,
R3F 9.8.1, drei 10.7.9, three 0.186.1, Vite 8 / Rolldown, oxlint 1.81, TS 6, Node v22.17.0), and
`@xyflow/react@12.12.0`?

How I checked: I read UI_DESIGN.md in full and DESIGN.md §3.12, §6.9 and §11. I read the cited source files, ran
`npm view` against the registry, and ran three local probes in the scratchpad: Node 22.17 strip-types + `--test`,
the React Compiler event log through the same babel setup as `test/frontend/review/compiler_check.mjs`, and gzip
sizes of the current `frontend/dist`. Nothing in the repo was edited.

Things I checked that hold up, so they are not listed below:
- `@xyflow/react` 12.12.0 is `latest` (published 2026-09-24), with peers `react >=17` and a nested
  `zustand ^4.4.0` (4.5.7). The §8.2 anchored vendor regex is right, and so is the hazard it fixes.
- The glob form of `node --experimental-strip-types --test` passes on 22.17. The directory form fails with
  MODULE_NOT_FOUND. Erasable classes and type-only extensionless imports work.
- A conditional render-time `setState` (the NodeCanvas sync pattern) compiles under the React Compiler.
- A file-level `'use no memo'` logs `CompileSuccess`, so it does not trip the compiler gate.
- The GLTFLoader facts are correct: `GLTFLoader.js:L4428` puts the original name in `userData.name`, `:L4871`
  lowercases custom attribute names, and node extras become `userData`.
- `viewport-fit=cover` is already set in `frontend/index.html`, so the `env(safe-area-inset-*)` rules work.
- Every other line reference I sampled matches today's code (± 1 line).

Severity: **blocker** = the design cannot be built or breaks a hard constraint. **major** = a slice's acceptance
or a §10.1 gate cannot pass as written, or a core feature misbehaves. **minor** = a local contradiction or a gap
with a cheap fix.

---------------------------------------------------------------------------------------------------

## Majors

1. **major — UI1 / F6 / F12 fingerprint acceptance cannot pass: the baseline contains the tuner UI that §3.9
   removes.**
   Evidence:
   - §10.5 `ui_probe.js` records the Default baseline fingerprint "from **today's** UI (legacy404)". UI1, F6 and
     F12 require "baseline + App settings".
   - Today's Default renders an "Auto Tune" `Panel` heading unconditionally (`TuningCard.tsx:L84/L96`, mounted at
     `AppShell.tsx:L64-66, L94-96, L134`).
   - The header has a button "Blender runtime: … Show details" (`Header.tsx:L40` → `BlenderRuntimeStatus.tsx:L106`).
   - §3.9 hides both in every mode. P21 adds a GpuMeter popover trigger at 1280–1439 px, but `GpuMeter.tsx` has no
     button today, and 1280×800 is a baseline viewport.
   - The fingerprint therefore must differ from "baseline + App settings".

   Fix:
   - In `ui_probe.js` (F0), define `EXPECTED_DEFAULT_DELTA` per viewport:
     - remove: the region labelled by heading "Auto Tune", and buttons whose name starts with "Blender runtime";
     - add: button "App settings", plus the P21 trigger name (e.g. "GPU memory") at widths 1280–1439.
   - UI1 becomes `fingerprint == apply(baseline, EXPECTED_DEFAULT_DELTA)`. Update UI1, the F6 acceptance and the
     F12 acceptance text to match.

2. **major — The initial-JS budget (≤ 195 kB gz, §8.1) is not reachable with the planned eager file set, and the
   gate runs after every slice from F11.**
   Evidence:
   - Measured today with gzip -9 on the current `dist`: index 108.2 + react 67.5 + vendor 2.0 + runtime 0.5 =
     **178.2 kB**. That leaves 16.8 kB of headroom.
   - About 10.7 k lines of app TS/TSX produce about 82 kB gz of app code in `index` (the rest is axios, dropzone and
     lucide). That is ≈ 7.7 B gz per source line.
   - §1.2 makes everything eager except the shells, the AppSettings body and the viewer. That covers 16 `lib`
     files, 9 stores, actions, 10 hooks, `app/*`, `components/{input,parts,export}` and the new settings,
     generation and common components: roughly 5–6 k runtime lines, or ≈ 40–45 kB gz.
   - The paused tuner UI (≈ 2.75 k lines, ≈ 20 kB gz) also stays in the initial chunk. `AppShell.tsx:L10`,
     `Header.tsx:L8`, `App.tsx:L8` (moving to `GlobalOverlays`) and `ModelStatsCard.tsx:L17` all import it
     statically, even though it is never rendered.
   - The CSS budget (18 kB vs 14.4 kB today) is similarly tight.

   Fix:
   - (a) Load the gated tuner components with `lazy()` from their importers. These are `TuningCard` in
     `AppShell.tsx`, `BlenderRuntimeStatus` in `Header.tsx`, `BlenderSetupDialog` in `GlobalOverlays.tsx` and
     `TunedDownloadButton` in `ModelStatsCard.tsx`. The protected files stay byte-identical; only the importers
     (already owned by F6/F7) change. This frees about 20 kB gz.
   - (b) Put the post-features content into one lazy chunk, mounted only when `features.status === 'ready'`:
     `PromptPanel`, `PromptReviewContent`, `SegmentationPreview`, `PartsPanel`/`PartsContent`, `ExportContent`
     and `PipelineSteps`.
   - (c) Replace the absolute numbers with deltas over a baseline that F0 records:
     - initial JS ≤ baseline + 25 kB gz once (a) and (b) are in;
     - CSS ≤ baseline + 5 kB;
     - Node shell budget re-measured after F11 lands, with a fixed +10 % ceiling from then on.

3. **major — Mobile and Tablet "0 targets < 48 px" (F9, F10, §9) depend on CSS files no slice owns, or that a
   parallel slice owns.**
   Evidence:
   - `layout/EngineStatus.module.css:L10` `button.pill { height: 30px }`. This is the status pill §4.1 reuses in
     `CompactTopBar`.
   - `settings/PolygonTargetControl.module.css:L19` sets preset `<button>`s (`PolygonTargetControl.tsx:L73, L85`)
     to `height: 24px`.
   - `settings/AdvancedSettings.module.css:L12` is `40px`.
   - `references/ReferenceTile.module.css:L207-208` (star button) is `26px`. F12 owns it, in wave D, parallel to
     F9/F10.
   - No slice in §11 owns `EngineStatus`, `PolygonTargetControl`, `AdvancedSettings`, `Field`, `SeedControl`,
     `ProgressCard` or `ModelStatsCard` `.module.css`.
   - §3.6 touch tokens set `--control-h-sm: 40px` / `--control-h-md: 44px`, which is below 48 even in
     Mobile/Tablet. Only `--hit-min` is 48, and nothing says every control consumes it.

   Fix:
   - Add these files to F4 (wave A) for a touch pass: `layout/{EngineStatus,ConnectionIndicator}.module.css`,
     `settings/{PolygonTargetControl,AdvancedSettings,Field,SeedControl}.module.css`,
     `generation/ProgressCard.module.css`, `viewer/ModelStatsCard.module.css` and
     `references/ReferenceTile.module.css`.
   - In each file, add rules under `:root[data-density='touch']` using
     `min-block-size: max(<today's px>, var(--hit-min))`, or a `::before` hit area for small icon buttons.
   - Hand `ReferenceTile.module.css` from F4 to F12 for its P17 shimmer, with "→" re-ownership as in §11.
   - State in F4's acceptance: "with `data-ui-mode=mobile`, every interactive element in the common components and
     the files above is ≥ 48 px".

4. **major — Part overlays and filtered region indices conflict with `sceneUtils.ts`, which F8 does not own.**
   Evidence:
   - §6.6 overlays are `MeshBasicMaterial` meshes "sharing the attributes" of the host.
   - `sceneUtils.ts:L128-131` `forEachMesh` skips only `__img3dWireOverlay`.
   - `applyViewMode` (L139-145) therefore replaces overlay materials with clay/normals. The F8 acceptance
     "highlight overlays in PBR and clay" fails.
   - `setWireframeOverlay` (L162-178) adds wire overlays to the overlays.
   - `disposeScene` (L190-199) calls `geometry.dispose()` on overlay clones. In three, that deletes the GL buffers
     of the shared attributes the host still draws.
   - If region hiding swaps the host's `mesh.geometry` for a clone, the existing wire overlay still points at the
     unfiltered geometry (`L173 new Mesh(mesh.geometry, …)`). `disposeScene` then frees the clone and leaks the
     original.
   - §1.2 and §11 F8 list `ModelScene/ModelViewer/ViewerPanel/ViewerToolbar/viewerTypes/viewerRuntime/PartsModel/
     partsScene/partsIndex`, but not `sceneUtils.ts`.

   Fix:
   - Add `src/components/viewer/sceneUtils.ts` (M) to F8, with three changes:
     - (1) `PART_OVERLAY_FLAG = '__img3dPartOverlay'`, skipped by `forEachMesh`, `setWireframeOverlay` and
       `disposeScene`.
     - (2) Region hiding calls `geometry.setIndex(filtered)` on the host's own geometry. The original index is kept
       in a `WeakMap` and restored before disposal and on PartsModel unmount, so clay, wireframe and normals
       follow automatically.
     - (3) Overlays dispose only their own index: call `deleteAttribute(name)` for each shared attribute, then
       `dispose()`.
   - Add a unit-free acceptance step: toggling clay ↔ PBR keeps overlay colours; hiding a region also hides its
     wireframe.

5. **major — The picking design cannot meet "≤ 1 frame" on a 2 M-face host. R3F raycasts on every pointer event,
   whatever the handler does.**
   Evidence:
   - §6.6 puts `onPointerMove` on the parts group, "throttled to one raycast per animation frame … disabled while
     OrbitControls is dragging".
   - R3F calls `state.raycaster.intersectObject(obj, true)` for every object with handlers, on every DOM pointer
     event (`@react-three/fiber/dist/events-9ce18a08.esm.js:L655`). Throttling the handler does not throttle the
     raycast, and it does not stop during orbit drags.
   - Without a BVH, `Mesh.raycast` is O(faces).
   - Selecting builds an `EdgesGeometry` (O(faces)) synchronously.
   - `ViewerPartsProps.states`/`colors`/`explode` are drilled from `ViewerPanel` → `ModelViewer` → `ModelScene`.
     Each hover then re-renders all three, which contradicts §8.3 "no React re-render of ModelScene".

   Fix:
   - (a) No R3F pointer props on parts. Add a `pointermove`/`pointerup` listener on `gl.domElement` that writes NDC
     and a dirty flag to a ref. `useFrame` runs one `raycaster.intersectObjects(pickTargets, false)` when dirty and
     not orbiting; track orbiting with OrbitControls `start`/`end` events.
   - (b) Pick targets are the physical meshes plus one pick-only clone per host. The clone shares `position` and
     keeps the original full index.
   - (c) Accelerate the pick targets with drei's BVH (`@react-three/drei/core/Bvh.js`, drei 10.7.9;
     three-mesh-bvh 0.8.3 is already installed), with `indirect: true` so the triangle order used by `faceLabels`
     is preserved. This adds no new npm dependency. Log the BVH build time once.
   - (d) Skip hits whose part or label is hidden.
   - (e) Build the selected-part outline lazily, then cache it per part. Exempt the first build from the 16 ms
     budget, or drop the outline.
   - (f) `PartsModel` reads `partStore.view` (`hidden` array ref, `isolated`, `selected`, `hovered`, `colorize`,
     `explode`) through primitive or stored-reference selectors. `ViewerPanel` passes only `url`, `binding` and
     `regionAttribute`.

6. **major — The NodeCanvas sync only reacts to `doc.revision`, so run status, selection and catalog availability
   never reach the canvas nodes.**
   Evidence:
   - §4.3.12 re-derives nodes only `if (doc.revision !== syncedRevision)`, through
     `toFlowNodes(doc, registry, statuses, selection)`.
   - Statuses come from `runStore` (§4.3.9). `select` is a store action, not a graph op (§4.3.10). Catalog
     availability arrives through `features.updated`. None of these bumps `revision` (§4.3.4).
   - Status chips and the inline progress bar therefore freeze during a run. The F11 acceptance "node statuses
     follow mock workflow steps" fails.
   - Selection made in the List view or Inspector is not shown on the canvas.
   - Edges are "derived each render" with no `selected` state. A canvas edge can never be selected, so the Delete
     shortcut cannot disconnect it (`deleteKeyCode={null}`, no `onReconnect`).

   Fix:
   - Keep `toFlowNodes` topology-only (`data: {id, type}`). Use
     `syncKey = `${doc.revision}|${selection?.id ?? ''}|${catalogEtag ?? ''}``.
   - `NodeCanvas` provides `NodeStatusContext` (value = the memoised `projectRunState(...)` result). `PipelineNode`
     reads its own entry from the context; context does pass into React Flow custom nodes.
   - Make `selection` a union `{kind: 'node' | 'edge'; id}` in `workflowStore`.
   - `toFlowEdges(doc, statuses, reducedMotion, selection)` sets `selected` from it. `classifyChanges` returns edge
     selections too. `nodeShortcuts` Delete calls `disconnect` for a selected edge.
   - Add matching cases to `flow_adapter.test.mjs`.

7. **major — `useNavPosture`'s "module-level ref per shell" is a React Compiler error, so it breaks the compiler
   gate.**
   Evidence:
   - §3.5: "`hooks/useNavPosture.ts` keeps the previous posture in a module-level ref per shell and applies a
     120 ms trailing debounce". Hysteresis needs `prev` read and written on each evaluation, i.e. during render.
   - Probe through the `compiler_check.mjs` babel config: a hook that reassigns a module variable gives
     `CompileError: "Cannot reassign variables declared outside of the component/hook"`. Mutating a module-level
     `{current}` object gives `CompileError: "This value cannot be modified"`.
   - §10.1 requires "non-success events: 0".

   Fix:
   - Compute posture outside render. The module-level external store behind `hooks/useViewport.ts` applies
     `nextTabletPosture`/`nextMobilePosture` inside its event listener (resize/orientation/focus). It handles the
     120 ms debounce with `setTimeout` there, and stores `{tablet, mobile}` postures in the frozen snapshot.
   - `useNavPosture(shell)` becomes `useViewport().posture[shell]`.
   - `bootDocument()` reads the same store's initial snapshot.

8. **major — Modal sheets make toasts and the global Announcer invisible and inert, which breaks Mobile error
   feedback and WCAG 4.1.3.**
   Evidence:
   - §4.4: the `create` (full by default, with the sticky Generate footer), `review`, `progress`, `library`,
     `blocked` and `status` sheets are modal `<dialog>.showModal()` (§4.1 `Sheet`).
   - `Toaster` and `Announcer` live in `app/GlobalOverlays.tsx`, outside every dialog (§3.7, §7 item 8).
   - A modal dialog makes everything outside it inert, which also removes it from the accessibility tree. Live
     regions there are not announced. `--z-toast: 80` (`tokens.css`) cannot rise above the top layer.
   - Several outcomes are reported only by toast or announcement (§2.11: 429 run queue, 503 feature/capability,
     422 `extra_forbidden`, "Generation queued", "Run blocked" assertive). They therefore vanish while the user is
     in the create or review sheet. The App settings dialog has the same problem: the mode-switch announcement is
     lost.

   Fix:
   - The `Sheet`/`Dialog` modal element is a full-viewport, untransformed `<dialog>`; the visual panel is an inner
     element that carries the drag transform.
   - While a modal is open, `GlobalOverlays` portals a `ToastOutlet` and a second `AnnouncerRegion` pair into the
     topmost open modal element. Modals register through a `modalStack` ref API exported by
     `components/common/modalStack.ts`, owned by F4.
   - The announcer store routes each message to the topmost region.
   - Add a §10.6 step: open the Create sheet, force a 429 from the mock, and check that the toast is visible and the
     alert region has the text.

9. **major — UI2's four-mode request-byte check has no data source as specified.**
   Evidence:
   - §10.6 step 2 reads the `POST /api/v1/jobs` body with `read_network_requests`. The pane tool is documented to
     list requests and fetch *response* bodies by `requestId`.
   - The mock request log (§10.4) stores only `body_sha256`. Ids (`rs_…`, `img_…`) differ per session, so the hash
     cannot be compared with G1.
   - UI2 is the only end-to-end proof of the hard constraint "image-to-3D request byte-identical" in all four modes.

   Fix:
   - `GET /api/__test/requests` also returns `body` (the raw request text; the mock is a test double).
   - The smoke and §10.6 step 2 rebuild G1 from `fixtures/requests/G1.json`, replacing the two ids with the values
     from the session's own `POST /reference-sets` and upload responses in the same log. They then compare raw
     strings with `===`.
   - Apply the same procedure to G2, G3, G5–G7.

10. **major — The shrink-only import allowlist turns red the moment F7 removes a grandfathered violation, and F7
    cannot fix it.**
    Evidence:
    - §1.4 grandfathers `components/viewer/ModelStatsCard.tsx → api/jobs.downloadJobGlb` "until F7 routes it
      through `actions/downloads.ts`, then the entry is removed".
    - The script "fails when an entry no longer violates".
    - §11 F0 owns `test/frontend/review/import_allowlist.json`, and F7's Owns list does not include it. F7's
      required "§10.1 green" is therefore impossible under disjoint ownership. The same applies to any later slice
      that removes a grandfathered import.

    Fix:
    - Add to the §11 rules: "Any slice may *delete* allowlist entries whose `from` file it owns; adding entries
      requires the F0 owner."
    - Or make stale entries a warning that lists the entry to delete. F13 then deletes them and the gate turns
      strict again.

## Minors

11. **minor — The §1.4 layer table contradicts today's code and the design's own Node and store code, so
    `import_boundaries.mjs` cannot pass "on today's tree" without an unlisted allowlist.**
    Evidence:
    - Today's tree breaks the table in several places:
      - `hooks/useBackendSession.ts:L11-12` imports `configureClientHooks`, `getAuthToken` (api/client) and
        `RealtimeClient` (api/ws). Hooks may import only URL helpers.
      - `layout/BackendBanner.tsx:L3` imports `API_BASE_URL`.
      - `ReferencePanel.tsx:L5`, `ReferenceTile.tsx:L6` and `AdvancedSettings.tsx:L4` value-import constants from
        `api/types`.
      - `ViewerPanel.tsx:L5` imports `tunedModelUrl` and `HistoryItem.tsx:L4` imports `jobPreviewUrl`.
    - The design's own code breaks it too:
      - §4.3.8 `runNodeWorkflow.ts` calls `validateWorkflow`, and §4.3.5 `useWorkflowValidation.ts` POSTs
        `/workflows/validate`, both from `shells/node/` (shells must not import `api/*`).
      - §4.3.6 has `workflowStore` call `applyPipelineConfig` from `actions/pipeline.ts` (store must not import
        actions).
      - The §1.4 store edges say "part → none", but `partStore.loadSet` needs the features snapshot
        (`normalizePartResult(run, manifest, f)`, §6.1), and `runStore` toasts and announces (§2.7 step 5).

    Fix:
    - Name the exceptions explicitly:
      - a permanent `useBackendSession → api/client{configureClientHooks,getAuthToken}, api/ws`;
      - "URL helpers" = {`resolveApiUrl`, `fileUrl`, `withQuery`, `jobModelUrl`, `jobPreviewUrl`,
        `referenceThumbnailUrl`, `tunedModelUrl`, `API_BASE_URL`};
      - `api/types.ts` and `api/partAwareTypes.ts` value constants are allowed from every layer.
    - Add `workflowStore.validateRemote(revision, request, signal)` and use it from both Node files.
    - `NodeShell` subscribes to `workflowStore` and calls `applyPipelineConfig` (shell → actions is allowed).
    - Store edges: `part → features, announcer, toast`; `run → job, part, reference, input, features, announcer,
      toast`.

12. **minor — Two F2 Node-runner assertions cannot pass.**
    Evidence:
    - `pure_imports.test.mjs` forbids `window.` and `localStorage` in every §1.5 file. `lib/storage.ts`, which is
      on that list, contains `window.localStorage` at L20.
    - `image_job_request.test.mjs` asserts "`settingsStore.ts` re-exports resolve". `settingsStore.ts:L8-22` has
      extensionless value imports (`'../api/types'`, `'../lib/format'`, `'../lib/storage'`). Importing it under
      Node 22.17 fails with ERR_MODULE_NOT_FOUND (verified with a probe).

    Fix:
    - Exempt `lib/storage.ts` from the global-token scan only; the import rules stay.
    - Make the re-export assertion a static check: parse `settingsStore.ts` for
      `export { … } from '../lib/settingsRequest.ts'` and rely on `tsc -b` for resolution.

13. **minor — "`types.ts` append-only, zero imports" cannot add five `WsServerMessage` variants.**
    Evidence:
    - `api/types.ts:L693-699` closes the union. Appending cannot widen it.
    - The variants' `data` types (`FeaturesOut`, …) live in `partAwareTypes.ts`, which `types.ts` may not import
      (§2.1 last paragraph).

    Fix:
    - Leave `WsServerMessage` untouched. Declare
      `PartAwareWsMessage = {[K in keyof PartAwareEventMap]: {type: K; ts: IsoDateTime; data: PartAwareEventMap[K]}}[keyof PartAwareEventMap]`
      in `partAwareTypes.ts`.
    - `ws.ts` dispatches `WsServerMessage | PartAwareWsMessage`.
    - Keep `ReferenceImage.source?` via interface merging in the appended block.

14. **minor — The Default reveal targets point at element ids that do not exist.**
    Evidence:
    - `Panel.tsx` uses `id` only for `<h2 id={`${id}-title`}>`; the `<section>` gets no id (L20-23, L31).
      `SettingsPanel.tsx:L53` `id="settings"` therefore yields only `#settings-title`.
    - §4.2's table scrolls `#parts` and `#settings`.

    Fix: the Default reveal handler resolves `document.getElementById(`${id}-title`)?.closest('section')`. This
    needs no DOM change and keeps UI1.

15. **minor — §2.4 says `part_analysis.updated` terminal → `loadModel(analysisId)`.**
    Evidence: `loadModel` is `featuresStore.loadModel` = `POST /models/{id}/load` (§2.5). The part-set loader is
    `partStore.loadSet(analysisId)` (§6.1).
    Fix: change §2.4 to `loadSet(analysisId)`.

16. **minor — Where `SHELL_READY` lives is inconsistent, and the placeholders break the cross-shell rule.**
    Evidence:
    - §3.5 says "`SHELL_READY` in each `shells/<mode>/index.ts`", but §3.7 says `ready.ts`. Statically importing
      `index.ts` would put each shell in the initial chunk.
    - §1.2 makes the placeholder `shells/{mobile,tablet,node}/index.ts` "re-export DefaultShell", which violates
      §1.4 "must not import another `shells/<mode>/`".

    Fix:
    - Change §3.5 to `ready.ts`.
    - Each placeholder `index.ts` is `export default function ShellPlaceholder(): null { return null }`. It is
      never loaded while `SHELL_READY = false`.

17. **minor — The `ShellErrorBoundary` [Retry] cannot recover.**
    Evidence: §3.7 declares `lazy(SHELL_LOADERS.node)` at module scope. `React.lazy` caches the rejected payload,
    and browsers cache a failed module fetch for the same specifier, so remounting re-throws.
    Fix: [Retry] calls `location.reload()`. "Use Default UI for this session" stays.

18. **minor — A mode switch drops the GLB cache whenever the shell chunk is cold.**
    Evidence:
    - zustand updates go through `useSyncExternalStore` and cannot be transitions. A suspending lazy shell
      therefore shows the fallback and unmounts the viewer.
    - `ModelScene.tsx:L44-52` disposes and clears the loader cache after 250 ms. §8.3 allows 600 ms for a cold
      chunk.
    - §3.7's "GLB comes from the useLoader cache within 250 ms" therefore fails, and an 80 MB parts GLB downloads
      again.

    Fix: `UiModePicker` (and `?ui=` boot) awaits `SHELL_LOADERS[mode]()` before calling `setUiMode`, so the lazy
    component resolves within a microtask.

19. **minor — UI6 checks via `getEventListeners`, which exists only in the DevTools console API, not in page
    JavaScript.**
    Fix: check by behaviour. Clear the mock request log, send one Ctrl+Enter keydown in the pane, and expect
    exactly one `POST /api/v1/jobs` (or `/workflows`) entry.

20. **minor — The sheet "body drag when `scrollTop === 0`" cannot be built with Pointer Events.**
    Evidence (§4.1 `Sheet`): with `touch-action: pan-y` the browser claims vertical pans and fires
    `pointercancel`. With `none` the body cannot scroll.
    Fix: drag only on the handle/header, which already has `touch-action: none`. Or use a non-passive `touchmove`
    listener that calls `preventDefault()` only when `scrollTop === 0 && deltaY > 0`.

21. **minor — The Node view matrix contradicts `resolveNodeView`.**
    Evidence:
    - §3.5: `auto → list` when `coarse && width < 900`.
    - §9 lists Node at 768×1024 and 820×1180 as "canvas, inspector sheet". On a real (coarse) tablet the rule gives
      the list view.

    Fix: change the rule to `list` when `width < 600 || (coarse && min(width, height) < 520)`. Real tablets then get
    the ComfyUI-style canvas, and the matrix holds for both pointer types.

22. **minor — `bundle_budget.mjs` cannot attribute the lazy shell chunks.**
    Evidence:
    - `SHELL_LOADERS` imports `shells/<m>/index.ts`, so all three lazy shells and the entry emit `index-<hash>.js`.
    - `dist/index.html` maps nothing, and `vite.config.ts` has no `build.manifest`.
    - `shells/shared` is shared by mobile and tablet, so "reachable only from `SHELL_LOADERS.mobile`" excludes it.

    Fix:
    - F11 sets `build.manifest: true`.
    - The script walks `dist/.vite/manifest.json` from `src/shells/<m>/index.ts` (`imports` + `dynamicImports`),
      minus chunks that the entry reaches statically.
    - Count the shared-chunk group once under "Mobile + shared".

23. **minor — The catalog 304 path needs explicit axios config.**
    Evidence: `apiClient` uses axios' default `validateStatus` (2xx). A 304 goes through the response error
    interceptor (`client.ts:L226-238`) and becomes `ApiError http_304`.
    Fix: `getWorkflowCatalog` passes `validateStatus: (s) => (s >= 200 && s < 300) || s === 304` and maps 304 to
    `{kind: 'not_modified'}`.

24. **minor — F9a's acceptance is circular.**
    Evidence: F9a (wave C) is accepted through "a temporary dev mount in the F9 branch", but F9 is wave D. While
    `SHELL_READY` is false, `?ui=mobile` resolves to Default, so F9a has no in-app mount.
    Fix: F9a's acceptance = §10.1 gates plus a review of the FAB/rail state tables. The browser checks move into
    F9's and F10's acceptance.

25. **minor — `THIRD_PARTY_NOTICES.md` ownership conflicts with DESIGN.md, which takes precedence.**
    Evidence: DESIGN §11 says "`THIRD_PARTY_NOTICES.md` belongs to S6 only". UI_DESIGN §11 gives it to F11 after S6
    merges.
    Fix: add to DESIGN §11's disjointness list: "then F11 (xyflow, `@xyflow/system`, d3-*, classcat, nested
    zustand 4 rows) after S6 merged". Until that sign-off, F11 hands its rows to the S6 owner.
