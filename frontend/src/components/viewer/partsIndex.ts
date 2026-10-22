/**
 * Per-face region labels, face subsets and the explode offset of the viewer's parts view (UI_DESIGN.md §6.6).
 *
 * The parts GLB carries one `_FEATURE_ID_0` value per vertex; DESIGN §3.4 duplicates vertices at region borders on
 * every mesh node, so the three corners of a face share its label. `faceLabels` turns the per-vertex copy (read once
 * with `getX`, see `partsScene.readFeatureIds`) into one label per face; `filterFaces` / `subsetFor` build the index
 * subsets that the viewer writes into a host's draw index or gives to an overlay.
 *
 * PURE (UI_DESIGN §1.5): no imports, no browser globals; typed arrays in, typed arrays out.
 */

/** Label of a face whose corners disagree (or that points past the label array). */
export const NO_LABEL = 0xffff

/** Faces sampled to decide whether the first corner alone gives the face label. */
const CONSISTENCY_SAMPLE = 1000

export type FaceIndexArray = Uint32Array | Uint16Array

/**
 * One label per face. Uses `featureIds[index[3f]]` when the first 1000 faces have consistent corners (the normal
 * case, DESIGN §3.4), else the 3-corner rule (`a === b === c` → that value, otherwise `NO_LABEL`).
 */
export function faceLabels(index: FaceIndexArray, featureIds: Uint16Array): Uint16Array {
  const faces = Math.floor(index.length / 3)
  const out = new Uint16Array(faces)
  const n = featureIds.length
  const label = (vertex: number): number => (vertex < n ? featureIds[vertex] : NO_LABEL)
  const sample = Math.min(faces, CONSISTENCY_SAMPLE)
  let consistent = true
  for (let f = 0; f < sample; f++) {
    const a = label(index[3 * f])
    if (a !== label(index[3 * f + 1]) || a !== label(index[3 * f + 2])) {
      consistent = false
      break
    }
  }
  if (consistent) {
    for (let f = 0; f < faces; f++) out[f] = label(index[3 * f])
    return out
  }
  for (let f = 0; f < faces; f++) {
    const a = label(index[3 * f])
    out[f] = a === label(index[3 * f + 1]) && a === label(index[3 * f + 2]) ? a : NO_LABEL
  }
  return out
}

/** The faces (3 indices each, original order) whose label passes `keep`. `keep` is called once per label value. */
export function filterFaces(index: FaceIndexArray, labels: Uint16Array, keep: (label: number) => boolean): Uint32Array {
  const table = new Uint8Array(NO_LABEL + 1)
  for (let value = 0; value <= NO_LABEL; value++) table[value] = keep(value) ? 1 : 0
  const faces = Math.min(labels.length, Math.floor(index.length / 3))
  let kept = 0
  for (let f = 0; f < faces; f++) kept += table[labels[f]]
  const out = new Uint32Array(kept * 3)
  let o = 0
  for (let f = 0; f < faces; f++) {
    if (table[labels[f]] === 0) continue
    out[o++] = index[3 * f]
    out[o++] = index[3 * f + 1]
    out[o++] = index[3 * f + 2]
  }
  return out
}

/** The faces carrying exactly `value`. */
export function subsetFor(index: FaceIndexArray, labels: Uint16Array, value: number): Uint32Array {
  return filterFaces(index, labels, (label) => label === value)
}

/** `subsetFor` for several values in one pass over the faces (colorize builds every region overlay at once). */
export function subsetsByLabel(index: FaceIndexArray, labels: Uint16Array, values: Iterable<number>): Map<number, Uint32Array> {
  const slot = new Int32Array(NO_LABEL + 1).fill(-1)
  const wanted = [...new Set(values)].filter((value) => Number.isInteger(value) && value >= 0 && value <= NO_LABEL)
  wanted.forEach((value, i) => {
    slot[value] = i
  })
  const faces = Math.min(labels.length, Math.floor(index.length / 3))
  const counts = new Uint32Array(wanted.length)
  for (let f = 0; f < faces; f++) {
    const s = slot[labels[f]]
    if (s >= 0) counts[s]++
  }
  const arrays = wanted.map((_, i) => new Uint32Array(counts[i] * 3))
  const fill = new Uint32Array(wanted.length)
  for (let f = 0; f < faces; f++) {
    const s = slot[labels[f]]
    if (s < 0) continue
    const out = arrays[s]
    let o = fill[s]
    out[o++] = index[3 * f]
    out[o++] = index[3 * f + 1]
    out[o++] = index[3 * f + 2]
    fill[s] = o
  }
  return new Map(wanted.map((value, i) => [value, arrays[i]]))
}

/** Translation of a physical part for the explode amount (0..1): `(part − asset) × amount × 0.6`. */
export function explodeOffset(
  partCentroid: readonly [number, number, number],
  assetCentroid: readonly [number, number, number],
  amount: number,
): [number, number, number] {
  const k = amount * 0.6
  return [
    (partCentroid[0] - assetCentroid[0]) * k,
    (partCentroid[1] - assetCentroid[1]) * k,
    (partCentroid[2] - assetCentroid[2]) * k,
  ]
}

/**
 * Cache key of a host's visible-face state: `''` = every face; `only:<v>` = one label (isolate); else the hidden
 * label values, sorted.
 */
export function visibilityKey(hiddenValues: Iterable<number>, only: number | null): string {
  if (only !== null) return `only:${only}`
  return [...new Set(hiddenValues)].sort((a, b) => a - b).join(',')
}

/** Whether a face with `label` is drawn for the given hidden values / isolated label. */
export function keepLabel(label: number, hiddenValues: ReadonlySet<number>, only: number | null): boolean {
  return only !== null ? label === only : !hiddenValues.has(label)
}

/** Small least-recently-used cache (CPU arrays only; UI_DESIGN §6.6: LRU 8 per host). */
export class LruCache<K, V> {
  readonly #max: number
  readonly #map = new Map<K, V>()

  constructor(max: number) {
    this.#max = Math.max(1, Math.floor(max))
  }

  get size(): number {
    return this.#map.size
  }

  get(key: K): V | undefined {
    const value = this.#map.get(key)
    if (value === undefined) return undefined
    this.#map.delete(key)
    this.#map.set(key, value)
    return value
  }

  set(key: K, value: V): void {
    this.#map.delete(key)
    this.#map.set(key, value)
    while (this.#map.size > this.#max) {
      const oldest = this.#map.keys().next().value as K
      this.#map.delete(oldest)
    }
  }

  clear(): void {
    this.#map.clear()
  }
}
