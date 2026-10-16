import { useEffect, useState, type RefObject } from 'react'

/** Enter or leave fullscreen for `element`. Browsers may refuse (iframe policy, missing user gesture). */
function requestToggle(element: HTMLElement | null): Promise<void> {
  if (document.fullscreenElement) return document.exitFullscreen()
  if (!element) return Promise.resolve()
  return element.requestFullscreen()
}

/** Fullscreen state/toggle for an element. */
export function useFullscreen(ref: RefObject<HTMLElement | null>) {
  const [isFullscreen, setIsFullscreen] = useState(false)
  const supported = typeof document !== 'undefined' && document.fullscreenEnabled

  useEffect(() => {
    const onChange = () => setIsFullscreen(!!ref.current && document.fullscreenElement === ref.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [ref])

  // Promise chaining instead of try/await keeps this hook compatible with the React Compiler; starting from a
  // resolved promise also turns synchronous throws (unsupported API) into a swallowed rejection.
  const toggle = (): Promise<void> =>
    Promise.resolve()
      .then(() => requestToggle(ref.current))
      .catch(() => undefined)

  return { isFullscreen, toggle, supported }
}
