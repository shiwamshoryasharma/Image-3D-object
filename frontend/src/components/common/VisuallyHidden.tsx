import type { ReactNode } from 'react'

export interface VisuallyHiddenProps {
  children: ReactNode
  id?: string
  /** Element to render (default `span`); use a heading tag for an off-screen section title. */
  as?: 'span' | 'div' | 'p' | 'h2' | 'h3'
}

/** Content for assistive technology only (the global `visually-hidden` class from styles/global.css). */
export function VisuallyHidden({ children, id, as: Tag = 'span' }: VisuallyHiddenProps) {
  return (
    <Tag id={id} className="visually-hidden">
      {children}
    </Tag>
  )
}
