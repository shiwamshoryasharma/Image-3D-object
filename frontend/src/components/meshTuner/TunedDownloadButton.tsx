import { Download } from 'lucide-react'
import { useState } from 'react'

import { downloadTunedGlb } from '../../api/tunings'
import type { TunedRun } from '../../hooks/useViewedTuning'
import { saveBlob } from '../../lib/download'
import { formatBytes } from '../../lib/format'
import { toast } from '../../store/toastStore'
import { Button, type ButtonVariant } from '../common/Button'
import { ProgressBar } from '../common/ProgressBar'
import styles from './TunedDownloadButton.module.css'

/** "Download tuned GLB" with progress (blob download through axios so the auth header is sent). */
export function TunedDownloadButton({ run, variant = 'primary' }: { run: TunedRun; variant?: ButtonVariant }) {
  const [progress, setProgress] = useState<number | null | undefined>(undefined)
  const downloading = progress !== undefined
  const size = run.result.stats?.file_size_bytes ?? null

  // Promise chaining (no try/finally) keeps this component compatible with the React Compiler.
  const download = () => {
    setProgress(null)
    return downloadTunedGlb(run, { onProgress: setProgress })
      .then(({ blob, filename }) => saveBlob(blob, filename))
      .catch((error: unknown) => {
        toast.error('Download failed', error instanceof Error ? error.message : 'Could not download the tuned GLB.')
      })
      .then(() => setProgress(undefined))
  }

  return (
    <div className={styles.download}>
      <Button variant={variant} fullWidth icon={<Download size={16} />} loading={downloading} onClick={() => void download()}>
        {downloading
          ? `Downloading${progress !== null && progress !== undefined ? ` ${Math.round(progress)}%` : '…'}`
          : 'Download tuned GLB'}
        {size !== null && <span className={styles.size}>{formatBytes(size)}</span>}
      </Button>
      {downloading && <ProgressBar value={progress ?? null} size="xs" label="Tuned GLB download progress" />}
    </div>
  )
}
