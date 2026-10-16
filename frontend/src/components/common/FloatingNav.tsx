import { useRef, type CSSProperties, type KeyboardEvent, type ReactNode, type Ref } from 'react'

import styles from './FloatingNav.module.css'

export interface NavItem {
  id: string
  label: string
  icon: ReactNode
  /** Short visible badge, read after the label; `''` shows a dot only (e.g. activity). */
  badge?: string | null
  /** Id of the overlay the item opens (`aria-controls`). */
  controls?: string
  /** Whether that overlay is open (`aria-expanded`); leave undefined for plain destinations. */
  expanded?: boolean
  /** The item is the current page (`aria-current="page"`). */
  current?: boolean
}

export interface FloatingNavProps {
  /** Accessible name of the `<nav>` landmark. */
  label: string
  items: readonly NavItem[]
  /** Highlighted item; also the keyboard tab stop. */
  value: string | null
  onSelect: (id: string) => void
  /** Rendered in the middle column (the Generate FAB). */
  center?: ReactNode
  /** Slides the bar away and makes it inert (e.g. while the soft keyboard is up). */
  hidden?: boolean
}

interface NavItemButtonProps {
  item: NavItem
  active: boolean
  tabbable: boolean
  onSelect: (id: string) => void
  buttonRef: Ref<HTMLButtonElement>
  className?: string
}

/** One navigation button (shared by FloatingNav and NavRail). Items open overlays, so they are buttons, not tabs. */
export function NavItemButton({ item, active, tabbable, onSelect, buttonRef, className }: NavItemButtonProps) {
  const badge = item.badge ?? null
  return (
    <button
      ref={buttonRef}
      type="button"
      className={`${styles.item} ${active ? styles.active : ''} ${className ?? ''}`}
      tabIndex={tabbable ? 0 : -1}
      aria-controls={item.controls}
      aria-expanded={item.expanded}
      aria-current={item.current ? 'page' : undefined}
      onClick={() => onSelect(item.id)}
    >
      <span className={styles.icon} aria-hidden>
        {item.icon}
        {badge !== null && <span className={badge === '' ? styles.dot : styles.badge}>{badge}</span>}
      </span>
      <span className={styles.label}>{item.label}</span>
      {badge && <span className="visually-hidden">, {badge}</span>}
    </button>
  )
}

const HORIZONTAL_KEYS: Record<string, (index: number, count: number) => number> = {
  ArrowRight: (index, count) => (index + 1) % count,
  ArrowLeft: (index, count) => (index - 1 + count) % count,
  Home: () => 0,
  End: (_index, count) => count - 1,
}

/**
 * Floating bottom navigation of the Mobile and Tablet shells: a glass card above the safe area with the items left
 * and right of an optional centre slot. Roving focus with Left/Right/Home/End (the centre slot keeps its own tab stop).
 */
export function FloatingNav({ label, items, value, onSelect, center, hidden = false }: FloatingNavProps) {
  const refs = useRef<Array<HTMLButtonElement | null>>([])
  const activeIndex = items.findIndex((item) => item.id === value)
  const tabStop = activeIndex >= 0 ? activeIndex : 0
  const half = Math.ceil(items.length / 2)
  const columns: CSSProperties = {
    gridTemplateColumns: center
      ? `repeat(${half}, minmax(0, 1fr)) var(--fab-size) repeat(${items.length - half}, minmax(0, 1fr))`
      : `repeat(${items.length}, minmax(0, 1fr))`,
  }

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const step = HORIZONTAL_KEYS[event.key]
    const index = refs.current.findIndex((node) => node !== null && node === document.activeElement)
    if (!step || index < 0) return
    event.preventDefault()
    refs.current[step(index, items.length)]?.focus()
  }

  const renderItem = (item: NavItem, index: number) => (
    <NavItemButton
      key={item.id}
      item={item}
      active={index === activeIndex}
      tabbable={index === tabStop}
      onSelect={onSelect}
      buttonRef={(node) => {
        refs.current[index] = node
      }}
    />
  )

  return (
    <nav
      aria-label={label}
      className={`${styles.nav} ${hidden ? styles.hidden : ''}`}
      style={columns}
      inert={hidden}
      onKeyDown={onKeyDown}
    >
      {items.slice(0, center ? half : items.length).map((item, index) => renderItem(item, index))}
      {center && <div className={styles.center}>{center}</div>}
      {center && items.slice(half).map((item, index) => renderItem(item, half + index))}
    </nav>
  )
}
