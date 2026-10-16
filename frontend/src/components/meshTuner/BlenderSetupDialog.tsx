import { Cuboid, Info, RefreshCw, WandSparkles } from 'lucide-react'
import { useId, useState } from 'react'

import type { BlenderRuntimeMode, BlenderRuntimeStatus } from '../../api/types'
import { useTuneAvailability } from '../../hooks/useTuneAvailability'
import { RUNTIME_MODE_HINT, RUNTIME_MODE_LABEL, RUNTIME_STATE_LABEL, isDownloadActive } from '../../lib/meshTuner'
import { selectActionError, useMeshTunerStore } from '../../store/meshTunerStore'
import { useUiStore } from '../../store/uiStore'
import { Button } from '../common/Button'
import { Dialog } from '../common/Dialog'
import { EmptyState } from '../common/EmptyState'
import { SegmentedControl } from '../common/SegmentedControl'
import { Spinner } from '../common/Spinner'
import { Tag, type TagTone } from '../common/Tag'
import { BlenderDownloadPanel } from './BlenderDownloadPanel'
import { BlenderInstallTable } from './BlenderInstallTable'
import { InlineAlert } from './InlineAlert'
import { SelectedRuntimeCard } from './SelectedRuntimeCard'
import styles from './BlenderSetupDialog.module.css'

const STATE_TONE: Record<BlenderRuntimeStatus['state'], TagTone> = {
  ready: 'success',
  needs_download: 'warning',
  downloading: 'accent',
  detecting: 'accent',
  error: 'danger',
}

/** Long label on wide screens, short label on phones (CSS swaps them; only one is ever rendered visibly). */
function ModeLabel({ mode, short }: { mode: BlenderRuntimeMode; short: string }) {
  return (
    <>
      <span className={styles.longLabel}>{RUNTIME_MODE_LABEL[mode]}</span>
      <span className={styles.shortLabel}>{short}</span>
    </>
  )
}

const MODE_OPTIONS = [
  { value: 'system' as const, label: <ModeLabel mode="system" short="System" />, title: RUNTIME_MODE_HINT.system },
  { value: 'embedded' as const, label: <ModeLabel mode="embedded" short="Embedded" />, title: RUNTIME_MODE_HINT.embedded },
  { value: 'auto' as const, label: <ModeLabel mode="auto" short="Auto" />, title: RUNTIME_MODE_HINT.auto },
]

function SetupContent({ runtime }: { runtime: BlenderRuntimeStatus }) {
  const select = useMeshTunerStore((s) => s.select)
  const selecting = useMeshTunerStore((s) => s.selecting)
  const tuneError = useMeshTunerStore((s) => selectActionError(s, 'tune'))
  const modeError = useMeshTunerStore((s) => selectActionError(s, 'mode'))
  const [picking, setPicking] = useState(false)
  const modeId = useId()
  const downloading = runtime.state === 'downloading' || isDownloadActive(runtime.download)

  return (
    <>
      <InlineAlert message={tuneError} />
      <InlineAlert message={runtime.state === 'error' ? runtime.error : null} />

      <section className={styles.section} aria-labelledby={modeId}>
        <h3 id={modeId} className={styles.heading}>
          Runtime source
        </h3>
        <SegmentedControl<BlenderRuntimeMode>
          label="Runtime source"
          value={runtime.mode}
          options={MODE_OPTIONS}
          onChange={(mode) => {
            if (mode !== runtime.mode) void select(mode)
          }}
          disabled={selecting || downloading}
        />
        <p className={styles.hint}>{RUNTIME_MODE_HINT[runtime.mode]}</p>
        <InlineAlert message={modeError} />
      </section>

      <SelectedRuntimeCard runtime={runtime} picking={picking} onChangeRuntime={() => setPicking((value) => !value)} />

      <BlenderDownloadPanel runtime={runtime} />

      <BlenderInstallTable runtime={runtime} picking={picking} onPickingDone={() => setPicking(false)} />
    </>
  )
}

function SetupFooter({ onClose }: { onClose: () => void }) {
  const { canTune, reason, jobId } = useTuneAvailability()
  const startTuning = useMeshTunerStore((s) => s.startTuning)
  const starting = useMeshTunerStore((s) => (jobId ? s.startingJobIds.includes(jobId) : false))
  const setMobileTab = useUiStore((s) => s.setMobileTab)
  const reasonId = useId()

  const tune = () => {
    if (!jobId) return
    void startTuning(jobId).then((ok) => {
      if (!ok) return
      onClose()
      setMobileTab('viewer')
    })
  }

  return (
    <>
      {reason && !starting && (
        <p id={reasonId} className={styles.footerReason}>
          <Info size={13} aria-hidden />
          <span>{reason}</span>
        </p>
      )}
      <Button variant="ghost" onClick={onClose}>
        Close
      </Button>
      <Button
        variant="primary"
        icon={<WandSparkles size={16} />}
        disabled={!canTune}
        loading={starting}
        aria-describedby={reason ? reasonId : undefined}
        onClick={tune}
      >
        Auto Tune
      </Button>
    </>
  )
}

/** Blender runtime setup: detected installs, selection mode, runtime pinning, official portable download. */
export function BlenderSetupDialog() {
  const open = useMeshTunerStore((s) => s.setupOpen)
  const setOpen = useMeshTunerStore((s) => s.setSetupOpen)
  const runtime = useMeshTunerStore((s) => s.runtime)
  const runtimeLoad = useMeshTunerStore((s) => s.runtimeStatus)
  const runtimeError = useMeshTunerStore((s) => s.runtimeError)
  const refreshRuntime = useMeshTunerStore((s) => s.refreshRuntime)
  const close = () => setOpen(false)

  const downloading = !!runtime && (runtime.state === 'downloading' || isDownloadActive(runtime.download))
  const stateLabel = runtime ? (downloading ? RUNTIME_STATE_LABEL.downloading : RUNTIME_STATE_LABEL[runtime.state]) : null

  return (
    <Dialog
      open={open}
      onClose={close}
      size="lg"
      icon={<Cuboid size={16} />}
      title="Blender runtime"
      subtitle={`Auto Mesh Tuner needs Blender ${runtime?.min_version ?? '5.1'} or newer`}
      headerAside={
        runtime &&
        stateLabel && <Tag tone={downloading ? 'accent' : STATE_TONE[runtime.state]}>{stateLabel}</Tag>
      }
      footer={<SetupFooter onClose={close} />}
    >
      {runtime ? (
        <SetupContent runtime={runtime} />
      ) : runtimeLoad === 'error' ? (
        <EmptyState
          compact
          tone="danger"
          icon={<Cuboid size={18} />}
          title="Blender runtime unavailable"
          description={runtimeError?.message ?? 'The backend did not report a Blender runtime.'}
          action={
            <Button size="sm" icon={<RefreshCw size={14} />} onClick={() => void refreshRuntime()}>
              Try again
            </Button>
          }
        />
      ) : (
        <div className={styles.loading} role="status">
          <Spinner size={18} />
          <span>Checking Blender installations…</span>
        </div>
      )}
    </Dialog>
  )
}
