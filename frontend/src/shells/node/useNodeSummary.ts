/**
 * What a built-in node binds to (UI_DESIGN §4.3.12 node bodies, list cards), read from the shared stores:
 * - `useNodeSummary`: one line for list cards — "3 images · primary: front", "a knight with a red… (one-shot)",
 *   "Auto · anime character", "1024 · 500k tris · 2K tex", "7 groups · semantic parts", "GLB + parts GLB";
 * - `useNodeWidgets`: the same values as label/value rows for the canvas node body (read-only; editing happens in
 *   the Inspector).
 */
import { defaultSeparate } from '../../lib/featureFlags.ts'
import { useFeaturesStore } from '../../store/featuresStore'
import { useInputStore } from '../../store/inputStore'
import { selectViewedJob, useJobStore } from '../../store/jobStore'
import { usePartOptionsStore } from '../../store/partOptionsStore'
import { useReferenceStore } from '../../store/referenceStore'
import { useSettingsStore } from '../../store/settingsStore'

const PROMPT_PREVIEW = 28

/** `anime_character` → "anime character" (backend values are shown, never hard-coded). */
export const humanize = (value: string): string => value.replaceAll('_', ' ')

const capitalize = (value: string): string => (value ? value[0].toUpperCase() + value.slice(1) : value)

function count(n: number): string {
  if (n >= 1_000_000) return `${Math.round(n / 100_000) / 10}M`
  if (n >= 1000) return `${Math.round(n / 1000)}k`
  return String(n)
}

const textureLabel = (size: number): string => (size >= 1024 ? `${Math.round(size / 1024)}K` : String(size))

const promptPreview = (text: string): string => (text.length > PROMPT_PREVIEW ? `${text.slice(0, PROMPT_PREVIEW)}…` : text)

/** One read-only row of a node body. `empty` marks a value that still has to be provided. */
export interface NodeWidget {
  readonly label: string
  readonly value: string
  readonly empty?: boolean
}

interface NodeValues {
  readonly imageCount: number
  readonly primarySlot: string | null
  readonly prompt: string
  readonly viewedCompleted: boolean
  readonly resolution: number
  readonly polygons: number
  readonly texture: number
  readonly subject: string
  readonly profile: string
  readonly groups: number
  readonly strategy: string | null
  readonly perPart: boolean
  readonly partsOn: boolean
}

function useNodeValues(): NodeValues {
  const imageCount = useReferenceStore((s) => s.set?.images.length ?? 0)
  const primarySlot = useReferenceStore((s) => s.set?.images.find((i) => i.id === s.set?.primary_image_id)?.slot ?? null)
  const prompt = useInputStore((s) => s.prompt.prompt)
  const viewedCompleted = useJobStore((s) => selectViewedJob(s)?.status === 'completed')
  const resolution = useSettingsStore((s) => s.form.resolution)
  const polygons = useSettingsStore((s) => s.form.polygon_target)
  const texture = useSettingsStore((s) => s.form.texture_size)
  const subject = usePartOptionsStore((s) => s.form.subject)
  const profile = usePartOptionsStore((s) => s.form.profile)
  const separate = usePartOptionsStore((s) => s.form.separate)
  const strategy = usePartOptionsStore((s) => s.form.strategy)
  const perPart = usePartOptionsStore((s) => s.form.perPartGlbs)
  const partsOn = usePartOptionsStore((s) => s.form.enabled)
  const features = useFeaturesStore((s) => s.snapshot)
  return {
    imageCount,
    primarySlot,
    prompt: prompt.trim(),
    viewedCompleted,
    resolution,
    polygons,
    texture,
    subject,
    profile,
    groups: (separate ?? defaultSeparate(features)).length,
    strategy: strategy ?? features.parts.defaultStrategy ?? null,
    perPart,
    partsOn,
  }
}

export function useNodeSummary(type: string): string | null {
  const v = useNodeValues()
  switch (type) {
    case 'input_image':
      if (!v.imageCount) return 'No reference images yet'
      return `${v.imageCount} image${v.imageCount === 1 ? '' : 's'}${v.primarySlot ? ` · primary: ${humanize(v.primarySlot)}` : ''}`
    case 'prompt':
      return v.prompt ? `${promptPreview(v.prompt)} (one-shot)` : 'No prompt yet'
    case 'job_result':
      return v.viewedCompleted ? 'The viewed model' : 'Open a finished model'
    case 'segmentation':
      return `${capitalize(humanize(v.profile))} · ${v.subject === 'auto' ? 'any subject' : humanize(v.subject)}`
    case 'image_to_3d':
      return `${v.resolution} · ${count(v.polygons)} tris · ${textureLabel(v.texture)} tex`
    case 'part_extraction':
      return `${v.groups} group${v.groups === 1 ? '' : 's'}${v.strategy ? ` · ${humanize(v.strategy)}` : ''}`
    case 'export':
      return v.partsOn ? (v.perPart ? 'GLB + parts GLB + part files' : 'GLB + parts GLB') : 'GLB'
    default:
      return null
  }
}

export function useNodeWidgets(type: string): readonly NodeWidget[] {
  const v = useNodeValues()
  switch (type) {
    case 'input_image':
      return [
        { label: 'images', value: v.imageCount ? String(v.imageCount) : 'none', empty: !v.imageCount },
        { label: 'primary', value: v.primarySlot ? humanize(v.primarySlot) : '—' },
      ]
    case 'prompt':
      return [
        { label: 'text', value: v.prompt ? promptPreview(v.prompt) : 'empty', empty: !v.prompt },
        { label: 'mode', value: 'one-shot' },
      ]
    case 'job_result':
      return [{ label: 'model', value: v.viewedCompleted ? 'viewed model' : 'none', empty: !v.viewedCompleted }]
    case 'segmentation':
      return [
        { label: 'profile', value: humanize(v.profile) },
        { label: 'subject', value: v.subject === 'auto' ? 'any' : humanize(v.subject) },
      ]
    case 'image_to_3d':
      return [
        { label: 'resolution', value: String(v.resolution) },
        { label: 'triangles', value: count(v.polygons) },
        { label: 'texture', value: textureLabel(v.texture) },
      ]
    case 'part_extraction':
      return [
        { label: 'groups', value: String(v.groups) },
        { label: 'strategy', value: v.strategy ? humanize(v.strategy) : '—' },
      ]
    case 'export':
      return [
        { label: 'format', value: 'GLB' },
        { label: 'parts GLB', value: v.partsOn ? 'on' : 'off' },
        { label: 'part files', value: v.partsOn && v.perPart ? 'on' : 'off' },
      ]
    default:
      return []
  }
}
