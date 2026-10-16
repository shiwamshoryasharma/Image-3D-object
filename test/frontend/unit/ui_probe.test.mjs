// Unit tests of test/frontend/review/ui_probe.js (UI_DESIGN.md §10.5, UI1; slice F0): the recorded BASELINE, the
// EXPECTED_DEFAULT_DELTA table and the applyDelta rule. The probe is a browser-pane script; without a `document` it
// evaluates to the probe object instead of a report, so it runs here in a plain vm context.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { runInNewContext } from 'node:vm'

const SOURCE = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'review', 'ui_probe.js'), 'utf8')
const probe = runInNewContext(SOURCE, {})
/** Probe values come from another realm (vm context): copy them before deep-equality checks. */
const local = (value) => JSON.parse(JSON.stringify(value))
const KEYS = ['1280x800', '768x1024', '390x844']

/** Today's fingerprint turned into the expected Default fingerprint of a later slice: tuner kept, `add` in after the banner. */
function futureFingerprint(key, add = probe.EXPECTED_DEFAULT_DELTA[key].legacy.add) {
  const today = probe.BASELINE[key].fingerprint
  return [today[0], ...add, ...today.slice(1)]
}

test('BASELINE is recorded at the three baseline viewports; tunerEntries are informational and inside the fingerprint', () => {
  assert.deepEqual(local(Object.keys(probe.BASELINE)), KEYS)
  for (const key of KEYS) {
    const { fingerprint, tunerEntries } = probe.BASELINE[key]
    assert.ok(fingerprint.length > 10, key)
    assert.ok(tunerEntries.length > 0, key)
    assert.ok(tunerEntries.every((entry) => fingerprint.includes(entry)), key)
    assert.ok(tunerEntries.some((entry) => entry.startsWith('button:Blender runtime')), key)
    assert.ok(fingerprint.every((entry) => /^(landmark|heading|button):/.test(entry)), key)
    assert.ok(!fingerprint.includes('button:App settings'), `${key}: today's UI has no App settings gear`)
  }
  assert.ok(probe.BASELINE['1280x800'].tunerEntries.includes('landmark:region:Auto Tune'))
})

test('EXPECTED_DEFAULT_DELTA never removes anything (tuner preserved) and adds the gear (+ GPU memory details at 1280–1439 px)', () => {
  for (const key of KEYS) {
    assert.deepEqual(local(probe.EXPECTED_DEFAULT_DELTA[key].legacy.remove), [], key)
    assert.deepEqual(local(probe.EXPECTED_DEFAULT_DELTA[key].locked.remove), [], key)
    assert.deepEqual(local(probe.EXPECTED_DEFAULT_DELTA[key].locked.add),
      [...local(probe.EXPECTED_DEFAULT_DELTA[key].legacy.add), `note:${probe.TUNER_LOCK_NOTICE}`], key)
  }
  assert.equal(probe.TUNER_LOCK_NOTICE, 'Paused until you approve the part-aware milestone')
  assert.deepEqual(local(probe.EXPECTED_DEFAULT_DELTA['1280x800'].legacy.add), ['button:App settings', 'button:GPU memory details'])
  assert.deepEqual(local(probe.EXPECTED_DEFAULT_DELTA['768x1024'].legacy.add), ['button:App settings'])
  assert.deepEqual(local(probe.EXPECTED_DEFAULT_DELTA['390x844'].legacy.add), ['button:App settings'])
})

test('applyDelta passes for today plus each added entry exactly once (legacy and locked)', () => {
  for (const key of KEYS) {
    const legacy = probe.applyDelta(probe.BASELINE[key], probe.EXPECTED_DEFAULT_DELTA[key].legacy, futureFingerprint(key))
    assert.equal(legacy.pass, true, `${key}: ${JSON.stringify(legacy.firstDifference)}`)
    const lockedAdd = probe.EXPECTED_DEFAULT_DELTA[key].locked.add
    const locked = probe.applyDelta(probe.BASELINE[key], probe.EXPECTED_DEFAULT_DELTA[key].locked, futureFingerprint(key, lockedAdd))
    assert.equal(locked.pass, true, `${key}: ${JSON.stringify(locked.firstDifference)}`)
  }
})

test('applyDelta fails when the tuner is removed, on today\'s UI, a missing or doubled add entry, a reordering or an extra entry', () => {
  const key = '1280x800'
  const delta = probe.EXPECTED_DEFAULT_DELTA[key].legacy
  const baseline = probe.BASELINE[key]
  const tunerGone = futureFingerprint(key).filter((entry) => !baseline.tunerEntries.includes(entry))
  assert.equal(probe.applyDelta(baseline, delta, tunerGone).pass, false)
  const today = probe.applyDelta(baseline, delta, baseline.fingerprint)
  assert.equal(today.pass, false)
  assert.equal(today.addCounts['button:App settings'], 0)
  assert.equal(probe.applyDelta(baseline, delta, futureFingerprint(key, ['button:App settings'])).pass, false)
  const doubled = probe.applyDelta(baseline, delta, [...futureFingerprint(key), 'button:App settings'])
  assert.deepEqual(local([doubled.pass, doubled.firstDifference, doubled.addCounts['button:App settings']]), [false, null, 2])
  const swapped = futureFingerprint(key)
  ;[swapped[5], swapped[6]] = [swapped[6], swapped[5]]
  const reordered = probe.applyDelta(baseline, delta, swapped)
  assert.equal(reordered.pass, false)
  assert.ok(reordered.firstDifference.index >= 2)
  const extra = probe.applyDelta(baseline, delta, [...futureFingerprint(key), 'button:Something new'])
  assert.deepEqual(local([extra.pass, extra.firstDifference.actual]), [false, 'button:Something new'])
})
