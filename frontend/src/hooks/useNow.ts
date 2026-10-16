import { useEffect, useState } from 'react'

/** Current time (ms), re-rendering every `intervalMs` while `enabled`. */
export function useNow(intervalMs = 1000, enabled = true): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) return
    const update = () => setNow(Date.now())
    const timer = window.setInterval(update, intervalMs)
    const kick = window.setTimeout(update, 0)
    return () => {
      window.clearInterval(timer)
      window.clearTimeout(kick)
    }
  }, [intervalMs, enabled])
  return now
}
