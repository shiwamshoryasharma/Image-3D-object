// Unit tests for the soft-keyboard rule of hooks/useSoftKeyboard.ts (UI_DESIGN.md §4.4, slice F4): open while an
// editable field has focus and the visual viewport is below 75 % of the layout height; `inset` = the part of the
// layout viewport hidden below the visual viewport (what --keyboard-inset lifts a sheet footer by).
// Only the pure `softKeyboardState` is exercised; the module's DOM listeners run only after a component subscribes.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { softKeyboardState } from '../../../frontend/src/hooks/useSoftKeyboard.ts'

const base = { typing: true, baselineHeight: 844, layoutHeight: 844, viewportHeight: 844, viewportOffsetTop: 0, scale: 1 }

test('no keyboard: full visual viewport', () => {
  assert.deepEqual(softKeyboardState(base), { open: false, inset: 0 })
})

test('iOS / resizes-visual: the visual viewport shrinks, the layout viewport does not', () => {
  assert.deepEqual(softKeyboardState({ ...base, viewportHeight: 508 }), { open: true, inset: 336 })
})

test('the page scrolled under the keyboard (offsetTop) reduces the inset', () => {
  assert.deepEqual(softKeyboardState({ ...base, viewportHeight: 508, viewportOffsetTop: 120 }), { open: true, inset: 216 })
})

test('Android resizes-content: the layout viewport shrinks too, so the keyboard is open but no inset is needed', () => {
  assert.deepEqual(softKeyboardState({ ...base, layoutHeight: 508, viewportHeight: 508 }), { open: true, inset: 0 })
})

test('a small shrink (browser toolbar, < 25 %) is not a keyboard', () => {
  assert.deepEqual(softKeyboardState({ ...base, viewportHeight: 700 }), { open: false, inset: 0 })
})

test('exactly 75 % is not open; just below is', () => {
  assert.equal(softKeyboardState({ ...base, layoutHeight: 800, baselineHeight: 800, viewportHeight: 600 }).open, false)
  assert.equal(softKeyboardState({ ...base, layoutHeight: 800, baselineHeight: 800, viewportHeight: 599 }).open, true)
})

test('without focus in an editable field nothing is reported (pinch zoom, toolbars)', () => {
  assert.deepEqual(softKeyboardState({ ...base, typing: false, viewportHeight: 400 }), { open: false, inset: 0 })
})

test('pinch zoom alone is not a keyboard; zoom plus keyboard is', () => {
  assert.equal(softKeyboardState({ ...base, viewportHeight: 422, scale: 2 }).open, false)
  assert.equal(softKeyboardState({ ...base, viewportHeight: 254, scale: 2 }).open, true)
})

test('an unknown layout height never reports a keyboard', () => {
  assert.deepEqual(softKeyboardState({ ...base, baselineHeight: 0, layoutHeight: 0, viewportHeight: 0 }), { open: false, inset: 0 })
})
