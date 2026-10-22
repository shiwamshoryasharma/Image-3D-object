import { Activity } from 'lucide-react'

import { selectActiveJob, useJobStore } from '../../store/jobStore'
import { Panel } from '../common/Panel'
import { StatusBadge } from '../common/StatusBadge'
import { ProgressContent } from './ProgressContent'

/**
 * Live progress card: `Panel` + `ProgressContent` (UI_DESIGN §1.3, §4.1 rule 2). Name, path and DOM are today's;
 * the header (job id, resolution, seed, status badge) is computed here as before.
 */
export function ProgressCard() {
  const job = useJobStore(selectActiveJob)

  if (!job) {
    return (
      <Panel id="progress" title="Progress" icon={<Activity size={16} />}>
        <ProgressContent />
      </Panel>
    )
  }

  const shortId = job.id.replace(/^job_/, '').slice(0, 8)
  return (
    <Panel
      id="progress"
      title="Progress"
      icon={<Activity size={16} />}
      subtitle={`Job ${shortId} · ${job.settings.resolution} · seed ${job.seed}`}
      actions={<StatusBadge status={job.status} />}
    >
      <ProgressContent />
    </Panel>
  )
}
