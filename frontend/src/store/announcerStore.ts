/**
 * Screen-reader announcement queue (UI_DESIGN §1.4, §4.1). The store only queues messages and imports nothing;
 * `app/Announcer.tsx` renders each message in the topmost live-region pair (root, or the open modal's pair).
 */
import { create } from 'zustand'

export type Politeness = 'polite' | 'assertive'

export interface Announcement {
  readonly id: number
  readonly message: string
  readonly politeness: Politeness
}

const MAX_QUEUE = 8

interface AnnouncerState {
  queue: Announcement[]
  announce: (message: string, politeness?: Politeness) => void
  /** The renderer has shown this message: drop it (and everything older). */
  consume: (id: number) => void
  clear: () => void
}

let nextId = 1

export const useAnnouncerStore = create<AnnouncerState>()((set) => ({
  queue: [],
  announce: (message, politeness = 'polite') => {
    const text = message.trim()
    if (!text) return
    const entry: Announcement = { id: nextId++, message: text, politeness }
    set((state) => ({ queue: [...state.queue, entry].slice(-MAX_QUEUE) }))
  },
  consume: (id) =>
    set((state) => (state.queue.some((a) => a.id <= id) ? { queue: state.queue.filter((a) => a.id > id) } : state)),
  clear: () => set((state) => (state.queue.length ? { queue: [] } : state)),
}))

/** Queue a live-region message (stores and actions call this; components may too). */
export function announce(message: string, politeness: Politeness = 'polite'): void {
  useAnnouncerStore.getState().announce(message, politeness)
}
