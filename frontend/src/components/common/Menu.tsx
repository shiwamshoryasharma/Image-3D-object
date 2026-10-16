import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { IconButton } from './Button'
import styles from './Menu.module.css'

export interface MenuItem {
  id: string
  label: ReactNode
  icon?: ReactNode
  hint?: string
  disabled?: boolean
  danger?: boolean
  onSelect: () => void
}

export interface MenuSection {
  title?: string
  items: MenuItem[]
}

export interface MenuProps {
  /** Accessible label of the trigger button. */
  label: string
  icon: ReactNode
  sections: MenuSection[]
  disabled?: boolean
  triggerSize?: 'sm' | 'md'
  triggerVariant?: 'ghost' | 'solid'
}

interface Position {
  top: number
  left: number
}

const MENU_WIDTH = 220

/** Dropdown menu rendered in a portal (never clipped by scroll containers), keyboard navigable. */
export function Menu({ label, icon, sections, disabled, triggerSize = 'sm', triggerVariant = 'solid' }: MenuProps) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<Position | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const menuId = useId()

  const close = (restoreFocus = true) => {
    setOpen(false)
    if (restoreFocus) triggerRef.current?.focus()
  }

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    const menuHeight = menuRef.current?.offsetHeight ?? 240
    const spaceBelow = window.innerHeight - rect.bottom
    const top = spaceBelow < menuHeight + 12 && rect.top > menuHeight + 12 ? rect.top - menuHeight - 6 : rect.bottom + 6
    const left = Math.min(Math.max(8, rect.right - MENU_WIDTH), window.innerWidth - MENU_WIDTH - 8)
    setPosition({ top, left })
    const first = menuRef.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')
    first?.focus()
  }, [open])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return
      setOpen(false)
    }
    const onViewportChange = () => setOpen(false)
    document.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('resize', onViewportChange)
    window.addEventListener('scroll', onViewportChange, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('resize', onViewportChange)
      window.removeEventListener('scroll', onViewportChange, true)
    }
  }, [open])

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const buttons = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      close()
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      buttons[(index + 1) % buttons.length]?.focus()
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      buttons[(index - 1 + buttons.length) % buttons.length]?.focus()
    } else if (event.key === 'Home') {
      event.preventDefault()
      buttons[0]?.focus()
    } else if (event.key === 'End') {
      event.preventDefault()
      buttons[buttons.length - 1]?.focus()
    } else if (event.key === 'Tab') {
      close(false)
    }
  }

  return (
    <>
      <IconButton
        ref={triggerRef}
        label={label}
        icon={icon}
        size={triggerSize}
        variant={triggerVariant}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={(event) => {
          event.stopPropagation()
          setOpen((value) => !value)
        }}
        onKeyDown={(event) => event.stopPropagation()}
      />
      {open &&
        createPortal(
          <div
            ref={menuRef}
            id={menuId}
            role="menu"
            aria-label={label}
            className={styles.menu}
            style={{
              top: position?.top ?? -9999,
              left: position?.left ?? -9999,
              width: MENU_WIDTH,
              visibility: position ? 'visible' : 'hidden',
            }}
            onKeyDown={handleKeyDown}
            onClick={(event) => event.stopPropagation()}
          >
            {sections.map((section, sectionIndex) => (
              <div key={section.title ?? sectionIndex} className={styles.section} role="group" aria-label={section.title}>
                {section.title && <div className={styles.sectionTitle}>{section.title}</div>}
                {section.items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="menuitem"
                    disabled={item.disabled}
                    title={item.hint}
                    className={`${styles.item} ${item.danger ? styles.danger : ''}`}
                    onClick={() => {
                      close()
                      item.onSelect()
                    }}
                  >
                    {item.icon && <span className={styles.itemIcon}>{item.icon}</span>}
                    <span className={styles.itemLabel}>{item.label}</span>
                    {item.hint && <span className={styles.itemHint}>{item.hint}</span>}
                  </button>
                ))}
              </div>
            ))}
          </div>,
          document.body,
        )}
    </>
  )
}
