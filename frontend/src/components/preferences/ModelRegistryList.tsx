import { ExternalLink, Flame, MemoryStick } from 'lucide-react'
import { useState } from 'react'

import { freeModelMemory, warmUpModel } from '../../actions/models'
import type { ModelOut } from '../../api/partAwareTypes.ts'
import { useFeatures } from '../../hooks/useFeatures'
import { MODEL_STATE_TEXT } from '../../lib/featureMeta.ts'
import { formatBytes, formatDuration } from '../../lib/format'
import { useFeaturesStore } from '../../store/featuresStore'
import { Button } from '../common/Button'
import { Spinner } from '../common/Spinner'
import { Switch } from '../common/Switch'
import { LicenseBadge } from './LicenseBadge'
import { MODEL_KIND_LABELS, MODEL_KIND_ORDER, WARM_UP_KINDS } from './preferencesText'
import styles from './preferences.module.css'

function stateText(model: ModelOut): string {
  const text = MODEL_STATE_TEXT[model.state]
  if ((model.state === 'unavailable' || model.state === 'error') && model.reason) return `${text}: ${model.reason}`
  return text
}

function facts(model: ModelOut): string[] {
  const out: string[] = []
  if (model.size_bytes != null) out.push(formatBytes(model.size_bytes))
  if (model.vram_estimate_gb != null) out.push(`about ${model.vram_estimate_gb} GB VRAM`)
  if (model.cold_load_s != null) out.push(`cold start about ${formatDuration(model.cold_load_s)}`)
  return out
}

function reloadWarning(model: ModelOut): string {
  if (model.cold_load_s == null) return 'Next use reloads it.'
  return `Next use reloads it (about ${Math.max(1, Math.round(model.cold_load_s / 60))} min).`
}

function ModelRow({ model, busy }: { model: ModelOut; busy: boolean }) {
  const canWarm = WARM_UP_KINDS.has(model.kind) && model.state === 'installed'
  const canFree = model.state === 'loaded'
  const details = facts(model)
  return (
    <li className={styles.modelRow}>
      <div className={styles.modelHead}>
        <span className={styles.modelName}>{model.display_name}</span>
        <LicenseBadge license={model.license} />
      </div>
      <p className={styles.modelState} data-state={model.state}>
        {model.state === 'loading' && <Spinner size={12} />}
        {stateText(model)}
      </p>
      {details.length > 0 && <p className={styles.modelFacts}>{details.join(' · ')}</p>}
      {model.license.url && (
        <a className={styles.licenseLink} href={model.license.url} target="_blank" rel="noreferrer noopener">
          {model.license.name}
          <ExternalLink size={12} aria-hidden />
        </a>
      )}
      {model.license.conditions.length > 0 && (
        <ul className={styles.conditions} aria-label={`Licence conditions of ${model.display_name}`}>
          {model.license.conditions.map((condition) => (
            <li key={condition}>{condition}</li>
          ))}
        </ul>
      )}
      {(canWarm || canFree) && (
        <div className={styles.modelActions}>
          {canWarm && (
            <Button size="sm" variant="secondary" icon={<Flame size={14} />} loading={busy} onClick={() => void warmUpModel(model.id)}>
              Warm up
            </Button>
          )}
          {canFree && (
            <>
              <Button
                size="sm"
                variant="subtle"
                icon={<MemoryStick size={14} />}
                loading={busy}
                onClick={() => void freeModelMemory(model.id)}
              >
                Free memory
              </Button>
              <span className={styles.hint}>{reloadWarning(model)}</span>
            </>
          )}
        </div>
      )}
    </li>
  )
}

/**
 * Read-only model registry (UI_DESIGN §3.8, DESIGN D5): models grouped by kind with state, size, VRAM, cold start
 * and licence; the only actions are Warm up (`POST /models/{id}/load`) and Free memory (`unload`). There is no
 * install button: models are installed manually.
 */
export function ModelRegistryList() {
  const f = useFeatures()
  const allModels = useFeaturesStore((s) => s.allModels)
  const loadAllModels = useFeaturesStore((s) => s.loadAllModels)
  const actionIds = useFeaturesStore((s) => s.modelActionIds)
  const [showHidden, setShowHidden] = useState(false)

  if (f.status !== 'ready') return null

  const toggleHidden = (value: boolean) => {
    setShowHidden(value)
    if (value) void loadAllModels()
  }
  const models: readonly ModelOut[] = showHidden && allModels ? allModels : f.models
  const groups = MODEL_KIND_ORDER.map((kind) => ({ kind, items: models.filter((model) => model.kind === kind) })).filter(
    (group) => group.items.length > 0,
  )

  return (
    <div className={styles.fields}>
      <Switch
        checked={showHidden}
        onChange={toggleHidden}
        label="Show unsupported models"
        size="sm"
      />
      {showHidden && !allModels && (
        <p className={styles.loading} role="status">
          <Spinner size={14} />
          <span>Loading all models…</span>
        </p>
      )}
      {groups.length === 0 ? (
        <p className={styles.hint}>The backend reports no models.</p>
      ) : (
        groups.map((group) => (
          <div key={group.kind} className={styles.modelGroup}>
            <h5 className={styles.groupTitle}>{MODEL_KIND_LABELS[group.kind]}</h5>
            <ul className={styles.modelList}>
              {group.items.map((model) => (
                <ModelRow key={model.id} model={model} busy={actionIds.includes(model.id)} />
              ))}
            </ul>
          </div>
        ))
      )}
      <p className={styles.hint}>Models are installed manually; see DECISIONS.md.</p>
    </div>
  )
}
