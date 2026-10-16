import { Check, CircleDashed, Download, LoaderCircle, RefreshCw, ShieldCheck, Square, TriangleAlert } from 'lucide-react'
import { useEffect, useId } from 'react'

import type { BlenderDownload, BlenderRelease, BlenderRuntimeStatus } from '../../api/types'
import { formatBytes, formatDuration } from '../../lib/format'
import {
  DOWNLOAD_PHASES,
  DOWNLOAD_STATE_LABEL,
  downloadPercent,
  downloadPhaseState,
  formatSpeed,
  hostOf,
  isDownloadActive,
  type PhaseState,
} from '../../lib/meshTuner'
import { selectActionError, useMeshTunerStore } from '../../store/meshTunerStore'
import { Button } from '../common/Button'
import { ProgressBar } from '../common/ProgressBar'
import { Spinner } from '../common/Spinner'
import { Tag, type TagTone } from '../common/Tag'
import { InlineAlert } from './InlineAlert'
import styles from './BlenderDownloadPanel.module.css'

const DOWNLOAD_TONE: Record<BlenderDownload['state'], TagTone> = {
  idle: 'neutral',
  resolving: 'accent',
  downloading: 'accent',
  verifying: 'accent',
  extracting: 'accent',
  validating: 'accent',
  completed: 'success',
  failed: 'danger',
  cancelled: 'warning',
}

function PhaseIcon({ state }: { state: PhaseState }) {
  if (state === 'done') return <Check size={12} aria-hidden />
  if (state === 'active') return <LoaderCircle size={12} className={styles.spin} aria-hidden />
  return <CircleDashed size={12} aria-hidden />
}

function ReleaseDetails({ release }: { release: BlenderRelease }) {
  return (
    <dl className={styles.release}>
      <div>
        <dt>Version</dt>
        <dd className={styles.releaseVersion}>
          Blender {release.version}
          {release.is_lts && <Tag tone="accent">LTS</Tag>}
        </dd>
      </div>
      <div>
        <dt>Size</dt>
        <dd>{formatBytes(release.size_bytes)}</dd>
      </div>
      <div>
        <dt>Source</dt>
        <dd>{hostOf(release.url)}</dd>
      </div>
      <div>
        <dt>Architecture</dt>
        <dd>{release.architecture}</dd>
      </div>
      <div className={styles.wide}>
        <dt>File</dt>
        <dd className={styles.mono}>{release.file_name}</dd>
      </div>
      <div className={styles.wide}>
        <dt>SHA-256</dt>
        <dd className={styles.mono}>{release.sha256 ?? 'No official checksum published — the download will not be verified.'}</dd>
      </div>
    </dl>
  )
}

/** Explicit download prompt: the backend's exact message + the resolved official release + a confirm button. */
function DownloadPrompt({ runtime }: { runtime: BlenderRuntimeStatus }) {
  const release = useMeshTunerStore((s) => s.release)
  const releaseStatus = useMeshTunerStore((s) => s.releaseStatus)
  const releaseError = useMeshTunerStore((s) => s.releaseError)
  const resolveRelease = useMeshTunerStore((s) => s.resolveRelease)
  const startDownload = useMeshTunerStore((s) => s.startDownload)
  const starting = useMeshTunerStore((s) => s.downloadStarting)
  const downloadError = useMeshTunerStore((s) => selectActionError(s, 'download'))
  const headingId = useId()
  const canDownload = runtime.can_download
  const previous = runtime.download
  const confirmVerb =
    previous?.state === 'cancelled' && previous.bytes_done > 0
      ? 'Resume download'
      : previous?.state === 'failed' || previous?.state === 'cancelled'
        ? 'Retry download'
        : 'Confirm download'

  // Resolving the official release only reads the public listing; it never downloads the ZIP.
  useEffect(() => {
    if (canDownload) void resolveRelease()
  }, [canDownload, resolveRelease])

  const message =
    runtime.message ??
    `No embedded Blender is installed. Download the official portable Blender ${runtime.min_version}+ build into Img3D's runtime folder?`

  return (
    <section className={styles.prompt} aria-labelledby={headingId}>
      <div className={styles.promptHead}>
        <TriangleAlert size={16} aria-hidden className={styles.promptIcon} />
        <h3 id={headingId} className={styles.heading}>
          {runtime.state === 'needs_download' ? 'Compatible Blender required' : 'Embedded Blender not installed'}
        </h3>
      </div>
      <p className={styles.promptMessage}>{message}</p>

      {canDownload ? (
        <>
          {release ? (
            <ReleaseDetails release={release} />
          ) : releaseStatus === 'error' ? (
            <div className={styles.releaseError} role="alert">
              <span>{releaseError ?? 'Could not resolve the official release.'}</span>
              <Button size="sm" variant="secondary" icon={<RefreshCw size={14} />} onClick={() => void resolveRelease(true)}>
                Retry
              </Button>
            </div>
          ) : (
            <p className={styles.resolving} role="status">
              <Spinner size={14} />
              <span>Resolving the latest official release on download.blender.org…</span>
            </p>
          )}
          <p className={styles.note}>
            Nothing is downloaded until you confirm. The official ZIP is verified against its published SHA-256 checksum
            and extracted into Img3D&apos;s own runtime folder; installed Blender versions are never modified.
          </p>
          <InlineAlert message={downloadError} />
          <div className={styles.promptActions}>
            <Button
              variant="primary"
              icon={<Download size={16} />}
              loading={starting}
              disabled={!release}
              onClick={() => void startDownload(release?.version)}
            >
              {release
                ? `${confirmVerb} · Blender ${release.version}${release.size_bytes ? ` (${formatBytes(release.size_bytes)})` : ''}`
                : confirmVerb}
            </Button>
          </div>
        </>
      ) : (
        <p className={styles.note}>
          Downloads are disabled on this server. Install Blender {runtime.min_version} or newer manually, then click
          Re-detect.
        </p>
      )}
    </section>
  )
}

/** Live progress of the portable download: bytes, %, speed, ETA, verify/extract/validate phases, Cancel. */
function DownloadProgress({ download }: { download: BlenderDownload }) {
  const cancelDownload = useMeshTunerStore((s) => s.cancelDownload)
  const cancelling = useMeshTunerStore((s) => s.downloadCancelling)
  const cancelError = useMeshTunerStore((s) => selectActionError(s, 'download'))
  const headingId = useId()
  const active = isDownloadActive(download)
  const percent = downloadPercent(download)
  const tone =
    download.state === 'failed' ? 'danger' : download.state === 'cancelled' ? 'warning' : download.state === 'completed' ? 'success' : 'accent'
  const title = active
    ? `Installing Blender ${download.version ?? ''}`.trim()
    : download.state === 'completed'
      ? `Blender ${download.version ?? ''} installed`.trim()
      : download.state === 'failed'
        ? 'Download failed'
        : 'Download cancelled'

  return (
    <section className={`${styles.progress} ${styles[download.state] ?? ''}`} aria-labelledby={headingId}>
      <div className={styles.progressHead}>
        <h3 id={headingId} className={styles.heading}>
          {title}
        </h3>
        <Tag tone={DOWNLOAD_TONE[download.state]}>{DOWNLOAD_STATE_LABEL[download.state]}</Tag>
        {download.resumed && <Tag tone="info">Resumed</Tag>}
        {download.verified === true && (
          <Tag tone="success" icon={<ShieldCheck size={11} aria-hidden />}>
            SHA-256 verified
          </Tag>
        )}
        {download.verified === false && <Tag tone="warning">Not verified</Tag>}
      </div>
      {/* Announce phase changes only, never the byte counter. */}
      <span className="visually-hidden" role="status">
        {`Blender download: ${DOWNLOAD_STATE_LABEL[download.state]}`}
      </span>

      <ProgressBar value={percent} tone={tone} size="md" label="Blender download progress" />

      <dl className={styles.stats}>
        <div>
          <dt>Downloaded</dt>
          <dd>
            {formatBytes(download.bytes_done)}
            {download.bytes_total ? ` / ${formatBytes(download.bytes_total)}` : ''}
          </dd>
        </div>
        <div>
          <dt>Progress</dt>
          <dd>{download.percent !== null ? `${download.percent.toFixed(1)}%` : percent !== null ? `${Math.round(percent)}%` : '—'}</dd>
        </div>
        <div>
          <dt>Speed</dt>
          <dd>{download.state === 'downloading' ? formatSpeed(download.speed_bps) : '—'}</dd>
        </div>
        <div>
          <dt>ETA</dt>
          <dd>{download.state === 'downloading' ? formatDuration(download.eta_s) : '—'}</dd>
        </div>
      </dl>

      <ol className={styles.phases} aria-label="Install steps">
        {DOWNLOAD_PHASES.map((phase) => {
          const state = downloadPhaseState(download, phase.state)
          return (
            <li key={phase.state} className={styles[`phase_${state}`]}>
              <span className={styles.phaseIcon}>
                <PhaseIcon state={state} />
              </span>
              <span>{phase.label}</span>
            </li>
          )
        })}
      </ol>

      {download.url && (
        <p className={styles.meta}>
          From <span className={styles.mono}>{hostOf(download.url)}</span>
          {download.file_name ? <> · {download.file_name}</> : null}
        </p>
      )}
      {download.state === 'failed' && (
        <p className={styles.error} role="alert">
          {download.error ?? 'The download failed.'}
          {download.error_code ? <span className={styles.code}> ({download.error_code})</span> : null}
        </p>
      )}
      {download.state === 'completed' && download.install_dir && (
        <p className={styles.meta}>
          Installed to <span className={styles.mono}>{download.install_dir}</span>
        </p>
      )}
      {active && <InlineAlert message={cancelError} />}
      {active && (
        <div className={styles.promptActions}>
          <Button
            variant="danger"
            size="sm"
            icon={<Square size={12} />}
            loading={cancelling}
            disabled={download.state === 'validating'}
            onClick={() => void cancelDownload()}
          >
            Cancel download
          </Button>
        </div>
      )}
      {download.state === 'cancelled' && (
        <p className={styles.meta}>The partial download is kept and resumes when you download again.</p>
      )}
    </section>
  )
}

/** Download section of the setup dialog: progress (when a download exists) and/or the explicit prompt. */
export function BlenderDownloadPanel({ runtime }: { runtime: BlenderRuntimeStatus }) {
  const download = runtime.download
  const active = isDownloadActive(download)
  const hasEmbedded = runtime.installations.some((install) => install.kind === 'embedded' && install.compatible)
  const offer =
    !active &&
    runtime.state !== 'detecting' &&
    (runtime.state === 'needs_download' || (runtime.mode === 'embedded' && !hasEmbedded && runtime.can_download))
  const showProgress = !!download && download.state !== 'idle'

  if (!offer && !showProgress) return null
  return (
    <>
      {showProgress && download && <DownloadProgress download={download} />}
      {offer && <DownloadPrompt runtime={runtime} />}
    </>
  )
}
