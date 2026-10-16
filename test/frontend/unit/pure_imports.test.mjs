// Static scan of the PURE module list (UI_DESIGN.md §1.5, UI8, §10.2; slice F2): relative value imports carry an
// explicit `.ts` specifier (Node 22.17 strip-types cannot resolve extensionless ones), no react / zustand / axios /
// three / @xyflow value import, and no browser global or timer token. The F2 modules are also imported here under
// plain Node to prove they load without a loader hook.
//
// Files owned by slices that have not landed yet (components/viewer/partsIndex.ts → F8,
// shells/node/canvas/flowAdapter.ts → F11) are skipped while they do not exist; they are scanned once they do.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const SRC = fileURLToPath(new URL('../../../frontend/src/', import.meta.url))

const F2_LIB = [
  'settingsRequest', 'imageJobRequest', 'workflowRequest', 'generatePlan', 'generateAvailability', 'forms', 'featureFlags',
  'uiModes', 'viewport', 'partsModel', 'runSteps', 'featureMeta', 'errorText', 'storage',
].map((name) => `lib/${name}.ts`)
const LATER = ['components/viewer/partsIndex.ts', 'shells/node/canvas/flowAdapter.ts']
const API = ['api/types.ts', 'api/partAwareTypes.ts']

function walk(dir) {
  if (!existsSync(dir)) return []
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? walk(path) : path.endsWith('.ts') ? [path] : []
  })
}
const rel = (path) => relative(SRC, path).replaceAll('\\', '/')

const PURE_FILES = [
  ...F2_LIB,
  ...walk(join(SRC, 'workflow')).map(rel).sort(),
  ...LATER.filter((file) => existsSync(join(SRC, file))),
  ...API,
]

/** `lib/storage.ts` guards `window.localStorage` inside a function (§1.5): import rules only, no token scan. */
const TOKEN_SCAN_EXEMPT = new Set(['lib/storage.ts'])
const FORBIDDEN_PACKAGES = /^(react|react-dom|zustand|axios|three|@react-three\/|@xyflow\/)/
const FORBIDDEN_TOKENS = ['window.', 'document.', 'localStorage', 'import.meta.env', 'matchMedia', 'navigator.', 'setTimeout(', 'setInterval(', 'requestAnimationFrame(']

/** Source without comments (string and regex contents are kept; `//` inside a URL-like `://` is not a comment). */
function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:\\'"`])\/\/.*$/gm, '$1')
}

/** Every static import / re-export and dynamic import, with whether it is erased (type-only). */
function importsOf(source) {
  const out = []
  const statement = /(?:^|\n)\s*(import|export)\s+(type\s+)?([^;'"]*?)\s*from\s*['"]([^'"]+)['"]/g
  for (const m of source.matchAll(statement)) {
    const clause = m[3].trim()
    const typeOnly = Boolean(m[2]) || /^\{[^}]*\}$/.test(clause) && clause.slice(1, -1).split(',').map((s) => s.trim()).filter(Boolean).every((s) => s.startsWith('type '))
    out.push({ spec: m[4], typeOnly })
  }
  for (const m of source.matchAll(/(?:^|\n)\s*import\s+['"]([^'"]+)['"]/g)) out.push({ spec: m[1], typeOnly: false })
  for (const m of source.matchAll(/\bimport\(\s*['"]([^'"]+)['"]\s*\)/g)) out.push({ spec: m[1], typeOnly: false })
  return out
}

test('the scanned list covers every F2 pure module and both import-free API files', () => {
  for (const file of [...F2_LIB, ...API]) assert.ok(existsSync(join(SRC, file)), `${file} is missing`)
  assert.ok(PURE_FILES.length >= F2_LIB.length + API.length)
})

for (const file of PURE_FILES) {
  test(`pure module rules: ${file}`, () => {
    const source = stripComments(readFileSync(join(SRC, file), 'utf8'))
    for (const imp of importsOf(source)) {
      if (imp.typeOnly) continue
      assert.doesNotMatch(imp.spec, FORBIDDEN_PACKAGES, `${file}: value import of ${imp.spec}`)
      if (imp.spec.startsWith('.')) assert.match(imp.spec, /\.ts$/, `${file}: relative value import without .ts: '${imp.spec}'`)
      else assert.fail(`${file}: value import of the package '${imp.spec}' (pure modules import only other pure files)`)
    }
    if (TOKEN_SCAN_EXEMPT.has(file)) return // §1.5: guarded `window.localStorage` inside a function
    for (const token of FORBIDDEN_TOKENS) assert.equal(source.includes(token), false, `${file}: uses ${token}`)
  })
}

test('the import scanner recognises value, type-only and mixed imports (guards the guard)', () => {
  const sample = [
    "import { a } from './a.ts'",
    "import type { B } from '../b'",
    "import { type C, d } from './c'",
    "import { type E } from './e'",
    "export { f } from './f.ts'",
    "export type { G } from './g'",
    "const h = import('./h')",
  ].join('\n')
  assert.deepEqual(importsOf(sample), [
    { spec: './a.ts', typeOnly: false },
    { spec: '../b', typeOnly: true },
    { spec: './c', typeOnly: false },
    { spec: './e', typeOnly: true },
    { spec: './f.ts', typeOnly: false },
    { spec: './g', typeOnly: true },
    { spec: './h', typeOnly: false },
  ])
  assert.equal(stripComments('const u = "http://x" // note: window.x\n/* document.y */').includes('window.'), false)
  assert.equal(stripComments('const u = "http://x"').includes('http://x'), true)
})

for (const file of F2_LIB) {
  test(`loads under plain Node strip-types without a loader hook: ${file}`, async () => {
    const module = await import(pathToFileURL(join(SRC, file)).href)
    assert.ok(Object.keys(module).length > 0)
  })
}
