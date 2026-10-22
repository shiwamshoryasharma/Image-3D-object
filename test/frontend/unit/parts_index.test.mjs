// Unit tests for components/viewer/partsIndex.ts (UI_DESIGN.md §6.6, §10.2; slice F8): per-face labels (first-corner
// fast path and the 3-corner rule), face filtering / subsets, visibility keys, the explode offset and the LRU cache,
// plus the 60 ms budget for one subset over 2 M faces.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  LruCache,
  NO_LABEL,
  explodeOffset,
  faceLabels,
  filterFaces,
  keepLabel,
  subsetFor,
  subsetsByLabel,
  visibilityKey,
} from '../../../frontend/src/components/viewer/partsIndex.ts'

// Two quads (4 faces); vertices duplicated at the label border like DESIGN §3.4.
const INDEX = new Uint32Array([0, 1, 2, 2, 1, 3, 4, 5, 6, 6, 5, 7])
const IDS = new Uint16Array([3, 3, 3, 3, 9, 9, 9, 9])

test('faceLabels uses the first corner when corners agree', () => {
  assert.deepEqual([...faceLabels(INDEX, IDS)], [3, 3, 9, 9])
  assert.deepEqual([...faceLabels(new Uint16Array(INDEX), IDS)], [3, 3, 9, 9])
})

test('faceLabels falls back to the 3-corner rule on inconsistent corners', () => {
  const index = new Uint32Array([0, 1, 4, 4, 5, 6, 0, 1, 2])
  assert.deepEqual([...faceLabels(index, IDS)], [NO_LABEL, 9, 3])
})

test('faceLabels maps vertices past the label array to NO_LABEL and ignores a trailing partial face', () => {
  const index = new Uint32Array([0, 1, 2, 20, 21, 22, 0])
  assert.deepEqual([...faceLabels(index, IDS)], [3, NO_LABEL])
  assert.equal(faceLabels(new Uint32Array(0), IDS).length, 0)
})

test('filterFaces / subsetFor keep whole faces in their original order', () => {
  const labels = faceLabels(INDEX, IDS)
  assert.deepEqual([...subsetFor(INDEX, labels, 9)], [4, 5, 6, 6, 5, 7])
  assert.deepEqual([...subsetFor(INDEX, labels, 3)], [0, 1, 2, 2, 1, 3])
  assert.deepEqual([...subsetFor(INDEX, labels, 4)], [])
  assert.deepEqual([...filterFaces(INDEX, labels, () => true)], [...INDEX])
  const hidden = new Set([3])
  assert.deepEqual([...filterFaces(INDEX, labels, (l) => keepLabel(l, hidden, null))], [4, 5, 6, 6, 5, 7])
})

test('subsetsByLabel equals subsetFor per value in one pass', () => {
  const labels = faceLabels(INDEX, IDS)
  const subsets = subsetsByLabel(INDEX, labels, [9, 3, 4, 9, -1, 70000])
  assert.deepEqual([...subsets.keys()], [9, 3, 4])
  for (const value of [3, 4, 9]) assert.deepEqual([...subsets.get(value)], [...subsetFor(INDEX, labels, value)])
})

test('keepLabel and visibilityKey: isolate wins over hidden; keys are order-free', () => {
  const hidden = new Set([2, 5])
  assert.equal(keepLabel(5, hidden, null), false)
  assert.equal(keepLabel(1, hidden, null), true)
  assert.equal(keepLabel(5, hidden, 5), true)
  assert.equal(keepLabel(1, hidden, 5), false)
  assert.equal(visibilityKey([], null), '')
  assert.equal(visibilityKey([5, 2, 5], null), '2,5')
  assert.equal(visibilityKey([2, 5], null), visibilityKey([5, 2], null))
  assert.equal(visibilityKey([2, 5], 7), 'only:7')
})

test('explodeOffset = (part - asset) * amount * 0.6; amount 0 is exactly zero', () => {
  const offset = explodeOffset([1, 2, 3], [0, 1, 1], 0.5)
  assert.ok(Math.abs(offset[0] - 0.3) < 1e-12)
  assert.ok(Math.abs(offset[1] - 0.3) < 1e-12)
  assert.ok(Math.abs(offset[2] - 0.6) < 1e-12)
  assert.deepEqual(explodeOffset([1, 2, 3], [0, 1, 1], 0).map(Math.abs), [0, 0, 0])
})

test('LruCache keeps the most recently used entries', () => {
  const cache = new LruCache(2)
  cache.set('a', 1)
  cache.set('b', 2)
  assert.equal(cache.get('a'), 1)
  cache.set('c', 3)
  assert.equal(cache.get('b'), undefined)
  assert.equal(cache.get('a'), 1)
  assert.equal(cache.get('c'), 3)
  assert.equal(cache.size, 2)
  cache.clear()
  assert.equal(cache.size, 0)
})

test('one subset over 2 M faces stays within the 60 ms budget (after warm-up)', () => {
  const faces = 2_000_000
  const index = new Uint32Array(faces * 3)
  for (let i = 0; i < index.length; i++) index[i] = i
  const ids = new Uint16Array(faces * 3)
  for (let f = 0; f < faces; f++) ids.fill(f % 40, 3 * f, 3 * f + 3)
  const labels = faceLabels(index, ids)
  assert.equal(labels[41], 1)
  subsetFor(index, labels, 7) // warm-up (JIT)
  const runs = []
  for (let i = 0; i < 3; i++) {
    const t0 = performance.now()
    const subset = subsetFor(index, labels, 7)
    runs.push(performance.now() - t0)
    assert.equal(subset.length, (faces / 40) * 3)
  }
  // Generous on shared CI machines: the median must meet the budget.
  runs.sort((a, b) => a - b)
  assert.ok(runs[1] <= 60, `subset took ${runs[1].toFixed(1)} ms`)
})
