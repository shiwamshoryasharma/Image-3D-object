import { Boxes, Info } from 'lucide-react'
import { useId } from 'react'

import { useFeatures } from '../../hooks/useFeatures'
import { PLAN_TEXT, reasonClause } from '../../lib/generatePlan.ts'
import { resolveSeparate } from '../../lib/workflowRequest.ts'
import { usePartOptionsStore } from '../../store/partOptionsStore'
import { NumberField } from '../common/NumberField'
import { Switch } from '../common/Switch'
import { Tag } from '../common/Tag'
import { SegmentationPreview } from '../input/SegmentationPreview'
import { PartKindBadge } from '../parts/PartKindBadge'
import { Field } from './Field'
import styles from './PartOptionsSection.module.css'
import { SegmentationOptionsContent } from './SegmentationOptionsContent'

export interface PartOptionsSectionProps {
  active?: boolean
  headingLevel?: 2 | 3
  compact?: boolean
  /** `parts` = without the Subject / Profile / Also look for / licence block (Node Part Extraction inspector). */
  mode?: 'all' | 'parts'
}

const SEGMENT_FIRST_HELP =
  'Shown while the model is built. The 3D parts reuse it only when the image has real transparency; otherwise the parts step segments the model’s own view again.'

/**
 * Part-aware options before generation (UI_DESIGN §6.2), bound to `partOptionsStore`. Turning the master switch off
 * is always possible; turning it on needs `parts.available && workflow.available` (UI11). Every group, strategy,
 * note and reason comes from `/features` (UI14).
 */
export function PartOptionsSection({ headingLevel = 3, mode = 'all' }: PartOptionsSectionProps) {
  const features = useFeatures()
  const form = usePartOptionsStore((s) => s.form)
  const update = usePartOptionsStore((s) => s.update)
  const headingId = useId()
  const strategyId = useId()
  const viewsId = useId()

  const { parts, workflow } = features
  const canEnable = parts.available && workflow.available
  const disabledReason = !parts.available
    ? PLAN_TEXT.partsUnavailable(parts)
    : !workflow.available
      ? PLAN_TEXT.partsNeedWorkflows(workflow)
      : null
  const separate = resolveSeparate(form, features)
  const strategyValue = form.strategy ?? 'semantic_parts'
  const strategy = parts.strategies.find((s) => s.id === strategyValue) ?? null
  const Heading = headingLevel === 2 ? 'h2' : 'h3'

  const toggleGroup = (id: string, checked: boolean) => {
    const next = checked ? [...separate.filter((g) => g !== id), id] : separate.filter((g) => g !== id)
    // Keep the backend's group order so the request is stable.
    const order = parts.separableGroups.map((g) => g.id)
    update({ separate: order.filter((g) => next.includes(g)) })
  }

  return (
    <section className={styles.section} aria-labelledby={headingId} data-ui-addition="part-options">
      <Heading id={headingId} className={styles.heading}>
        Part-aware output
      </Heading>
      <Switch
        label="Part-aware output"
        description={
          disabledReason ??
          'After the model is built, split it into parts (hair, clothes, accessories…) and label the body regions.'
        }
        checked={form.enabled}
        disabled={!form.enabled && !canEnable}
        onChange={(enabled) => update({ enabled })}
      />

      {form.enabled && (
        <>
          {parts.separableGroups.length > 0 && (
            <fieldset className={styles.group}>
              <legend className={styles.legend}>Separate as physical assets</legend>
              {parts.separableGroups.map((group) => (
                <label key={group.id} className={styles.checkRow}>
                  <input
                    type="checkbox"
                    checked={separate.includes(group.id)}
                    onChange={(event) => toggleGroup(group.id, event.target.checked)}
                  />
                  <span>{group.label}</span>
                  <PartKindBadge kind={group.default_kind === 'physical_deferred' ? 'deferred' : group.default_kind} />
                  {group.note && <span className={styles.checkNote}>{group.note}</span>}
                </label>
              ))}
            </fieldset>
          )}

          {parts.regionGroups.length > 0 && (
            <div className={styles.group}>
              <p className={styles.legend}>Kept as regions on the continuous body</p>
              <ul className={styles.regionList}>
                {parts.regionGroups.map((group) => (
                  <li key={group.id}>
                    <Tag>{group.label}</Tag>
                  </li>
                ))}
              </ul>
              <p className={styles.muted}>
                Groups you do not separate are kept as regions too. Regions are labels on the surface, not cuts.
              </p>
            </div>
          )}

          {parts.strategies.length > 0 && (
            <Field
              label="Method"
              htmlFor={strategyId}
              hint={strategy && !strategy.available ? reasonClause(strategy) : undefined}
            >
              <select
                id={strategyId}
                className={styles.select}
                value={strategyValue}
                onChange={(event) =>
                  update({ strategy: event.target.value })
                }
              >
                {parts.strategies.map((option) => (
                  <option key={option.id} value={option.id} disabled={!option.available && option.id !== strategyValue}>
                    {option.label}
                    {option.available ? '' : ` (unavailable: ${reasonClause(option)})`}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {mode === 'all' && <SegmentationOptionsContent />}

          <Switch
            label="Segment the image before 3D"
            description={SEGMENT_FIRST_HELP}
            checked={form.previewSegmentation}
            disabled={!form.previewSegmentation && !features.segmentation.available}
            onChange={(previewSegmentation) => update({ previewSegmentation })}
          />
          <SegmentationPreview />

          <details className={styles.disclosure}>
            <summary>More options</summary>
            <Field label="Extra views" htmlFor={viewsId} hint="Additional rendered views used to label the mesh.">
              <NumberField
                id={viewsId}
                value={form.views}
                onChange={(views) => update({ views: views === parts.defaultViews ? null : views })}
                min={0}
                max={parts.maxViews}
                integer
                nullable
                placeholder={`Default (${parts.defaultViews})`}
                ariaLabel="Extra views"
              />
            </Field>
          </details>

          <div className={styles.group}>
            <p className={styles.legend}>Export</p>
            <p className={styles.info}>
              <Boxes size={14} aria-hidden />
              <span>Assembled parts GLB — always written for every analysis.</span>
            </p>
            <Switch
              label="Separate part files"
              description="Write one GLB per physical part with the analysis. Otherwise part files are built when you download them."
              checked={form.perPartGlbs}
              onChange={(perPartGlbs) => update({ perPartGlbs })}
            />
          </div>
        </>
      )}

      {!form.enabled && canEnable && (
        <p className={styles.info}>
          <Info size={14} aria-hidden />
          <span>Generations stay exactly as today while this is off.</span>
        </p>
      )}
    </section>
  )
}
