/**
 * Import boundaries and mechanical code rules of UI_DESIGN.md §1.4 / §10.5 over frontend/src.
 *
 *   node test/frontend/review/import_boundaries.mjs                 check frontend/src
 *   node test/frontend/review/import_boundaries.mjs --root <src>    check another tree (e.g. a clean 8654b90 export)
 *   node test/frontend/review/import_boundaries.mjs --allowlist <json>  use another allowlist (unit tests)
 *
 * Checks (value imports; `import type …` is allowed from anywhere):
 * - the layer table of §1.4 (api, PURE, store, actions, hooks, components, shells/<mode>, app; the impure lib/ files
 *   and the root files main.tsx/App.tsx follow the closest rule, see LAYER_RULES), the URL-helper set, the API constants
 *   of api/types.ts + api/partAwareTypes.ts (allowed everywhere), and the permanent useBackendSession exception;
 * - api/types.ts is import-free; api/partAwareTypes.ts only has `import type` / `export type` from './types';
 * - PURE files use `.ts` specifiers for relative value imports and import no package values;
 * - the store edge table as an explicit adjacency map plus a cycle check over every store → store value import;
 *   `store/planFromStores.ts` is value-imported only from actions/ and hooks/; no store imports components/**;
 * - `@xyflow/*` only under shells/node/canvas/; `three` / `@react-three/*` only in the lazy viewer files;
 * - no `use*`-named non-hook export in actions/, store/ (except `export const useXStore = create…`), lib/, api/;
 * - UI9: no '/part-analyses/', '/candidates/', '/masks/' string literal outside src/api/*.ts;
 * - UI14: no literal model id or dotted taxonomy id outside lib/featureMeta.ts, api/partAwareTypes.ts and type unions.
 *
 * Grandfathered import edges live in import_allowlist.json. The allowlist is deletion-only (§1.4/§11): F0 alone adds
 * entries; an entry (or one of its `to` targets) that no longer matches a violation fails with
 * "stale allowlist entry: delete it". Exit code 1 on any violation or stale entry.
 */
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, posix, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..', '..')
const args = process.argv.slice(2)
const SRC = args.includes('--root') ? args[args.indexOf('--root') + 1] : join(REPO, 'frontend', 'src')
const ALLOWLIST_FILE = args.includes('--allowlist') ? args[args.indexOf('--allowlist') + 1] : join(HERE, 'import_allowlist.json')
const ALLOWLIST = JSON.parse(readFileSync(ALLOWLIST_FILE, 'utf8'))
const FEATURES = JSON.parse(readFileSync(join(HERE, '..', 'fixtures', 'features', 'full.json'), 'utf8'))
const MODELS = JSON.parse(readFileSync(join(HERE, '..', 'fixtures', 'models.json'), 'utf8'))

// ------------------------------------------------------------------------------------------------
// Definitions (UI_DESIGN §1.4, §1.5)
// ------------------------------------------------------------------------------------------------
const PURE_LIB = ['settingsRequest', 'imageJobRequest', 'workflowRequest', 'generatePlan', 'generateAvailability', 'forms', 'featureFlags', 'uiModes', 'viewport', 'partsModel', 'runSteps', 'featureMeta', 'errorText', 'storage']
const PURE_FILES = new Set([...PURE_LIB.map((name) => `lib/${name}.ts`), 'components/viewer/partsIndex.ts', 'shells/node/canvas/flowAdapter.ts'])
const API_CONSTANT_FILES = new Set(['api/types.ts', 'api/partAwareTypes.ts'])
const URL_HELPERS = new Set(['resolveApiUrl', 'fileUrl', 'withQuery', 'jobModelUrl', 'jobPreviewUrl', 'referenceThumbnailUrl', 'tunedModelUrl', 'API_BASE_URL'])
const URL_HELPER_MODULES = new Set(['api/client', 'api/files', 'api/jobs', 'api/references', 'api/tunings'])
const SESSION_EXCEPTION = { file: 'hooks/useBackendSession.ts', allowed: { 'api/client': ['configureClientHooks', 'getAuthToken'], 'api/ws': ['RealtimeClient'] } }
const THREE_FILES = new Set(['components/viewer/ModelViewer.tsx', 'components/viewer/ModelScene.tsx', 'components/viewer/PartsModel.tsx', 'components/viewer/sceneUtils.ts', 'components/viewer/partsScene.ts'])
const THREE_PACKAGES = /^(three|@react-three\/|three-stdlib$|three-mesh-bvh$)/
/** Store adjacency (§1.4 "acyclic: …"); stores not listed (the existing ones) are only checked for cycles. */
const STORE_EDGES = {
  run: ['job', 'part', 'reference', 'input', 'features', 'announcer', 'toast'],
  part: ['features', 'announcer', 'toast'],
  workflow: ['features', 'input', 'partOptions', 'settings', 'toast'],
  planFromStores: ['features', 'input', 'partOptions', 'reference', 'settings', 'job', 'run', 'navigation', 'system'],
  features: [], preferences: [], navigation: [], input: [], partOptions: [], announcer: [],
}
const PATH_LITERALS = ['/part-analyses/', '/candidates/', '/masks/']
const LITERAL_EXEMPT = new Set(['lib/featureMeta.ts', 'api/partAwareTypes.ts'])
const MODEL_IDS = new Set(MODELS.items.map((model) => model.id))
const TAXONOMY_IDS = new Set(FEATURES.taxonomy.classes.map((cls) => cls.id).filter((id) => id.includes('.')))

// ------------------------------------------------------------------------------------------------
// Source scanning: strip comments, collect string literals (with the significant chars around them)
// ------------------------------------------------------------------------------------------------
function scan(code) {
  let out = ''
  const strings = []
  let i = 0
  let line = 1
  let lastSig = ''
  let lastWord = ''
  const push = (text) => { out += text; for (const ch of text) if (ch === '\n') line += 1 }
  const readQuoted = (start) => {
    const quote = code[start]
    let j = start + 1
    let value = ''
    while (j < code.length && code[j] !== quote && code[j] !== '\n') {
      if (code[j] === '\\') { value += code[j + 1] ?? ''; j += 2 } else value += code[j++]
    }
    return { end: j + 1, value }
  }
  /** Template literal from `start` (a backtick): returns its end and the static text (expressions → ' '). */
  const readTemplate = (start) => {
    let j = start + 1
    let value = ''
    while (j < code.length && code[j] !== '`') {
      if (code[j] === '\\') { value += code[j + 1] ?? ''; j += 2; continue }
      if (code[j] === '$' && code[j + 1] === '{') {
        j = skipExpression(j + 2)
        value += ' '
        continue
      }
      value += code[j++]
    }
    return { end: j + 1, value }
  }
  const skipExpression = (start) => {
    let depth = 1
    let j = start
    while (j < code.length && depth > 0) {
      const ch = code[j]
      if (ch === '{') depth += 1
      else if (ch === '}') depth -= 1
      else if (ch === "'" || ch === '"') { const q = readQuoted(j); strings.push({ value: q.value, line, before: '', after: '' }); j = q.end; continue }
      else if (ch === '`') { const tpl = readTemplate(j); strings.push({ value: tpl.value, line, before: '', after: '' }); j = tpl.end; continue }
      j += 1
    }
    return j
  }
  while (i < code.length) {
    const ch = code[i]
    const next = code[i + 1]
    if (ch === '/' && next === '/') {
      while (i < code.length && code[i] !== '\n') { out += ' '; i += 1 }
      continue
    }
    if (ch === '/' && next === '*') {
      const end = code.indexOf('*/', i + 2)
      const stop = end === -1 ? code.length : end + 2
      for (; i < stop; i += 1) push(code[i] === '\n' ? '\n' : ' ')
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') {
      const startLine = line
      const literal = ch === '`' ? readTemplate(i) : readQuoted(i)
      const raw = code.slice(i, literal.end)
      const after = /\S/.exec(code.slice(literal.end))?.[0] ?? ''
      strings.push({ value: literal.value, line: startLine, before: lastSig, after })
      push(raw)
      i = literal.end
      lastSig = 'a'
      lastWord = ''
      continue
    }
    const regexAllowed = lastSig === '' || '(,=:[!&|?{};+-*%<>~^'.includes(lastSig) || ['return', 'typeof', 'case', 'do', 'else', 'in', 'of'].includes(lastWord)
    if (ch === '/' && regexAllowed) {
      let j = i + 1
      let inClass = false
      while (j < code.length && code[j] !== '\n') {
        if (code[j] === '\\') { j += 2; continue }
        if (code[j] === '[') inClass = true
        else if (code[j] === ']') inClass = false
        else if (code[j] === '/' && !inClass) break
        j += 1
      }
      j += 1
      while (/[a-z]/i.test(code[j] ?? '')) j += 1
      push(code.slice(i, j))
      i = j
      lastSig = 'a'
      lastWord = ''
      continue
    }
    push(ch)
    if (/\s/.test(ch)) { i += 1; continue }
    if (/[\w$]/.test(ch)) {
      const word = /^[\w$]+/.exec(code.slice(i))[0]
      if (word.length > 1) push(word.slice(1))
      i += word.length
      lastWord = word
      lastSig = 'a'
      continue
    }
    lastSig = ch
    lastWord = ''
    i += 1
  }
  return { code: out, strings }
}

const lineAt = (text, index) => text.slice(0, index).split('\n').length

function parseClause(clause) {
  const text = clause.trim()
  if (text.startsWith('type ')) return { typeOnly: true, names: [] }
  const names = []
  const types = []
  const brace = /\{([^}]*)\}/.exec(text)
  const head = brace ? text.slice(0, brace.index) : text
  for (const part of head.split(',').map((s) => s.trim()).filter(Boolean)) {
    if (part.startsWith('*')) names.push('*')
    else names.push('default')
  }
  if (brace) {
    for (const item of brace[1].split(',').map((s) => s.trim()).filter(Boolean)) {
      if (item.startsWith('type ')) types.push(item.slice(5).split(/\s+as\s+/)[0].trim())
      else names.push(item.split(/\s+as\s+/)[0].trim())
    }
  }
  return { typeOnly: names.length === 0 && types.length > 0, names }
}

function parseImports(code) {
  const imports = []
  const add = (match, kind, spec, clause) => {
    const parsed = clause === null ? { typeOnly: false, names: kind === 'dynamic' ? ['*'] : [] } : parseClause(clause)
    imports.push({ kind, spec, line: lineAt(code, match.index + (match[0].length - match[0].trimStart().length)), ...parsed })
  }
  for (const m of code.matchAll(/(?:^|[\n;])\s*import\s+((?:type\s+)?[\w$*\s{},]+?)\s+from\s*(['"])([^'"\n]+)\2/g)) add(m, 'static', m[3], m[1])
  for (const m of code.matchAll(/(?:^|[\n;])\s*import\s*(['"])([^'"\n]+)\1/g)) add(m, 'side-effect', m[2], null)
  for (const m of code.matchAll(/(?:^|[\n;])\s*export\s+((?:type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[^}]*\}))\s+from\s*(['"])([^'"\n]+)\2/g)) {
    const clause = m[1].trim().replace(/^\*(\s+as\s+[\w$]+)?$/, '* as ns')
    add(m, 'reexport', m[3], clause)
  }
  for (const m of code.matchAll(/\bimport\s*\(\s*(['"])([^'"\n]+)\1\s*\)/g)) add(m, 'dynamic', m[2], null)
  return imports
}

// ------------------------------------------------------------------------------------------------
// Resolution and layers
// ------------------------------------------------------------------------------------------------
function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? walk(path) : /\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts') ? [path] : []
  })
}

function resolveRelative(fromRel, spec) {
  const target = posix.normalize(posix.join(posix.dirname(fromRel), spec))
  const candidates = /\.(ts|tsx|js|css)$/.test(target) ? [target] : [`${target}.ts`, `${target}.tsx`, `${target}/index.ts`, `${target}/index.tsx`]
  const found = candidates.find((candidate) => existsSync(join(SRC, candidate)))
  return found ?? candidates[0]
}

const moduleId = (rel) => rel.replace(/\.(ts|tsx)$/, '').replace(/\/index$/, '/index')
const packageName = (spec) => (spec.startsWith('@') ? spec.split('/').slice(0, 2).join('/') : spec.split('/')[0])

function layerOf(rel) {
  if (PURE_FILES.has(rel) || rel.startsWith('workflow/')) return 'pure'
  const top = rel.split('/')[0]
  if (['api', 'lib', 'store', 'actions', 'hooks', 'components', 'shells', 'app'].includes(top) && rel.includes('/')) return top
  return 'root'
}

const storeName = (rel) => /^store\/(\w+?)(Store)?\.ts$/.exec(rel)?.[1] ?? null
const shellMode = (rel) => /^shells\/([^/]+)\//.exec(rel)?.[1] ?? null

/** Value-import targets each layer may reach (§1.4). `kind` is the target's layer, or 'apiConst' / 'urlHelper'. */
const LAYER_RULES = {
  api: ['api', 'apiConst', 'lib/storage'],
  pure: ['pure', 'apiConst'],
  lib: ['lib', 'pure', 'apiConst'],                       // impure lib/ files (format, stages, buildFlags, motion, …)
  store: ['api', 'apiConst', 'lib', 'pure', 'store'],
  actions: ['store', 'api', 'apiConst', 'lib', 'pure'],
  hooks: ['hooks', 'store', 'actions', 'lib', 'pure', 'apiConst', 'urlHelper'],
  components: ['components', 'hooks', 'actions', 'store', 'lib', 'pure', 'apiConst', 'urlHelper'],
  shells: ['components', 'hooks', 'actions', 'store', 'lib', 'pure', 'apiConst'],
  app: ['app', 'components', 'hooks', 'store', 'lib', 'pure', 'apiConst'],
  root: ['root', 'app', 'components', 'hooks', 'store', 'lib', 'pure', 'apiConst'],
}

// ------------------------------------------------------------------------------------------------
// Checks
// ------------------------------------------------------------------------------------------------
const violations = []
const storeGraph = new Map()
const violation = (file, line, rule, message, edge = null) => violations.push({ file, line, rule, message, edge })

function checkImport(fromRel, imp) {
  const fromLayer = layerOf(fromRel)
  const value = !imp.typeOnly
  if (!imp.spec.startsWith('.')) {
    const pkg = packageName(imp.spec)
    if (pkg.startsWith('@xyflow/') && !fromRel.startsWith('shells/node/canvas/') && value) violation(fromRel, imp.line, 'xyflow', `${pkg} imported outside shells/node/canvas/`)
    if (!value) return
    if (THREE_PACKAGES.test(imp.spec) && !THREE_FILES.has(fromRel)) violation(fromRel, imp.line, 'three', `${imp.spec} imported outside the lazy viewer files`)
    if (fromLayer === 'pure') violation(fromRel, imp.line, 'pure-package', `PURE module imports the package value ${imp.spec}`)
    if (fromLayer === 'api' && pkg !== 'axios') violation(fromRel, imp.line, 'api-package', `api/ may only import axios (got ${imp.spec})`)
    if (fromLayer === 'actions' && (pkg === 'react' || pkg === 'react-dom')) violation(fromRel, imp.line, 'actions-react', 'actions/ must not import react')
    return
  }
  const targetRel = resolveRelative(fromRel, imp.spec)
  if (/\.css$/.test(targetRel)) return
  if (fromRel === 'api/types.ts') violation(fromRel, imp.line, 'api-types-import-free', 'api/types.ts must be import-free')
  if (fromRel === 'api/partAwareTypes.ts' && (value || targetRel !== 'api/types.ts')) violation(fromRel, imp.line, 'api-types-import-free', "api/partAwareTypes.ts may only `import type` from './types'")
  if (!value) return
  if (fromLayer === 'pure' && !/\.ts$/.test(imp.spec)) violation(fromRel, imp.line, 'pure-extension', `relative value import without a .ts specifier: '${imp.spec}'`)

  const target = moduleId(targetRel)
  const targetLayer = layerOf(targetRel)
  const edge = { from: fromRel, to: target, names: imp.names }

  // store graph (cycle check + adjacency) and planFromStores ownership
  if (targetLayer === 'store') {
    const toStore = storeName(targetRel)
    if (toStore === 'planFromStores' && !['actions', 'hooks'].includes(fromLayer)) violation(fromRel, imp.line, 'planFromStores', 'store/planFromStores is value-imported only by actions/ and hooks/', edge)
    if (fromLayer === 'store') {
      const fromStore = storeName(fromRel)
      if (!storeGraph.has(fromStore)) storeGraph.set(fromStore, new Set())
      storeGraph.get(fromStore).add(toStore)
      if (STORE_EDGES[fromStore] && !STORE_EDGES[fromStore].includes(toStore)) violation(fromRel, imp.line, 'store-edge', `store edge ${fromStore} → ${toStore} is not in the §1.4 adjacency table`, edge)
    }
  }
  if (targetLayer === 'pure' && targetRel.startsWith('workflow/') && !(fromRel === 'store/workflowStore.ts' || fromLayer === 'pure' || (fromLayer === 'shells' && shellMode(fromRel) === 'node'))) {
    violation(fromRel, imp.line, 'workflow-engine', 'workflow/ is imported only by store/workflowStore.ts and shells/node/', edge)
    return
  }
  if (targetRel === 'shells/node/canvas/flowAdapter.ts' && !fromRel.startsWith('shells/node/')) {
    violation(fromRel, imp.line, 'shell-cross', 'flowAdapter is private to shells/node/', edge)
    return
  }

  // shells: own mode + shared only; app: index.ts (lazy via shellRegistry) and ready.ts only
  if (targetLayer === 'shells') {
    const mode = shellMode(targetRel)
    if (fromLayer === 'shells') {
      const own = shellMode(fromRel)
      if (mode !== own && mode !== 'shared') violation(fromRel, imp.line, 'shell-cross', `shells/${own} imports shells/${mode}`, edge)
      return
    }
    if (fromLayer === 'app' || fromLayer === 'root') {
      const file = targetRel.slice(`shells/${mode}/`.length)
      if (file === 'ready.ts' && imp.kind !== 'dynamic') return
      if (file === 'index.ts' && fromRel === 'app/shellRegistry.ts' && (imp.kind === 'dynamic' || mode === 'default')) return
      violation(fromRel, imp.line, 'app-shells', `app/ reaches shells only through shells/*/ready.ts (static) and shells/*/index.ts (lazy, in app/shellRegistry.ts); got ${targetRel}`, edge)
      return
    }
    violation(fromRel, imp.line, 'layer', `${fromLayer}/ must not import shells/`, edge)
    return
  }

  let kind = targetLayer
  if (API_CONSTANT_FILES.has(targetRel)) kind = 'apiConst'
  else if (targetLayer === 'api' && URL_HELPER_MODULES.has(target) && imp.names.length > 0 && imp.names.every((name) => URL_HELPERS.has(name))) kind = 'urlHelper'
  else if (target === 'lib/storage') kind = fromLayer === 'api' ? 'lib/storage' : 'pure'
  if (fromRel === SESSION_EXCEPTION.file && SESSION_EXCEPTION.allowed[target] && imp.names.every((name) => SESSION_EXCEPTION.allowed[target].includes(name))) return
  let allowed = LAYER_RULES[fromLayer] ?? []
  if (fromLayer === 'app' && kind === 'app') allowed = LAYER_RULES.app
  if (!allowed.includes(kind)) {
    const what = kind === 'api' ? `api value(s) ${imp.names.join(', ') || '(side effect)'} from ${target}` : `${target} (${kind})`
    violation(fromRel, imp.line, 'layer', `${fromLayer}/ must not import ${what}`, edge)
  }
}

function checkExports(rel, code) {
  const layer = layerOf(rel)
  const top = rel.split('/')[0]
  if (!['actions', 'store', 'lib', 'api'].includes(top) && layer !== 'pure') return
  for (const m of code.matchAll(/\bexport\s+(?:async\s+)?(?:function\s*\*?\s*|const\s+|let\s+|var\s+|class\s+)(use[A-Z][\w$]*)([^\n]*)/g)) {
    const isStoreHook = top === 'store' && /^\s*(?::[^=]+)?=\s*create\b/.test(m[2])
    if (!isStoreHook) violation(rel, lineAt(code, m.index), 'use-named-export', `non-hook export named ${m[1]} (oxlint rules-of-hooks / React Compiler treat use* as hooks)`)
  }
  for (const m of code.matchAll(/\bexport\s*\{([^}]*)\}/g)) {
    for (const item of m[1].split(',').map((s) => s.trim().split(/\s+as\s+/).pop()).filter(Boolean)) {
      if (/^use[A-Z]/.test(item) && top !== 'store') violation(rel, lineAt(code, m.index), 'use-named-export', `non-hook export named ${item}`)
    }
  }
}

function checkLiterals(rel, strings) {
  for (const literal of strings) {
    if (!rel.startsWith('api/')) {
      for (const fragment of PATH_LITERALS) if (literal.value.includes(fragment)) violation(rel, literal.line, 'UI9', `path literal containing '${fragment}' outside src/api/*.ts (use payload URLs)`)
    }
    if (LITERAL_EXEMPT.has(rel)) continue
    const inUnion = literal.before === '|' || literal.after === '|'
    if (inUnion) continue
    const value = literal.value.startsWith('part:') ? literal.value.slice(5) : literal.value
    if (MODEL_IDS.has(value)) violation(rel, literal.line, 'UI14', `literal model id '${literal.value}' (only lib/featureMeta.ts and type unions)`)
    else if (TAXONOMY_IDS.has(value) || /^component_\d{3}$/.test(value)) violation(rel, literal.line, 'UI14', `literal part/taxonomy id '${literal.value}' (only lib/featureMeta.ts and type unions)`)
  }
}

function findCycles() {
  const cycles = []
  const state = new Map()
  const stack = []
  const visit = (node) => {
    state.set(node, 1)
    stack.push(node)
    for (const next of storeGraph.get(node) ?? []) {
      if (state.get(next) === 1) cycles.push([...stack.slice(stack.indexOf(next)), next].join(' → '))
      else if (!state.has(next)) visit(next)
    }
    stack.pop()
    state.set(node, 2)
  }
  for (const node of storeGraph.keys()) if (!state.has(node)) visit(node)
  return cycles
}

// ------------------------------------------------------------------------------------------------
// Allowlist (deletion-only)
// ------------------------------------------------------------------------------------------------
const globToRegex = (glob) => new RegExp(`^${glob.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*\*/g, '\u0000').replace(/\*/g, '[^/]*').replace(/\u0000/g, '.*')}$`)
function applyAllowlist() {
  const used = new Set()
  const remaining = []
  for (const v of violations) {
    let allowed = false
    if (v.edge) {
      ALLOWLIST.entries.forEach((entry, index) => {
        if (!globToRegex(entry.from).test(v.edge.from)) return
        const targets = Array.isArray(entry.to) ? entry.to : [entry.to]
        if (!targets.includes(v.edge.to)) return
        const names = entry.names ?? ['*']
        if (!names.includes('*') && !v.edge.names.every((name) => names.includes(name))) return
        used.add(`${index}:${v.edge.to}`)
        allowed = true
      })
    }
    if (!allowed) remaining.push(v)
  }
  const stale = []
  ALLOWLIST.entries.forEach((entry, index) => {
    for (const to of Array.isArray(entry.to) ? entry.to : [entry.to]) if (!used.has(`${index}:${to}`)) stale.push(`stale allowlist entry: delete it (${entry.from} → ${to})`)
  })
  return { remaining, stale, used: used.size }
}

// ------------------------------------------------------------------------------------------------
// Run
// ------------------------------------------------------------------------------------------------
const files = walk(SRC).map((path) => relative(SRC, path).split('\\').join('/')).sort()
let importCount = 0
for (const rel of files) {
  const { code, strings } = scan(readFileSync(join(SRC, rel), 'utf8'))
  const imports = parseImports(code)
  importCount += imports.length
  for (const imp of imports) checkImport(rel, imp)
  checkExports(rel, code)
  checkLiterals(rel, strings)
}
for (const cycle of findCycles()) violation('store/', 0, 'store-cycle', `store import cycle: ${cycle}`)
const { remaining, stale, used } = applyAllowlist()

for (const v of remaining) console.log(`${v.file}${v.line ? `:${v.line}` : ''}  [${v.rule}] ${v.message}`)
for (const line of stale) console.log(line)
console.log(`import boundaries: ${files.length} files, ${importCount} imports, ${remaining.length} violation(s), ${stale.length} stale allowlist entr${stale.length === 1 ? 'y' : 'ies'}, ${used} allowlisted edge(s) (${relative(REPO, SRC) || SRC})`)
process.exit(remaining.length || stale.length ? 1 : 0)
