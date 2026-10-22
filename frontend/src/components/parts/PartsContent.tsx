import { ChevronDown, ChevronRight, Info, Puzzle, RefreshCw, X } from 'lucide-react'
import { useId, useState, type KeyboardEvent } from 'react'

import { analyzePartsForViewedJob } from '../../actions/parts'
import { useFeatures } from '../../hooks/useFeatures'
import { usePartsView } from '../../hooks/usePartsView'
import { errorText } from '../../lib/errorText.ts'
import {
  displayStates,
  groupsForDisplay,
  hierarchy,
  partColorIndices,
  type PartSetView,
  type PartView,
} from '../../lib/partsModel.ts'
import { runPercent, runStepViews, warningViews } from '../../lib/runSteps.ts'
import { selectViewedJob, useJobStore } from '../../store/jobStore'
import { usePartStore, type PartViewState } from '../../store/partStore'
import { Button } from '../common/Button'
import { EmptyState } from '../common/EmptyState'
import { Skeleton } from '../common/Skeleton'
import { PipelineSteps } from '../generation/PipelineSteps'
import { PartInspector } from './PartInspector'
import { PartRow } from './PartRow'
import { PartsEmpty } from './PartsEmpty'
import { PartsToolbar, type PartsGroupBy } from './PartsToolbar'
import styles from './Parts.module.css'
import { percentText } from './partText'

export interface PartsContentProps {
  active?: boolean
  headingLevel?: 2 | 3
  compact?: boolean
}

const ROVE_KEYS: ReadonlySet<string> = new Set(['ArrowDown', 'ArrowUp', 'Home', 'End'])

/** Roving focus over every `role="option"` row of the list (all groups), Up/Down/Home/End. */
function onListKeyDown(event: KeyboardEvent<HTMLDivElement>) {
  if (!ROVE_KEYS.has(event.key)) return
  const target = event.target as HTMLElement
  if (target.getAttribute('role') !== 'option') return
  const options = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="option"]'))
  const index = options.indexOf(target)
  const last = options.length - 1
  const next =
    event.key === 'Home' ? 0 : event.key === 'End' ? last : event.key === 'ArrowDown' ? Math.min(last, index + 1) : Math.max(0, index - 1)
  event.preventDefault()
  options[next]?.focus()
}

const matches = (part: PartView, query: string) =>
  !query || `${part.label} ${part.category} ${part.id}`.toLowerCase().includes(query)

function Loading({ label }: { label: string }) {
  return (
    <div className={styles.root} aria-busy="true">
      <span className="visually-hidden">{label}</span>
      <Skeleton shape="line" lines={4} height={28} />
    </div>
  )
}

function ReadyView({ set, view, note }: { set: PartSetView; view: PartViewState; note: string | null }) {
  const features = useFeatures()
  const [groupBy, setGroupBy] = useState<PartsGroupBy>('category')
  const [filter, setFilter] = useState('')
  const [focusId, setFocusId] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<string[]>([])
  const listId = useId()

  const query = filter.trim().toLowerCase()
  const states = displayStates(set, view)
  const colors = partColorIndices(set)
  const hidden = new Set(view.hidden)
  const groups = groupsForDisplay(set, features)
    .map((group) => ({ ...group, parts: group.parts.filter((part) => matches(part, query)) }))
    .filter((group) => group.parts.length > 0)
  const tree = groupBy === 'hierarchy' ? hierarchy(set).filter((entry) => matches(entry.part, query)) : []
  const visibleIds =
    groupBy === 'hierarchy'
      ? tree.map((entry) => entry.part.id)
      : groups.flatMap((group) => (collapsed.includes(group.group) ? [] : group.parts.map((part) => part.id)))
  const tabStopId =
    (focusId && visibleIds.includes(focusId) ? focusId : null) ??
    (view.selected && visibleIds.includes(view.selected) ? view.selected : null) ??
    visibleIds[0] ??
    null
  const selected = view.selected ? (set.byId[view.selected] ?? null) : null
  const warnings = warningViews(set.warnings)
  const quality = [
    ['Camera alignment', percentText(set.quality.cameraFitIou)],
    ['Unlabelled surface', percentText(set.quality.unlabelledFaceRatio)],
    ['Inner wall matched', percentText(set.quality.partnerFilledRatio)],
    ['Cut parts keep both walls', percentText(set.quality.cutPartnerConsistency)],
  ].filter((entry): entry is [string, string] => entry[1] !== null)

  const row = (part: PartView, depth = 0) => (
    <PartRow
      key={part.id}
      part={part}
      analysisId={set.analysisId}
      state={states[part.id] ?? 'normal'}
      colorIndex={colors[part.id] ?? 0}
      colorize={view.colorize}
      hidden={hidden.has(part.id)}
      isolated={view.isolated === part.id}
      hovered={view.hovered === part.id}
      tabStop={tabStopId === part.id}
      depth={depth}
      onFocusRow={setFocusId}
    />
  )

  return (
    <div className={styles.root}>
      {note && (
        <p className={styles.note}>
          <Info size={12} aria-hidden /> {note}
        </p>
      )}
      {set.status === 'partial' && (
        <div className={styles.banner} role="note">
          <p>Some parts could not be built. The rest of the analysis is usable.</p>
          {warnings.length > 0 && (
            <ul className={styles.bannerList}>
              {warnings.map((w) => (
                <li key={`${w.code}:${w.text}`}>{w.text}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      <p className={styles.counts}>
        {set.counts.parts} parts · {set.counts.physical} physical · {set.counts.deferred} geometry later · {set.counts.regions}{' '}
        regions
      </p>
      {set.missingExpected.length > 0 && (
        <p className={styles.note}>Not found (asked for in the prompt): {set.missingExpected.join(', ')}</p>
      )}
      <PartsToolbar set={set} view={view} groupBy={groupBy} onGroupBy={setGroupBy} filter={filter} onFilter={setFilter} />

      <div className={styles.readyGrid}>
        <div className={styles.group} onKeyDown={onListKeyDown}>
          {groupBy === 'hierarchy' ? (
            <div role="listbox" aria-label="Parts by hierarchy" className={styles.listbox}>
              {tree.map((entry) => row(entry.part, entry.depth))}
            </div>
          ) : (
            groups.map((group) => {
              const open = !collapsed.includes(group.group)
              const groupListId = `${listId}-${group.group}`
              return (
                <section key={group.group} className={styles.group} aria-label={group.label}>
                  <button
                    type="button"
                    className={styles.groupToggle}
                    aria-expanded={open}
                    aria-controls={groupListId}
                    onClick={() =>
                      setCollapsed((current) =>
                        open ? [...current, group.group] : current.filter((g) => g !== group.group),
                      )
                    }
                  >
                    {open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
                    {group.label} ({group.parts.length})
                  </button>
                  {open && (
                    <div id={groupListId} role="listbox" aria-label={group.label} className={styles.listbox}>
                      {group.parts.map((part) => row(part))}
                    </div>
                  )}
                </section>
              )
            })
          )}
          {visibleIds.length === 0 && <p className={styles.note}>No part matches the filter.</p>}
        </div>
        {selected && <PartInspector set={set} part={selected} />}
      </div>

      <details className={styles.details}>
        <summary>Analysis details</summary>
        <ul className={styles.quality}>
          {quality.map(([label, value]) => (
            <li key={label}>
              {label} {value}
            </li>
          ))}
          {set.status === 'completed' &&
            warnings.map((w) => (
              <li key={`${w.code}:${w.text}`}>{w.text}</li>
            ))}
        </ul>
      </details>
    </div>
  )
}

/**
 * Parts of the model in the viewer (UI_DESIGN §6.3): every state of the table — legacy, turned off, no model, no
 * analysis, queued/running (live steps + Cancel), failed (+ Try again), ready/partial (toolbar, grouped list,
 * inspector, analysis details) and an unreadable manifest (list from the result + note).
 */
export function PartsContent(_props: PartsContentProps) {
  const features = useFeatures()
  const viewed = useJobStore(selectViewedJob)
  const { jobId, job, latest, set, active, view, requesting } = usePartsView()
  const cancelAnalysis = usePartStore((s) => s.cancelAnalysis)
  const loadForJob = usePartStore((s) => s.loadForJob)

  if (features.status === 'legacy') {
    return <EmptyState compact icon={<Puzzle size={18} />} title="Part output needs a newer Img3D backend." />
  }
  if (features.status === 'loading') return <Loading label="Checking part analysis…" />
  if (features.status === 'ready' && !features.flags.partAnalysis) {
    return <EmptyState compact icon={<Puzzle size={18} />} title="Part analysis is turned off on the server." />
  }
  if (!viewed || viewed.status !== 'completed' || !jobId) {
    return <EmptyState compact icon={<Puzzle size={18} />} title="Generate or open a model to see its parts." />
  }

  if (active) {
    return (
      <div className={styles.root}>
        <PipelineSteps
          steps={runStepViews(active.steps, active.warnings)}
          title="Part analysis"
          percent={runPercent(active.progress)}
          live
        />
        <div className={styles.actions}>
          <Button size="sm" variant="ghost" icon={<X size={14} />} onClick={() => void cancelAnalysis(active.id)}>
            Cancel
          </Button>
        </div>
      </div>
    )
  }

  if (set && latest) return <ReadyView set={set} view={view} note={latest.note} />
  if (!job || job.status === 'idle' || job.status === 'loading' || latest?.status === 'loading') {
    return <Loading label="Loading parts…" />
  }
  if (job.status === 'error') {
    return (
      <EmptyState
        compact
        tone="danger"
        icon={<Puzzle size={18} />}
        title="Could not load the parts"
        description={job.error ?? undefined}
        action={
          <Button size="sm" icon={<RefreshCw size={14} />} onClick={() => void loadForJob(jobId, true)}>
            Try again
          </Button>
        }
      />
    )
  }
  const newest = job.analyses[0]
  if (newest && newest.status === 'failed') {
    return (
      <EmptyState
        compact
        tone="danger"
        icon={<Puzzle size={18} />}
        title="Part analysis failed"
        description={newest.error ? errorText(newest.error, { subject: 'part_analysis' }) : undefined}
        action={
          <Button size="sm" icon={<RefreshCw size={14} />} loading={requesting} onClick={() => void analyzePartsForViewedJob()}>
            Try again
          </Button>
        }
      />
    )
  }
  if (latest?.status === 'error') {
    return (
      <EmptyState
        compact
        tone="danger"
        icon={<Puzzle size={18} />}
        title="Could not read the parts"
        description={latest.error ?? undefined}
        action={
          <Button size="sm" icon={<RefreshCw size={14} />} onClick={() => void loadForJob(jobId, true)}>
            Try again
          </Button>
        }
      />
    )
  }
  return <PartsEmpty requesting={requesting} />
}
