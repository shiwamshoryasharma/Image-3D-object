/**
 * Bundle budgets (UI_DESIGN.md §8.1): gzip -9 (node:zlib) sizes of a production build, as deltas over the baseline
 * recorded from the clean 8654b90 build in bundle_baseline.json.
 *
 *   node test/frontend/review/bundle_budget.mjs <dist>                    print the table, exit 1 on an enforced violation
 *   node test/frontend/review/bundle_budget.mjs <dist> --src <frontend/src>   source tree used for the wave-C check
 *   node test/frontend/review/bundle_budget.mjs <dist> --record-baseline  (F0 only) write bundle_baseline.json from <dist>
 *   --baseline <json>   read/record another baseline file (unit tests; they never touch the recorded file)
 *
 * - Initial set = the `<script type=module>` + `<link rel=modulepreload>` (JS) and `<link rel=stylesheet>` (CSS) files
 *   of `<dist>/index.html`.
 * - Initial JS ≤ baseline + 25 kB is enforced once F6 and F7 have both merged (wave C, detected by
 *   `src/app/ModeSwitch.tsx` + `src/components/featureBundle.ts`, or forced with `--enforce-initial`); printed only
 *   before that. Initial CSS ≤ baseline + 5 kB and "no initial file contains `react-flow__`" are always enforced.
 * - Lazy chunk attribution needs `<dist>/.vite/manifest.json` (F11 sets `build.manifest: true`). Without it only the
 *   initial totals (plus the named vendor chunks, informational) are printed. Rows whose manifest key does not exist yet
 *   (slice not merged) print "n/a".
 * kB = 1000 bytes (as Vite reports).
 */
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..', '..')

const args = process.argv.slice(2)
const option = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : null)
const BASELINE_FILE = option('--baseline') ?? join(HERE, 'bundle_baseline.json')
const dist = args.find((arg, index) => !arg.startsWith('--') && !['--src', '--baseline'].includes(args[index - 1]))
if (!dist) {
  console.error('usage: node test/frontend/review/bundle_budget.mjs <dist> [--src <frontend/src>] [--baseline <json>] [--enforce-initial] [--record-baseline]')
  process.exit(2)
}
const srcRoot = option('--src') ?? join(REPO, 'frontend', 'src')

const gzipCache = new Map()
function gz(relFile) {
  const file = relFile.replace(/^\//, '')
  if (!gzipCache.has(file)) gzipCache.set(file, gzipSync(readFileSync(join(dist, file)), { level: 9 }).length)
  return gzipCache.get(file)
}
const kB = (bytes) => (bytes / 1000).toFixed(2)
const sum = (files) => files.reduce((total, file) => total + gz(file), 0)

// ------------------------------------------------------------------------------------------------
// Initial set from index.html
// ------------------------------------------------------------------------------------------------
const html = readFileSync(join(dist, 'index.html'), 'utf8')
const attr = (tag, name) => new RegExp(`${name}="([^"]+)"`).exec(tag)?.[1] ?? null
const tags = html.match(/<(script|link)\b[^>]*>/g) ?? []
const initialJs = [], initialCss = []
for (const tag of tags) {
  if (tag.startsWith('<script') && attr(tag, 'type') === 'module' && attr(tag, 'src')) initialJs.push(attr(tag, 'src'))
  else if (tag.startsWith('<link') && attr(tag, 'rel') === 'modulepreload') initialJs.push(attr(tag, 'href'))
  else if (tag.startsWith('<link') && attr(tag, 'rel') === 'stylesheet' && !/^https?:/.test(attr(tag, 'href') ?? '')) initialCss.push(attr(tag, 'href'))
}
const assetFiles = existsSync(join(dist, 'assets')) ? readdirSync(join(dist, 'assets')).map((name) => `assets/${name}`) : []
const named = (prefix) => assetFiles.filter((file) => basename(file).startsWith(`${prefix}-`) && file.endsWith('.js'))

// ------------------------------------------------------------------------------------------------
// --record-baseline (F0: from the clean 8654b90 build; F11 later adds its measured Node shell size)
// ------------------------------------------------------------------------------------------------
if (args.includes('--record-baseline')) {
  const previous = existsSync(BASELINE_FILE) ? JSON.parse(readFileSync(BASELINE_FILE, 'utf8')) : {}
  const baseline = {
    $comment: 'gzip -9 bytes (node:zlib) of the clean 8654b90 production build (UI_DESIGN.md §8.1). Recorded by bundle_budget.mjs --record-baseline; F11 adds nodeShell.',
    commit: '8654b90',
    unit: 'bytes',
    initialJs: sum(initialJs),
    initialCss: sum(initialCss),
    chunks: { three: sum(named('three')), r3f: sum(named('r3f')), ModelViewer: sum(named('ModelViewer')) },
    files: Object.fromEntries([...initialJs, ...initialCss, ...named('three'), ...named('r3f'), ...named('ModelViewer')].map((file) => [basename(file).replace(/-[\w-]{8}\.(js|css)$/, '.$1'), gz(file)])),
    ...(previous.nodeShell ? { nodeShell: previous.nodeShell } : {}),
  }
  writeFileSync(BASELINE_FILE, `${JSON.stringify(baseline, null, 2)}\n`)
  console.log(`recorded ${BASELINE_FILE}: initial JS ${kB(baseline.initialJs)} kB, CSS ${kB(baseline.initialCss)} kB`)
  process.exit(0)
}

const baseline = JSON.parse(readFileSync(BASELINE_FILE, 'utf8'))
const waveC = args.includes('--enforce-initial') || (existsSync(join(srcRoot, 'app', 'ModeSwitch.tsx')) && existsSync(join(srcRoot, 'components', 'featureBundle.ts')))

const rows = []
let violations = 0
function row(label, bytes, limit, { enforced = true, note = '' } = {}) {
  const status = bytes === null ? 'n/a' : limit === null ? 'info' : bytes <= limit ? 'ok' : enforced ? 'FAIL' : 'over (not enforced yet)'
  if (status === 'FAIL') violations += 1
  rows.push({ label, size: bytes === null ? 'n/a' : kB(bytes), limit: limit === null ? '–' : kB(limit), status, note })
}
function band(label, bytes, base, tolerance, enforced = true) {
  if (bytes === null) return row(label, null, null)
  const ok = Math.abs(bytes - base) <= tolerance
  if (!ok && enforced) violations += 1
  rows.push({ label, size: kB(bytes), limit: `${kB(base)} ± ${kB(tolerance)}`, status: ok ? 'ok' : enforced ? 'FAIL' : 'over (not enforced yet)', note: '' })
}

const initialJsBytes = sum(initialJs)
row('Initial JS', initialJsBytes, baseline.initialJs + 25_000, { enforced: waveC, note: waveC ? 'baseline + 25 kB' : 'baseline + 25 kB, printed only until F6 + F7 merged' })
row('Initial CSS', sum(initialCss), baseline.initialCss + 5_000, { note: 'baseline + 5 kB' })
const initialText = initialJs.map((file) => readFileSync(join(dist, file.replace(/^\//, '')), 'utf8'))
const flowInitial = initialText.some((text) => text.includes('react-flow__'))
if (flowInitial) violations += 1
rows.push({ label: 'nodeui not initial (no "react-flow__")', size: '–', limit: '–', status: flowInitial ? 'FAIL' : 'ok', note: '' })

// ------------------------------------------------------------------------------------------------
// Manifest-based attribution (after F11)
// ------------------------------------------------------------------------------------------------
const manifestPath = join(dist, '.vite', 'manifest.json')
if (!existsSync(manifestPath)) {
  for (const prefix of ['three', 'r3f', 'ModelViewer']) row(`${prefix} (by file name)`, named(prefix).length ? sum(named(prefix)) : null, null, { note: `baseline ${kB(baseline.chunks[prefix])} kB; enforced once the manifest exists (F11)` })
  rows.push({ label: 'lazy chunk attribution', size: '–', limit: '–', status: 'n/a', note: 'no .vite/manifest.json yet (F11 sets build.manifest)' })
} else {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  const isVendor = (key) => /^(nodeui|three|r3f)-/.test(basename(manifest[key]?.file ?? ''))
  const reach = (start, { dynamic = true, stop = new Set() } = {}) => {
    const seen = new Set()
    const stack = [start]
    while (stack.length) {
      const key = stack.pop()
      if (seen.has(key) || !manifest[key] || stop.has(key)) continue
      seen.add(key)
      stack.push(...(manifest[key].imports ?? []), ...(dynamic ? manifest[key].dynamicImports ?? [] : []))
    }
    return seen
  }
  const entryStatic = reach('index.html', { dynamic: false })
  const jsOf = (keys) => [...new Set([...keys].map((key) => manifest[key]?.file).filter((file) => file?.endsWith('.js')))]
  const lazySet = (key, stop) => (manifest[key] ? new Set([...reach(key, { stop })].filter((k) => !entryStatic.has(k) && !isVendor(k))) : null)
  const ownChunk = (key) => (manifest[key] ? gz(manifest[key].file) : null)

  row('Feature bundle (featureBundle)', ownChunk('src/components/featureBundle.ts'), 30_000)
  row('App settings body', ownChunk('src/components/preferences/AppSettingsBody.tsx'), 8_000)
  const mobile = lazySet('src/shells/mobile/index.ts')
  const tablet = lazySet('src/shells/tablet/index.ts')
  row('Mobile shell + shells/shared', mobile ? sum(jsOf(mobile)) : null, 22_000, { note: 'chunks shared with Tablet counted here' })
  row('Tablet shell (excluding shared)', tablet ? sum(jsOf([...tablet].filter((key) => !mobile?.has(key)))) : null, 12_000)
  const canvasKey = 'src/shells/node/canvas/NodeCanvas.tsx'
  const node = lazySet('src/shells/node/index.ts', new Set([canvasKey]))
  const nodeLimit = baseline.nodeShell ? Math.round(baseline.nodeShell * 1.1) : 30_000
  row('Node shell + engine + workflowStore (excl. canvas)', node ? sum(jsOf(node)) : null, nodeLimit, { note: baseline.nodeShell ? 'F11 measurement + 10 %' : '30 kB until F11 records nodeShell' })
  row('Node canvas own code', ownChunk(canvasKey), 8_000)
  const nodeuiKeys = Object.keys(manifest).filter((key) => basename(manifest[key].file ?? '').startsWith('nodeui-'))
  row('nodeui vendor group', nodeuiKeys.length ? sum(jsOf(nodeuiKeys)) : null, 65_000)
  const nodeuiInitial = nodeuiKeys.some((key) => entryStatic.has(key))
  if (nodeuiInitial) violations += 1
  rows.push({ label: 'nodeui not statically reachable from the entry', size: '–', limit: '–', status: nodeuiInitial ? 'FAIL' : 'ok', note: '' })
  row('ModelViewer chunk', ownChunk('src/components/viewer/ModelViewer.tsx'), baseline.chunks.ModelViewer + 8_000, { note: 'baseline + 8 kB' })
  band('three', named('three').length ? sum(named('three')) : null, baseline.chunks.three, 3_000)
  band('r3f', named('r3f').length ? sum(named('r3f')) : null, baseline.chunks.r3f, 3_000)
}

console.log(`bundle budget for ${dist} (gzip -9, kB = 1000 B)`)
console.log(`baseline ${baseline.commit}: initial JS ${kB(baseline.initialJs)} kB · CSS ${kB(baseline.initialCss)} kB · three ${kB(baseline.chunks.three)} · r3f ${kB(baseline.chunks.r3f)} · ModelViewer ${kB(baseline.chunks.ModelViewer)}`)
console.log(`initial files: ${[...initialJs, ...initialCss].map((file) => basename(file)).join(', ')}`)
const width = Math.max(...rows.map((r) => r.label.length))
for (const r of rows) console.log(`${r.label.padEnd(width)}  ${String(r.size).padStart(8)}  ≤ ${String(r.limit).padEnd(16)} ${r.status}${r.note ? `  (${r.note})` : ''}`)
console.log(violations ? `\n${violations} budget violation(s)` : '\nall enforced budgets ok')
process.exit(violations ? 1 : 0)
