import { ArrowLeftRight, CircleCheck, CircleX, TriangleAlert } from 'lucide-react'
import { useId } from 'react'

import type { BlenderRuntimeStatus } from '../../api/types'
import { INSTALL_KIND_LABEL, cycleTag, incompatibleText, installVersion } from '../../lib/meshTuner'
import { Button } from '../common/Button'
import { Tag } from '../common/Tag'
import styles from './SelectedRuntimeCard.module.css'

export interface SelectedRuntimeCardProps {
  runtime: BlenderRuntimeStatus
  picking: boolean
  onChangeRuntime: () => void
}

/** The runtime Auto Tune will use: kind, path, version, compatibility, why it was selected, warnings. */
export function SelectedRuntimeCard({ runtime, picking, onChangeRuntime }: SelectedRuntimeCardProps) {
  const selected = runtime.selected
  const headingId = useId()
  const cycle = selected ? cycleTag(selected) : null

  return (
    <section className={`${styles.card} ${selected ? '' : styles.empty}`} aria-labelledby={headingId}>
      <div className={styles.head}>
        <h3 id={headingId} className={styles.heading}>
          Selected runtime
        </h3>
        {selected &&
          (selected.compatible ? (
            <Tag tone="success" icon={<CircleCheck size={11} aria-hidden />}>
              Compatible
            </Tag>
          ) : (
            <Tag tone="danger" icon={<CircleX size={11} aria-hidden />}>
              Incompatible
            </Tag>
          ))}
        <Button
          className={styles.change}
          size="sm"
          variant={picking ? 'subtle' : 'secondary'}
          icon={<ArrowLeftRight size={14} />}
          aria-expanded={picking}
          disabled={runtime.installations.length === 0 && !picking}
          onClick={onChangeRuntime}
          title="Pick a specific Blender installation"
        >
          {picking ? 'Cancel change' : 'Change Runtime'}
        </Button>
      </div>

      {selected ? (
        <dl className={styles.details}>
          <div>
            <dt>Version</dt>
            <dd className={styles.version}>
              <span>Blender {installVersion(selected)}</span>
              {selected.is_lts && <Tag tone="accent">LTS</Tag>}
              {cycle && <Tag tone="warning">{cycle}</Tag>}
            </dd>
          </div>
          <div>
            <dt>Type</dt>
            <dd>
              {INSTALL_KIND_LABEL[selected.kind]}
              {selected.architecture ? ` · ${selected.architecture}` : ''}
            </dd>
          </div>
          <div className={styles.wide}>
            <dt>Executable</dt>
            <dd className={styles.path}>{selected.executable}</dd>
          </div>
          <div>
            <dt>Compatibility</dt>
            <dd>
              {selected.compatible
                ? `Meets the ${runtime.min_version}+ requirement`
                : incompatibleText(selected)}
            </dd>
          </div>
          <div>
            <dt>Mode</dt>
            <dd>{runtime.mode === 'auto' ? 'Auto' : `${INSTALL_KIND_LABEL[runtime.mode]} (chosen)`}</dd>
          </div>
          {runtime.selection_reason && (
            <div className={styles.wide}>
              <dt>Selection reason</dt>
              <dd>{runtime.selection_reason}</dd>
            </div>
          )}
        </dl>
      ) : (
        <p className={styles.none}>
          No compatible Blender is selected. Auto Mesh Tuner requires Blender {runtime.min_version} or newer.
        </p>
      )}

      {runtime.warnings.length > 0 && (
        <ul className={styles.warnings} aria-label="Runtime warnings">
          {runtime.warnings.map((warning) => (
            <li key={warning}>
              <TriangleAlert size={13} aria-hidden />
              <span>{warning}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
