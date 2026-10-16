// Unit tests for lib/uiModes.ts (UI_DESIGN.md §3.1, §3.5, §9, §10.2; slice F2): the `?ui=` override, the persisted
// preference sanitizer, the zustand persist envelope reader, and the resolution of every "auto" value.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  DEFAULT_PREFERENCES,
  UI_MODES,
  UI_MODE_META,
  isUiMode,
  parseUiOverride,
  readPersistedState,
  resolveDensity,
  resolveMotion,
  resolveNodeView,
  resolveUiMode,
  sanitizePreferences,
  suggestUiMode,
} from '../../../frontend/src/lib/uiModes.ts'

test('four modes, Default first and default', () => {
  assert.deepEqual([...UI_MODES], ['default', 'node', 'mobile', 'tablet'])
  assert.equal(DEFAULT_PREFERENCES.uiMode, 'default')
  for (const mode of UI_MODES) assert.ok(UI_MODE_META[mode].label && UI_MODE_META[mode].name && UI_MODE_META[mode].description)
  assert.equal(UI_MODE_META.mobile.name, 'Mobile UI')
  assert.equal(isUiMode('tablet'), true)
  assert.equal(isUiMode('auto'), false)
})

test('parseUiOverride: valid values, case, repeated parameters, invalid values', () => {
  assert.equal(parseUiOverride('?ui=node'), 'node')
  assert.equal(parseUiOverride('ui=mobile'), 'mobile')
  assert.equal(parseUiOverride('?ui=TaBlEt'), 'tablet')
  assert.equal(parseUiOverride('?ui=%20default%20'), 'default')
  assert.equal(parseUiOverride('?ui=node&x=1&ui=mobile'), 'mobile') // the last occurrence wins
  assert.equal(parseUiOverride('?ui=mobile&ui=bogus'), null) // …even when it is invalid
  for (const search of ['', '?', '?ui=', '?ui=auto', '?ui=desktop', '?mode=node', '?UI=node']) assert.equal(parseUiOverride(search), null, search)
})

test('sanitizePreferences keeps only members of their tuples', () => {
  assert.deepEqual(sanitizePreferences(null), {})
  assert.deepEqual(sanitizePreferences('node'), {})
  assert.deepEqual(sanitizePreferences({ uiMode: 'auto', density: 'huge', motion: 'fast', node: 'x', dismissedHints: 'a' }), {})
  assert.deepEqual(
    sanitizePreferences({ uiMode: 'tablet', density: 'touch', motion: 'reduce', node: { view: 'list', minimap: false, snapToGrid: false, linkStyle: 'straight' }, dismissedHints: ['a', 'a', '', 3, 'b'], extra: 1 }),
    { uiMode: 'tablet', density: 'touch', motion: 'reduce', node: { view: 'list', minimap: false, snapToGrid: false, linkStyle: 'straight' }, dismissedHints: ['a', 'b'] },
  )
  // a partly broken node object keeps its valid members and defaults the rest
  assert.deepEqual(sanitizePreferences({ node: { view: 'grid', minimap: 'yes', linkStyle: 'smoothstep' } }).node, {
    view: 'auto',
    minimap: true,
    snapToGrid: true,
    linkStyle: 'smoothstep',
  })
  assert.equal(sanitizePreferences({ dismissedHints: Array.from({ length: 80 }, (_, i) => `h${i}`) }).dismissedHints.length, 50)
  assert.deepEqual(sanitizePreferences({ uiMode: 'Node' }), {}) // stored values are exact, not case-folded
})

test('readPersistedState: zustand persist envelope → state; anything else → null', () => {
  assert.deepEqual(readPersistedState('{"state":{"uiMode":"node"},"version":1}'), { uiMode: 'node' })
  assert.equal(readPersistedState('{"uiMode":"node"}'), null) // bare object without `state`
  assert.equal(readPersistedState('{not json'), null)
  assert.equal(readPersistedState(null), null)
  assert.equal(readPersistedState(''), null)
  assert.equal(readPersistedState('[1,2]'), null)
  assert.equal(readPersistedState('"text"'), null)
  assert.deepEqual(sanitizePreferences(readPersistedState('{"state":{"uiMode":"mobile","density":"x"},"version":1}')), { uiMode: 'mobile' })
})

test('resolveUiMode: the override wins; a mode whose shell has not landed resolves to Default', () => {
  const all = new Set(UI_MODES)
  const defaultOnly = new Set(['default'])
  assert.equal(resolveUiMode('node', null, all), 'node')
  assert.equal(resolveUiMode('node', 'mobile', all), 'mobile')
  assert.equal(resolveUiMode('default', 'tablet', all), 'tablet')
  assert.equal(resolveUiMode('node', null, defaultOnly), 'default')
  assert.equal(resolveUiMode('default', 'mobile', defaultOnly), 'default')
  assert.equal(resolveUiMode('tablet', 'node', new Set(['default', 'tablet'])), 'default') // override not ready → Default, not the preference
})

// §9 viewports; `coarse` = (pointer: coarse)
const VIEWPORTS = [
  [320, 640], [360, 800], [375, 812], [390, 844], [740, 360], [812, 375], [768, 1024], [820, 1180],
  [1024, 768], [1180, 820], [1280, 800], [1366, 768], [1920, 1080],
]
const SUGGESTED_FINE = ['mobile', 'mobile', 'mobile', 'mobile', 'default', 'default', 'default', 'default', 'default', 'default', 'default', 'default', 'default']
const SUGGESTED_COARSE = ['mobile', 'mobile', 'mobile', 'mobile', 'mobile', 'mobile', 'tablet', 'tablet', 'tablet', 'tablet', 'tablet', 'default', 'default']

test('suggestUiMode at every §9 viewport, both pointer types', () => {
  VIEWPORTS.forEach(([width, height], i) => {
    assert.equal(suggestUiMode({ width, height, coarse: false }), SUGGESTED_FINE[i], `${width}×${height} fine`)
    assert.equal(suggestUiMode({ width, height, coarse: true }), SUGGESTED_COARSE[i], `${width}×${height} coarse`)
  })
})

test('resolveDensity: Mobile/Tablet force touch; otherwise the preference, auto following the pointer', () => {
  for (const pref of ['auto', 'comfortable', 'touch']) {
    for (const coarse of [false, true]) {
      assert.equal(resolveDensity(pref, 'mobile', coarse), 'touch')
      assert.equal(resolveDensity(pref, 'tablet', coarse), 'touch')
    }
  }
  for (const mode of ['default', 'node']) {
    assert.equal(resolveDensity('touch', mode, false), 'touch')
    assert.equal(resolveDensity('comfortable', mode, true), 'comfortable')
    assert.equal(resolveDensity('auto', mode, true), 'touch')
    assert.equal(resolveDensity('auto', mode, false), 'comfortable')
  }
})

test('resolveMotion', () => {
  assert.equal(resolveMotion('reduce'), 'reduce')
  assert.equal(resolveMotion('system'), 'system')
})

const NODE_VIEW_FINE = ['list', 'list', 'list', 'list', 'canvas', 'canvas', 'canvas', 'canvas', 'canvas', 'canvas', 'canvas', 'canvas', 'canvas']
const NODE_VIEW_COARSE = ['list', 'list', 'list', 'list', 'list', 'list', 'canvas', 'canvas', 'canvas', 'canvas', 'canvas', 'canvas', 'canvas']

test('resolveNodeView: explicit preference wins; auto follows the §9 matrix for both pointer types', () => {
  VIEWPORTS.forEach(([width, height], i) => {
    assert.equal(resolveNodeView('auto', { width, height, coarse: false }), NODE_VIEW_FINE[i], `${width}×${height} fine`)
    assert.equal(resolveNodeView('auto', { width, height, coarse: true }), NODE_VIEW_COARSE[i], `${width}×${height} coarse`)
    assert.equal(resolveNodeView('canvas', { width, height, coarse: true }), 'canvas')
    assert.equal(resolveNodeView('list', { width, height, coarse: false }), 'list')
  })
})
