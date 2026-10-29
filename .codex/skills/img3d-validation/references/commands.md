# Img3D validation commands

Run PowerShell commands from the project root. Select the checks relevant to
the requested change; this is a reference, not an instruction to run every suite.

## Common test isolation

Use these variables in a dedicated test shell. They are process-local; do not
persist them to the user or machine environment.

~~~powershell
Set-Location -LiteralPath 'E:/14-OCT-2026/Img3D'
$projectRoot = (Get-Location).Path
$py = Join-Path $projectRoot 'backend/python/python.exe'
$testRoot = Join-Path $projectRoot 'test'
$testRuntime = Join-Path $testRoot '.runtime/codex'
$testBuild = Join-Path $testRoot '.runtime/frontend/codex'
$testCache = Join-Path $testRuntime 'cache'
New-Item -ItemType Directory -Force -Path $testRuntime,$testBuild,$testCache,(Join-Path $testRoot '.logs/codex'),(Join-Path $testRoot '.pytest_tmp/codex'),(Join-Path $testRoot '.hf_home/codex'),(Join-Path $testCache 'triton'),(Join-Path $testCache 'flex_gemm') | Out-Null
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:IMG3D_RUNTIME_DIR = $testRuntime
$env:IMG3D_LOG_DIR = Join-Path $testRoot '.logs/codex'
$env:IMG3D_HF_HOME = Join-Path $testRoot '.hf_home/codex'
$env:HF_HOME = $env:IMG3D_HF_HOME
$env:TRITON_CACHE_DIR = Join-Path $testCache 'triton'
$env:FLEX_GEMM_AUTOTUNE_CACHE_PATH = Join-Path $testCache 'flex_gemm/autotune_cache.json'
$env:TORCH_EXTENSIONS_DIR = Join-Path $testCache 'torch_extensions'
$env:CUDA_CACHE_PATH = Join-Path $testCache 'cuda'
$env:IMG3D_PRELOAD_MODEL = 'false'
$env:IMG3D_BLENDER_DETECT_ON_STARTUP = 'false'
$env:IMG3D_FRONTEND_DIST_DIR = Join-Path $testBuild 'dist'
~~~

Use -B for each Python process as well: embedded isolated mode can ignore Python
environment variables. This suppresses project bytecode outside test. Reuse the
existing weights; changing HF_HOME only isolates generated Hugging Face state.

## Pytest without a real GPU/model run

~~~powershell
& $py -s -B -m pytest -c test/pytest.ini test/backend test/aux_models test/launcher -m 'not gpu and not network and not blender' -q -p no:cacheprovider --basetemp test/.pytest_tmp/codex
& $py -s -B -m pytest -c test/pytest.ini test/engine -m 'not gpu' -q -p no:cacheprovider --basetemp test/.pytest_tmp/codex
~~~

For a narrow change, substitute the affected test file(s) for the suite paths.
Inspect marks and fixtures before running any test believed to be CPU-only.
test/pytest.ini defaults to test/backend, so specify other tracks explicitly.
test/conftest.py handles test bootstrap, fake providers, secret-independent
settings and test-only runtime setup.

Protected/import guard examples:

~~~powershell
& $py -s -B -m pytest -c test/pytest.ini test/backend/test_import_boundaries.py test/backend/test_protected_files.py test/backend/test_legacy_invariance.py -q -p no:cacheprovider --basetemp test/.pytest_tmp/codex
~~~

## Frontend checks with generated files under test

Keep both tsbuildinfo files and Vite output/cache under test rather than using
the ordinary npm build script during validation.

~~~powershell
node frontend/node_modules/typescript/bin/tsc -p frontend/tsconfig.app.json --incremental --tsBuildInfoFile test/.runtime/frontend/codex/tsconfig.app.tsbuildinfo
node frontend/node_modules/typescript/bin/tsc -p frontend/tsconfig.node.json --incremental --tsBuildInfoFile test/.runtime/frontend/codex/tsconfig.node.tsbuildinfo
npm --prefix frontend run lint
node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
node test/frontend/review/compiler_check.mjs
node test/frontend/review/import_boundaries.mjs
node test/frontend/review/protected_frontend.mjs
node test/frontend/ui_modes_mock_smoke.mjs
node test/frontend/mesh_tuner_mock_smoke.mjs
~~~

For an isolated production build, Vite's API can override root/cache/output
without adding a new config file outside test:

~~~powershell
node --input-type=module -e "import {build} from './frontend/node_modules/vite/dist/node/index.js'; await build({root:'frontend',cacheDir:'../test/.runtime/frontend/codex/vite-cache',build:{outDir:'../test/.runtime/frontend/codex/dist',emptyOutDir:true}})"
node test/frontend/review/bundle_budget.mjs test/.runtime/frontend/codex/dist
~~~

The outDir is deliberately inside test. Check the resolved location before
changing emptyOutDir or substituting another path. compiler_check.mjs can return
success while printing diagnostics: inspect its reported non-success events.

Mock smoke starts its own mock backend and writes reports under test/outputs.
For manual UI testing, use test/frontend/mock_backend.mjs (default port 8765)
and set IMG3D_BACKEND_URL to that mock for a localhost-only Vite session.
Use a test-local Vite cache/config when launching it. Browser screenshots and
traces go under test/outputs. Verify REST and WebSocket behavior in each affected
mode; a mock smoke alone does not render the UI.

## Real engine generation

First check that the GPU is available and no other server/process is consuming
the model's VRAM. Do not stop an existing user process as a side effect.

~~~powershell
& $py -s -B test/part_aware/gpu_lock.py --label codex-engine-smoke -- $py -s -B test/engine/run_generation_smoke.py --image test/images/ALICE/ALICE_full_front_view.png --resolution 512 --texture-size 1024 --polygons 100000 --seed 42
~~~

Existing script output: test/outputs/engine_smoke/<job_id>/.
It checks GLB faces/UVs and the preview and records timing/device samples.
For a second input use test/images/ANIME_CHARACTER_01/Alice.png.
Higher resolutions and acceptance fixtures cost substantially more; match
settings to the requested validation.

Native extension verification can execute CUDA work and uses the same lock:

~~~powershell
& $py -s -B test/part_aware/gpu_lock.py --label codex-extensions -- $py -s -B test/env_setup/verify_extensions.py
~~~

## Real part/workflow acceptance

Both scripts launch an isolated localhost backend with refusal checks for
production data. Inspect the subprocess command before running: Python -B on
the parent is not inherited by the server. If the server argv lacks -B, make a
test-local copy/helper that forwards it before executing under the user's
all-test-caches-under-test rule. Keep that helper under test/part_aware; do not
change backend code just to arrange testing. The commands below show the
existing script entry points, subject to that preflight. Wrap the complete
script once, not the child server again.

~~~powershell
& $py -s -B test/part_aware/gpu_lock.py --label codex-parts-smoke -- $py -s -B test/part_aware/run_parts_smoke.py
& $py -s -B test/part_aware/gpu_lock.py --label codex-workflow-smoke -- $py -s -B test/part_aware/run_workflow_smoke.py
~~~

run_parts_smoke.py uses both approved images, fresh reconstruction jobs,
components and semantic analyses at 0/8 views, immutable artifact hashes and
manifest/reassembly checks. --reuse-jobs is only for completed jobs created
earlier by that script in its test runtime. Report:
test/outputs/part_aware/s10_smoke/report.json.

The workflow script exercises prompt -> Z-Image -> TRELLIS -> parts and can take
many minutes. Use it only for the corresponding requested validation.

## HTTP E2E

Use a separately running server with IMG3D_RUNTIME_DIR and IMG3D_LOG_DIR under
test, test-local caches, and the GPU lock held for its lifetime. Point the client
at its verified test URL; do not use a production server merely because :8000
answers. Preserve normal auth; read credentials through existing configuration
rather than putting passwords on the command line or in reports.

The existing test/e2e/e2e_generate.py accepts --base-url, --image and --output-dir.
Its default image is the ALICE front view and default output is test/outputs/e2e.
The client is HTTP-only and must not try to acquire the lock held by its server.
