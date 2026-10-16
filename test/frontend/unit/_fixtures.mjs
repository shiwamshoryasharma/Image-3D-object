/**
 * Shared fixture helpers for the frontend unit tests, the mock backend (strict request mode) and the smoke scripts
 * (UI_DESIGN.md §10.2–§10.4). Test-only; no dependencies. Fixtures live in `test/frontend/fixtures/`; their contents
 * are specified in UI_DESIGN.md §10.3 and checked by `fixtures.test.mjs`.
 *
 *   import { readFixture, readFixtureText, assertKnownKeys } from './_fixtures.mjs'
 *   const g1 = readFixtureText('requests/G1.json')            // exact golden bytes (no trailing newline)
 *   assertKnownKeys(buildWorkflowRequest(...), 'WorkflowRequest')
 */
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const FIXTURES_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'fixtures')

/** Golden-body placeholder ids (UI_DESIGN §10.3); substitute real ids with `withIds`. */
export const PLACEHOLDER_IDS = Object.freeze({
  rs: 'rs_0123456789ab', img: 'img_0123456789ab', job: 'job_0123456789ab', ig: 'ig_0123456789ab',
  sg: 'sg_0123456789ab', pa: 'pa_0123456789ab', wf: 'wf_0123456789ab',
})

export const fixturePath = (name) => join(FIXTURES_DIR, name)

/** Raw fixture text, byte for byte (golden request bodies are compact JSON without a trailing newline). */
export const readFixtureText = (name) => readFileSync(fixturePath(name), 'utf8')

/** Parsed fixture JSON; every call returns a fresh object, so tests may mutate it. */
export const readFixture = (name) => JSON.parse(readFixtureText(name))

/** File names (without `.json`) in a fixture sub-folder, sorted. */
export const listFixtures = (dir) => readdirSync(fixturePath(dir)).filter((name) => name.endsWith('.json')).map((name) => name.slice(0, -5)).sort()

/** Replace placeholder ids in a golden body text: `withIds(text, {rs: 'rs_…', img: 'img_…'})`. */
export function withIds(text, ids) {
  let out = text
  for (const [kind, id] of Object.entries(ids)) {
    const placeholder = PLACEHOLDER_IDS[kind]
    if (!placeholder) throw new Error(`unknown id kind ${kind}`)
    if (id !== undefined && id !== null) out = out.split(placeholder).join(id)
  }
  return out
}

// ------------------------------------------------------------------------------------------------
// Request key sets (fixtures/request_keys.json, UI_DESIGN §2.6)
// ------------------------------------------------------------------------------------------------
export const REQUEST_KEYS = Object.freeze(readFixture('request_keys.json'))

/** Follow `"=Name"` aliases (`node.prompt` → `ImageGenerationRequest`). */
function resolveName(keys, name) {
  let current = name
  const seen = new Set()
  while (typeof keys[current] === 'string' && keys[current].startsWith('=') && !seen.has(current)) {
    seen.add(current)
    current = keys[current].slice(1)
  }
  return current
}

/** Object name for a nested path; `<kind>` is the child's own `kind`, `<type>` the parent's `type` (node params). */
function nameForPath(keys, path, child, parent) {
  const template = keys.paths?.[path]
  if (typeof template !== 'string') return null
  return template.replace('<kind>', String(child?.kind)).replace('<type>', String(parent?.type))
}

const isPlainObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)

/**
 * Walk a request body recursively against the key sets and return every problem:
 * - `{type: 'extra_forbidden', loc, object}`  a key outside the object's set (pydantic `extra="forbid"`);
 * - `{type: 'unnamed_object', loc, path}`    a nested object reached through a path missing from `paths`;
 * - `{type: 'unknown_object', loc, name}`    a path resolved to a name with no key set (e.g. an unknown `kind`).
 * `loc` starts with `'body'` like the backend's 422 details. `opaque` entries (`layout`) are not descended into.
 */
export function findUnknownKeys(body, rootName, keys = REQUEST_KEYS) {
  const problems = []
  const opaque = new Set(keys.opaque ?? [])
  const walk = (value, name, loc) => {
    const resolved = resolveName(keys, name)
    const allowed = keys[resolved]
    if (!Array.isArray(allowed)) {
      problems.push({ type: 'unknown_object', loc, name: resolved })
      return
    }
    if (!isPlainObject(value)) return
    for (const key of Object.keys(value)) {
      if (!allowed.includes(key)) {
        problems.push({ type: 'extra_forbidden', loc: [...loc, key], object: resolved })
        continue
      }
      const child = value[key]
      const path = `${resolved}.${key}`
      if (child === null || typeof child !== 'object' || opaque.has(key) || opaque.has(path)) continue
      if (Array.isArray(child)) {
        child.forEach((item, index) => {
          if (!isPlainObject(item)) return
          const target = nameForPath(keys, `${path}[]`, item, value)
          if (target) walk(item, target, [...loc, key, index])
          else problems.push({ type: 'unnamed_object', loc: [...loc, key, index], path: `${path}[]` })
        })
        continue
      }
      const target = nameForPath(keys, path, child, value) ?? nameForPath(keys, `${resolved}.*`, child, value)
      if (target) walk(child, target, [...loc, key])
      else problems.push({ type: 'unnamed_object', loc: [...loc, key], path })
    }
  }
  walk(body, rootName, ['body'])
  return problems
}

/** Assert that every key of every (nested) object of `body` is in the DESIGN key set of `rootName`. */
export function assertKnownKeys(body, rootName, keys = REQUEST_KEYS) {
  const problems = findUnknownKeys(body, rootName, keys)
  if (problems.length === 0) return
  const lines = problems.map((p) => `${p.type} at ${p.loc.join('.')}${p.object ? ` (${p.object})` : ''}${p.path ? ` (path ${p.path})` : ''}${p.name ? ` (${p.name})` : ''}`)
  assert.fail(`request ${rootName} has keys outside fixtures/request_keys.json:\n  ${lines.join('\n  ')}`)
}
