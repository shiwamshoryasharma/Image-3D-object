import { Box, Eye, Palette, Shapes, Square } from 'lucide-react'
import { useId } from 'react'

import type { PartSetView } from '../../lib/partsModel.ts'
import { usePartStore, type PartViewState } from '../../store/partStore'
import { RangeSlider } from '../common/RangeSlider'
import { SegmentedControl } from '../common/SegmentedControl'
import styles from './Parts.module.css'

export type PartsGroupBy = 'category' | 'hierarchy'

export interface PartsToolbarProps {
  set: PartSetView
  view: PartViewState
  groupBy: PartsGroupBy
  onGroupBy: (value: PartsGroupBy) => void
  filter: string
  onFilter: (value: string) => void
}

/** Parts that can be hidden (shared-label rows have no toggle, §6.1 rule 7). */
const toggleable = (set: PartSetView) => set.parts.filter((p) => p.highlight !== 'shared_label')

/**
 * Parts view, Colorize, Show all / Physical only / Regions only, Group by, Explode (Parts view only; 0 restores the
 * exact positions) and a filter when the list has more than 12 parts (UI_DESIGN §6.4).
 */
export function PartsToolbar({ set, view, groupBy, onGroupBy, filter, onFilter }: PartsToolbarProps) {
  const setShow = usePartStore((s) => s.setShow)
  const setColorize = usePartStore((s) => s.setColorize)
  const setExplode = usePartStore((s) => s.setExplode)
  const setHidden = usePartStore((s) => s.setHidden)
  const filterId = useId()

  const hideKinds = (kinds: readonly string[]) =>
    setHidden(toggleable(set).filter((p) => kinds.includes(p.kind)).map((p) => p.id))

  return (
    <div className={styles.root}>
      <div className={styles.toolbar} role="toolbar" aria-label="Parts view options">
        <button type="button" className={styles.toggle} aria-pressed={view.show} onClick={() => setShow(!view.show)}>
          <Box size={13} aria-hidden /> Parts view
        </button>
        <button type="button" className={styles.toggle} aria-pressed={view.colorize} onClick={() => setColorize(!view.colorize)}>
          <Palette size={13} aria-hidden /> Colorize
        </button>
        <button type="button" className={styles.toggle} disabled={view.hidden.length === 0} onClick={() => setHidden([])}>
          <Eye size={13} aria-hidden /> Show all
        </button>
        <button type="button" className={styles.toggle} disabled={set.counts.physical === 0} onClick={() => hideKinds(['region', 'deferred'])}>
          <Shapes size={13} aria-hidden /> Physical only
        </button>
        <button type="button" className={styles.toggle} disabled={set.counts.regions === 0} onClick={() => hideKinds(['physical', 'deferred'])}>
          <Square size={13} aria-hidden /> Regions only
        </button>
      </div>
      <SegmentedControl<PartsGroupBy>
        label="Group by"
        size="sm"
        value={groupBy}
        options={[
          { value: 'category', label: 'Category' },
          { value: 'hierarchy', label: 'Hierarchy' },
        ]}
        onChange={onGroupBy}
      />
      {view.show && (
        <div className={styles.explode}>
          <span>Explode</span>
          <RangeSlider
            value={Math.round(view.explode * 100)}
            min={0}
            max={100}
            step={1}
            ariaLabel="Explode parts"
            ariaValueText={`${Math.round(view.explode * 100)} %`}
            onChange={(value) => setExplode(value / 100)}
          />
        </div>
      )}
      {set.parts.length > 12 && (
        <>
          <label htmlFor={filterId} className="visually-hidden">
            Filter parts
          </label>
          <input
            id={filterId}
            type="search"
            className={styles.filter}
            placeholder="Filter parts"
            value={filter}
            onChange={(event) => onFilter(event.target.value)}
          />
        </>
      )}
    </div>
  )
}
