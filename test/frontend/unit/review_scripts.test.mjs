// Unit tests of the F0 review scripts (UI_DESIGN.md §10.5, slice F0): import_boundaries.mjs (layer rules, deletion-only
// allowlist), protected_frontend.mjs (UI13 hashes) and bundle_budget.mjs (§8.1 deltas, manifest attribution), each run
// as a child process on synthetic trees under test/.runtime/frontend/ (never on the live tree or the recorded files).
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { after, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'

const HERE = dirname(fileURLToPath(import.meta.url))
const REVIEW = join(HERE, '..', 'review')
const RUNTIME = join(HERE, '..', '..', '.runtime', 'frontend')
mkdirSync(RUNTIME, { recursive: true })
const TMP = mkdtempSync(join(RUNTIME, 'unit-review-'))
after(() => rmSync(TMP, { recursive: true, force: true }))

function run(script, args) {
  const result = spawnSync(process.execPath, [join(REVIEW, script), ...args], { encoding: 'utf8' })
  return { code: result.status, out: `${result.stdout}${result.stderr}` }
}

function writeTree(root, files) {
  for (const [path, text] of Object.entries(files)) {
    mkdirSync(dirname(join(root, path)), { recursive: true })
    writeFileSync(join(root, path), text)
  }
  return root
}

// ------------------------------------------------------------------------------------------------
// import_boundaries.mjs
// ------------------------------------------------------------------------------------------------
const CLEAN_SRC = {
  'api/types.ts': "export const SAMPLER_KEYS = ['steps'] as const\n",
  'api/client.ts': "import axios from 'axios'\nexport const API_BASE_URL = '/api/v1'\nexport const resolveApiUrl = (path: string) => path\nexport const http = axios\n",
  'lib/storage.ts': "export const STORAGE_KEYS = { a: 'img3d.a' }\n",
  'lib/featureMeta.ts': "export const DEFAULT_T2I = 'z_image_turbo'\n",
  'store/featuresStore.ts': "import { create } from 'zustand'\nimport { STORAGE_KEYS } from '../lib/storage'\nexport const useFeaturesStore = create(() => ({ keys: STORAGE_KEYS }))\n",
  'components/Foo.tsx': "import { useFeaturesStore } from '../store/featuresStore'\nimport { resolveApiUrl } from '../api/client'\nimport { SAMPLER_KEYS } from '../api/types'\nexport function Foo() { return [useFeaturesStore, resolveApiUrl, SAMPLER_KEYS] }\n",
}

function boundaries(extra = {}, allowlist = { entries: [] }) {
  const root = mkdtempSync(join(TMP, 'src-'))
  writeTree(root, { ...CLEAN_SRC, ...extra })
  const list = join(root, '..', `${root.split(/[\\/]/).pop()}.allowlist.json`)
  writeFileSync(list, JSON.stringify(allowlist))
  return run('import_boundaries.mjs', ['--root', root, '--allowlist', list])
}

test('import_boundaries: a clean tree passes with an empty allowlist', () => {
  const { code, out } = boundaries()
  assert.equal(code, 0, out)
  assert.match(out, /0 violation\(s\), 0 stale allowlist entries/)
})

test('import_boundaries: layer, pure, store, hook-name, xyflow, UI9 and UI14 rules each fire', () => {
  const { code, out } = boundaries({
    'store/runStore.ts': "import { Foo } from '../components/Foo'\nexport const x = Foo\n",
    'store/aStore.ts': "import { b } from './bStore'\nexport const a = b\n",
    'store/bStore.ts': "import { a } from './aStore'\nexport const b = a\n",
    'lib/generatePlan.ts': "import { STORAGE_KEYS } from './storage'\nexport const plan = STORAGE_KEYS\n",
    'lib/forms.ts': 'export function useForm() { return 1 }\n',
    'hooks/useThing.ts': "import { http } from '../api/client'\nexport const useThing = () => http\n",
    'components/Bar.tsx': "import { ReactFlow } from '@xyflow/react'\nexport const url = (id: string) => '/api/v1/part-analyses/' + id\nexport const model = 'z_image_turbo'\nexport const flow = ReactFlow\n",
  })
  assert.equal(code, 1)
  for (const rule of ['layer', 'store-cycle', 'pure-extension', 'use-named-export', 'xyflow', 'UI9', 'UI14']) assert.match(out, new RegExp(`\\[${rule}\\]`), `${rule} missing:\n${out}`)
  assert.match(out, /store\/runStore\.ts:1 {2}\[layer\] store\/ must not import components\/Foo/)
  assert.match(out, /hooks\/useThing\.ts:1 {2}\[layer\] hooks\/ must not import api value\(s\) http from api\/client/)
})

test('import_boundaries: allowlist entries suppress their edge; an entry that no longer matches is stale', () => {
  const extra = { 'hooks/useThing.ts': "import { http } from '../api/client'\nexport const useThing = () => http\n" }
  const allowed = boundaries(extra, { entries: [{ from: 'hooks/useThing.ts', to: ['api/client'], names: ['http'], reason: 'test' }] })
  assert.equal(allowed.code, 0, allowed.out)
  assert.match(allowed.out, /1 allowlisted edge/)
  const narrow = boundaries(extra, { entries: [{ from: 'hooks/useThing.ts', to: ['api/client'], names: ['other'], reason: 'test' }] })
  assert.equal(narrow.code, 1)
  assert.match(narrow.out, /stale allowlist entry: delete it \(hooks\/useThing\.ts → api\/client\)/)
  const stale = boundaries({}, { entries: [{ from: 'components/meshTuner/**', to: ['api/tunings'], names: ['*'], reason: 'test' }] })
  assert.equal(stale.code, 1)
  assert.match(stale.out, /stale allowlist entry: delete it \(components\/meshTuner\/\*\* → api\/tunings\)/)
})

test('import_allowlist.json: the two seeded entries of §1.4, each with a reason', () => {
  const allowlist = JSON.parse(readFileSync(join(REVIEW, 'import_allowlist.json'), 'utf8'))
  assert.deepEqual(allowlist.entries.map((entry) => entry.from), ['components/meshTuner/**', 'components/viewer/ModelStatsCard.tsx'])
  assert.ok(allowlist.entries.every((entry) => entry.reason && Array.isArray(entry.to) && entry.to.length))
})

// ------------------------------------------------------------------------------------------------
// protected_frontend.mjs
// ------------------------------------------------------------------------------------------------
test('protected_frontend: records a tree, then detects changed, added and missing files (CRLF-insensitive)', () => {
  const root = writeTree(mkdtempSync(join(TMP, 'fe-')), {
    'src/components/meshTuner/TuningCard.tsx': 'export const a = 1\nexport const b = 2\n',
    'src/store/meshTunerStore.ts': 'export const s = 1\n',
    'src/lib/meshTuner.ts': 'export const l = 1\n',
    'src/api/blender.ts': 'export const b = 1\n',
    'src/api/tunings.ts': 'export const t = 1\n',
    'src/hooks/useTuneAvailability.ts': 'export const h = 1\n',
    'src/App.tsx': 'export const notProtected = 1\n',
  })
  const sha = join(root, 'protected.sha256')
  const recorded = run('protected_frontend.mjs', ['--record', root, '--sha-file', sha])
  assert.equal(recorded.code, 0, recorded.out)
  assert.equal(readFileSync(sha, 'utf8').split('\n').filter((line) => /^[0-9a-f]{64} {2}frontend\//.test(line)).length, 6)
  assert.equal(run('protected_frontend.mjs', ['--root', root, '--sha-file', sha]).code, 0)
  writeFileSync(join(root, 'src/components/meshTuner/TuningCard.tsx'), 'export const a = 1\r\nexport const b = 2\r\n')
  writeFileSync(join(root, 'src/App.tsx'), 'export const changedButNotProtected = 2\n')
  assert.equal(run('protected_frontend.mjs', ['--root', root, '--sha-file', sha]).code, 0, 'CRLF and unprotected edits pass')
  writeFileSync(join(root, 'src/lib/meshTuner.ts'), 'export const l = 2\n')
  writeFileSync(join(root, 'src/components/meshTuner/Extra.tsx'), 'export const x = 1\n')
  unlinkSync(join(root, 'src/api/tunings.ts'))
  const broken = run('protected_frontend.mjs', ['--root', root, '--sha-file', sha])
  assert.equal(broken.code, 1)
  assert.match(broken.out, /changed: {2}frontend\/src\/lib\/meshTuner\.ts/)
  assert.match(broken.out, /added: {4}frontend\/src\/components\/meshTuner\/Extra\.tsx/)
  assert.match(broken.out, /missing: {2}frontend\/src\/api\/tunings\.ts/)
})

test('protected_frontend.sha256: recorded from 8654b90 and lists the §10.5 protected set', () => {
  const text = readFileSync(join(REVIEW, 'protected_frontend.sha256'), 'utf8')
  assert.match(text, /commit 8654b90/)
  const paths = text.split('\n').filter((line) => /^[0-9a-f]{64} /.test(line)).map((line) => line.split(/\s+/)[1])
  for (const path of ['frontend/src/store/meshTunerStore.ts', 'frontend/src/lib/meshTuner.ts', 'frontend/src/api/blender.ts', 'frontend/src/api/tunings.ts', 'frontend/src/hooks/useTuneAvailability.ts']) assert.ok(paths.includes(path), path)
  assert.ok(paths.filter((path) => path.startsWith('frontend/src/components/meshTuner/')).length >= 20)
})

// ------------------------------------------------------------------------------------------------
// bundle_budget.mjs
// ------------------------------------------------------------------------------------------------
const pseudoRandom = (seed, length) => {
  let state = seed
  return Array.from({ length }, () => ((state = (state * 1103515245 + 12345) >>> 0) >>> 16).toString(36)).join('').slice(0, length)
}

function makeDist(files, html) {
  const dist = mkdtempSync(join(TMP, 'dist-'))
  writeTree(dist, { 'index.html': html, ...files })
  return dist
}

const HTML = '<!doctype html><html><head><script type="module" crossorigin src="/assets/index-AAAAAAAA.js"></script>'
  + '<link rel="modulepreload" crossorigin href="/assets/react-BBBBBBBB.js"><link rel="stylesheet" crossorigin href="/assets/index-CCCCCCCC.css"></head></html>'
const gz = (text) => gzipSync(Buffer.from(text), { level: 9 }).length

test('bundle_budget: --record-baseline writes gzip -9 sizes of the initial set; equal sizes pass', () => {
  const files = { 'assets/index-AAAAAAAA.js': pseudoRandom(1, 9000), 'assets/react-BBBBBBBB.js': pseudoRandom(2, 6000), 'assets/index-CCCCCCCC.css': pseudoRandom(3, 2000), 'assets/three-DDDDDDDD.js': pseudoRandom(4, 3000) }
  const dist = makeDist(files, HTML)
  const baseline = join(dist, 'baseline.json')
  const empty = mkdtempSync(join(TMP, 'src-empty-'))
  assert.equal(run('bundle_budget.mjs', [dist, '--record-baseline', '--baseline', baseline]).code, 0)
  const recorded = JSON.parse(readFileSync(baseline, 'utf8'))
  assert.equal(recorded.initialJs, gz(files['assets/index-AAAAAAAA.js']) + gz(files['assets/react-BBBBBBBB.js']))
  assert.equal(recorded.initialCss, gz(files['assets/index-CCCCCCCC.css']))
  assert.equal(recorded.chunks.three, gz(files['assets/three-DDDDDDDD.js']))
  const ok = run('bundle_budget.mjs', [dist, '--baseline', baseline, '--src', empty])
  assert.equal(ok.code, 0, ok.out)
  assert.match(ok.out, /initial files: index-AAAAAAAA\.js, react-BBBBBBBB\.js, index-CCCCCCCC\.css/)
  assert.match(ok.out, /lazy chunk attribution .* n\/a/)
})

test('bundle_budget: react-flow__ in an initial file and CSS over baseline + 5 kB fail; initial JS is enforced only in wave C', () => {
  const files = { 'assets/index-AAAAAAAA.js': `${pseudoRandom(5, 40000)}react-flow__`, 'assets/react-BBBBBBBB.js': 'r', 'assets/index-CCCCCCCC.css': pseudoRandom(6, 12000) }
  const dist = makeDist(files, HTML)
  const baseline = join(dist, 'baseline.json')
  writeFileSync(baseline, JSON.stringify({ commit: 'test', initialJs: 100, initialCss: 100, chunks: { three: 1, r3f: 1, ModelViewer: 1 } }))
  const empty = mkdtempSync(join(TMP, 'src-empty-'))
  const result = run('bundle_budget.mjs', [dist, '--baseline', baseline, '--src', empty])
  assert.equal(result.code, 1)
  assert.match(result.out, /Initial JS .* over \(not enforced yet\)/)
  assert.match(result.out, /Initial CSS .* FAIL/)
  assert.match(result.out, /nodeui not initial .* FAIL/)
  assert.match(result.out, /2 budget violation\(s\)/)
  const enforced = run('bundle_budget.mjs', [dist, '--baseline', baseline, '--src', empty, '--enforce-initial'])
  assert.match(enforced.out, /Initial JS .* FAIL/)
  assert.match(enforced.out, /3 budget violation\(s\)/)
})

test('bundle_budget: manifest attribution counts chunks shared by mobile and tablet once, under Mobile', () => {
  const files = {
    'assets/index-AAAAAAAA.js': 'entry', 'assets/react-BBBBBBBB.js': 'react', 'assets/index-CCCCCCCC.css': 'css',
    'assets/index-MMMMMMMM.js': pseudoRandom(7, 3000), 'assets/shared-SSSSSSSS.js': pseudoRandom(8, 2000), 'assets/index-TTTTTTTT.js': pseudoRandom(9, 1000),
  }
  files['.vite/manifest.json'] = JSON.stringify({
    'index.html': { file: 'assets/index-AAAAAAAA.js', imports: ['_react-BBBBBBBB.js'], dynamicImports: ['src/shells/mobile/index.ts', 'src/shells/tablet/index.ts'] },
    '_react-BBBBBBBB.js': { file: 'assets/react-BBBBBBBB.js' },
    'src/shells/mobile/index.ts': { file: 'assets/index-MMMMMMMM.js', imports: ['_shared-SSSSSSSS.js', '_react-BBBBBBBB.js'] },
    'src/shells/tablet/index.ts': { file: 'assets/index-TTTTTTTT.js', imports: ['_shared-SSSSSSSS.js'] },
    '_shared-SSSSSSSS.js': { file: 'assets/shared-SSSSSSSS.js' },
  })
  const dist = makeDist(files, HTML)
  const baseline = join(dist, 'baseline.json')
  writeFileSync(baseline, JSON.stringify({ commit: 'test', initialJs: 1000, initialCss: 1000, chunks: { three: 1, r3f: 1, ModelViewer: 1 } }))
  const result = run('bundle_budget.mjs', [dist, '--baseline', baseline, '--src', mkdtempSync(join(TMP, 'src-empty-'))])
  assert.equal(result.code, 0, result.out)
  const mobile = ((gz(files['assets/index-MMMMMMMM.js']) + gz(files['assets/shared-SSSSSSSS.js'])) / 1000).toFixed(2)
  const tablet = (gz(files['assets/index-TTTTTTTT.js']) / 1000).toFixed(2)
  assert.match(result.out, new RegExp(`Mobile shell \\+ shells/shared\\s+${mobile.replace('.', '\\.')}`))
  assert.match(result.out, new RegExp(`Tablet shell \\(excluding shared\\)\\s+${tablet.replace('.', '\\.')}`))
  assert.match(result.out, /Feature bundle \(featureBundle\)\s+n\/a/)
})

test('bundle_baseline.json: recorded from the 8654b90 build (initial JS 178.2 kB gz ± 1 kB)', () => {
  const baseline = JSON.parse(readFileSync(join(REVIEW, 'bundle_baseline.json'), 'utf8'))
  assert.equal(baseline.commit, '8654b90')
  assert.ok(Math.abs(baseline.initialJs - 178_200) <= 1_000, String(baseline.initialJs))
  assert.ok(baseline.initialCss > 0 && baseline.chunks.three > 0 && baseline.chunks.r3f > 0 && baseline.chunks.ModelViewer > 0)
})
