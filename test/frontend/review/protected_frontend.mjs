/**
 * UI13: the paused Auto Mesh Tuner frontend files stay byte-identical to commit 8654b90 (UI_DESIGN.md §0.4, §10.5).
 *
 *   node test/frontend/review/protected_frontend.mjs                  check frontend/src against protected_frontend.sha256
 *   node test/frontend/review/protected_frontend.mjs --root <frontend> check another frontend tree
 *   node test/frontend/review/protected_frontend.mjs --record <frontend>
 *        rewrite protected_frontend.sha256 from a CLEAN export of 8654b90 (git archive), never from the live tree
 *   --sha-file <path>   read/record another hash list (unit tests; they never touch the recorded file)
 *
 * Hashes are sha256 of the file text with CRLF normalised to LF, so a checkout's line endings do not matter.
 * Added or removed files under components/meshTuner/ fail the check too. Exit code 1 on any difference.
 */
import { createHash } from 'node:crypto'
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..', '..')
const BASELINE_COMMIT = '8654b90'

/** The protected set, relative to the frontend root (UI_DESIGN §0.3 "Paused tuner frontend files untouched"). */
const PROTECTED_DIRS = ['src/components/meshTuner']
const PROTECTED_FILES = ['src/store/meshTunerStore.ts', 'src/lib/meshTuner.ts', 'src/api/blender.ts', 'src/api/tunings.ts', 'src/hooks/useTuneAvailability.ts']

const args = process.argv.slice(2)
const option = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : null)
const SHA_FILE = option('--sha-file') ?? join(HERE, 'protected_frontend.sha256')

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? walk(path) : [path]
  })
}

function protectedFiles(frontendRoot) {
  const files = PROTECTED_FILES.map((rel) => join(frontendRoot, rel)).filter((path) => existsSync(path))
  for (const dir of PROTECTED_DIRS) if (existsSync(join(frontendRoot, dir))) files.push(...walk(join(frontendRoot, dir)))
  return files.map((path) => `frontend/${relative(frontendRoot, path).split('\\').join('/')}`).sort()
}

const hashFile = (path) => createHash('sha256').update(readFileSync(path, 'utf8').replace(/\r\n/g, '\n'), 'utf8').digest('hex')

function readShaFile() {
  const entries = new Map()
  for (const line of readFileSync(SHA_FILE, 'utf8').split(/\r?\n/)) {
    const text = line.trim()
    if (!text || text.startsWith('#')) continue
    const match = /^([0-9a-f]{64})\s+(\S+)$/.exec(text)
    if (!match) throw new Error(`malformed line in ${SHA_FILE}: ${text}`)
    entries.set(match[2], match[1])
  }
  return entries
}

function resolveFile(frontendRoot, repoPath) {
  return join(frontendRoot, repoPath.replace(/^frontend\//, ''))
}

const recordRoot = option('--record')
if (recordRoot) {
  const files = protectedFiles(recordRoot)
  if (files.length === 0) throw new Error(`no protected files under ${recordRoot}`)
  const lines = [
    `# sha256 (CRLF→LF) of the paused Auto Mesh Tuner frontend files at commit ${BASELINE_COMMIT} (UI_DESIGN.md UI13).`,
    '# Recorded by test/frontend/review/protected_frontend.mjs --record from a clean `git archive` export. Do not edit.',
    ...files.map((repoPath) => `${hashFile(resolveFile(recordRoot, repoPath))}  ${repoPath}`),
  ]
  writeFileSync(SHA_FILE, `${lines.join('\n')}\n`)
  console.log(`recorded ${files.length} protected files from ${recordRoot}`)
  process.exit(0)
}

const frontendRoot = option('--root') ?? join(REPO, 'frontend')
const expected = readShaFile()
const present = protectedFiles(frontendRoot)
const problems = []
for (const [repoPath, sha] of expected) {
  const path = resolveFile(frontendRoot, repoPath)
  if (!existsSync(path)) problems.push(`missing:  ${repoPath}`)
  else if (hashFile(path) !== sha) problems.push(`changed:  ${repoPath}`)
}
for (const repoPath of present) if (!expected.has(repoPath)) problems.push(`added:    ${repoPath}`)

if (problems.length) {
  console.log(`protected tuner files differ from ${BASELINE_COMMIT} (UI13):\n  ${problems.join('\n  ')}`)
  process.exit(1)
}
console.log(`protected tuner files: ${expected.size} files byte-identical to ${BASELINE_COMMIT} (CRLF-normalised)`)
