import { useSyncExternalStore } from 'react'

export type Breakpoint = 'mobile' | 'tablet' | 'desktop'

/**
 * Layout breakpoints: mobile < 768 ≤ tablet < 1280 ≤ desktop. A phone held in landscape (short and touch-driven,
 * e.g. 812×375) also gets the mobile layout (UI_DESIGN P7); short desktop windows with a mouse keep their layout.
 */
const TABLET_QUERY = '(min-width: 768px)'
const DESKTOP_QUERY = '(min-width: 1280px)'
const PHONE_LANDSCAPE_QUERY = '(max-height: 500px) and (pointer: coarse)'

function read(): Breakpoint {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'desktop'
  if (window.matchMedia(PHONE_LANDSCAPE_QUERY).matches) return 'mobile'
  if (window.matchMedia(DESKTOP_QUERY).matches) return 'desktop'
  if (window.matchMedia(TABLET_QUERY).matches) return 'tablet'
  return 'mobile'
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => undefined
  const queries = [TABLET_QUERY, DESKTOP_QUERY, PHONE_LANDSCAPE_QUERY].map((query) => window.matchMedia(query))
  queries.forEach((query) => query.addEventListener('change', onChange))
  return () => queries.forEach((query) => query.removeEventListener('change', onChange))
}

export function useBreakpoint(): Breakpoint {
  return useSyncExternalStore(subscribe, read, () => 'desktop')
}
