import { MoreHorizontal, Puzzle, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { analyzePartsForViewedJob } from '../../actions/parts'
import { useFeatures } from '../../hooks/useFeatures'
import { usePartsView } from '../../hooks/usePartsView'
import { usePartStore } from '../../store/partStore'
import { Button } from '../common/Button'
import { Dialog } from '../common/Dialog'
import { Menu, type MenuSection } from '../common/Menu'
import { Panel } from '../common/Panel'
import { ExportContent } from '../export/ExportContent'
import styles from './Parts.module.css'
import { PartsContent } from './PartsContent'

const when = (iso: string): string => {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/**
 * Default-mode parts card (UI_DESIGN §4.2 placements, §6.3, §6.8): `Panel id="parts"` + `PartsContent`, the
 * downloads section, and an overflow menu with "Analyse again with current options" and the earlier analyses
 * (deletable, with confirmation). Rendered only when `/features` is ready.
 */
export function PartsPanel() {
  const features = useFeatures()
  const { job, set, active, requesting } = usePartsView()
  const deleteAnalysis = usePartStore((s) => s.deleteAnalysis)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const canAnalyse = features.parts.available && features.parts.rebuild && !!job && !active && !requesting
  const shownId = set?.analysisId ?? null
  const earlier = (job?.analyses ?? []).filter((run) => run.id !== shownId && run.id !== active?.id)
  const sections: MenuSection[] = [
    {
      items: [
        {
          id: 'again',
          label: 'Analyse again with current options',
          icon: <RefreshCw size={14} />,
          disabled: !canAnalyse,
          onSelect: () => void analyzePartsForViewedJob(),
        },
      ],
    },
  ]
  if (earlier.length > 0) {
    sections.push({
      title: `Earlier analyses (${earlier.length})`,
      items: earlier.map((run) => ({
        id: run.id,
        label: `Delete analysis of ${when(run.created_at)}`,
        hint: run.status,
        icon: <Trash2 size={14} />,
        danger: true,
        onSelect: () => setConfirmId(run.id),
      })),
    })
  }

  return (
    <div data-ui-addition="parts">
      <Panel
        id="parts"
        title="Parts"
        icon={<Puzzle size={16} />}
        subtitle={set ? `${set.counts.parts} parts · ${set.status === 'partial' ? 'partial' : 'complete'}` : undefined}
        actions={job ? <Menu label="Parts actions" icon={<MoreHorizontal size={14} />} sections={sections} triggerSize="sm" /> : undefined}
      >
        <div className={styles.panelWrap}>
          <PartsContent />
          {set && <ExportContent />}
        </div>
      </Panel>
      <Dialog
        open={confirmId !== null}
        onClose={() => setConfirmId(null)}
        title="Delete this analysis?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmId(null)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              icon={<Trash2 size={14} />}
              onClick={() => {
                const id = confirmId
                setConfirmId(null)
                if (id) void deleteAnalysis(id)
              }}
            >
              Delete
            </Button>
          </>
        }
      >
        <p>The analysis files are removed. The model itself (model.glb) is not changed.</p>
      </Dialog>
    </div>
  )
}
