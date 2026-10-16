import { create } from 'zustand'

export type ToastTone = 'info' | 'success' | 'warning' | 'error'

export interface Toast {
  id: number
  tone: ToastTone
  title: string
  message?: string
  /** Auto-dismiss delay in ms; 0 keeps the toast until dismissed. */
  duration: number
}

interface ToastState {
  toasts: Toast[]
  push: (toast: Omit<Toast, 'id' | 'duration'> & { duration?: number }) => number
  dismiss: (id: number) => void
}

const MAX_TOASTS = 5
let nextId = 1

export const useToastStore = create<ToastState>()((set) => ({
  toasts: [],
  push: (toast) => {
    const id = nextId++
    const duration = toast.duration ?? (toast.tone === 'error' ? 8000 : 4500)
    set((state) => {
      // Collapse identical consecutive toasts (e.g. repeated network failures).
      const duplicate = state.toasts.find((t) => t.title === toast.title && t.message === toast.message)
      const rest = duplicate ? state.toasts.filter((t) => t.id !== duplicate.id) : state.toasts
      return { toasts: [...rest, { ...toast, id, duration }].slice(-MAX_TOASTS) }
    })
    return id
  },
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
}))

/** Imperative helpers usable outside React components. */
export const toast = {
  info: (title: string, message?: string) => useToastStore.getState().push({ tone: 'info', title, message }),
  success: (title: string, message?: string) => useToastStore.getState().push({ tone: 'success', title, message }),
  warning: (title: string, message?: string) => useToastStore.getState().push({ tone: 'warning', title, message }),
  error: (title: string, message?: string) => useToastStore.getState().push({ tone: 'error', title, message }),
}
