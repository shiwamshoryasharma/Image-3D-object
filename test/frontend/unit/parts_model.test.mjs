// Unit tests for lib/partsModel.ts (UI_DESIGN.md §6.1, §6.3–§6.5, §10.2; slice F2): part analysis payload + manifest
// → `PartSetView`, the normalisation rules 1–7 (incl. the judge-grafted hard reject of duplicate ids and the
// shared-label rule), and the display helpers (groups, hierarchy, relations, display states, colour slots).
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { readFixture } from './_fixtures.mjs'
import { LEGACY_FEATURES, normalizeFeatures } from '../../../frontend/src/lib/featureFlags.ts'
import {
  MAX_PARTS,
  displayGroupOf,
  displayOrder,
  displayStates,
  findRegionAttribute,
  groupsForDisplay,
  hierarchy,
  isValidPartId,
  normalizePartResult,
  partColorIndex,
  partColorIndices,
  relationsOf,
} from '../../../frontend/src/lib/partsModel.ts'

const FULL = normalizeFeatures(readFixture('features/full.json'))
const run = () => readFixture('part_analysis_completed.json')
const manifest = () => readFixture('manifest_parts.json')

function normalized(r = run(), m = manifest(), f = FULL) {
  const out = normalizePartResult(r, m, f)
  assert.equal(out.ok, true, out.ok ? '' : out.error)
  return out.set
}

const SELECTION = { hidden: [], isolated: null, selected: null, hovered: null }

// ------------------------------------------------------------------------------------------------
// Normalisation
// ------------------------------------------------------------------------------------------------

test('payload + manifest → a complete set (rule 1: base data from result.parts, details from the manifest)', () => {
  const r = run()
  const s = normalized(r)
  assert.equal(s.analysisId, r.id)
  assert.equal(s.jobId, r.job_id)
  assert.equal(s.status, 'completed')
  assert.equal(s.hasManifest, true)
  assert.deepEqual(s.parts.map((p) => p.id), r.result.parts.map((p) => p.id))
  assert.equal(Object.keys(s.byId).length, r.result.parts.length)
  for (const p of s.parts) assert.equal(s.byId[p.id], p)

  const hair = s.byId.hair
  assert.equal(hair.kind, 'physical')
  assert.equal(hair.nodeName, 'part:hair')
  assert.equal(hair.parentId, 'body')
  assert.equal(hair.attachToId, 'body.head.face')
  assert.equal(hair.regionValue, 30)
  assert.deepEqual(hair.centroid, [0, 0.453271, 0])
  assert.equal(hair.groupNote, FULL.parts.separableGroups.find((g) => g.id === 'hair').note)
  assert.equal(hair.modelUrl, r.result.parts.find((p) => p.id === 'hair').model_url)
  assert.equal(hair.downloadUrl, r.result.parts.find((p) => p.id === 'hair').download_url)
  assert.equal(hair.confidence, 0.88)
  assert.deepEqual(hair.refinement, { status: 'untouched', locked: true, eligible: true, needs: ['closure'] })

  const eye = s.byId['eye.left']
  assert.equal(eye.symmetricWithId, 'eye.right')
  assert.equal(eye.side, 'left')
  assert.equal(eye.geometryNote, 'Hidden geometry not generated')
  assert.equal(s.byId.hair.geometryNote, null)

  const body = s.byId.body
  assert.equal(body.kind, 'base')
  assert.equal(body.parentId, null) // the base record names itself as parent
  assert.equal(body.floaterCount, 2)
  assert.deepEqual(body.geometry, { status: 'open_surface', openBoundaryLoops: 4, watertight: false })
  assert.equal(s.byId.hair.floaterCount, 0) // floaters count on the base record only

  assert.equal(s.byId['body.head.face'].hostId, 'body')
  assert.equal(s.relations.length, manifest().relations.length)
  assert.deepEqual(s.missingExpected, ['object.held'])
})

test('kind mapping: physical_deferred → deferred; regions and deferred parts have no node name', () => {
  const s = normalized()
  const kinds = Object.fromEntries(s.parts.map((p) => [p.id, p.kind]))
  assert.equal(kinds.body, 'base')
  assert.equal(kinds['eye.left'], 'physical')
  assert.equal(kinds['eyebrow.left'], 'deferred')
  assert.equal(kinds['body.head.face'], 'region')
  for (const p of s.parts) {
    if (p.kind === 'region' || p.kind === 'deferred') assert.equal(p.nodeName, null, p.id)
    else assert.equal(p.nodeName, `part:${p.id}`, p.id)
  }
})

test('unknown kinds are skipped with a warning; the rest of the list stays', () => {
  const r = run()
  r.result.parts.find((p) => p.id === 'hair').kind = 'hologram'
  const s = normalized(r)
  assert.equal('hair' in s.byId, false)
  assert.equal(s.parts.length, r.result.parts.length - 1)
  assert.ok(s.warnings.some((w) => w.code === 'unknown_part_kind' && w.partId === 'hair'))
})

test('without a manifest: a usable list, no relations, no details', () => {
  const s = normalized(run(), null)
  assert.equal(s.hasManifest, false)
  assert.equal(s.parts.length, run().result.parts.length)
  assert.deepEqual(s.relations, [])
  assert.deepEqual(s.missingExpected, [])
  const hair = s.byId.hair
  assert.deepEqual([hair.parentId, hair.attachToId, hair.hostId, hair.symmetricWithId, hair.centroid, hair.geometry], [null, null, null, null, null, null])
  assert.equal(hair.nodeName, 'part:hair')
  assert.equal(s.byId.body.floaterCount, 0) // no geometry → no floaters
  assert.equal(s.regionAttribute, '_FEATURE_ID_0') // from the result
  // run warnings are listed when there is no manifest
  assert.deepEqual(s.warnings.map((w) => w.code), run().warnings.map((w) => w.code))
})

test('manifest warnings carry part ids; run warnings with other codes are added once', () => {
  const r = run()
  r.warnings.push({ code: 'segmentation_rerun_for_alignment', message: 'Segmented again.', step_id: 'segmentation_2d' })
  const s = normalized(r)
  const codes = s.warnings.map((w) => w.code)
  assert.deepEqual(codes, [...manifest().warnings.map((w) => w.code), 'segmentation_rerun_for_alignment'])
  assert.equal(s.warnings.find((w) => w.code === 'floater_candidates').partId, 'body')
})

test('quality, counts and URLs are copied from the payload (rule 6: nothing constructed)', () => {
  const r = run()
  const s = normalized(r)
  assert.deepEqual(s.quality, { cameraFitIou: 0.91, unlabelledFaceRatio: 0.0061, partnerFilledRatio: 0.43, cutPartnerConsistency: 0.97 })
  assert.deepEqual(s.counts, { parts: 6, physical: 4, deferred: 2, regions: 4 })
  assert.deepEqual(s.urls, {
    assembled: r.result.assembled_url,
    download: r.result.download_url,
    zip: r.result.zip_url,
    manifest: r.result.manifest_url,
    overlay: r.result.overlay_url,
  })
  assert.ok(s.urls.assembled)
  const components = run()
  Object.assign(components.result, { overlay_url: null, camera_fit_iou: null, cut_partner_consistency: null, status: 'partial' })
  const c = normalized(components)
  assert.deepEqual([c.urls.overlay, c.quality.cameraFitIou, c.quality.cutPartnerConsistency, c.status], [null, null, null, 'partial'])
})

test('rule 2: a manifest that is not major 1 → unsupported_manifest (the caller retries with manifest = null)', () => {
  for (const schema of ['img3d.parts/2', 'img3d.parts/10', 'parts/1', '', undefined]) {
    const m = manifest()
    m.schema = schema
    assert.deepEqual(normalizePartResult(run(), m, FULL), { ok: false, error: 'unsupported_manifest' }, String(schema))
  }
  assert.deepEqual(normalizePartResult(run(), 'not json', FULL), { ok: false, error: 'unsupported_manifest' })
  const minor = manifest()
  minor.schema = 'img3d.parts/1.3'
  assert.equal(normalizePartResult(run(), minor, FULL).ok, true)
  // the documented fallback keeps a list
  assert.equal(normalizePartResult(run(), null, FULL).ok, true)
})

test('rule 3: invalid part ids → invalid_part_id; duplicates → duplicate_part_id (payload or manifest)', () => {
  for (const bad of ['Hair', '1hair', 'hair..x', 'hair-x', 'a.b.c.d.e.f.g', `h${'a'.repeat(80)}`, '', 42]) {
    const r = run()
    r.result.parts[1].id = bad
    assert.deepEqual(normalizePartResult(r, null, FULL), { ok: false, error: 'invalid_part_id' }, String(bad))
  }
  const dup = run()
  dup.result.parts[2].id = 'hair'
  assert.deepEqual(normalizePartResult(dup, null, FULL), { ok: false, error: 'duplicate_part_id' })
  const dupManifest = manifest()
  dupManifest.parts[3].id = dupManifest.parts[2].id
  assert.deepEqual(normalizePartResult(run(), dupManifest, FULL), { ok: false, error: 'duplicate_part_id' })
  const badManifest = manifest()
  badManifest.parts[1].id = 'Bad Id'
  assert.deepEqual(normalizePartResult(run(), badManifest, FULL), { ok: false, error: 'invalid_part_id' })
  assert.equal(isValidPartId('accessory.jewelry_2'), true)
  assert.equal(isValidPartId('a.b.c.d.e.f'), true) // five dotted segments after the first
})

test('malformed payloads → malformed (never a throw)', () => {
  for (const mutate of [(r) => { r.result = null }, (r) => { r.result.parts = 'x' }, (r) => { r.result.parts.push(null) }]) {
    const r = run()
    mutate(r)
    assert.deepEqual(normalizePartResult(r, null, FULL), { ok: false, error: 'malformed' })
  }
  assert.deepEqual(normalizePartResult(null, null, FULL), { ok: false, error: 'malformed' })
})

test(`at most ${MAX_PARTS} parts are listed, with a warning`, () => {
  const r = run()
  const template = r.result.parts.find((p) => p.id === 'body.head.face')
  r.result.parts = Array.from({ length: MAX_PARTS + 5 }, (_, i) => ({ ...template, id: `region_${i}`, region_value: i + 1 }))
  const s = normalized(r, null)
  assert.equal(s.parts.length, MAX_PARTS)
  assert.ok(s.warnings.some((w) => w.code === 'parts_truncated'))
})

test('rule 5: the region attribute is matched case-insensitively against geometry attributes', () => {
  const s = normalized()
  assert.equal(s.regionAttribute, '_FEATURE_ID_0')
  assert.equal(findRegionAttribute(['position', 'normal', 'uv', '_feature_id_0'], s.regionAttribute), '_feature_id_0')
  assert.equal(findRegionAttribute(new Set(['_FEATURE_ID_0']), '_feature_id_0'), '_FEATURE_ID_0')
  assert.equal(findRegionAttribute(['position', 'normal'], s.regionAttribute), null)
  assert.equal(findRegionAttribute(['_feature_id_0'], ''), null)
  const r = run()
  delete r.result.region_attribute
  assert.equal(normalized(r).regionAttribute, manifest().region_attribute) // manifest fallback
})

test('rule 7: value-0 regions and regions sharing (host, value) get highlight shared_label; the others ok', () => {
  for (const s of [normalized(), normalized(run(), null)]) {
    const shared = s.parts.filter((p) => p.highlight === 'shared_label').map((p) => p.id).sort()
    assert.deepEqual(shared, ['accessory.jewelry', 'accessory.jewelry_2', 'component_001'])
    for (const p of s.parts) if (!shared.includes(p.id)) assert.equal(p.highlight, 'ok', p.id)
  }
  // the same value on different hosts is not shared
  const m = manifest()
  m.parts.find((p) => p.id === 'accessory.jewelry_2').host = 'clothing.top'
  const s = normalized(run(), m)
  assert.equal(s.byId['accessory.jewelry'].highlight, 'ok')
  assert.equal(s.byId['accessory.jewelry_2'].highlight, 'ok')
  assert.equal(s.byId.component_001.highlight, 'shared_label')
  // physical parts never get shared_label, even with a duplicated value
  const r = run()
  r.result.parts.find((p) => p.id === 'eye.right').region_value = 20
  assert.equal(normalized(r).byId['eye.right'].highlight, 'ok')
})

test('normalisation does not mutate its inputs', () => {
  const r = run()
  const m = manifest()
  const before = JSON.stringify([r, m])
  normalized(r, m)
  assert.equal(JSON.stringify([r, m]), before)
})

// ------------------------------------------------------------------------------------------------
// Display helpers
// ------------------------------------------------------------------------------------------------

test('groupsForDisplay: Base, Eyes, Eyebrows, …, Face, Body, Other; labels from /features', () => {
  const s = normalized()
  const groups = groupsForDisplay(s, FULL)
  assert.deepEqual(groups.map((g) => g.group), ['base', 'eyes', 'eyebrows', 'hair', 'clothes', 'accessories', 'face', 'other'])
  assert.deepEqual(groups.map((g) => g.label), ['Base', 'Eyes', 'Eyebrows', 'Hair', 'Clothes', 'Accessories', 'Face', 'Other'])
  assert.deepEqual(groups.find((g) => g.group === 'eyes').parts.map((p) => p.id), ['eye.left', 'eye.right'])
  assert.deepEqual(groups[0].parts.map((p) => p.id), ['body'])
  // labels fall back to the fixed vocabulary without /features
  assert.deepEqual(groupsForDisplay(s, LEGACY_FEATURES).map((g) => g.label), ['Base', 'Eyes', 'Eyebrows', 'Hair', 'Clothes', 'Accessories', 'Face', 'Other'])
  // every part appears exactly once
  assert.equal(groups.reduce((n, g) => n + g.parts.length, 0), s.parts.length)
})

test('displayGroupOf: unknown groups go to Other; a non-base part in group body goes to Body', () => {
  const r = run()
  r.result.parts.find((p) => p.id === 'hair').group = 'tentacles'
  r.result.parts.push({ ...r.result.parts.find((p) => p.id === 'body.head.face'), id: 'body.torso', group: 'body', region_value: 4 })
  const s = normalized(r, null)
  assert.equal(displayGroupOf(s.byId.hair), 'other')
  assert.equal(displayGroupOf(s.byId['body.torso']), 'body')
  assert.equal(displayGroupOf(s.byId.body), 'base')
  const order = displayOrder(s).map((p) => p.id)
  assert.ok(order.indexOf('body.torso') > order.indexOf('body.head.face'))
  assert.ok(order.indexOf('hair') > order.indexOf('body.torso')) // Other is last
})

test('hierarchy: depth-first by parentId, siblings in payload order', () => {
  const tree = hierarchy(normalized())
  assert.deepEqual(tree[0], { part: normalized().byId.body, depth: 0 })
  assert.equal(tree.length, run().result.parts.length)
  assert.ok(tree.slice(1).every((n) => n.depth === 1))
  assert.deepEqual(tree.slice(1).map((n) => n.part.id), run().result.parts.slice(1).map((p) => p.id))
  // nested parents and cycles
  const m = manifest()
  m.parts.find((p) => p.id === 'eye.left').parent = 'body.head.face'
  m.parts.find((p) => p.id === 'hair').parent = 'clothing.top'
  m.parts.find((p) => p.id === 'clothing.top').parent = 'hair'
  const nested = hierarchy(normalized(run(), m))
  const depth = Object.fromEntries(nested.map((n) => [n.part.id, n.depth]))
  assert.equal(depth['eye.left'], 2)
  assert.equal(nested.length, run().result.parts.length) // cycle members are still listed
  assert.equal(new Set(nested.map((n) => n.part.id)).size, nested.length)
  // without a manifest every part is a root
  assert.ok(hierarchy(normalized(run(), null)).every((n) => n.depth === 0))
})

test('relationsOf: outgoing and incoming relations in manifest order', () => {
  const s = normalized()
  assert.deepEqual(relationsOf(s, 'eye.left'), [
    { type: 'attached_to', otherId: 'body.head.face', direction: 'out' },
    { type: 'symmetric_with', otherId: 'eye.right', direction: 'out' },
  ])
  assert.deepEqual(relationsOf(s, 'eye.right'), [
    { type: 'attached_to', otherId: 'body.head.face', direction: 'out' },
    { type: 'symmetric_with', otherId: 'eye.left', direction: 'in' },
  ])
  assert.ok(relationsOf(s, 'body').some((r) => r.type === 'layered_over' && r.otherId === 'clothing.top' && r.direction === 'in'))
  assert.deepEqual(relationsOf(s, 'nope'), [])
  assert.deepEqual(relationsOf(normalized(run(), null), 'eye.left'), [])
})

test('displayStates for hidden / isolated / selected / hovered combinations', () => {
  const s = normalized()
  const all = (states, value) => Object.values(states).every((v) => v === value)
  assert.ok(all(displayStates(s, SELECTION), 'normal'))
  assert.equal(Object.keys(displayStates(s, SELECTION)).length, s.parts.length)

  const sel = displayStates(s, { ...SELECTION, selected: 'hair', hovered: 'eye.left' })
  assert.deepEqual([sel.hair, sel['eye.left'], sel.body], ['selected', 'highlighted', 'normal'])

  const same = displayStates(s, { ...SELECTION, selected: 'hair', hovered: 'hair' })
  assert.equal(same.hair, 'selected') // selection wins over hover

  const hidden = displayStates(s, { ...SELECTION, hidden: ['hair', 'eye.right'], selected: 'hair', hovered: 'eye.right' })
  assert.deepEqual([hidden.hair, hidden['eye.right'], hidden['eye.left']], ['hidden', 'hidden', 'normal'])

  const iso = displayStates(s, { ...SELECTION, isolated: 'eye.left', hidden: ['hair'], hovered: 'body' })
  assert.equal(iso['eye.left'], 'normal')
  assert.equal(iso.hair, 'hidden') // hidden by the user stays hidden
  assert.equal(iso.body, 'dimmed') // outside the isolated part
  assert.ok(Object.entries(iso).filter(([id]) => !['eye.left', 'hair'].includes(id)).every(([, v]) => v === 'dimmed'))

  const isoSelected = displayStates(s, { ...SELECTION, isolated: 'eye.left', selected: 'eye.left' })
  assert.equal(isoSelected['eye.left'], 'selected')

  const unknownIsolation = displayStates(s, { ...SELECTION, isolated: 'not_a_part' })
  assert.ok(all(unknownIsolation, 'normal'))
})

test('partColorIndex: stable 1..12 by display order; 0 for unknown ids', () => {
  const s = normalized()
  const order = displayOrder(s)
  order.forEach((p, i) => assert.equal(partColorIndex(s, p.id), (i % 12) + 1, p.id))
  assert.equal(partColorIndex(s, 'body'), 1)
  assert.equal(partColorIndex(s, 'nope'), 0)
  const bulk = partColorIndices(s)
  for (const p of s.parts) assert.equal(bulk[p.id], partColorIndex(s, p.id))
  // stable across re-normalisation and independent of the manifest
  const again = normalized()
  for (const p of s.parts) assert.equal(partColorIndex(again, p.id), partColorIndex(s, p.id))
  const noManifest = normalized(run(), null)
  for (const p of s.parts) assert.equal(partColorIndex(noManifest, p.id), partColorIndex(s, p.id))
  // more than 12 parts wrap around
  const r = run()
  const template = r.result.parts.find((p) => p.id === 'body.head.face')
  r.result.parts = Array.from({ length: 30 }, (_, i) => ({ ...template, id: `region_${i}`, region_value: i + 1 }))
  const many = normalized(r, null)
  const indices = many.parts.map((p) => partColorIndex(many, p.id))
  assert.ok(indices.every((n) => n >= 1 && n <= 12))
  assert.equal(indices[12], 1)
})
