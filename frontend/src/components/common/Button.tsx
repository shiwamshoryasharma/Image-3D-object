import type { ComponentProps, ReactNode } from 'react'

import { Spinner } from './Spinner'
import styles from './Button.module.css'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'subtle'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ComponentProps<'button'> {
  variant?: ButtonVariant
  size?: ButtonSize
  icon?: ReactNode
  iconRight?: ReactNode
  loading?: boolean
  fullWidth?: boolean
}

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  iconRight,
  loading = false,
  fullWidth = false,
  className,
  children,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  const classes = [styles.button, styles[variant], styles[size], fullWidth ? styles.fullWidth : '', className ?? '']
    .filter(Boolean)
    .join(' ')
  return (
    <button type={type} className={classes} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading ? <Spinner size={size === 'lg' ? 18 : 14} /> : icon}
      {children !== undefined && children !== null && <span className={styles.label}>{children}</span>}
      {iconRight}
    </button>
  )
}

export interface IconButtonProps extends ComponentProps<'button'> {
  /** Accessible label (also used as tooltip). */
  label: string
  icon: ReactNode
  active?: boolean
  size?: 'sm' | 'md'
  variant?: 'ghost' | 'solid' | 'danger'
}

export function IconButton({
  label,
  icon,
  active = false,
  size = 'md',
  variant = 'ghost',
  className,
  type = 'button',
  title,
  ...rest
}: IconButtonProps) {
  const classes = [styles.iconButton, styles[`icon_${size}`], styles[`icon_${variant}`], active ? styles.active : '', className ?? '']
    .filter(Boolean)
    .join(' ')
  return (
    <button type={type} className={classes} aria-label={label} title={title ?? label} {...rest}>
      {icon}
    </button>
  )
}
