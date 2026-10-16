// Unit tests for the Node registry (UI_DESIGN.md §4.3.2, §4.3.11, slice F3): node types from `CatalogOut` plus the
// UI-only overlays, the judge-mandated guard that rejects an unlocked refine node, duplicate / port-clash errors, the
// built-in fallback registry for legacy backends, live `/features` node states and the Add-node palette (hidden
// types only with "Show unsupported", the refine group only behind the preview flag, present types disabled).
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  BUILTIN_REGISTRY,
  NODE_CATEGORIES,
  REFINE_LOCKED_REASON,
  RegistryError,
  createRegistry,
  defFromCatalogNode,
  registryFromCatalog,
  withNodeStates,
} from '../../../frontend/src/workflow/engine/index.ts'
import { LEGACY_NODE_CODE, LEGACY_NODE_REASON, NODE_OVERLAYS } from '../../../frontend/src/workflow/nodes/index.ts'
import { readFixture } from './_fixtures.mjs'

const CATALOG = readFixture('catalog.json')
const USER_TYPES = ['input_image', 'prompt', 'job_result', 'segmentation', 'image_to_3d', 'part_extraction', 'export']
const REFINE_TYPES = ['part_refine', 'mesh_tune', 'eye_correction', 'rig_correction']
const UNSUPPORTED_TYPES = ['segmentation_3d', 'part_completion', 'part_generation']

const PALETTE_DEFAULT = { showHidden: false, refinementPreview: false, present: new Set() }
const paletteTypes = (groups) => Object.fromEntries(groups.map((g) => [g.category, g.entries.map((e) => e.def.type)]))
const entryOf = (groups, type) => groups.flatMap((g) => g.entries).find((e) => e.def.type === type)

test('registryFromCatalog: the 7 user + 4 refine + 3 unsupported types with the catalog ports', () => {
  const r = registryFromCatalog(CATALOG)
  assert.deepEqual(r.list().map((d) => d.type), [...USER_TYPES, ...REFINE_TYPES, ...UNSUPPORTED_TYPES])
  for (const n of CATALOG.node_types) {
    const def = r.get(n.type)
    assert.ok(def, n.type)
    assert.deepEqual(
      def.inputs.map((p) => ({ ...p, accepts: [...p.accepts] })),
      n.inputs.map((p) => ({ id: p.id, label: p.label, accepts: p.accepts, required: p.required, multiple: p.multiple })),
      `${n.type} inputs`,
    )
    assert.deepEqual(def.outputs.map((p) => ({ ...p })), n.outputs, `${n.type} outputs`)
    assert.equal(def.title, n.title)
    assert.equal(def.category, n.category)
    assert.equal(def.version, n.version)
    assert.equal(def.maxInstances, 1)
    assert.equal(def.available, n.available)
    assert.equal(def.locked, n.locked)
    assert.equal(def.code, n.code)
    assert.equal(def.reason, n.reason)
    assert.equal(def.hiddenByDefault, n.hidden_by_default)
    assert.deepEqual({ ...def.defaults }, n.defaults)
  }
  assert.equal(r.get('ui.mystery'), undefined)
})

test('overlays: bindings, sources and bypass rules come from NODE_OVERLAYS', () => {
  const r = registryFromCatalog(CATALOG)
  const facts = Object.fromEntries(r.list().map((d) => [d.type, [d.binds, d.isSource, d.bypass]]))
  assert.deepEqual(facts.input_image, ['references', true, null])
  assert.deepEqual(facts.prompt, ['prompt', true, null])
  assert.deepEqual(facts.job_result, ['viewed_job', true, null])
  assert.deepEqual(facts.segmentation, ['segmentation', false, 'drop'])
  assert.deepEqual(facts.image_to_3d, ['settings', false, null])
  assert.deepEqual(facts.part_extraction, ['part_options', false, { from: 'mesh', to: 'parts' }])
  assert.deepEqual(facts.export, ['export', false, null])
  // Refine / unsupported types: no overlay row → binds 'none'; a parts → parts node passes its input through.
  for (const type of REFINE_TYPES) assert.deepEqual(facts[type], ['none', false, { from: 'parts', to: 'parts' }])
  assert.deepEqual(facts.segmentation_3d, ['none', false, null])
  assert.equal(r.get('part_refine').locked, true)
})

test('registry definitions are frozen', () => {
  const def = registryFromCatalog(CATALOG).get('image_to_3d')
  assert.ok(Object.isFrozen(def))
  assert.ok(Object.isFrozen(def.inputs))
  assert.ok(Object.isFrozen(def.inputs[0].accepts))
  assert.throws(() => {
    def.inputs.push({})
  }, TypeError)
})

test('guard: createRegistry throws refine_unlocked for an unlocked refine node (catalog and direct)', () => {
  const refine = registryFromCatalog(CATALOG).get('part_refine')
  assert.throws(
    () => createRegistry([{ ...refine, locked: false }]),
    (e) => e instanceof RegistryError && e.code === 'refine_unlocked',
  )
  const catalog = structuredClone(CATALOG)
  catalog.node_types.find((n) => n.type === 'mesh_tune').locked = false
  assert.throws(() => registryFromCatalog(catalog), (e) => e instanceof RegistryError && e.code === 'refine_unlocked')
  // Locked refine nodes are accepted.
  assert.equal(createRegistry([refine]).get('part_refine').locked, true)
})

test('createRegistry throws duplicate_type and port_clash', () => {
  const r = registryFromCatalog(CATALOG)
  const i2d = r.get('image_to_3d')
  assert.throws(() => createRegistry([i2d, i2d]), (e) => e instanceof RegistryError && e.code === 'duplicate_type')
  const twoInputs = { ...i2d, inputs: [i2d.inputs[0], { ...i2d.inputs[0], label: 'Again' }] }
  assert.throws(() => createRegistry([twoInputs]), (e) => e instanceof RegistryError && e.code === 'port_clash')
  const twoOutputs = { ...i2d, type: 'other', outputs: [i2d.outputs[0], i2d.outputs[0]] }
  assert.throws(() => createRegistry([twoOutputs]), (e) => e instanceof RegistryError && e.code === 'port_clash')
})

test('a catalog type this build cannot represent is skipped, not fatal', () => {
  const catalog = structuredClone(CATALOG)
  catalog.node_types.push({ ...structuredClone(catalog.node_types[4]), type: 'future_a', category: 'teleport' })
  catalog.node_types.push({ ...structuredClone(catalog.node_types[4]), type: 'future_b', outputs: [{ id: 'x', label: 'X', type: 'voxels' }] })
  const r = registryFromCatalog(catalog)
  assert.equal(r.get('future_a'), undefined)
  assert.equal(r.get('future_b'), undefined)
  assert.equal(r.list().length, 14)
  assert.equal(defFromCatalogNode(catalog.node_types.at(-1)), null)
})

test('BUILTIN_REGISTRY: only the POST /jobs nodes are available on a legacy backend', () => {
  const available = BUILTIN_REGISTRY.list().filter((d) => d.available).map((d) => d.type)
  assert.deepEqual(available, ['input_image', 'image_to_3d', 'export'])
  for (const def of BUILTIN_REGISTRY.list()) {
    if (def.available) continue
    assert.equal(def.code, LEGACY_NODE_CODE, def.type)
    assert.equal(def.reason, LEGACY_NODE_REASON, def.type)
  }
  assert.equal(LEGACY_NODE_REASON, 'Needs a newer Img3D backend.')
  for (const type of REFINE_TYPES) {
    assert.equal(BUILTIN_REGISTRY.get(type).locked, true)
    assert.equal(BUILTIN_REGISTRY.get(type).hiddenByDefault, true)
  }
  // Same ports as the server catalog (DESIGN §4.7.2).
  const catalog = registryFromCatalog(CATALOG)
  for (const def of BUILTIN_REGISTRY.list()) {
    const server = catalog.get(def.type)
    assert.deepEqual(def.inputs.map((p) => [p.id, [...p.accepts], p.required]), server.inputs.map((p) => [p.id, [...p.accepts], p.required]), def.type)
    assert.deepEqual(def.outputs.map((p) => [p.id, p.type]), server.outputs.map((p) => [p.id, p.type]), def.type)
    assert.equal(def.binds, server.binds, def.type)
  }
})

test('withNodeStates applies /features node states and never unlocks', () => {
  const features = readFixture('features/no-t2i.json')
  const r = withNodeStates(registryFromCatalog(CATALOG), features.nodes)
  assert.equal(r.get('prompt').available, false)
  assert.equal(r.get('prompt').code, 'not_installed')
  assert.match(r.get('prompt').reason, /Z-Image-Turbo/)
  assert.equal(r.get('input_image').available, true)
  const unlocked = features.nodes.map((n) => (n.type === 'part_refine' ? { ...n, locked: false } : n))
  assert.equal(withNodeStates(registryFromCatalog(CATALOG), unlocked).get('part_refine').locked, true)
  // The built-in registry becomes fully usable with a `full` snapshot while the catalog loads.
  const live = withNodeStates(BUILTIN_REGISTRY, readFixture('features/full.json').nodes)
  assert.deepEqual(live.list().filter((d) => d.available).map((d) => d.type), USER_TYPES)
})

test('palette: categories in order; hidden non-refine types only with "Show unsupported"', () => {
  const r = registryFromCatalog(CATALOG)
  const groups = r.palette(PALETTE_DEFAULT)
  assert.deepEqual(groups.map((g) => g.category), ['input', 'analyze', 'generate', 'parts', 'output'])
  assert.deepEqual(groups.map((g) => g.title), ['Inputs', 'Analyze', 'Generate', 'Parts', 'Output'])
  assert.deepEqual(paletteTypes(groups).parts, ['part_extraction'])
  assert.ok(groups.flatMap((g) => g.entries).every((e) => e.enabled && e.reason === null))

  const shown = r.palette({ ...PALETTE_DEFAULT, showHidden: true })
  assert.deepEqual(paletteTypes(shown).parts, ['part_extraction', ...UNSUPPORTED_TYPES])
  assert.equal(shown.some((g) => g.category === 'refine'), false, '"Show unsupported" alone never shows the refine group')
  const unsupported = entryOf(shown, 'segmentation_3d')
  assert.equal(unsupported.enabled, false)
  assert.equal(unsupported.reason, 'Needs spconv/torch_scatter (no Python 3.14 / CUDA 13 wheels)')
  const order = NODE_CATEGORIES.filter((c) => shown.some((g) => g.category === c))
  assert.deepEqual(shown.map((g) => g.category), order)
})

test('palette: the refine group only behind the preview flag, every entry locked', () => {
  const r = registryFromCatalog(CATALOG)
  for (const showHidden of [false, true]) {
    const groups = r.palette({ ...PALETTE_DEFAULT, showHidden, refinementPreview: true })
    const refine = groups.find((g) => g.category === 'refine')
    assert.ok(refine, `refine group with showHidden=${showHidden}`)
    assert.equal(refine.title, 'Refinement (paused)')
    assert.deepEqual(refine.entries.map((e) => e.def.type), REFINE_TYPES)
    for (const entry of refine.entries) {
      assert.equal(entry.enabled, false)
      assert.equal(entry.reason, REFINE_LOCKED_REASON)
    }
    assert.equal(groups.at(-1).category, 'refine')
  }
  assert.equal(REFINE_LOCKED_REASON, 'Paused until you approve refinement')
})

test('palette: present types disabled, unavailable types carry the backend reason', () => {
  const r = withNodeStates(registryFromCatalog(CATALOG), readFixture('features/no-t2i.json').nodes)
  const groups = r.palette({ ...PALETTE_DEFAULT, present: new Set(['image_to_3d', 'input_image']) })
  assert.deepEqual(entryOf(groups, 'image_to_3d'), {
    def: r.get('image_to_3d'),
    enabled: false,
    reason: 'Only one Image-to-3D node is supported.',
  })
  assert.equal(entryOf(groups, 'input_image').reason, 'Only one Input Image node is supported.')
  assert.equal(entryOf(groups, 'prompt').enabled, false)
  assert.equal(entryOf(groups, 'prompt').reason, 'Z-Image-Turbo weights are not installed under backend/.model/t2i/Z-Image-Turbo')
  assert.equal(entryOf(groups, 'export').enabled, true)
})

test('NODE_OVERLAYS covers exactly the seven built-in user types', () => {
  assert.deepEqual(Object.keys(NODE_OVERLAYS).sort(), [...USER_TYPES].sort())
})
