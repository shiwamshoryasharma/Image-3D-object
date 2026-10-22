/** Words of the shared shell chrome (UI_DESIGN §4.1); constants live in `.ts` files (React Compiler rule). */
import type { JobStatus } from '../../api/types'

export const BACKEND_OFFLINE_TEXT = 'Backend offline'
export const CONNECTING_TEXT = 'Connecting…'

/** How long `ProgressPill` stays after a run ended. */
export const ENDED_PILL_MS = 8000

/** Terminal text of the pill, by status; `{label}` = `RUN_KIND_LABELS[kind]`. */
export function endedText(label: string, status: JobStatus | null): string {
  if (status === 'failed') return `${label} failed`
  if (status === 'cancelled') return `${label} cancelled`
  return `${label} finished`
}
