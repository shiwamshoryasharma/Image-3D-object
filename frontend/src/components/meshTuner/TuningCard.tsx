import { Cuboid, Download, Info, Settings2, Square, TriangleAlert, WandSparkles } from 'lucide-react'
import { useId } from 'react'

import { useTuneAvailability } from '../../hooks/useTuneAvailability'
import { useViewedTuning } from '../../hooks/useViewedTuning'
import { INSTALL_KIND_LABEL, downloadPercent, installVersion, isDownloadActive } from '../../lib/meshTuner'
import { isRunTerminal, useMeshTunerStore } from '../../store/meshTunerStore'
import { Button } from '../common/Button'
import { EmptyState } from '../common/EmptyState'
import { Panel } from '../common/Panel'
import { StatusBadge } from '../common/StatusBadge'
import { Tag } from '../common/Tag'
import { TuningOptionsForm } from './TuningOptionsForm'
import { TuningReport } from './TuningReport'
import { TuningSteps } from './TuningSteps'
import styles from './TuningCard.module.css'

/** Which Blender will run, or what is missing (with a shortcut into the setup dialog). */
function RuntimeLine() {
  const runtime = useMeshTunerStore((s) => s.runtime)
  const setSetupOpen = useMeshTunerStore((s) => s.setSetupOpen)
  if (!runtime) return null

  const selected = runtime.selected
  if (runtime.state === 'ready' && selected) {
    return (
      <div className={styles.runtime}>
        <Cuboid size={14} aria-hidden className={styles.runtimeIcon} />
        <span className={styles.runtimeText} title={selected.executable}>
          Blender {installVersion(selected)}
        </span>
        {selected.is_lts && <Tag tone="accent">LTS</Tag>}
        <Tag tone={selected.kind === 'embedded' ? 'info' : 'neutral'}>{INSTALL_KIND_LABEL[selected.kind]}</Tag>
        <Button
          className={styles.runtimeAction}
          size="sm"
          variant="ghost"
          icon={<Settings2 size={14} />}
          onClick={() => setSetupOpen(true)}
          title="Blender runtime settings"
        >
          Change
        </Button>
      </div>
    )
  }

  const download = runtime.download
  const downloading = runtime.state === 'downloading' || isDownloadActive(download)
  const percent = download ? downloadPercent(download) : null
  const text = downloading
    ? `Installing portable Blender${download?.version ? ` ${download.version}` : ''}${percent !== null ? ` — ${Math.round(percent)}%` : '…'}`
    : runtime.state === 'detecting'
      ? 'Detecting Blender installations…'
      : (runtime.message ?? runtime.error ?? `Blender ${runtime.min_version} or newer is required.`)

  return (
    <div className={`${styles.runtimeAlert} ${downloading || runtime.state === 'detecting' ? styles.runtimeBusy : ''}`}>
      <p className={styles.runtimeAlertText}>
        {downloading || runtime.state === 'detecting' ? <Download size={14} aria-hidden /> : <TriangleAlert size={14} aria-hidden />}
        <span>{text}</span>
      </p>
      {runtime.state !== 'detecting' && (
        <Button size="sm" variant={downloading ? 'ghost' : 'subtle'} icon={<Settings2 size={14} />} onClick={() => setSetupOpen(true)}>
          {downloading ? 'Show progress' : 'Set up Blender'}
        </Button>
      )}
    </div>
  )
}

/** Auto Tune card: runtime line, preset/options, Auto Tune / Cancel, live step checklist and the report. */
export function TuningCard() {
  const { job, latestRun } = useViewedTuning()
  const { canTune, reason, busy } = useTuneAvailability()
  const startTuning = useMeshTunerStore((s) => s.startTuning)
  const cancelTuning = useMeshTunerStore((s) => s.cancelTuning)
  const starting = useMeshTunerStore((s) => (job ? s.startingJobIds.includes(job.id) : false))
  const cancelling = useMeshTunerStore((s) => (latestRun ? s.cancellingRunIds.includes(latestRun.id) : false))
  const reasonId = useId()

  if (!job) {
    return (
      <Panel id="tuning" title="Auto Tune" icon={<WandSparkles size={16} />}>
        <EmptyState
          compact
          icon={<WandSparkles size={18} />}
          title="No model to tune"
          description="Open a completed model to repair and refine its mesh with Blender (inner shell, holes, noise, face)."
        />
      </Panel>
    )
  }

  return (
    <Panel
      id="tuning"
      title="Auto Tune"
      icon={<WandSparkles size={16} />}
      subtitle="Blender mesh repair & refinement"
      actions={latestRun ? <StatusBadge status={latestRun.status} /> : undefined}
    >
      <div className={styles.body}>
        <RuntimeLine />
        <TuningOptionsForm disabled={busy || starting} />

        <div className={styles.action}>
          {busy && latestRun ? (
            <Button
              variant="danger"
              size="lg"
              fullWidth
              icon={<Square size={16} />}
              loading={cancelling}
              onClick={() => void cancelTuning(latestRun.id)}
            >
              {cancelling ? 'Cancelling…' : latestRun.status === 'queued' ? 'Cancel queued tuning' : 'Cancel Auto Tune'}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="lg"
              fullWidth
              icon={<WandSparkles size={18} />}
              loading={starting}
              disabled={!canTune}
              aria-describedby={reason ? reasonId : undefined}
              onClick={() => void startTuning(job.id)}
            >
              {starting ? 'Starting…' : latestRun && isRunTerminal(latestRun) ? 'Auto Tune again' : 'Auto Tune'}
            </Button>
          )}
          {reason && !busy && !starting && (
            <p id={reasonId} className={styles.reason}>
              <Info size={13} aria-hidden />
              <span>{reason}</span>
            </p>
          )}
        </div>

        {latestRun && <TuningSteps run={latestRun} />}
        {latestRun && isRunTerminal(latestRun) && <TuningReport run={latestRun} />}
      </div>
    </Panel>
  )
}
