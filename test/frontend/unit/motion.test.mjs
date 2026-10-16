// Unit tests for lib/motion.ts (UI_DESIGN.md §3.5 "Motion", §7 item 9, slice F4): JS-driven motion is reduced when
// App settings write <html data-motion="reduce"> or the system reports prefers-reduced-motion. `document` and
// `window.matchMedia` are stubbed on globalThis per test and restored afterwards.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { prefersReducedMotion, scrollBehavior } from '../../../frontend/src/lib/motion.ts'

const REDUCE_QUERY = '(prefers-reduced-motion: reduce)'

function stub({ motion, systemReduce }) {
  globalThis.document = { documentElement: { dataset: motion === undefined ? {} : { motion } } }
  globalThis.window = {
    matchMedia: (query) => ({ matches: query === REDUCE_QUERY && systemReduce, media: query }),
  }
}

afterEach(() => {
  delete globalThis.document
  delete globalThis.window
})

test('no DOM (server, Node): motion allowed', () => {
  assert.equal(prefersReducedMotion(), false)
  assert.equal(scrollBehavior(), 'smooth')
})

test('App settings "Reduce motion" wins over the system setting', () => {
  stub({ motion: 'reduce', systemReduce: false })
  assert.equal(prefersReducedMotion(), true)
  assert.equal(scrollBehavior(), 'auto')
})

test('"Follow system" (data-motion="system") follows prefers-reduced-motion', () => {
  stub({ motion: 'system', systemReduce: true })
  assert.equal(prefersReducedMotion(), true)
  stub({ motion: 'system', systemReduce: false })
  assert.equal(prefersReducedMotion(), false)
  assert.equal(scrollBehavior(), 'smooth')
})

test('before boot (no data-motion attribute) the system setting decides', () => {
  stub({ motion: undefined, systemReduce: true })
  assert.equal(prefersReducedMotion(), true)
})

test('a browser without matchMedia allows motion unless App settings reduce it', () => {
  globalThis.document = { documentElement: { dataset: {} } }
  globalThis.window = {}
  assert.equal(prefersReducedMotion(), false)
})
