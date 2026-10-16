import { CircleCheck, CircleX, Info, TriangleAlert, X } from 'lucide-react'
import { useEffect, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'

import { useToastStore, type Toast } from '../../store/toastStore'
import { getTopModal, subscribeModalStack } from './modalStack'
import styles from './Toaster.module.css'

const ICONS = {
  info: Info,
  success: CircleCheck,
  warning: TriangleAlert,
  error: CircleX,
} as const

function noModal(): null {
  return null
}

function ToastItem({ toast }: { toast: Toast }) {
  const dismiss = useToastStore((s) => s.dismiss)
  useEffect(() => {
    if (toast.duration <= 0) return
    const timer = window.setTimeout(() => dismiss(toast.id), toast.duration)
    return () => window.clearTimeout(timer)
  }, [toast.id, toast.duration, dismiss])

  const Icon = ICONS[toast.tone]
  return (
    <div className={`${styles.toast} ${styles[toast.tone]}`} role={toast.tone === 'error' ? 'alert' : 'status'}>
      <Icon size={18} className={styles.icon} aria-hidden />
      <div className={styles.content}>
        <p className={styles.title}>{toast.title}</p>
        {toast.message && <p className={styles.message}>{toast.message}</p>}
      </div>
      <button type="button" className={styles.close} aria-label="Dismiss notification" onClick={() => dismiss(toast.id)}>
        <X size={14} />
      </button>
    </div>
  )
}

/**
 * Toast stack (aria-live). Mounted once at the app root. A modal `<dialog>` makes everything outside it inert and
 * hides it from assistive technology, so while one is open the stack renders inside the topmost modal (modalStack).
 */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts)
  const topModal = useSyncExternalStore(subscribeModalStack, getTopModal, noModal)
  const region = (
    <div className={styles.region} aria-live="polite" aria-relevant="additions">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} />
      ))}
    </div>
  )
  return topModal ? createPortal(region, topModal) : region
}
