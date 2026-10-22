/**
 * Viewer dock under the canvas (UI_DESIGN §4.3.13, ≥ 1024 px): the one `ViewerPanel` (UI5), resizable between 30 and
 * 60 % of the column with a keyboard-operable splitter (arrows, Home/End), and collapsible.
 */
import { useState, type KeyboardEvent, type PointerEvent } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

import { ViewerPanel } from '../../components/viewer/ViewerPanel'
import styles from './NodeShell.module.css'

const MIN = 30
const MAX = 60
const STEP = 5

const clamp = (value: number): number => Math.min(MAX, Math.max(MIN, Math.round(value)))

export function ViewerDock({ collapsed, onCollapsedChange }: { collapsed: boolean; onCollapsedChange: (collapsed: boolean) => void }) {
  const [percent, setPercent] = useState(40)
  const [dragging, setDragging] = useState(false)

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const next =
      event.key === 'ArrowUp' ? percent + STEP : event.key === 'ArrowDown' ? percent - STEP : event.key === 'Home' ? MAX : event.key === 'End' ? MIN : null
    if (next === null) return
    event.preventDefault()
    setPercent(clamp(next))
  }
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    setDragging(true)
  }
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return
    const column = event.currentTarget.parentElement?.parentElement?.getBoundingClientRect()
    if (!column || column.height <= 0) return
    setPercent(clamp(((column.bottom - event.clientY) / column.height) * 100))
  }

  return (
    <section className={styles.dock} style={collapsed ? undefined : { blockSize: `${percent}%` }} aria-label="3D viewer">
      {!collapsed && (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Viewer height"
          aria-valuemin={MIN}
          aria-valuemax={MAX}
          aria-valuenow={percent}
          aria-valuetext={`${percent} %`}
          tabIndex={0}
          className={styles.splitter}
          onKeyDown={onKeyDown}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={() => setDragging(false)}
          onPointerCancel={() => setDragging(false)}
        />
      )}
      <div className={styles.dockBar}>
        <button type="button" className={styles.dockToggle} aria-expanded={!collapsed} onClick={() => onCollapsedChange(!collapsed)}>
          {collapsed ? <ChevronRight size={16} aria-hidden /> : <ChevronDown size={16} aria-hidden />} Viewer
        </button>
      </div>
      <div className={styles.dockBody} hidden={collapsed}>
        <ViewerPanel active={!collapsed} />
      </div>
    </section>
  )
}
