# UI modes research: Node UI, Mobile UI, Tablet UI, Default polish

Research date: 2026-10-16. Phase: read-only understanding. No project file other than this report was created or changed.

Legend: **[V]** means verified in this session (I read the file, ran the command or fetched the primary source).
**[A]** means an assumption or general knowledge that I did not re-verify here. Check [A] items during implementation.

---

## 0. TL;DR (recommendations)

1. **Node editor renderer: `@xyflow/react` 12.12.0** (React Flow, MIT). Lazy-load it so it only downloads when Node UI
   is selected. Use `base.css` plus a small token-mapped stylesheet. Rete.js v2 is the runner-up. Do not use litegraph:
   `@comfyorg/litegraph` is deprecated and archived, and ComfyUI itself is moving off the canvas renderer.
2. **Keep a pure TypeScript node engine** in `frontend/src/workflow/engine/`. It holds the graph model, the node-type
   registry with typed ports, validation, the topological plan, a compiler to the backend request, and versioned
   serialization. It has no React, DOM, axios or React Flow imports. React Flow is only a view, connected through a thin
   adapter, and its `isValidConnection` calls the engine. Future tuning nodes become registry entries in a `refine`
   category. That category stays **locked** until you approve it.
3. **The graph is compiled into a backend job. Nodes are not executed in the browser.** Run builds the same
   `CreateJobRequest` that the Default UI sends, and later the part-aware pipeline request. Per-node highlighting and
   progress come from the existing `job.updated` WebSocket events, through a `JobStage` → `nodeId` map.
4. **One shared state/API layer and four renderers.** The existing Zustand stores and `src/api/*` are already the shared
   layer. Add a persisted `preferences` store (`uiMode`), a renderer host that lazy-loads `default | node | mobile |
   tablet`, and small shared components (FloatingNav, NavRail, Sheet, GenerateFab, InputSourcePanel) that reuse the
   existing panels. No renderer calls the API directly. They all go through store actions plus
   `useGenerateAvailability`.
5. **Mobile:** a floating bottom nav with a centre-docked Generate FAB in the thumb zone. Panels open as `<dialog>`
   sheets (modal) or standard sheets (non-modal) with snap points and a keyboard-operable drag handle. Respect
   `env(safe-area-inset-*)`, use 48 px targets and honour `prefers-reduced-motion`. The `--duration-*` tokens are
   already zeroed for reduced motion.
6. **Tablet:** a floating left navigation rail when `width ≥ 840px` and landscape, and the mobile-style floating
   bottom nav otherwise. Switching is automatic through `matchMedia`, `useSyncExternalStore` (same pattern as
   `useBreakpoint.ts`) and a soft-keyboard guard. Panels adapt to their own width with container queries, so each
   panel is written once.
7. **Fix the a11y debt found in the current UI as part of the Default polish:**
   - `--color-text-subtle` is 3.99:1 on surface-1, below the 4.5:1 AA threshold.
   - White on `--color-accent` is 3.69:1, so the primary button fails AA.
   - Controls are 26–34 px tall. That passes WCAG's 24 px minimum but is below the 44/48 px touch guidance.
   - The History drawer is not a modal or a focus trap, and nothing behind it is inert.
   - `scrollIntoView({behavior:'smooth'})` ignores reduced motion.

---

## 1. Current frontend baseline (what to reuse)

Installed versions **[V]** (read from `frontend/node_modules/*/package.json`): react 19.3.0, react-dom 19.3.0,
vite 8.3.4, typescript 6.0.3, zustand 5.0.15, babel-plugin-react-compiler 1.0.0, lucide-react 1.57.0,
@use-gesture/react 10.3.1. use-gesture is already installed as a dependency of drei.

`frontend/tsconfig.app.json` sets `erasableSyntaxOnly: true`, `verbatimModuleSyntax: true` and
`allowImportingTsExtensions: true` **[V]**. So the engine must avoid `enum`, `namespace` and constructor
parameter-properties. It must use `import type`. In return, Node 22 type-stripping can run it directly in tests.
`strict` is not set explicitly. TypeScript 6 defaults it to true **[A]**.

| Area | File (verified) | Reuse / note |
|---|---|---|
| Design tokens | `src/styles/tokens.css:5-111` | Dark-only. Surfaces 1–4, accent `#8b6cff`, accent-2 `#22d3ee`, 4 px spacing grid, radii, shadows, `--ease-out`, `--duration-*`, `--header-height 56px`, `--tabbar-height 60px`, z-index scale. |
| Reduced motion | `tokens.css:113-119` | Already sets every `--duration-*` to 0 under `prefers-reduced-motion`. Keep using the tokens in all new motion. |
| Global CSS | `src/styles/global.css:21-38` | `body{overflow:hidden}` and `#root{height:100dvh}`. The app scrolls inside containers, which makes scroll-locking for sheets easy. `:focus-visible` outline is at `:84-87`. |
| Viewport meta | `frontend/index.html` | `viewport-fit=cover` is present, so `env(safe-area-inset-*)` works on iOS. `theme-color #0b0d12`. |
| Breakpoints | `src/hooks/useBreakpoint.ts:6-24` | mobile < 768 ≤ tablet < 1280 ≤ desktop, via `useSyncExternalStore` + `matchMedia`. Copy this pattern for orientation. |
| Layouts | `src/components/layout/AppShell.tsx:46-156` | `DesktopLayout` has 3 columns, `TabletLayout` 2 columns, `MobileLayout` is tabs plus `GenerateCard variant="bar"`. This is the **Default renderer**. Keep it as is. |
| Mobile nav | `src/components/layout/MobileTabBar.tsx:15-79` | WAI-ARIA tabs with roving focus, a badge and a running pulse. The styles are a docked full-width bar (`MobileTabBar.module.css:1-8`, `env(safe-area-inset-bottom)`). Its logic can be reused for the floating nav. |
| Generate action | `src/components/generation/GenerateCard.tsx:19-127` + `src/hooks/useGenerateAvailability.ts` | The single source of `canGenerate`, `reason`, `note`, `busy` and `engineProblem`. Ctrl/Cmd+Enter is registered by exactly one instance (`shortcut` prop). The FAB, the rail button and the Node "Run" must reuse this hook. |
| Submit | `src/store/jobStore.ts:197-223` | `submit()` reads the reference and settings stores and builds a `CreateJobRequest` (`api/types.ts:300-304`). Split it into `buildCreateJobRequest()` and `submitJob(request)` so Node UI can submit a compiled request. |
| Stages | `src/lib/stages.ts:10-19` | 8 pipeline stages. They become the source of the stage→node map for per-node progress. |
| Realtime | `src/api/ws.ts:34-42` | Events `job.updated`, `system.updated`, `tuning.updated`. Node status projection subscribes indirectly through `jobStore`. |
| Dialog | `src/components/common/Dialog.tsx:24-68` + `.module.css:105-128` | Native `<dialog>.showModal()` (top layer, inert background, Escape) with manual backdrop-click close. Already full-height on < 768 px. Base the modal bottom sheet on it. |
| Drawer | `src/components/history/HistoryDrawer.tsx:11-60` | Focuses the panel and restores focus, Escape closes. **No focus trap and the background is not inert while open** (only the drawer gets `inert` when closed). |
| Persistence | `src/lib/storage.ts:6-16`, `src/store/uiStore.ts:32-52` | Keys are `img3d.*`. `uiStore` persists only `viewer` (partialize). Add `img3d.preferences` and leave `uiStore` alone (backward compatible). |
| Controls size | `Button.module.css:39-57,113-135`, `SegmentedControl.module.css:28-33`, `Switch.module.css:44-70` | Buttons are 28/34/44 px, icon buttons 28/34 px, segments 26/30 px, switch track 22 px. Fine for mouse, small for touch. |
| Hover-none hook | `GenerateCard.module.css` (`@media (hover: none)` hides the kbd hint) | Precedent for input-capability media queries. |
| Compiler check | `test/frontend/review/compiler_check.mjs` | Runs babel-plugin-react-compiler over `src/` and lists bail-outs. Run it on all new Node/Mobile/Tablet components. |
| Code splitting | `frontend/vite.config.ts:58-77` | Rolldown `codeSplitting.groups`: react(50), vendor(40), three(30), r3f(20). |
| Bundle today | `frontend/dist/assets` (built 2026-10-16 11:58) | `index` 326.6 kB raw / 108.5 kB gz, `react` 218.9 / 67.6 kB gz, `vendor` 4.4 kB, CSS 84.3 / 14.6 kB gz. three (808 kB) and r3f (249 kB) are lazy. Initial JS is about 178 kB gz. |

Gaps relevant to this milestone **[V]**:
- There is no prompt input anywhere. The `CreateJobRequest` is image-only (`reference_set_id`, `primary_image_id`,
  `settings`).
- There is no app-level Settings/Preferences surface. `SettingsPanel` holds generation settings only.

---

## 2. (A) Node-editor library comparison

Registry metadata **[V]** comes from `npm view` (2026-10-16). Sizes **[V]** come from the Bundlephobia API (min / min+gzip,
peer dependencies excluded).

| | **@xyflow/react** (React Flow 12) | **litegraph** (`litegraph.js` / `@comfyorg/litegraph`) | **Rete.js v2** (+ react plugin) | **Custom SVG/DOM editor** |
|---|---|---|---|---|
| Latest | 12.12.0 (registry modified 2026-10-11); `@xyflow/system` 0.0.83 | `litegraph.js` 0.7.18 (last publish 2024-01-08). `@comfyorg/litegraph` 0.17.2 is **npm-deprecated**, its repo is **archived** and the code now lives in `Comfy-Org/ComfyUI_frontend` `src/lib/litegraph` | `rete` 2.0.6, `rete-react-plugin` 2.1.2 (2026-07), `rete-area-plugin` 2.3.2, `rete-connection-plugin` 2.0.5 (2024-08), `rete-minimap-plugin` 2.0.3 | n/a |
| License | MIT | MIT | MIT (all packages) | own (GPL-3.0 repo) |
| Size (min / gz) | 187.9 kB / **60.0 kB** (with zustand v4, d3-zoom/drag/selection, classcat) | 0.7.18: 499.8 / 121.2 kB. comfyorg 0.17.2: 305.2 / 85.3 kB | rete 3.1 + area 9.7 + connection 7.3 + react-plugin 10.7 + render-utils 5.2 + minimap 2.3 + **styled-components 6 12.9** ≈ **51 kB gz** | roughly 10–20 kB gz **[A]** |
| React 19 | peer `react >=17` (works with 19) | Not React. Imperative canvas inside a React wrapper | peer `^16.8.6 \|\| ^17 \|\| ^18 \|\| ^19`. React 19 needs `new ReactPlugin({ createRoot })` | yes |
| Rendering model | DOM nodes (your React components) + SVG edges, CSS transform viewport | Canvas2D. Nodes and widgets are painted, not DOM | DOM per element. "Each node, control, socket, or connection is its own element tree" (separate React roots, so no shared React context) | your choice |
| Typed ports + validation | `Handle` with `id`/`type`. Graph-level `isValidConnection(edge\|connection)` prop. `connectionMode` default `'strict'`. Docs have a cycle-prevention example | Slot types (strings). Built-in type matching with type colours | `ClassicPreset.Socket` subclasses with `isCompatibleWith()`, enforced in `editor.addPipe` on `connectioncreate` | you build it |
| Custom nodes | Any React component via `nodeTypes` (must be stable/module-scope). Typed with `Node<Data,'type'>` / `NodeProps<T>` | Subclass `LGraphNode`, paint in `onDrawForeground` | `Presets.classic.setup({customize:{node, socket, connection}})` | yes |
| Minimap / controls / background | `<MiniMap>`, `<Controls>`, `<Background>`, `NodeToolbar`, `NodeResizer` built in | built in (Comfy fork) | minimap plugin. Zoom/drag in area plugin | build |
| Zoom/pan | d3-zoom. `minZoom 0.5`, `maxZoom 2` defaults, `panOnDrag`, `panOnScroll`, `zoomOnPinch: true` | yes | area plugin (wheel, dblclick, pinch) | build |
| **Touch** | Tap-to-connect (`connectOnClick: true`, since v10). Drag-to-connect on touch since v11.5 with auto-pan and `connectionRadius` (20). Pinch zoom on by default. Official "Touch Device" example enlarges handles | Historically weak **[A]** | Pinch zoom through Pointer Events (`Zoom` class tracks ≥2 pointers, `source:'touch'`) **[V source]**. Connection drag on touch not documented **[A]** | build (Pointer Events) |
| **Keyboard a11y** | Tab/Shift+Tab through nodes and edges, Enter/Space select, Escape clear, arrows move (Shift faster), Delete. `nodesFocusable`/`edgesFocusable` default true. `disableKeyboardA11y`, `autoPanOnNodeFocus`, `ariaLabelConfig` (i18n), `aria-live` move announcements, nodes `role="group"`, `ariaRole`, `domAttributes` | None (canvas has no DOM nodes) | Nothing documented **[A]** | build |
| Theming via CSS variables | Yes. `--xy-*` variables (edge stroke, node bg/border/colour, handle, minimap, controls, selection, background pattern), `colorMode` (`light` default, `dark`, `system`), `base.css` vs `style.css` | JS properties on canvas | styled-components by default. CSS vars only inside your own custom components | yes |
| Performance | DOM nodes are fine at a few hundred. Guidance: memo custom nodes (React Compiler handles this), avoid subscribing to the whole `nodes` array, collapse/hide large trees, simpler CSS. Opt-in `onlyRenderVisibleElements` | Best for huge graphs (canvas) | similar to DOM | depends |
| React Compiler | No reported incompatibility found (searched xyflow issues/docs, nothing) **[V search]**. Immutable `applyNodeChanges` model fits the compiler. Keep `nodeTypes` at module scope | n/a | Separate roots per element is fine, but there is no shared context | fully controlled |
| Maintenance | Very active (12.4 `useNodeConnections` 2025-01, 12.10.1 2026-02, 12.12.0 2026-10) | archived / deprecated | core 2.0.6 (2025-06), react plugin 2026-07. Slower cadence | you |
| Attribution | Small "React Flow" link. Docs: "Only remove this attribution, if you are subscribed to React Flow Pro" (MIT allows it legally; this is a request) | none | none | none |

Other libraries checked **[V npm]**:
- `flume` 1.2.0 has a React ^18.2 peer, so React 19 is not supported.
- `@projectstorm/react-diagrams` 7.0.4 was last published 2024-02, so it looks stale.
- `reaflow` 5.4.1 (Apache-2.0) is diagram-oriented and not built for socket editing.
- Auto-layout helpers:
  - `@dagrejs/dagre` 3.1.1 (MIT) is fine as an optional "Tidy up" action.
  - `elkjs` 0.12.0 is **EPL-2.0 OR GPL-3.0-or-later**. That is acceptable under this GPL-3.0 repo, but heavier. Prefer dagre.

### Recommendation

**Use `@xyflow/react` (^12.12.0).** It is the only option that meets all of these at once:
- React 19 support and React Compiler friendliness
- DOM nodes, so the existing `Button`, `SegmentedControl`, `NumberField` and `Switch` can be reused inside nodes
- built-in keyboard a11y
- documented touch connect and pinch
- CSS-variable theming that maps onto `tokens.css`

Costs and how to handle them:
- **About 60 kB gz.** Lazy-load it with the Node renderer so the Default/Mobile/Tablet initial bundle stays around 178 kB gz.
  Add a Rolldown group such as `{ name: 'xyflow', test: /node_modules[\\/](@xyflow[\\/]|d3-|classcat[\\/])/, priority: 35 }`.
  Then check the build to confirm the initial chunk did not grow.
- **zustand v4 is bundled privately.** `@xyflow/react` depends on `zustand ^4.4.0` **[V]**, which nests next to the
  app's zustand 5. The current `vendor` group regex (`vite.config.ts:66`) would also match the nested
  `@xyflow/react/node_modules/zustand`, so verify chunk membership after adding the group.
- **Keep the attribution** (restyle it with tokens), or subscribe to Pro.
- **Licensing:** MIT is compatible with GPL-3.0. Add it to `THIRD_PARTY_NOTICES.md` per the publishing skill.

**Runner-up: Rete.js v2.**
- Pros: smaller core and an explicit socket model.
- Cons:
  - It requires styled-components, a second styling system next to CSS Modules.
  - Each element is a separate React root.
  - No documented keyboard a11y.
  - Slower release cadence.

**Rejected: litegraph.**
- The package is deprecated and the repo archived.
- Canvas rendering gives no DOM, no screen reader support and no CSS tokens.
- ComfyUI's own docs say the canvas "had become a development bottleneck", and they are migrating to Vue DOM nodes ("Nodes 2.0").

**Custom editor:** this is feasible, at about 1.5–2.5 k LOC plus tests and 1–2 weeks of work **[A]**. Rough breakdown:
- pan/zoom/pinch with Pointer Events
- node drag/selection
- SVG bezier edges
- connection drag plus tap-connect
- keyboard focus model
- minimap

You would own every edge case (pointer capture, iOS gesture quirks, a11y). That is not worth it now. The engine/adapter
split below keeps a later swap cheap.

---

## 3. ComfyUI UX conventions worth copying

Sources: docs.comfy.org (shortcuts, Lite Graph settings, links, workflow JSON spec, API format, server messages,
Nodes 2.0) **[V]**, plus general ComfyUI knowledge **[A]** where marked.

| Convention | ComfyUI behaviour | Img3D adaptation |
|---|---|---|
| Typed, coloured sockets | "You can only connect ports of the same color". Each type has one colour shared by port and wire (CLIP yellow, VAE rose, latent pink, pixel image blue, mask green, mesh bright green, …) **[V]** | One colour **and one shape** per `PortType`. Shape is needed for WCAG 1.4.1, colour alone is not enough. image_set ● cyan/blue, prompt ◆ amber, segmentation ■ green, mesh ▲ violet, part_set ⬢ pink, export sink. Expose them as `--port-*` tokens. The wire takes the source port colour. |
| Node colour per category | User recolouring via context menu "Colors"; built-in palette **[A]** | A category header stripe from tokens: input = accent-2, analyze = info, generate = accent, parts = pink, output = success, refine (locked) = warning plus a dashed border. Allow optional per-node colour, stored in the layout, not the semantics. |
| Queue / Run button | Ctrl+Enter queue, Ctrl+Shift+Enter queue front, Ctrl+Alt+Enter interrupt **[V]**. Queue modes Normal / Instant / On-change **[A, third-party guide]**. The newer UI labels it "Run" **[A]** | Toolbar **Run** (Ctrl/Cmd+Enter, the same shortcut as GenerateCard; register it in only one place). It shows the issue count and is disabled (with `aria-disabled` and a reason) while the graph is invalid or `useGenerateAvailability().canGenerate` is false. While running it switches to **Cancel**. No "Instant/On-change" modes; GPU jobs are minutes long. |
| Per-node execution highlight + progress | Server messages `execution_start`, `execution_cached` (nodes), `executing` (node), `progress` (node, value, max), `executed`, `execution_success`, `execution_error`, `execution_interrupted` **[V]**. The frontend outlines the executing node and draws a progress bar on it **[A]** | Project `job.updated` (`stage`, `percent`, `step/total_steps`, `eta_seconds`) onto nodes through `stageMap`. Node states: idle, queued, running (accent ring plus inline progress bar, `aria-valuenow`), done, skipped/cached, error (danger ring plus message chip), bypassed, unavailable (lock). Done/error/cancel go in an `aria-live="polite"` summary. |
| Collapsed nodes | Alt+C toggles collapse; `flags.collapsed` in JSON **[V]** | Collapse button in the node header plus Alt+C. Collapsed = header only, ports stacked on the edges. Stored in the layout. |
| Groups / frames | Ctrl+G adds a group around the selection. JSON `groups[{title,bounding,color,font_size,locked}]` **[V]** | v1 optional. In React Flow use a `group` node type and `parentId`. Keep groups in the layout. |
| Bypass / mute | Ctrl+B bypass, Ctrl+M mute; node `mode` field **[V]** | `mode: 'active' \| 'bypass' \| 'muted'` in the engine. Bypass means pass-through when an output type equals an input type (e.g. skip Segmentation). Mute means excluded. |
| Link styles | Link Render Mode: Straight, Linear, Spline (default), Hidden. Midpoint markers None/Circle/Arrow **[V]** | Preference `linkStyle: 'bezier' \| 'smoothstep' \| 'straight'` mapped to React Flow `connectionLineType` and edge type. Animated dashes only while running, and not under reduced motion. |
| Add-node menu / search | Double-click canvas opens quick search **[V]**. Link release: no modifier opens the context menu, Shift opens the search box **[V]**. Right-click opens a hierarchical "Add Node" menu **[A]** | Double-click pane, `/`, or the toolbar "+" opens a searchable command palette (`role="dialog"` + `combobox`/`listbox`). Right-click (or long-press 500 ms on touch) opens the context menu. Dropping a dragged connection on empty canvas opens search **pre-filtered to compatible types** and auto-connects. |
| Save/load workflow JSON | Ctrl+S / Ctrl+O **[V]**. UI format v1.0: `version:1`, `state{lastNodeId,lastLinkId,…}`, `nodes[{id,type,pos,size,flags,order,mode,inputs[{name,type,link}],outputs[{name,type,links}],widgets_values,color,bgcolor}]`, `links[{id,origin_id,origin_slot,target_id,target_slot,type}]`, `groups`, `extra.ds{scale,offset}` **[V]** | Our own format (section 4.5). Copy the **key idea**: the *UI workflow* (layout, colours, groups, viewport) is separate from the *executable/API form*. ComfyUI's API format keeps only `{id: {class_type, inputs:{name: literal \| [nodeId, slot]}}}` and drops positions, colours and groups **[V]**. Export/import as a `.img3d-workflow.json` file, autosave to localStorage, and drag-drop a file onto the canvas. |
| Other shortcuts | Space+drag pan, `.` fit selection, Ctrl+A, copy/paste with or without links, Ctrl+Z/Y **[V]** | Ctrl+Z/Y (engine snapshots), Delete, Ctrl+A, `.` fit view, Ctrl+C/V. Avoid hijacking browser Ctrl+S/Ctrl+O unless the canvas has focus. |
| Node badges | Node ID badge / source badge settings **[V]** | Optional "availability" badge (e.g. "needs backend part-aware", "locked: tuning"). |

---

## 4. Pure TypeScript node engine (separate from rendering)

### 4.1 Layering

```
frontend/src/
  api/ , store/                    (existing: the shared state/API layer, unchanged)
  workflow/
    engine/                        PURE TS. No React/DOM/axios/@xyflow imports (enforce with an oxlint import rule or a CI grep)
      types.ts                     Graph, GraphNode, GraphEdge, PortType, NodeTypeDef, GraphIssue, Layout
      registry.ts                  createRegistry(defs) → lookup, palette(capabilities), compatible(from,to)
      validate.ts                  validateGraph(graph, registry, caps) → GraphIssue[]; canConnect(...)
      plan.ts                      topoOrder(graph) (Kahn) + cycle detection; buildPlan(...)
      compile.ts                   compile(plan, ctx) → { request, stageMap } (backend request draft)
      serialize.ts                 toJSON / fromJSON (versioned, migrations), toExecutable(graph)
      status.ts                    projectRunState(plan, job) → Record<nodeId, NodeRunState>
    nodes/                         data-only node definitions (one file per type) + index.ts
    workflowStore.ts               zustand: { graph, layout, history[], future[] } + actions
  renderers/node/                  React Flow adapter + components (lazy chunk)
```

### 4.2 Core types (TS-6/erasable-syntax compatible, plain data, React-Compiler friendly)

```ts
// workflow/engine/types.ts
export type PortType = 'image_set' | 'prompt' | 'segmentation' | 'mesh' | 'part_set'
export type NodeCategory = 'input' | 'analyze' | 'generate' | 'parts' | 'output' | 'refine'
export type NodeMode = 'active' | 'bypass' | 'muted'

export interface InputPortDef { readonly id: string; readonly label: string; readonly accepts: readonly PortType[]
  readonly required?: boolean; readonly multiple?: boolean }
export interface OutputPortDef { readonly id: string; readonly label: string; readonly type: PortType }

export interface NodeTypeDef<P extends Record<string, unknown> = Record<string, unknown>> {
  readonly type: string                 // namespaced, e.g. 'img3d.image_to_3d'
  readonly version: number              // bump + migrate() when params change
  readonly category: NodeCategory
  readonly title: string
  readonly inputs: readonly InputPortDef[]
  readonly outputs: readonly OutputPortDef[]
  readonly defaults: P
  readonly requires?: readonly string[] // capability flags from /system (e.g. 'part_aware', 'prompt_to_3d')
  readonly locked?: boolean             // 'refine' (tuning) nodes: visible as locked, never compiled, until approved
  readonly maxInstances?: number        // e.g. 1 Image-to-3D per graph while the backend runs one job per graph
  readonly validateParams?: (params: P) => readonly GraphIssue[]
  readonly migrate?: (params: unknown, fromVersion: number) => P
}

export interface GraphNode { readonly id: string; readonly type: string; readonly version: number
  readonly mode: NodeMode; readonly params: Readonly<Record<string, unknown>> }
export interface PortRef { readonly node: string; readonly port: string }
export interface GraphEdge { readonly id: string; readonly from: PortRef; readonly to: PortRef }
export interface Graph { readonly nodes: readonly GraphNode[]; readonly edges: readonly GraphEdge[] }

/** Layout/visual state is NOT part of the executable graph (ComfyUI UI-vs-API split). */
export interface Layout { readonly positions: Readonly<Record<string, { x: number; y: number }>>
  readonly collapsed: readonly string[]; readonly colors: Readonly<Record<string, string>>
  readonly groups: readonly { id: string; title: string; nodeIds: readonly string[]; color?: string }[]
  readonly viewport: { x: number; y: number; zoom: number } }

export type IssueCode = 'unknown_node_type' | 'type_mismatch' | 'missing_required_input' | 'multiple_sources'
  | 'cycle' | 'capability_missing' | 'node_locked' | 'too_many_instances' | 'no_sink' | 'invalid_param'
export interface GraphIssue { readonly severity: 'error' | 'warning'; readonly code: IssueCode
  readonly message: string; readonly nodeId?: string; readonly portId?: string; readonly edgeId?: string }
```

Rules:
- Use only plain objects and pure functions (`addNode(graph, …) → Graph`). Do not use classes that mutate.
  This keeps the React Compiler's memoization correct, makes undo/redo trivial (snapshot arrays) and makes
  serialization lossless.
- Parameter schemas stay small and hand-written (`kind: 'number'|'enum'|'boolean'|'text'|'seed'`, bounds). The
  inspector UI then renders widgets generically. Reuse `SAMPLER_BOUNDS` and the capabilities from
  `settingsStore.ts`/`/system` rather than duplicating bounds.

### 4.3 Initial node catalogue (maps the user's six stages)

| Node | Inputs (accepts) | Outputs | Params (examples) | Compiles to | Availability |
|---|---|---|---|---|---|
| **Input Image** | none | `images: image_set` | reference set id, primary image, slots (reuse referenceStore) | `reference_set_id`, `primary_image_id` | now |
| **Prompt** | none | `prompt: prompt` | text, negative text (optional), seed | `prompt` field (new contract) | `requires:['prompt_to_3d']` |
| **Segmentation** (2D, before reconstruction) | `source: image_set` (req) | `masks: segmentation` | targets: eyes/brows/lashes/hair/clothes/accessories/detachables, keep-regions: face/body | pipeline stage `segmentation` | `requires:['part_aware']` |
| **Image-to-3D** | `source: [image_set, prompt]` (req, exactly one), `masks: segmentation` (opt) | `mesh: mesh` | existing `GenerationSettings` (resolution, seed, polygon target, texture, advanced samplers) | today's `CreateJobRequest.settings` | now |
| **Part Extraction** (3D, after reconstruction) | `mesh: mesh` (req), `masks: segmentation` (opt) | `parts: part_set` | separate physical parts vs semantic regions, naming, pivot/attachment metadata | pipeline stage `part_extraction` | `requires:['part_aware']` |
| **Export** | `asset: [mesh, part_set]` (req) | none (sink) | format GLB (single scene with named nodes) / per-part GLBs / manifest JSON | `export` options | now (mesh); parts when `part_aware` |
| *(future)* Tune Part / Repair / Eye fix / Rig fix | `parts: part_set` | `parts: part_set` | none | none | `category:'refine', locked:true` until you approve |

The capability flag names (`prompt_to_3d`, `part_aware`) are **proposals**. They must be added to
`api-contract.md` §7.2 `/system` by the backend workstream, so the UI never hard-codes availability. Until a flag
exists:
- The node renders with a lock and an "unavailable" badge.
- It can still be placed and saved.
- Validation returns `capability_missing` (an error if the node is `active`; nothing if it is bypassed or muted).
- **Run then compiles exactly today's request, so the working pipeline is untouched.**

### 4.4 Validation, plan and compile

- `canConnect(graph, registry, from, to)` returns `{ ok: true } | { ok: false, code, message }`. It checks:
  - output type ∈ input `accepts`
  - the target port is not already connected unless `multiple`
  - no self-loop
  - **no cycle**: a DFS from `to.node` must not reach `from.node`; this is React Flow's documented "prevent cycles" approach
- React Flow's `isValidConnection` calls this, and so do touch tap-connect and the list view.
- `validateGraph` also checks:
  - required inputs
  - unknown types (keep them as "unknown" nodes; never drop data on import)
  - locked nodes
  - capabilities
  - `maxInstances`
  - at least one sink
  - per-node `validateParams`
  - Image-to-3D's "exactly one of image_set/prompt" rule
- `topoOrder` uses Kahn's algorithm with stable ordering (by node id) so the plan is deterministic. Leftover nodes
  are reported as a `cycle` issue listing the node ids.
- `compile(plan, ctx)` walks the plan, reducing each node's contribution into a **backend request draft**. It returns
  `stageMap: Record<JobStage, nodeId>`, for example `preprocessing → Input Image`, `conditioning…decoding →
  Image-to-3D` and `exporting/finalizing → Export`, plus future `segmentation`/`part_extraction` stages.
- Bypassed nodes are skipped. Muted nodes and their exclusive downstream are excluded.
- Execution stays server-side. The browser never runs node logic, so there is a single generation implementation
  for all UIs.

### 4.5 Serialization (versioned)

```json
{
  "schema": "img3d.workflow", "version": 1, "app": "img3d-frontend@<pkg version>",
  "graph": { "nodes": [{ "id": "n1", "type": "img3d.input_image", "version": 1, "mode": "active", "params": {} }],
             "edges": [{ "id": "e1", "from": { "node": "n1", "port": "images" }, "to": { "node": "n3", "port": "source" } }] },
  "layout": { "positions": { "n1": { "x": 0, "y": 0 } }, "collapsed": [], "colors": {}, "groups": [],
              "viewport": { "x": 0, "y": 0, "zoom": 1 } }
}
```

- `fromJSON` validates the shape, runs per-node `migrate()` and keeps unknown node types (marked).
- Do not embed secrets or tokens. Reference sets are ids. On import, a missing set produces a warning, not a crash.
- `toExecutable(graph)` produces the layout-free form. A future backend can store it in `job.json` so a History item
  can "Open in Node UI". That needs a contract change, so it is optional and comes later.
- Persist the working graph in `localStorage` under `img3d.workflow` (best-effort via `lib/storage.ts`). Provide
  Import/Export file buttons.

### 4.6 Tests (project rule: tests only under `test/`)

The engine uses erasable syntax only, so Node 22.17's type stripping can run it without a bundler:
`node --experimental-strip-types --test test/frontend/workflow_engine.test.mjs`. The test imports
`frontend/src/workflow/engine/index.ts` directly. Node 23.6+ strips types by default **[A]**.

Cover: canConnect matrix, cycle rejection, required inputs, capability gating, locked refine nodes, stable topo
order, compile of the default graph producing exactly today's `CreateJobRequest`, JSON round-trip and migrations.

---

## 5. Node UI rendering layer (React Flow adapter)

- **Adapter (`renderers/node/flowAdapter.ts`).** Derive `Node<PipelineNodeData,'pipeline'>[]` and `Edge[]` from
  `graph + layout + runState`.
  - Position changes from `onNodesChange` go to `layout` only (`applyNodeChanges` on a layout projection).
  - `onConnect` and `onEdgesChange`/`onNodesDelete` call engine actions.
  - `nodeTypes = { pipeline: PipelineNode, group: GroupNode }` is defined at **module scope** (React Flow warns otherwise).
  - Type everything with `Node<Data,'type'>`, `NodeProps<T>` and `useReactFlow<AppNode, AppEdge>()`.
- **PipelineNode component.** Contents:
  - header: category stripe, title, availability/lock badge, collapse button, status chip
  - inline progress bar (`role="progressbar"`, aria-valuenow from `job.progress.percent`)
  - a few key params; full params go in the Inspector panel (right side panel on desktop, bottom sheet on touch)
  - one `<Handle>` per port, carrying `data-port-type`, a colour token and a shape glyph
  - `aria-label`/`aria-describedby` on the node through `domAttributes`/`ariaRole`
  - interactive children get the `nodrag nopan` classes (and `nowheel` on scroll areas) so sliders do not drag or pan the canvas **[A: React Flow class conventions]**
- **Validation UI.**
  - `isValidConnection` → `canConnect`. The invalid connection line turns danger-coloured.
  - On drop over an incompatible target, a toast gives the reason.
  - Graph issues appear in a collapsible "Problems" list. Each item focuses its node.
- **Theme.** Import `@xyflow/react/dist/base.css` (structural only) inside the lazy chunk and map variables onto tokens:

  ```css
  .flow {
    --xy-node-background-color: var(--color-surface-2);
    --xy-node-border: 1px solid var(--color-border-strong);
    --xy-node-color: var(--color-text);
    --xy-node-boxshadow-selected: 0 0 0 2px var(--color-accent);
    --xy-edge-stroke: var(--color-border-strong);
    --xy-edge-stroke-selected: var(--color-accent);
    --xy-connectionline-stroke: var(--color-accent);
    --xy-handle-background-color: var(--color-surface-4);
    --xy-handle-border-color: var(--color-border-strong);
    --xy-minimap-background-color: var(--color-surface-1);
    --xy-controls-button-background-color: var(--color-surface-2);
    --xy-controls-button-background-color-hover: var(--color-surface-3);
    --xy-controls-button-color: var(--color-text-muted);
    --xy-controls-button-color-hover: var(--color-text);
    --xy-controls-button-border-color: var(--color-border);
    --xy-selection-background-color: var(--color-accent-soft);
    --xy-selection-border: 1px dashed var(--color-accent-border);
    --xy-background-pattern-dots-color: var(--color-surface-4);
  }
  ```

  The docs list each variable with a `-default` suffix. The un-suffixed name as the override hook is **[A]**; confirm
  in `dist/base.css` after install. Use `colorMode="dark"`.
- **Viewport.** `fitView`, `minZoom 0.25`, `maxZoom 2`, `<Background variant="dots">`, and `<Controls showInteractive={false}>`.
  The zoom buttons are the single-pointer alternative to pinch (WCAG 2.5.1). Use `<MiniMap pannable zoomable>`, hidden
  below 840 px or behind a toggle. `zoomOnDoubleClick={false}`, and a double-click on `.react-flow__pane` opens the
  add-node search, as in ComfyUI.
- **Touch config:**
  - keep `connectOnClick` (tap source handle, then tap target handle)
  - keep `zoomOnPinch` and `panOnDrag` (one finger pans)
  - `selectionOnDrag={false}`
  - `connectionRadius` 28–32 under coarse pointers
  - enlarge handle hit areas without changing visuals:

  ```css
  @media (pointer: coarse) {
    .flow .react-flow__handle { width: 14px; height: 14px; }
    .flow .react-flow__handle::before { content: ''; position: absolute; inset: -15px; } /* ≈44px hit box */
  }
  ```

  - long-press (500 ms, cancel on >8 px move) on the pane opens the add-node menu; on a node it opens the node menu
  - a floating "+" FAB is always available
- **Keyboard.** Keep React Flow's built-in model and add:
  - Ctrl/Cmd+Enter Run, `/` or Ctrl+K add-node search, Alt+C collapse, Ctrl+B bypass, Ctrl+M mute
  - Ctrl+Z/Y undo/redo, `.` fit view, Delete/Backspace remove (React Flow's `deleteKeyCode` default is `'Backspace'`)
  - Shortcuts are scoped to the canvas so they do not fire inside text inputs
  - Set `ariaLabelConfig` strings
- **Accessible and touch alternative: "List view" toggle.** Render the same engine graph as an ordered stepper (topological
  order). Each node is a card with its params and input selectors (`<select>` of compatible upstream outputs) to
  connect without dragging. This satisfies WCAG 2.5.7 Dragging Movements (moving/connecting without drag). It is
  also the **default Node UI presentation on compact widths (< 600 px)**, where canvas editing is impractical. The
  canvas stays available.
- **Run state.** A `useWorkflowRunState()` selector combines `jobStore` (active job), the last compiled `stageMap`
  and `projectRunState`. Edges from completed nodes get a subtle success colour. The running node's outgoing edges
  are `animated`, disabled under reduced motion.
- **Code splitting.**
  `const NodeRenderer = lazy(() => import('./renderers/node/NodeRenderer'))` inside `<Suspense>` with a skeleton
  canvas. The xyflow CSS is imported only in that module.
- **Compiler check.** Run `test/frontend/review/compiler_check.mjs` on the new folders. If a component bails out or
  misbehaves, opt it out with the `'use no memo'` directive **[A]** and record why.

---

## 6. (B) Mobile UI (app-style)

### 6.1 Structure

- **Floating bottom nav, 4 destinations plus a centre-docked Generate FAB:**
  `Create | Viewer | [ Generate ] | Parts | Library`
  - Create covers inputs: Image/Prompt toggle, slots, prompt text.
  - Parts holds the part list, regions and visibility once part-aware data exists. Until then it shows the model stats card.
  - Library is History.
  - Settings, including **UI style**, opens from a gear in the compact top app bar and from Library. Settings is
    infrequent, so the top corner is acceptable.
- **Why centre:** in Hoober's 2013 field study of 1,333 people (780 touching the screen), 49% used one hand, 36% cradled and 15% used two hands. Two-thirds of one-handed grips were right-handed **[V, secondary]**.
  - Bottom-centre is reachable for both hands and is where users prefer to bring content.
  - Material and Apple both place top-level navigation at the bottom on phones.
- **Generate FAB.**
  - 56–64 px, raised above the nav, with the accent fill.
  - The label "Generate" is visible beside or below the icon, or as `aria-label`.
  - **States:** ready, submitting (spinner), running (progress ring showing `percent` plus a cancel affordance; tap opens the Progress sheet with Cancel), blocked.
  - **Blocked state:** use `aria-disabled="true"` instead of `disabled`, so the button stays focusable and tapping
    it opens a sheet explaining `reason` and `engineProblem` (from `useGenerateAvailability`).
  - After submit, auto-switch to Viewer, as `focusViewerOnSubmit` does today.
- **Prompt vs Image:** a shared `InputSourcePanel` with a `SegmentedControl` `[Image | Prompt]`.
  - Image mode reuses `ReferencePanel`.
  - Prompt mode uses a textarea with `enterkeyhint="go"` and a character count.
  - The input mode is stored in a shared store, so all four UIs see the same choice.

### 6.2 Floating bottom nav (CSS, no UI kit, uses the existing tokens)

```css
:root { --floating-nav-height: 64px; --fab-size: 60px; --floating-gap: var(--space-3); }

.floatingNav {
  position: fixed; z-index: var(--z-header);
  left:  max(var(--floating-gap), env(safe-area-inset-left));
  right: max(var(--floating-gap), env(safe-area-inset-right));
  bottom: calc(var(--floating-gap) + env(safe-area-inset-bottom));
  max-width: 560px; margin-inline: auto;            /* centred on wide phones / tablet portrait */
  height: var(--floating-nav-height);
  display: grid; grid-template-columns: 1fr 1fr var(--fab-size) 1fr 1fr; align-items: center;
  padding-inline: var(--space-2);
  border: 1px solid var(--color-border-strong); border-radius: 22px;
  background: color-mix(in srgb, var(--color-surface-2) 86%, transparent);
  backdrop-filter: blur(16px) saturate(1.3);
  box-shadow: var(--shadow-lg);
}
@supports not (backdrop-filter: blur(1px)) { .floatingNav { background: var(--color-surface-2); } }

.navItem { min-width: 48px; min-height: 48px; display: grid; place-items: center; gap: 2px;
  border: 0; border-radius: var(--radius-md); background: transparent; color: var(--color-text-muted);
  font-size: var(--text-2xs); transition: color var(--duration-fast) ease, background-color var(--duration-fast) ease; }
.navItem[aria-selected='true'] { color: var(--color-text); background: var(--color-accent-soft); }
.fab { width: var(--fab-size); height: var(--fab-size); border-radius: 50%; transform: translateY(-14px);
  background: var(--color-accent-active); color: #fff; box-shadow: var(--shadow-lg); }

/* Content must never hide behind the nav (WCAG 2.4.11 Focus Not Obscured) */
.screen { padding-bottom: calc(var(--floating-nav-height) + 2 * var(--floating-gap) + env(safe-area-inset-bottom));
  scroll-padding-bottom: calc(var(--floating-nav-height) + 2 * var(--floating-gap) + env(safe-area-inset-bottom)); }
```

- Keep the **WAI-ARIA tabs** semantics and roving focus from `MobileTabBar.tsx`. The FAB is a separate `<button>`
  outside the tablist.
- Optional: hide the nav on scroll-down (iOS 26 tab bars minimise on scroll **[A, secondary]**). Never hide it while
  focus is inside the nav or a sheet is open, and never under reduced motion.
- Keyboard visible: by default Chrome ≥108 and Safari only resize the *visual* viewport **[V]**, so a fixed bottom
  nav would sit under the keyboard. Hide the floating nav while an editable element is focused (`focusin`/`focusout`),
  or use `visualViewport` resize events to detect the keyboard. Alternatively, opt into
  `interactive-widget=resizes-content` in the viewport meta: Chromium only, it shrinks `dvh`, and it must be tested
  with the R3F canvas **[V]**.

### 6.3 Bottom sheets / drawers

| | Modal sheet (settings, parts detail, export, UI-style picker) | Standard sheet (persistent panel over the viewer) |
|---|---|---|
| Element | native `<dialog>` + `showModal()` (reuse `Dialog.tsx` with a `variant="sheet"`) | `<section role="region" aria-label="…">` |
| Background | scrim (`::backdrop` with `--color-overlay`), page inert (top layer) | no scrim; the viewer stays interactive |
| Focus | contained by the modal dialog. Focus returns to the opener on close (spec behaviour, test on Safari **[A]**) | no trap. The handle toggles `aria-expanded` |
| Dismiss | Escape/back gesture (`cancel` event), backdrop tap (manual, as `Dialog.tsx` does; `closedby="any"` is Chrome/Edge 134+ and Firefox 141+, but **not Safari** **[V]**, so keep the JS fallback), close button, drag down | collapse to peek; never fully hidden |
| Snap points | `[0.5, 0.92]` of viewport height | `[peek 96px, 0.5, 0.92]` |

Implementation, about 150 LOC and no library:
- **Why no library.** Vaul 1.1.2 is MIT and supports React 19, but its README says "This repo is unmaintained" **[V]**
  and it pulls Radix Dialog (18.5 kB gz). `react-modal-sheet` 5.6.0 needs `motion` (≥11).
- **Drag on the handle only.** Attach Pointer Events to the drag handle and header only, with `touch-action: none` there.
  The sheet body keeps native scrolling (`overflow-y: auto; overscroll-behavior: contain`).
  - Start a drag from the body only when `scrollTop === 0` and the gesture is downward (Vaul's behaviour).
  - Use `setPointerCapture`. Write `transform: translateY()` straight to a ref'd element during the drag; do not set
    React state per frame. This is compiler-safe because it mutates a ref inside an event handler.
  - On release, project the velocity (`y + v × 200 ms`) and snap to the nearest snap point. Animate with
    `transition: transform var(--duration-slow) var(--ease-out)`. The tokens zero this under reduced motion, so
    snapping becomes instant.
  - `@use-gesture/react` 10.3.1 is already installed (via drei) **[V]** and is MIT. You may use it for velocity and
    rubber-banding instead of the hand-written logic.
- **Drag handle a11y.** Use a real `<button>` at least 48 × 48 px whose visual pill is 36 × 4 px **[A]**. Its label
  reads "Resize panel, currently half height".
  - Enter/Space cycles the snap points, ArrowUp/ArrowDown move up or down one snap, Escape closes a modal sheet.
  - This mirrors Material's handle: "has a default min width and height of 48dp", supports "tapping to cycle through
    expanded and collapsed states", and responds to accessibility actions **[V]**.
  - It also provides the single-pointer, no-drag alternative required by WCAG 2.5.7.
- **Entry/exit animation.** Use `@starting-style` plus `transition: … overlay … allow-discrete, display …
  allow-discrete`. `@starting-style` is Baseline 2024 **[V]**. It degrades to no animation.
- **Scroll locking.** `body` is already `overflow:hidden` and the screens scroll internally, so only add
  `overscroll-behavior: contain` on sheet bodies to stop scroll chaining. No body position hacks are needed.
- **Width and insets.** On tablet and desktop use `max-width: 640px` centred (Material's default sheet max width,
  640dp **[V]**). Add `padding-bottom: env(safe-area-inset-bottom)`.
- **Viewer gestures.** Keep the R3F canvas `touch-action: none` (OrbitControls) and make sure sheet handles stop
  propagation. Pause the viewer render loop when the Viewer screen is hidden. `ViewerPanel` already takes an
  `active` prop (`AppShell.tsx:130`) **[V]**.

### 6.4 Touch targets and density

- **Standards.**
  - WCAG 2.2 SC 2.5.8 (AA) requires ≥ 24 × 24 CSS px, with spacing exceptions **[V]**.
  - SC 2.5.5 (AAA) asks for 44 × 44 **[A]**.
  - Apple HIG: 44 × 44 pt **[A, consistent secondary sources]**.
  - Material: 48 × 48 dp **[V, from the bottom sheet handle spec]**.
- **Target.** 48 px on Mobile and Tablet modes, and ≥ 44 px for any control under `(pointer: coarse)` in Default mode.
- **Implementation.**
  - Add size tokens: `--control-h-sm: 28px; --control-h-md: 34px; --control-h-lg: 44px; --hit-min: 24px`.
  - Override them under `[data-density='touch']`: 40/44/52 and `--hit-min: 44px`.
  - Set `data-density` on `<html>` from the preference: `auto` resolves via `matchMedia('(pointer: coarse)')`; Mobile and Tablet modes force `touch`.
  - Change `Button.module.css`, `SegmentedControl`, `Switch` (label row ≥ 44 px) and `RangeSlider` (thumb hit area
    via `::before`) to read the tokens. This changes no markup and keeps the Default look on desktop.
- Do not UA-sniff for touch. iPadOS Safari reports a Mac UA **[A]**. Use the `pointer`/`any-pointer`/`hover` media queries.

### 6.5 Safe areas and viewport

- `viewport-fit=cover` is already set **[V]**. Use `env(safe-area-inset-*)` for fixed bars, the FAB, the rail and
  sheets, always with fallbacks (`env(safe-area-inset-bottom, 0px)`).
- Use `100dvh` for full-height shells, as the app already does.
- `@media (orientation)` reflects the **viewport**, not the device. "Opening the soft keyboard on many devices in
  portrait orientation will cause the viewport to become wider than it is tall" **[V MDN]**. See the guard in §7.3.

---

## 7. Tablet UI

### 7.1 Postures

| Condition | Navigation | Content arrangement |
|---|---|---|
| `width ≥ 840px` **and** landscape (Material "expanded" width; covers about 97% of tablets in landscape **[V]**) | **Floating left nav rail**, 80 px wide, vertically centred, glass card. Destinations: Create, Viewer, Parts, Library, then a separator, Settings. **Generate** sits at the rail's bottom group (left-thumb reach) and is mirrored by the existing `GenerateCard` in the side panel footer | **Split view.** The side panel (320–400 px, collapsible) holds Create/Settings/Parts for the selected destination. The viewer fills the rest. Library opens as a side sheet. |
| otherwise (portrait, or landscape below 840 px, e.g. split screen or Stage Manager) | **Floating bottom nav**, the same component as mobile, centred with `max-width: 560px` | Viewer on top (about 55% height). Panels sit in a **standard bottom sheet** with snap points `[peek, 0.5, 0.9]`. Portrait tablets have "expanded" height (≥ 900dp) **[V]**, so a half sheet still leaves a usable viewer. |
| width < 600 px inside Tablet mode (e.g. narrow split screen) | bottom nav | single column, like Mobile |

- Material 3 Expressive (2025) deprecates the navigation drawer in favour of an expanded navigation rail **[V, 9to5Google]**.
- It replaces the bottom bar with a shorter "flexible navigation bar" (80dp → 64dp) **[V]**.
- Hence the 64 px floating bottom nav and the 80 px rail.
- An optional expanded rail (labels beside icons, about 220 px) on ≥ 1200 px widths.

### 7.2 Rail CSS sketch

```css
.rail { position: fixed; z-index: var(--z-header);
  left: calc(var(--floating-gap) + env(safe-area-inset-left)); top: 50%; translate: 0 -50%;
  width: 80px; padding: var(--space-3) 0; display: flex; flex-direction: column; align-items: center; gap: var(--space-2);
  border: 1px solid var(--color-border-strong); border-radius: 28px;
  background: color-mix(in srgb, var(--color-surface-2) 86%, transparent); backdrop-filter: blur(16px);
  box-shadow: var(--shadow-lg); max-height: calc(100dvh - 2 * var(--floating-gap)); overflow-y: auto; }
.railItem { width: 56px; min-height: 56px; border-radius: 16px; display: grid; place-items: center; font-size: var(--text-2xs); }
.tabletContent { padding-left: calc(80px + 2 * var(--floating-gap) + env(safe-area-inset-left)); height: 100dvh;
  display: grid; grid-template-columns: minmax(300px, 380px) minmax(0, 1fr); gap: var(--layout-gap); }
```

ARIA: the rail is `<nav aria-label="Sections">` with the same tabs semantics and **ArrowUp/ArrowDown** roving focus.
The mobile nav uses ArrowLeft/Right. Generate is a separate button.

### 7.3 Automatic switching (no duplicated logic)

```ts
// hooks/useNavPosture.ts — same pattern as useBreakpoint.ts
export type NavPosture = 'rail' | 'bottom' | 'compact'
const RAIL = '(min-width: 840px) and (orientation: landscape)'
const COMPACT = '(max-width: 599.98px)'
let stable: NavPosture | null = null
const isTyping = () => { const el = document.activeElement
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || (el instanceof HTMLElement && el.isContentEditable) }
function read(): NavPosture {
  const next: NavPosture = matchMedia(COMPACT).matches ? 'compact' : matchMedia(RAIL).matches ? 'rail' : 'bottom'
  if (stable === null || !isTyping()) stable = next     // soft-keyboard guard: don't flip layout mid-typing
  return stable
}
function subscribe(cb: () => void) {
  const mqs = [matchMedia(RAIL), matchMedia(COMPACT)]
  mqs.forEach((m) => m.addEventListener('change', cb)); document.addEventListener('focusout', cb)
  screen.orientation?.addEventListener('change', cb)
  return () => { mqs.forEach((m) => m.removeEventListener('change', cb)); document.removeEventListener('focusout', cb)
    screen.orientation?.removeEventListener('change', cb) }
}
export const useNavPosture = () => useSyncExternalStore(subscribe, read, () => 'rail' as const)
```

- Inside panels, use **container queries**, not viewport queries. Declare `container: panel / inline-size` on the
  side panel and sheet bodies, then write `@container panel (min-width: 420px) { … }`. This lets the same
  `ReferencePanel`, `SettingsPanel` and Parts list adapt whether they sit in a 360 px rail side panel, a full-width
  sheet or the desktop column, so components are written once. Size container queries are Baseline 2023 **[A]**.
  Style queries only support custom properties **[V MDN]**.
- Optional polish: same-document **View Transitions** (`document.startViewTransition`) are Baseline as of Firefox 144
  (Oct 2025; Chrome/Edge 111, Safari 18) **[V]**. Use them for posture changes and tab switches, and skip them under
  reduced motion.

---

## 8. Shared architecture for the four UI modes

```
Backend API (FastAPI /api/v1, WS /api/v1/ws)
  → src/api/*            (axios client, ws RealtimeClient; unchanged)
  → src/store/*          (jobStore, referenceStore, settingsStore, systemStore, uiStore, toastStore …)
  → src/actions/*        NEW thin command layer: generate(), cancel(), openHistory(), setInputMode(), exportModel()
                         (wraps store actions; one Ctrl/Cmd+Enter registration; used by every renderer)
  → src/workflow/*       NEW pure engine + workflowStore (Node UI; Default/Mobile/Tablet use the implicit default graph)
  → src/ui-modes/*       NEW preferencesStore + RendererHost + UiStylePicker
  → renderers: default (existing AppShell) | node (lazy, React Flow) | mobile (lazy) | tablet (lazy)
  → src/components/*     existing panels reused by all renderers (ReferencePanel, SettingsPanel, ViewerPanel, ProgressCard, ModelStatsCard, HistoryList…)
```

- **Preference store.** Zustand `persist`, key `img3d.preferences`, version 1, guarded by `lib/storage.ts` semantics:

  ```ts
  { uiMode: 'default' | 'node' | 'mobile' | 'tablet', density: 'auto' | 'comfortable' | 'touch',
    node: { linkStyle: 'bezier' | 'smoothstep' | 'straight', minimap: boolean, snapToGrid: boolean, view: 'canvas' | 'list' } }
  ```

  - The default `uiMode` is `'default'`, as the user requires.
  - Cross-tab sync: listen to `storage` events and call `usePreferencesStore.persist.rehydrate()`.
  - Apply `data-ui-mode` and `data-density` to `<html>` **synchronously in `main.tsx` before `createRoot`**. This
    avoids a flash without an inline script.
- **Escape hatches.** `?ui=default` (or `?ui=node|mobile|tablet`) overrides the mode for that session. Every
  renderer exposes Settings → "UI style":
  - Default: a header gear next to History in `Header.tsx`
  - Node: a toolbar gear
  - Mobile: the top-bar gear and Library
  - Tablet: the rail's bottom group
  - The picker is a `radiogroup` (reuse `SegmentedControl` or radio cards with previews).
- **No backend logic in renderers.** Renderers may only read stores and call `actions/*`. They do not import
  `api/*` directly; enforce this with an oxlint `no-restricted-imports`-style rule or a test grep.
  - `jobStore.submit()` is split into `buildCreateJobRequest()` (pure, from stores) and `submitJob(request)`.
  - Default, Mobile and Tablet call `generate()`, which builds the request from the stores.
  - Node calls `generate(compile(graph).request)`.
  - Both paths share `submitJob`, toasts, error mapping and `activeJobId` handling.
- **Header/BackendBanner/Toaster/BlenderSetupDialog** stay global in `App.tsx`. Each renderer decides whether to show
  the full `Header` or a compact top bar. Mobile/Tablet use a compact bar with `EngineStatus compact` and
  `ConnectionIndicator`.
- **Mesh tuner UI stays as is and disabled** per the milestone. `TuningCard` remains in the Default renderer.
  New renderers do not surface tuning, and the engine's `refine` category is `locked`.

---

## 9. Default UI: concrete polish list (no redesign)

1. **Contrast (verified by computation from `tokens.css`).**
   - `--color-text-subtle #6d7588` measures 4.21 on bg, 3.99 on surface-1, 3.49 on surface-3 and 3.11 on surface-4,
     so it **fails 4.5:1**. Proposed `#8a92a6`: 6.24 / 5.92 / 5.17 / 4.61. Mobile tab labels use it at 11 px.
   - **White on `--color-accent` measures 3.69**, so the primary "Generate 3D model" button fails AA. Use `#7a58f5`
     (the existing `--color-accent-active`) as the primary background: 4.62. Use `#6d4ae8` for the active state
     (5.53), and keep `#8b6cff` for focus rings and accents on dark (5.26 on bg).
   - `--color-border` is 1.25:1 on surface-1. That is fine for decorative dividers, but **input boundaries** that
     identify a control need 3:1 (SC 1.4.11). `#5b6580` gives 3.17.
2. **Touch density tokens** as in §6.4, applied under `(pointer: coarse)` in Default mode.
3. **Reduced motion in JS.** `AppShell.tsx:25` and `:41` call `scrollIntoView({ behavior: 'smooth' })`. Pick
   `'auto'` when `matchMedia('(prefers-reduced-motion: reduce)')` matches.
4. **History drawer.** Make it a real modal: native `<dialog>` side-sheet variant, or keep the aside and set `inert`
   on `#main-content` while it is open. Today focus can Tab out behind the backdrop (`HistoryDrawer.tsx:18-31`).
5. **Loading states.** Use skeletons, not spinners, for reference thumbnails, history items and the viewer's first
   load (a shimmer using `--color-surface-3/4`, static under reduced motion). Show "Connecting…" inline in panels
   rather than a full splash once the first session exists.
6. **Transitions.** Use one shared enter animation (`img3d-fade-in` plus a 4 px translate) via `@starting-style` for
   panels, cards and toasts. Animate the drawer, sheets and dialog exit with `allow-discrete`. Use only
   `--duration-*` and `--ease-out`.
7. **Spacing rhythm.**
   - Unify panel paddings to `--space-4` on desktop and `--space-3` below 768 px.
   - Add `scroll-padding-bottom` to scroll areas that sit above the sticky GenerateCard bar on mobile.
   - Use `scrollbar-gutter: stable` on `.scrollArea`/`.scrollColumn` so content doesn't jump when the scrollbar appears.
8. **Tablet layout (768–1279 px) responsiveness.** `.tabletViewer { height: min(62vh, 640px); min-height: 340px }`
   is fine. Make the left column collapsible on 768–900 px, because the fixed `minmax(300px, 380px)` leaves about
   390 px for the viewer.
9. **`background-attachment: fixed`** on `body` (`global.css:25`) is ignored or janky on iOS **[A]**. Drop it on
   coarse pointers.
10. **Settings entry point.** Add a gear in `Header.tsx` that opens the Preferences dialog (UI style, density, Node UI
    options). The generation `SettingsPanel` stays where it is.

---

## 10. Accessibility requirements (acceptance checklist)

1. **Keyboard.** Every function is reachable by keyboard (2.1.1), including Node UI via React Flow's focus model plus
   List view. There are no keyboard traps except intentional modal dialogs, which close with Escape (2.1.2).
2. **Focus visible** (2.4.7). Keep the global `:focus-visible` outline (`global.css:84-87`). Rings need ≥ 3:1 against
   adjacent colours (1.4.11); the `#8b6cff` ring on bg is 5.26.
3. **Focus Not Obscured, minimum** (2.4.11, AA, WCAG 2.2). Floating nav, rail, FAB and peek sheets must not cover the
   focused element. Use `scroll-padding-*` and content padding equal to the bar heights plus safe areas.
4. **Target size.** At least 24 px everywhere (2.5.8). 48 px in Mobile/Tablet modes and 44 px under coarse pointers.
   Enlarge React Flow handle hit areas.
5. **Dragging Movements** (2.5.7). Every drag interaction has a single-pointer alternative:
   - sheet handle: tap or arrow keys
   - node connect: tap-tap (`connectOnClick`) or List view selects
   - node move: arrow keys or "Tidy up" auto-layout
   - pinch zoom: zoom buttons (2.5.1 Pointer Gestures)
6. **Use of Color** (1.4.1). Port types carry shape plus text labels. Node status uses an icon and text, not only
   colour. Validation errors use text.
7. **Contrast.** Text ≥ 4.5:1 and large text ≥ 3:1 (1.4.3). UI components and graphical objects ≥ 3:1 (1.4.11).
   Fix the tokens per §9.1.
8. **Name, Role, Value** (4.1.2).
   - nav: `<nav>` with tablist/tab and `aria-selected`
   - FAB: `aria-label`, plus `aria-disabled` and `aria-describedby` when blocked
   - sheets: `<dialog aria-labelledby>`, or a region with a handle `aria-expanded`
   - nodes: `role="group"` with `aria-label` "Image-to-3D, running 45%"
   - progress bars: `role="progressbar"` with `aria-valuenow`
9. **Status messages** (4.1.3). Job start, done, error and validation summaries go to an `aria-live="polite"`
   region. React Flow's assertive move announcements stay as is.
10. **Reduced motion** (2.3.3 AAA, plus the `prefers-reduced-motion` media query).
    - All CSS motion uses `--duration-*` tokens, which are already zeroed.
    - JS smooth scrolling, View Transitions, animated edges, FAB progress ring spin and the sheet spring respect the
      preference.
    - Nothing auto-plays longer than 5 s without a pause; the viewer's `autoRotate` is off by default and toggleable (`uiStore.ts:24`).
11. **Orientation** (1.3.4). Never lock orientation. Tablet mode supports both orientations.
12. **Reflow** (1.4.10). Every mode works at 320 CSS px width without 2-D scrolling. The Node canvas is exempt as
    "content requiring two-dimensional layout", but List view must reflow.
13. **Consistent Navigation/Identification** (3.2.3/3.2.4). The same destinations use the same labels and icons
    across Mobile, Tablet and Default (Create, Viewer, Parts, Library, Settings).
14. **Language and labels.** Prompt textarea has a visible `<label>`. Error messages explain how to fix (3.3.1/3.3.3),
    reusing `engineProblem.details`.
15. **Screen-reader parity for Node UI.** List view is the canonical accessible representation of the graph. The
    canvas `aria-describedby` points to a "Graph summary" (N nodes, issues, running node).

---

## 11. Risks and open questions

- **Backend capability flags.** Segmentation, Part Extraction, the Prompt node and part-aware Export depend on new
  `/system` capability flags and new request fields (`prompt`, pipeline stages, part manifest URLs). Those belong to
  the backend workstream and `api-contract.md`. The UI must gate on them, and **Run must compile exactly today's
  request when they are absent.**
- **One job per graph.** The backend runs one job at a time, so v1 Node UI should allow one Image-to-3D per graph
  (`maxInstances: 1`) and reject branching into multiple generations. Revisit when the backend supports multi-stage
  jobs.
- **Bundle.** React Flow adds about 60 kB gz. It must stay in a lazy chunk; check with `vite build` plus a chunk-size
  budget. Watch the nested zustand v4 and the `vendor` regex.
- **Touch editing of graphs on phones** is inherently cramped, even in ComfyUI. Hence List view as the default on
  compact widths.
- **Keyboard-driven layout flips.** Firefox Android may default to `resizes-content` **[V, dated comment]**,
  flipping `orientation`. Mitigated by the typing guard in §7.3.
- **`<dialog>` details.** `closedby` is not in Safari **[V]**, so keep manual light-dismiss. Verify focus-restore
  on iPadOS.
- **React Flow attribution policy.** Decide: keep it (themed) or subscribe to Pro.
- **Licensing.** All recommended libraries are MIT; `@use-gesture` is MIT and already present. Update
  `THIRD_PARTY_NOTICES.md` when adding `@xyflow/react` (plus d3-zoom/drag/selection/interpolate (ISC), classcat (MIT)
  and zustand v4 (MIT)). The d3 ISC licence is **[A]**; check the installed packages.

---

## 12. Sources

Libraries and registries:
- npm registry metadata via `npm view` (2026-10-16): [@xyflow/react](https://www.npmjs.com/package/@xyflow/react), [@xyflow/system](https://www.npmjs.com/package/@xyflow/system), [rete](https://www.npmjs.com/package/rete), [rete-react-plugin](https://www.npmjs.com/package/rete-react-plugin), [rete-area-plugin](https://www.npmjs.com/package/rete-area-plugin), [rete-connection-plugin](https://www.npmjs.com/package/rete-connection-plugin), [rete-minimap-plugin](https://www.npmjs.com/package/rete-minimap-plugin), [rete-engine](https://www.npmjs.com/package/rete-engine), [litegraph.js](https://www.npmjs.com/package/litegraph.js), [@comfyorg/litegraph](https://www.npmjs.com/package/@comfyorg/litegraph), [vaul](https://www.npmjs.com/package/vaul), [react-modal-sheet](https://www.npmjs.com/package/react-modal-sheet), [@use-gesture/react](https://www.npmjs.com/package/@use-gesture/react), [focus-trap-react](https://www.npmjs.com/package/focus-trap-react), [flume](https://www.npmjs.com/package/flume), [@projectstorm/react-diagrams](https://www.npmjs.com/package/@projectstorm/react-diagrams), [reaflow](https://www.npmjs.com/package/reaflow), [elkjs](https://www.npmjs.com/package/elkjs), [@dagrejs/dagre](https://www.npmjs.com/package/@dagrejs/dagre)
- Bundle sizes: [Bundlephobia API](https://bundlephobia.com/package/@xyflow/react@12.12.0) (also rete*, litegraph*, styled-components@6, vaul)

React Flow:
- [Accessibility](https://reactflow.dev/learn/advanced-use/accessibility)
- [Theming](https://reactflow.dev/learn/customization/theming)
- [ReactFlow props](https://reactflow.dev/api-reference/react-flow)
- [Touch device example](https://reactflow.dev/examples/interaction/touch-device)
- [v11.5 touch connections](https://reactflow.dev/blog/react-flow-v-11-5)
- [Performance](https://reactflow.dev/learn/advanced-use/performance)
- [TypeScript](https://reactflow.dev/learn/advanced-use/typescript)
- [Common errors](https://reactflow.dev/learn/troubleshooting/common-errors)
- [Remove attribution](https://reactflow.dev/learn/troubleshooting/remove-attribution)
- [12.4 useNodeConnections](https://reactflow.dev/whats-new/2025-01-15)
- [v12 release](https://reactflow.dev/whats-new/2024-07-09)

Rete.js:
- [React renderer](https://retejs.org/docs/guides/renderers/react)
- [Validation](https://retejs.org/docs/guides/validation)
- [Editor concepts](https://retejs.org/docs/concepts/editor)
- [area-plugin zoom.ts](https://github.com/retejs/area-plugin/blob/main/src/zoom.ts)

litegraph:
- [Comfy-Org/litegraph.js (archived)](https://github.com/Comfy-Org/litegraph.js)
- [jagenjo/litegraph.js](https://github.com/jagenjo/litegraph.js)

ComfyUI:
- [Shortcuts](https://docs.comfy.org/interface/shortcuts)
- [Lite Graph settings](https://docs.comfy.org/interface/settings/lite-graph)
- [Links / data types](https://docs.comfy.org/development/core-concepts/links)
- [Workflow JSON 1.0 spec](https://docs.comfy.org/specs/workflow_json)
- [Workflow JSON 0.4](https://docs.comfy.org/specs/workflow_json_0.4)
- [API format](https://docs.comfy.org/development/api-development/workflow-api-format)
- [Server messages](https://docs.comfy.org/development/comfyui-server/comms_messages)
- [Nodes 2.0](https://docs.comfy.org/interface/nodes-2)

Mobile and tablet UX:
- [Vaul (unmaintained notice)](https://github.com/emilkowalski/vaul)
- [Material Components Android: BottomSheet](https://github.com/material-components/material-components-android/blob/master/docs/components/BottomSheet.md)
- [M3 Expressive navigation (9to5Google)](https://9to5google.com/2025/05/14/material-3-expressive-navigation/)
- [Android window size classes](https://developer.android.com/develop/ui/compose/layouts/adaptive/use-window-size-classes)
- Hoober thumb study: [UXmatters 2013 original](https://www.uxmatters.com/mt/archives/2013/02/how-do-users-really-hold-mobile-devices.php) **[A: not fetched]**, figures confirmed via secondary sources such as [A List Apart](https://alistapart.com/1-2-phone-grips-jpg/)
- Apple HIG [Accessibility (44 pt)](https://developer.apple.com/design/human-interface-guidelines/accessibility) **[A: not fetched]**

Web platform and accessibility:
- [WCAG 2.2 SC 2.5.8](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
- MDN [@media orientation](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/orientation), [env()](https://developer.mozilla.org/en-US/docs/Web/CSS/env), [@starting-style](https://developer.mozilla.org/en-US/docs/Web/CSS/@starting-style), [Container queries](https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_containment/Container_size_and_style_queries), [HTMLDialogElement.closedBy](https://developer.mozilla.org/docs/Web/API/HTMLDialogElement/closedBy)
- [caniuse: dialog closedby](https://caniuse.com/mdn-html_elements_dialog_closedby)
- [Chrome viewport resize behaviour (interactive-widget)](https://developer.chrome.com/blog/viewport-resize-behavior)
- [web.dev: View Transitions Baseline](https://web.dev/blog/same-document-view-transitions-are-now-baseline-newly-available)

Project files read (all under `E:/14-OCT-2026/Img3D/`):
- `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.app.json`, `frontend/index.html`
- `frontend/src/styles/{tokens,global}.css`, `frontend/src/App.tsx`, `frontend/src/main.tsx`
- `frontend/src/components/layout/{AppShell,MobileTabBar,Header}.tsx` (+ CSS)
- `frontend/src/hooks/useBreakpoint.ts`
- `frontend/src/store/{uiStore,settingsStore,jobStore}.ts`, `frontend/src/lib/{storage,stages}.ts`
- `frontend/src/components/common/{Dialog,Button,SegmentedControl}*`
- `frontend/src/components/history/HistoryDrawer*`, `frontend/src/components/generation/GenerateCard*`
- `frontend/src/api/{client,ws,types}.ts`
- `frontend/dist/assets/*` (sizes), `test/frontend/review/compiler_check.mjs`, `.claude/CLAUDE.md`
