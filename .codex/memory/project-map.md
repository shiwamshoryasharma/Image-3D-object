# Img3D project map

Source and directory inspection: 2026-10-29. Paths are project-relative.
Use this as a navigation index, not a replacement for the implementation.

## Top-level structure

~~~text
Img3D/
  .claude/                 Existing project memory, skills, designs and progress reports
  .codex/                  Codex entry point, skills and concise evidence-based memory
  .prompts/                Private original user prompts; do not publish
  backend/
    main.py                Sole application entry point
    api/                   REST routers, dependencies, upload routing, WebSocket hub
    services/              Application orchestration and API-facing behavior
    workers/               Job queues, GPU generation, run worker, Blender worker
    pipelines/             Image-to-3D and additive run executors
    inference/             Engine seam, TRELLIS adapter, lazy model providers
    parts/                 Torch-free geometry and semantic-part asset library
    processing/            Image conditioning, validation and 2D label processing
    storage/               JSON-backed stores, runtime and run layouts
    schemas/ models/       API shapes and domain records
    core/ config/ utils/   Composition, lifecycle, errors, events, settings
    launcher/              Standalone stdlib Blender runtime/process tools
    blender_scripts/       Python code executed inside Blender
    scripts/               Product maintenance/build/download/CLI helpers
    third_party/           Pinned upstream sources and build patches
    python/                Existing embedded Python installation
    .model/ .toolchain/    Local model weights and CUDA build tooling
    runtime/ logs/         Production/generated application data
  frontend/
    src/                   React application, API, state, workflows and UI shells
    node_modules/          Existing npm dependencies
    vite.config.ts         REST/WebSocket proxy and lazy-chunk configuration
  test/                    All tests, probes, test config and generated testing artifacts
  docs/assets/             Existing documentation assets
  README.md                Public overview; roadmap has older status wording
  CLAUDE.local.md           Private machine-local notes; do not copy into shared memory
~~~

## Backend execution path

Original generation:
api/router.py and api/v1 -> services -> job queue / GenerationWorker ->
pipelines/image_to_3d.py -> inference/base.py seam -> inference/trellis.

Composition: core/container.py::build_container wires long-lived services,
stores, workers and event publishers. core/part_aware_container.py::build_part_aware
adds providers, feature services and run execution.

Part-aware image generation, segmentation and analysis share workers/run_worker.py.
Run preparation/finalization leave the event loop; CPU phases use parts-cpu;
GPU phases use GenerationWorker.submit_gpu_task on the shared gpu-worker.
Keep cancellation and timeout handling coupled to completion of the actual
callable so temporary state is not removed while work still runs.

Workflow services compile and orchestrate child resources. Workflows do not
access CUDA directly. Resource prefixes are ig_, sg_, pa_ and wf_.

inference/providers contains catalog, lazy registry, unavailable-provider
handling, VRAM/offloading and vendor_import. Model implementations live under
inference/imagegen, inference/segmentation and inference/parts.
parts contains taxonomy, geometry, labeling, manifests, export, validation,
publication and asset reading. It must remain usable without torch or Blender.

API prefix: /api/v1. WebSocket endpoint: /api/v1/ws. Detailed payloads, event
envelopes and state transitions are in the existing API contract; inspect the
relevant router/schema alongside it before changing a boundary.

## Frontend execution path

src/main.tsx -> src/App.tsx -> app/PreSession or app/ModeSwitch, plus GlobalOverlays.
App owns the one useBackendSession call. Mode changes use app/shellRegistry.ts;
Default loads directly, Node/Mobile/Tablet use cached lazy loaders.

- api/: shared client, auth/error/URL handling, REST clients, types, WebSocket.
- lib/: pure helpers, preferences, part/viewer and workflow utilities.
- store/: Zustand state, including session/auth, preferences, parts and workflows.
- actions/ and hooks/: effects and orchestration shared by all shells.
- shells/{default,node,mobile,tablet}: presentation and mode-specific navigation.
- shells/shared/: common chrome, section/sheet hosts and GenerateAction.
- shells/node/canvas/: React Flow integration; isolate @xyflow/react here.
- workflow/engine and workflow/nodes: pure graph, validation, planning and compile.
- components/: common primitives, feature panels, reference inputs and 3D viewer.
- styles/: shared tokens and styles; component/shell CSS modules remain local.

vite.config.ts proxies /api (including WebSocket) to 127.0.0.1:8000 by default.
Dev defaults to port 5173, preview 4173, with strictPort. It listens on all
interfaces by default; set IMG3D_DEV_HOST=127.0.0.1 for isolated local testing.
Do not disable allowed-host checks or change firewall settings as a test shortcut.

## Test tracks

| Path | Purpose |
|---|---|
| test/backend | API/services/stores/runs/parts and import/protected-file guards |
| test/engine | Adapter unit/parity tests, runtime probes and real generation smoke |
| test/aux_models | Local auxiliary asset checks and targeted smoke scripts |
| test/launcher | Blender runtime/process/download behavior |
| test/mesh_tuner | Blender framework, rig, repair/refine tests and earlier probes |
| test/frontend | Unit tests, strict API mock, mock smoke and review scripts |
| test/part_aware | GPU lock, fixtures, parts/workflow real-weight smoke scripts |
| test/e2e | Client validation against a separately running backend |
| test/env_setup | Native extension verification and build probes |
| test/lan_check | LAN proxy checks when explicitly needed |
| test/images | Personal input images; preserve the existing ignore policy |
| test/outputs | Generated meshes, renders, reports and comparisons |
| test/.runtime and test/.logs | Isolated state, locks, build output and logs |

Existing developer probes can contain absolute machine paths; inspect before reuse.
test/pytest.ini defaults testpaths to backend, so select other suites explicitly.
test/conftest.py redirects runtime/logs and removes test dependence on real secrets.

## Detailed references

- [API and architecture contract](../../.claude/skills/img3d-architecture/api-contract.md)
- [Part-aware backend design](../../.claude/skills/img3d-part-aware/DESIGN.md)
- [UI design](../../.claude/skills/img3d-part-aware/UI_DESIGN.md)
- [Test guide](../../test/README.md)
- [Native build patches](../../backend/third_party/PATCHES.md)
