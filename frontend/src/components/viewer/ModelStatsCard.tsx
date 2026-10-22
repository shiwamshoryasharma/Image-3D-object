import { ChartColumn, SlidersHorizontal, Undo2 } from 'lucide-react'

import { useTunerVisible } from '../../hooks/useTunerVisible'
import { useViewedTuning, type CompletedJob } from '../../hooks/useViewedTuning'
import { selectViewedJob, useJobStore } from '../../store/jobStore'
import { useMeshTunerStore } from '../../store/meshTunerStore'
import { formFromJobSettings, useSettingsStore } from '../../store/settingsStore'
import { useSystemStore } from '../../store/systemStore'
import { toast } from '../../store/toastStore'
import { Button } from '../common/Button'
import { Panel } from '../common/Panel'
import { ModelStatsContent } from './ModelStatsContent'

/**
 * Model statistics card: `Panel` + `ModelStatsContent` (UI_DESIGN §1.3, §4.1 rule 2). Name, path and DOM are
 * today's: "Model" for the original, "Model · Tuned" while the viewer shows a tuned result (only where the tuner is
 * visible, §3.9). The GLB download goes through `actions/downloads.downloadArtifact`.
 */
export function ModelStatsCard() {
  const job = useJobStore(selectViewedJob)
  const replaceForm = useSettingsStore((s) => s.replaceForm)
  const capabilities = useSystemStore((s) => s.system?.engine.capabilities ?? null)
  const setVariant = useMeshTunerStore((s) => s.setVariant)
  const { tunedRun, variant } = useViewedTuning()
  const tunerVisible = useTunerVisible()

  if (!job || job.status !== 'completed' || !job.result) {
    return (
      <Panel id="model-stats" title="Model" icon={<ChartColumn size={16} />}>
        <ModelStatsContent />
      </Panel>
    )
  }

  const completed = job as CompletedJob
  if (tunerVisible && variant === 'tuned' && tunedRun) {
    return (
      <Panel
        id="model-stats"
        title="Model · Tuned"
        icon={<ChartColumn size={16} />}
        subtitle={`${completed.id} · ${tunedRun.id}`}
        actions={
          <Button
            size="sm"
            variant="ghost"
            icon={<Undo2 size={14} />}
            title="Show the original model in the viewer"
            onClick={() => setVariant('original')}
          >
            Original
          </Button>
        }
      >
        <ModelStatsContent />
      </Panel>
    )
  }

  return (
    <Panel
      id="model-stats"
      title="Model"
      icon={<ChartColumn size={16} />}
      subtitle={job.id}
      actions={
        <Button
          size="sm"
          variant="ghost"
          icon={<SlidersHorizontal size={14} />}
          title="Load this job's settings (including its seed) into the settings panel"
          onClick={() => {
            replaceForm(formFromJobSettings(completed.settings, completed.result.seed), capabilities)
            toast.info('Settings loaded', `Using the settings and seed ${completed.result.seed} from this job.`)
          }}
        >
          Reuse
        </Button>
      }
    >
      <ModelStatsContent />
    </Panel>
  )
}
