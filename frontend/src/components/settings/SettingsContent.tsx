import { Suspense, useId } from 'react'

import type { EngineCapabilities, Resolution, TextureSize } from '../../api/types'
import { useFeatures } from '../../hooks/useFeatures'
import { shouldRenderPartOptions } from '../../lib/generatePlan.ts'
import { selectActiveJob, selectViewedJob, useJobStore } from '../../store/jobStore'
import { usePartOptionsStore } from '../../store/partOptionsStore'
import { useSettingsStore } from '../../store/settingsStore'
import { selectCapabilities, useSystemStore } from '../../store/systemStore'
import { SegmentedControl } from '../common/SegmentedControl'
import { Skeleton } from '../common/Skeleton'
import { Switch } from '../common/Switch'
import { LazyPartOptionsSection } from '../lazyFeatures'
import { AdvancedSettings } from './AdvancedSettings'
import { Field } from './Field'
import { PolygonTargetControl } from './PolygonTargetControl'
import { SeedControl } from './SeedControl'
import styles from './SettingsPanel.module.css'

const RESOLUTION_HINT: Record<number, string> = {
  512: 'Fastest, lowest VRAM — good for quick previews.',
  1024: 'Balanced quality and speed (recommended).',
  1536: 'Maximum detail — slowest and uses the most VRAM.',
}

/** Capabilities from the contract defaults, used only until `/system` responds. */
const FALLBACK_CAPABILITIES: Pick<EngineCapabilities, 'resolutions' | 'texture_sizes' | 'polygon_target' | 'supports_texture'> = {
  resolutions: [512, 1024, 1536],
  texture_sizes: [512, 1024, 2048, 4096],
  polygon_target: { min: 5_000, max: 2_000_000, default: 500_000 },
  supports_texture: true,
}

function textureLabel(size: number): string {
  return size >= 1024 ? `${size / 1024}K` : String(size)
}

export interface SettingsContentProps {
  active?: boolean
  headingLevel?: 2 | 3
  compact?: boolean
  /** Append the part-aware options section (when `/features` allows it, §4.2). Default true. */
  showPartOptions?: boolean
}

/**
 * Generation settings body (persisted). Bounds and options come from `/system` capabilities. The part options are
 * the last section, loaded from the feature chunk only under `shouldRenderPartOptions` (never on a legacy backend),
 * so a legacy Default renders today's DOM exactly (UI1).
 */
export function SettingsContent({ showPartOptions = true }: SettingsContentProps) {
  const form = useSettingsStore((s) => s.form)
  const update = useSettingsStore((s) => s.update)
  const capabilities = useSystemStore(selectCapabilities)
  const activeJob = useJobStore(selectActiveJob)
  const viewedJob = useJobStore(selectViewedJob)
  const features = useFeatures()
  const partForm = usePartOptionsStore((s) => s.form)
  const resolutionLabelId = useId()
  const textureLabelId = useId()

  const caps = capabilities ?? FALLBACK_CAPABILITIES
  const lastSeed = activeJob?.seed ?? viewedJob?.seed ?? null
  const partOptions = showPartOptions && shouldRenderPartOptions(features, partForm)

  return (
    <div className={styles.stack}>
      <Field label="Resolution" labelId={resolutionLabelId} hint={RESOLUTION_HINT[form.resolution]}>
        <SegmentedControl<Resolution>
          label="Resolution"
          value={form.resolution}
          options={caps.resolutions.map((resolution) => ({
            value: resolution,
            label: String(resolution),
            title: RESOLUTION_HINT[resolution],
          }))}
          onChange={(resolution) => update({ resolution })}
        />
      </Field>

      <SeedControl
        seed={form.seed}
        randomize={form.randomize_seed}
        lastSeed={lastSeed}
        onSeedChange={(seed) => update({ seed })}
        onRandomizeChange={(randomize_seed) => update({ randomize_seed })}
      />

      <PolygonTargetControl
        value={form.polygon_target}
        range={caps.polygon_target}
        onChange={(polygon_target) => update({ polygon_target })}
      />

      {caps.supports_texture && (
        <Field
          label="Texture size"
          labelId={textureLabelId}
          hint="Baked PBR texture resolution. Larger textures increase export time and file size."
        >
          <SegmentedControl<TextureSize>
            label="Texture size"
            size="sm"
            value={form.texture_size}
            options={caps.texture_sizes.map((size) => ({ value: size, label: textureLabel(size), title: `${size}×${size}` }))}
            onChange={(texture_size) => update({ texture_size })}
          />
        </Field>
      )}

      <Switch
        label="Remove background"
        description="Uses the image alpha if present, otherwise removes the background automatically."
        checked={form.remove_background}
        onChange={(remove_background) => update({ remove_background })}
      />

      <AdvancedSettings />

      {partOptions &&
        (features.status === 'loading' ? (
          // A stored `parts.enabled` keeps the slot, but the feature chunk waits for /features so a legacy
          // backend never requests it.
          <div data-ui-addition="part-options" aria-busy="true">
            <Skeleton shape="block" height={120} />
          </div>
        ) : (
          <Suspense
            fallback={
              <div data-ui-addition="part-options" aria-busy="true">
                <Skeleton shape="block" height={120} />
              </div>
            }
          >
            <LazyPartOptionsSection />
          </Suspense>
        ))}
    </div>
  )
}
