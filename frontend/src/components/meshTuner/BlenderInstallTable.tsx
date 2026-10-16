import { Check, CircleCheck, CircleX, RefreshCw } from 'lucide-react'
import { useId, useState } from 'react'

import type { BlenderRuntimeStatus } from '../../api/types'
import { useNow } from '../../hooks/useNow'
import { formatRelativeTime } from '../../lib/format'
import { INSTALL_KIND_LABEL, cycleTag, incompatibleText, installVersion, isDownloadActive } from '../../lib/meshTuner'
import { selectActionError, useMeshTunerStore } from '../../store/meshTunerStore'
import { Button } from '../common/Button'
import { Tag } from '../common/Tag'
import { InlineAlert } from './InlineAlert'
import styles from './BlenderInstallTable.module.css'

export interface BlenderInstallTableProps {
  runtime: BlenderRuntimeStatus
  /** "Change Runtime" is active: rows become selectable and a custom path can be entered. */
  picking: boolean
  onPickingDone: () => void
}

function initialChoice(runtime: BlenderRuntimeStatus): string | null {
  const selected = runtime.installations.find((install) => install.selected && install.compatible)
  return selected?.id ?? runtime.installations.find((install) => install.compatible)?.id ?? null
}

/** Detected Blender installations, with selection ("Change Runtime"), custom path and re-detection. */
export function BlenderInstallTable({ runtime, picking, onPickingDone }: BlenderInstallTableProps) {
  const detect = useMeshTunerStore((s) => s.detect)
  const detecting = useMeshTunerStore((s) => s.detecting)
  const select = useMeshTunerStore((s) => s.select)
  const selecting = useMeshTunerStore((s) => s.selecting)
  const detectError = useMeshTunerStore((s) => selectActionError(s, 'detect'))
  const pinError = useMeshTunerStore((s) => selectActionError(s, 'pin'))
  const [choice, setChoice] = useState<string | null>(() => initialChoice(runtime))
  const [customPath, setCustomPath] = useState('')
  const headingId = useId()
  const radioName = useId()
  const pathId = useId()
  const now = useNow(30_000)
  const downloading = runtime.state === 'downloading' || isDownloadActive(runtime.download)
  const installs = runtime.installations
  const trimmedPath = customPath.trim()

  // Pinning persists the install's own kind as the mode (system pin → "system", embedded pin → "embedded").
  const apply = () => {
    const install = installs.find((item) => item.id === choice)
    const request = trimmedPath
      ? select('system', trimmedPath)
      : install
        ? select(install.kind, install.executable)
        : null
    if (!request) return
    void request.then((ok) => {
      if (!ok) return
      setCustomPath('')
      onPickingDone()
    })
  }

  return (
    <section className={styles.section} aria-labelledby={headingId}>
      <div className={styles.head}>
        <h3 id={headingId} className={styles.heading}>
          Detected installations <span className={styles.count}>({installs.length})</span>
        </h3>
        <Button
          size="sm"
          variant="ghost"
          icon={<RefreshCw size={14} />}
          loading={detecting || runtime.state === 'detecting'}
          disabled={downloading}
          onClick={() => void detect()}
          title="Scan this PC for Blender installations again"
        >
          Re-detect
        </Button>
      </div>
      {runtime.last_scan_at && (
        <p className={styles.meta}>
          Last scan {formatRelativeTime(runtime.last_scan_at, now)}
          {runtime.scan_duration_ms !== null ? ` · ${Math.round(runtime.scan_duration_ms)} ms` : ''} · every
          candidate is executed to read its real version
        </p>
      )}
      <InlineAlert message={detectError} />

      {installs.length === 0 ? (
        <p className={styles.none}>No Blender installation was found on this PC.</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={`${styles.table} ${picking ? styles.picking : ''}`}>
            <thead>
              <tr>
                {picking && (
                  <th scope="col" className={styles.pickCol}>
                    <span className="visually-hidden">Use</span>
                  </th>
                )}
                <th scope="col">Version</th>
                <th scope="col">Type</th>
                <th scope="col">Path</th>
                <th scope="col">Compatibility</th>
              </tr>
            </thead>
            <tbody>
              {installs.map((install) => {
                const cycle = cycleTag(install)
                const label = `Blender ${installVersion(install)} (${INSTALL_KIND_LABEL[install.kind]})`
                return (
                  <tr
                    key={install.id}
                    className={`${install.selected ? styles.selectedRow : ''} ${install.compatible ? '' : styles.incompatibleRow}`}
                  >
                    {picking && (
                      <td className={styles.pickCol}>
                        <input
                          type="radio"
                          name={radioName}
                          className={styles.radio}
                          checked={!trimmedPath && choice === install.id}
                          disabled={!install.compatible}
                          aria-label={`Use ${label}`}
                          onChange={() => {
                            setChoice(install.id)
                            setCustomPath('')
                          }}
                        />
                      </td>
                    )}
                    <td data-label="Version">
                      <span className={styles.version}>
                        <span className={styles.versionText}>{installVersion(install)}</span>
                        {install.is_lts && <Tag tone="accent">LTS</Tag>}
                        {cycle && <Tag tone="warning">{cycle}</Tag>}
                        {install.selected && (
                          <Tag tone="success" icon={<Check size={11} aria-hidden />}>
                            Selected
                          </Tag>
                        )}
                      </span>
                    </td>
                    <td data-label="Type">
                      <span className={styles.kind}>
                        <Tag tone={install.kind === 'embedded' ? 'info' : 'neutral'}>{INSTALL_KIND_LABEL[install.kind]}</Tag>
                        {install.architecture && <span className={styles.arch}>{install.architecture}</span>}
                      </span>
                    </td>
                    <td data-label="Path" className={styles.path}>
                      {install.executable}
                    </td>
                    <td data-label="Compatibility">
                      {install.compatible ? (
                        <Tag tone="success" icon={<CircleCheck size={11} aria-hidden />}>
                          Compatible
                        </Tag>
                      ) : (
                        <span className={styles.status}>
                          <Tag tone="danger" icon={<CircleX size={11} aria-hidden />}>
                            Incompatible
                          </Tag>
                          <span className={styles.reason}>{incompatibleText(install)}</span>
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {picking && (
        <div className={styles.picker}>
          <label className={styles.pathLabel} htmlFor={pathId}>
            Or use a specific <span className={styles.inlineMono}>blender.exe</span>
          </label>
          <input
            id={pathId}
            className={styles.input}
            type="text"
            spellCheck={false}
            autoComplete="off"
            placeholder="C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
            value={customPath}
            onChange={(event) => setCustomPath(event.target.value)}
          />
          <p className={styles.meta}>
            The executable is run with <span className={styles.inlineMono}>--version</span> to verify it is Blender{' '}
            {runtime.min_version} or newer before it is used.
          </p>
          <InlineAlert message={pinError} />
          <div className={styles.pickerActions}>
            <Button variant="ghost" size="sm" onClick={onPickingDone}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={selecting}
              disabled={!trimmedPath && !choice}
              onClick={apply}
            >
              Use selected runtime
            </Button>
          </div>
        </div>
      )}

      <p className={styles.meta}>
        Embedded runtimes live in <span className={styles.inlineMono}>{runtime.runtime_root}</span>
      </p>
    </section>
  )
}
