// Unit tests for lib/viewport.ts (UI_DESIGN.md §3.5, §4.4, §4.5.1, §10.2; slice F2): the tablet and mobile posture
// rules with both hysteresis bands and the typing guard (pure functions only — the 120 ms debounce lives in the impure
// hooks/useViewport.ts store), width classes, the low-memory rule and the Node view at two key viewports.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { resolveNodeView } from '../../../frontend/src/lib/uiModes.ts'
import {
  SERVER_VIEWPORT_SNAPSHOT,
  isLowMemory,
  nextMobilePosture,
  nextPostures,
  nextTabletPosture,
  widthClass,
} from '../../../frontend/src/lib/viewport.ts'

const metrics = (width, height, patch = {}) => ({ width, height, coarse: false, hover: true, deviceMemory: null, typing: false, ...patch })

test('tablet posture table of §4.5.1 (from prev = null)', () => {
  const table = [
    [[1024, 768], 'rail'], [[1180, 820], 'rail'], [[1366, 768], 'rail'], [[1920, 1080], 'rail'], [[1280, 800], 'rail'],
    [[812, 375], 'rail'], [[740, 360], 'rail'],
    [[768, 1024], 'bottom'], [[820, 1180], 'bottom'], [[600, 960], 'bottom'],
    [[360, 800], 'compact'], [[390, 844], 'compact'], [[320, 640], 'compact'],
  ]
  for (const [[w, h], expected] of table) assert.equal(nextTabletPosture(null, metrics(w, h)), expected, `${w}×${h}`)
})

test('tablet hysteresis bands', () => {
  // rail ↔ bottom band: 830×800 is below the 840 entry width but above the 800 stay width
  assert.equal(nextTabletPosture('rail', metrics(830, 800)), 'rail')
  assert.equal(nextTabletPosture('bottom', metrics(830, 800)), 'bottom')
  assert.equal(nextTabletPosture(null, metrics(830, 800)), 'bottom')
  // compact ↔ bottom band: 615 px is above the 600 entry width but below the 632 stay width
  assert.equal(nextTabletPosture('compact', metrics(615, 900)), 'compact')
  assert.equal(nextTabletPosture('bottom', metrics(615, 900)), 'bottom')
  assert.equal(nextTabletPosture('compact', metrics(632, 900)), 'bottom')
  // rail aspect band: 0.95·h ≤ w < 1.05·h keeps rail only when already in rail
  assert.equal(nextTabletPosture('rail', metrics(900, 920)), 'rail')
  assert.equal(nextTabletPosture('bottom', metrics(900, 920)), 'bottom')
  assert.equal(nextTabletPosture('rail', metrics(900, 960)), 'bottom') // w < 0.95·h
  assert.equal(nextTabletPosture('rail', metrics(799, 700)), 'bottom') // below the stay width, tall enough
  // phone-landscape band: h < 540 and w ≥ 620 keeps rail
  assert.equal(nextTabletPosture('rail', metrics(630, 530)), 'rail')
  assert.equal(nextTabletPosture(null, metrics(630, 530)), 'bottom')
  assert.equal(nextTabletPosture('rail', metrics(610, 500)), 'bottom') // below 620 wide
  assert.equal(nextTabletPosture(null, metrics(640, 500)), 'rail') // h < 520 and w ≥ 640 enters
})

test('tablet typing guard: the soft keyboard never flips the layout (but boot still resolves)', () => {
  assert.equal(nextTabletPosture('bottom', metrics(768, 400, { typing: true })), 'bottom') // 768×1024 with a keyboard up
  assert.equal(nextTabletPosture('bottom', metrics(1024, 768, { typing: true })), 'bottom')
  assert.equal(nextTabletPosture('rail', metrics(500, 900, { typing: true })), 'rail')
  assert.equal(nextTabletPosture(null, metrics(1024, 768, { typing: true })), 'rail')
  assert.equal(nextTabletPosture('bottom', metrics(1024, 768, { typing: false })), 'rail')
})

test('mobile posture: enter landscape at w ≥ 1.05·h and h < 520', () => {
  const table = [
    [[812, 375], 'landscape'], [[740, 360], 'landscape'],
    [[390, 844], 'portrait'], [[375, 812], 'portrait'], [[320, 640], 'portrait'], [[360, 800], 'portrait'],
    [[1024, 768], 'portrait'], [[1920, 1080], 'portrait'], [[768, 1024], 'portrait'],
    [[545, 515], 'landscape'], [[540, 515], 'portrait'], [[560, 520], 'portrait'], [[500, 480], 'portrait'], // 540 < 1.05·515; 500 < 1.05·480
  ]
  for (const [[w, h], expected] of table) assert.equal(nextMobilePosture(null, metrics(w, h)), expected, `${w}×${h}`)
})

test('mobile hysteresis: stay landscape while w ≥ 0.95·h and h < 540', () => {
  assert.equal(nextMobilePosture('landscape', metrics(500, 510)), 'landscape')
  assert.equal(nextMobilePosture('portrait', metrics(500, 510)), 'portrait')
  assert.equal(nextMobilePosture('landscape', metrics(600, 530)), 'landscape')
  assert.equal(nextMobilePosture(null, metrics(600, 530)), 'portrait')
  assert.equal(nextMobilePosture('landscape', metrics(600, 540)), 'portrait') // h reached 540
  assert.equal(nextMobilePosture('landscape', metrics(470, 500)), 'portrait') // w < 0.95·h
})

test('mobile typing guard keeps the posture while an editable has focus', () => {
  assert.equal(nextMobilePosture('portrait', metrics(812, 375, { typing: true })), 'portrait')
  assert.equal(nextMobilePosture('landscape', metrics(390, 844, { typing: true })), 'landscape')
  assert.equal(nextMobilePosture(null, metrics(812, 375, { typing: true })), 'landscape')
})

test('nextPostures computes both postures from the previous pair', () => {
  assert.deepEqual(nextPostures(null, metrics(812, 375)), { tablet: 'rail', mobile: 'landscape' })
  assert.deepEqual(nextPostures({ tablet: 'rail', mobile: 'portrait' }, metrics(830, 800)), { tablet: 'rail', mobile: 'portrait' })
  assert.deepEqual(nextPostures({ tablet: 'compact', mobile: 'portrait' }, metrics(390, 844)), { tablet: 'compact', mobile: 'portrait' })
})

test('the server snapshot matches §3.5 and is frozen', () => {
  assert.deepEqual(SERVER_VIEWPORT_SNAPSHOT, {
    width: 1280,
    height: 800,
    coarse: false,
    hover: true,
    deviceMemory: null,
    typing: false,
    posture: { tablet: 'rail', mobile: 'portrait' },
  })
  assert.equal(Object.isFrozen(SERVER_VIEWPORT_SNAPSHOT) && Object.isFrozen(SERVER_VIEWPORT_SNAPSHOT.posture), true)
  assert.deepEqual(nextPostures(null, SERVER_VIEWPORT_SNAPSHOT), SERVER_VIEWPORT_SNAPSHOT.posture)
})

test('widthClass boundaries: <600 · 600–839 · 840–1199 · ≥1200', () => {
  const table = [[0, 'compact'], [599, 'compact'], [600, 'medium'], [839, 'medium'], [840, 'expanded'], [1199, 'expanded'], [1200, 'large'], [3840, 'large']]
  for (const [w, expected] of table) assert.equal(widthClass(w), expected, String(w))
})

test('isLowMemory: deviceMemory ≤ 4 GB, or a coarse pointer narrower than 1024 px', () => {
  assert.equal(isLowMemory(metrics(1920, 1080, { deviceMemory: 4 })), true)
  assert.equal(isLowMemory(metrics(1920, 1080, { deviceMemory: 2 })), true)
  assert.equal(isLowMemory(metrics(1920, 1080, { deviceMemory: 8 })), false)
  assert.equal(isLowMemory(metrics(800, 600)), false)
  assert.equal(isLowMemory(metrics(1023, 768, { coarse: true })), true)
  assert.equal(isLowMemory(metrics(1024, 768, { coarse: true })), false)
  assert.equal(isLowMemory(metrics(390, 844, { coarse: true, deviceMemory: 8 })), true)
})

test('resolveNodeView at 812×375 coarse → list, 768×1024 coarse → canvas', () => {
  assert.equal(resolveNodeView('auto', metrics(812, 375, { coarse: true })), 'list')
  assert.equal(resolveNodeView('auto', metrics(768, 1024, { coarse: true })), 'canvas')
})
