import { X } from 'lucide-react'
import { useEffect, useId, useRef, type ReactNode } from 'react'

import { IconButton } from './Button'
import styles from './Dialog.module.css'

export interface DialogProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  icon?: ReactNode
  subtitle?: ReactNode
  /** Extra header content (badges) next to the close button. */
  headerAside?: ReactNode
  children: ReactNode
  footer?: ReactNode
  size?: 'md' | 'lg'
  className?: string
}

/**
 * Modal dialog on the native `<dialog>` element: top layer, focus trap and inert background come from the
 * browser. Escape (`cancel`) and a click on the backdrop call `onClose`. Full-height sheet on phones.
 */
export function Dialog({ open, onClose, title, icon, subtitle, headerAside, children, footer, size = 'md', className }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    else if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      className={`${styles.dialog} ${styles[size]} ${className ?? ''}`}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        // The surface fills the dialog box, so a click that targets the <dialog> itself hit the backdrop.
        if (event.target === event.currentTarget) onClose()
      }}
    >
      {open && (
        <div className={styles.surface}>
          <header className={styles.header}>
            {icon && <span className={styles.icon}>{icon}</span>}
            <div className={styles.titles}>
              <h2 id={titleId} className={styles.title}>
                {title}
              </h2>
              {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
            </div>
            {headerAside && <div className={styles.aside}>{headerAside}</div>}
            <IconButton label="Close" icon={<X size={16} />} size="sm" onClick={onClose} />
          </header>
          <div className={styles.body}>{children}</div>
          {footer && <footer className={styles.footer}>{footer}</footer>}
        </div>
      )}
    </dialog>
  )
}
