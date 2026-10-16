/**
 * Reduced-motion check for motion driven from JavaScript (smooth scrolling, sheet settling, edge animation, the FAB
 * ring, explode tweening, View Transitions). CSS motion needs no call: the --duration-* tokens are already zero for
 * `prefers-reduced-motion: reduce` (styles/tokens.css) and for `<html data-motion="reduce">` (styles/modes.css).
 */
const REDUCE_QUERY = '(prefers-reduced-motion: reduce)'

/** True when the App settings ask for reduced motion (`data-motion="reduce"`) or the system does. */
export function prefersReducedMotion(): boolean {
  if (typeof document !== 'undefined' && document.documentElement.dataset.motion === 'reduce') return true
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia(REDUCE_QUERY).matches
}

/** `behavior` for `scrollIntoView` / `scrollTo`: instant under reduced motion, smooth otherwise. */
export function scrollBehavior(): ScrollBehavior {
  return prefersReducedMotion() ? 'auto' : 'smooth'
}
