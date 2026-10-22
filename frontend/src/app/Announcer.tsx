import { useEffect, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'

import { getTopModal, subscribeModalStack } from '../components/common/modalStack'
import { useAnnouncerStore, type Announcement, type Politeness } from '../store/announcerStore'

const CLEAR_AFTER_MS = 8000

const noModal = () => null

function latest(queue: readonly Announcement[], politeness: Politeness): Announcement | null {
  for (let i = queue.length - 1; i >= 0; i--) if (queue[i].politeness === politeness) return queue[i]
  return null
}

function RegionPair({ polite, assertive }: { polite: Announcement | null; assertive: Announcement | null }) {
  // A keyed child replaces the text node, so the same message announced twice is spoken twice.
  return (
    <div className="visually-hidden" data-a11y-announcer="">
      <div role="status" aria-live="polite" aria-atomic="true">
        {polite && <span key={polite.id}>{polite.message}</span>}
      </div>
      <div role="alert" aria-live="assertive" aria-atomic="true">
        {assertive && <span key={assertive.id}>{assertive.message}</span>}
      </div>
    </div>
  )
}

/**
 * Live regions for `announcerStore` (UI_DESIGN §4.1): a permanent root pair, plus, while a modal `<dialog>` is open
 * (everything outside it is inert), a second pair portaled into the topmost modal. Each message renders in the
 * topmost pair only; the queue is cleared a few seconds after the latest message.
 */
export function Announcer() {
  const topModal = useSyncExternalStore(subscribeModalStack, getTopModal, noModal)
  const polite = useAnnouncerStore((s) => latest(s.queue, 'polite'))
  const assertive = useAnnouncerStore((s) => latest(s.queue, 'assertive'))
  const newestId = Math.max(polite?.id ?? 0, assertive?.id ?? 0)

  useEffect(() => {
    if (newestId === 0) return
    const timer = window.setTimeout(() => useAnnouncerStore.getState().clear(), CLEAR_AFTER_MS)
    return () => window.clearTimeout(timer)
  }, [newestId])

  return (
    <>
      <RegionPair polite={topModal ? null : polite} assertive={topModal ? null : assertive} />
      {topModal && createPortal(<RegionPair polite={polite} assertive={assertive} />, topModal)}
    </>
  )
}
