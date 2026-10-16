import type { ReactNode } from 'react'

import styles from './Panel.module.css'

export interface PanelProps {
  title?: ReactNode
  icon?: ReactNode
  /** Right side of the header (buttons, badges). */
  actions?: ReactNode
  /** Small text under the title. */
  subtitle?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  /** Element id used for `aria-labelledby`. */
  id?: string
  flush?: boolean
}

/** Card container with an optional header. Rendered as a labelled `<section>`. */
export function Panel({ title, icon, actions, subtitle, children, className, bodyClassName, id, flush }: PanelProps) {
  const headingId = id ? `${id}-title` : undefined
  return (
    <section className={`${styles.panel} ${className ?? ''}`} aria-labelledby={title ? headingId : undefined}>
      {(title || actions) && (
        <header className={styles.header}>
          <div className={styles.heading}>
            {icon && <span className={styles.icon}>{icon}</span>}
            <div className={styles.titles}>
              {title && (
                <h2 id={headingId} className={styles.title}>
                  {title}
                </h2>
              )}
              {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
            </div>
          </div>
          {actions && <div className={styles.actions}>{actions}</div>}
        </header>
      )}
      <div className={`${flush ? styles.flush : styles.body} ${bodyClassName ?? ''}`}>{children}</div>
    </section>
  )
}
