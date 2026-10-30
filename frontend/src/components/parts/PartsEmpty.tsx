import { Puzzle } from 'lucide-react'

import { analyzePartsForViewedJob } from '../../actions/parts'
import { useFeatures } from '../../hooks/useFeatures'
import { reasonClause } from '../../lib/generatePlan.ts'
import { usePartOptionsStore } from '../../store/partOptionsStore'
import { Button } from '../common/Button'
import { EmptyState } from '../common/EmptyState'

/** "No parts yet." + [Analyse parts] with the current part options (UI_DESIGN §6.3, §6.8). */
export function PartsEmpty({ requesting }: { requesting: boolean }) {
  const features = useFeatures()
  const strategyId = usePartOptionsStore((s) => s.form.strategy)
  const { parts } = features
  const strategy = parts.strategies.find((s) => s.id === (strategyId ?? 'semantic_parts'))
  const canAnalyse = parts.available && parts.rebuild && strategy?.available
  const strategyLabel = strategy?.label ?? null

  return (
    <EmptyState
      compact
      icon={<Puzzle size={18} />}
      title="No parts yet."
      description={
        canAnalyse
          ? `Split this model into parts with the current part options${strategyLabel ? ` (method: ${strategyLabel})` : ''}.`
          : parts.available && parts.rebuild
            ? `Individual part splitting is unavailable: ${strategy ? reasonClause(strategy) : 'semantic segmentation models are unavailable'}.`
            : parts.available
              ? 'This backend does not analyse finished models. Turn on part-aware output before generating.'
            : `Part analysis is unavailable: ${reasonClause(parts)}.`
      }
      action={
        canAnalyse ? (
          <Button size="sm" variant="secondary" icon={<Puzzle size={14} />} loading={requesting} onClick={() => void analyzePartsForViewedJob()}>
            Analyse parts
          </Button>
        ) : undefined
      }
    />
  )
}
