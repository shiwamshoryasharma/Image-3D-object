---
name: img3d-frontend
description: Modify Img3D React UI modes, Zustand state, part viewer, workflow editor or API integration while preserving its shared session, lazy chunks, protected tuner UI and frontend validation gates.
---

# Img3D frontend changes

Read [memory](../../MEMORY.md), then the relevant section of
[UI_DESIGN.md](../../../.claude/skills/img3d-part-aware/UI_DESIGN.md).
The Vite application already exists. Reuse its components, stores, styles and
API utilities.

## Shared state and mode boundaries

App.tsx owns the one useBackendSession call, pre-session UI, ModeSwitch and
GlobalOverlays. app/shellRegistry.ts owns cached lazy mode loading.
Default, Node, Mobile and Tablet are shells around shared actions/hooks/state;
do not duplicate backend sessions or business logic in each shell.

api/client.ts centralizes the API origin, bearer token, normalized errors and
resolveApiUrl. Keep request/response types in api and pure graph/state helpers
in lib or workflow/engine. Use existing actions/hooks for side effects and
Zustand stores for shared state. Follow the existing import-boundary rules.

@xyflow/react is pinned to 12.12.0 and belongs in shells/node/canvas integration.
Preserve lazy loading of Node mode, feature panels and the Three.js viewer.
React Compiler is enabled; verify diagnostics rather than adding speculative
memoization or disabling compiler checks.

## Preserve user-visible behavior

- Keep all seven reference slots and primary selection.
- Keep the existing Default UI and accessible generate/navigation behavior.
- Default mode's tuner card and Blender status stay visible but locked when
  features.mesh_tuning.enabled is false. Do not add tuner controls to other modes.
- Preserve the viewer's one-click Split into parts action and completion behavior.
- Use parts focus/select/isolate/explode and semantic-region APIs instead of
  inventing an independent geometry state.
- Preserve the node canvas SVG sizing override: global max-width rules previously
  collapsed React Flow edges. Inspect flow.module.css when debugging missing links.
- Preserve semantic HTML, keyboard access and main-content focus target when
  changing shell layouts.

## Validation

Use [the validation skill](../img3d-validation/SKILL.md) and its commands reference.
Relevant gates include strict TypeScript, oxlint, unit tests, compiler diagnostics,
import boundaries, protected tuner files, mock contract smoke and bundle budgets.

All verification build output, incremental TypeScript metadata, Vite caches,
screenshots and logs stay under test. A plain npm run build writes frontend/dist
and tsbuildinfo under node_modules; use the isolated commands for testing.

Mock API smoke tests verify contracts; they do not prove browser behavior or
real model generation. For visual/interactive changes, inspect the affected
modes in a real browser and state which backend (mock or real) was used.
