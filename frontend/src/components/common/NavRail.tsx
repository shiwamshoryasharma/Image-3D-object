import { useRef, type KeyboardEvent, type ReactNode } from 'react'

import { NavItemButton, type NavItem } from './FloatingNav'
import styles from './NavRail.module.css'

export interface NavRailProps {
  /** Accessible name of the `<nav>` landmark. */
  label: string
  items: readonly NavItem[]
  /** Highlighted item; also the keyboard tab stop. */
  value: string | null
  onSelect: (id: string) => void
  /** Bottom slot (the rail Generate button, App settings). */
  footer?: ReactNode
}

const VERTICAL_KEYS: Record<string, (index: number, count: number) => number> = {
  ArrowDown: (index, count) => (index + 1) % count,
  ArrowUp: (index, count) => (index - 1 + count) % count,
  Home: () => 0,
  End: (_index, count) => count - 1,
}

/**
 * Floating left rail (Tablet landscape, Mobile landscape): the FloatingNav semantics laid out vertically, with
 * roving focus on Up/Down/Home/End. The footer keeps its own tab stops.
 */
export function NavRail({ label, items, value, onSelect, footer }: NavRailProps) {
  const refs = useRef<Array<HTMLButtonElement | null>>([])
  const activeIndex = items.findIndex((item) => item.id === value)
  const tabStop = activeIndex >= 0 ? activeIndex : 0

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = VERTICAL_KEYS[event.key]
    const index = refs.current.findIndex((node) => node !== null && node === document.activeElement)
    if (!step || index < 0) return
    event.preventDefault()
    refs.current[step(index, items.length)]?.focus()
  }

  return (
    <nav aria-label={label} className={styles.rail}>
      <div className={styles.items} onKeyDown={onKeyDown}>
        {items.map((item, index) => (
          <NavItemButton
            key={item.id}
            item={item}
            active={index === activeIndex}
            tabbable={index === tabStop}
            onSelect={onSelect}
            className={styles.item}
            buttonRef={(node) => {
              refs.current[index] = node
            }}
          />
        ))}
      </div>
      {footer && <div className={styles.footer}>{footer}</div>}
    </nav>
  )
}
