// PROBE (test-only): compile proposed Auto Mesh Tuner UI snippets with the frontend's React Compiler to make sure the
// recommended patterns (native <dialog>, variant toggle, derived reset, store-driven async) compile without bailouts.
// Usage: node test/mesh_tuner/probe/frontend/proposed_components.mjs [outJson]
import { createRequire } from 'node:module'
import { writeFileSync } from 'node:fs'

const FRONTEND = 'E:/14-OCT-2026/Img3D/frontend'
const require = createRequire(`${FRONTEND}/package.json`)
const babel = require('@babel/core')
const compiler = require.resolve('babel-plugin-react-compiler')

const SOURCES = {
  Dialog: `
import { useEffect, useId, useRef, type ReactNode } from 'react'
export interface DialogProps { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode }
export function Dialog({ open, onClose, title, children, footer }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    else if (!open && dialog.open) dialog.close()
  }, [open])
  return (
    <dialog ref={ref} aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); onClose() }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <header><h2 id={titleId}>{title}</h2><button type="button" aria-label="Close" onClick={onClose}>x</button></header>
      <div>{open ? children : null}</div>
      {footer && <footer>{footer}</footer>}
    </dialog>
  )
}`,
  RuntimePill: `
import { useEffect, useId, useRef, useState } from 'react'
import { create } from 'zustand'
type S = { runtime: { state: string; selected: { version: string } | null } | null; download: { percent: number | null } | null; detect: () => Promise<void> }
const useBlenderStore = create<S>()(() => ({ runtime: null, download: null, detect: async () => {} }))
export function BlenderRuntimeStatus({ compact = false }: { compact?: boolean }) {
  const runtime = useBlenderStore((s) => s.runtime)
  const percent = useBlenderStore((s) => s.download?.percent ?? null)
  const detect = useBlenderStore((s) => s.detect)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const popoverId = useId()
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => { if (!rootRef.current?.contains(event.target as Node)) setOpen(false) }
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('pointerdown', onPointerDown); document.removeEventListener('keydown', onKeyDown) }
  }, [open])
  const label = runtime?.selected ? 'Blender ' + runtime.selected.version : 'Blender not set up'
  return (
    <div ref={rootRef}>
      <button type="button" aria-expanded={open} aria-controls={popoverId} onClick={() => setOpen((v) => !v)}>
        {!compact && <span>{label}</span>}{percent !== null && <span>{Math.round(percent)}%</span>}
      </button>
      {open && <div id={popoverId} role="dialog" aria-label="Blender runtime"><button type="button" onClick={() => void detect()}>Re-detect</button></div>}
    </div>
  )
}`,
  ViewerVariant: `
import { useState } from 'react'
type Variant = 'original' | 'tuned'
export function useModelVariant(jobId: string | null, originalUrl: string | null, tunedUrl: string | null) {
  const [variant, setVariant] = useState<Variant>('tuned')
  const [prevJob, setPrevJob] = useState(jobId)
  if (prevJob !== jobId) { setPrevJob(jobId); setVariant('tuned') }
  const effective: Variant = variant === 'tuned' && tunedUrl ? 'tuned' : 'original'
  const url = effective === 'tuned' ? tunedUrl : originalUrl
  return { variant: effective, setVariant, url, hasTuned: !!tunedUrl }
}`,
  TuningChecklist: `
type StepStatus = 'pending' | 'running' | 'done' | 'skipped' | 'failed' | 'warning'
interface Step { id: string; label: string; status: StepStatus; message: string | null; duration_seconds: number | null }
interface Metric { key: string; label: string; before: number | null; after: number | null; better: 'lower' | 'higher' | 'neutral' }
export function TuningReportView({ steps, metrics }: { steps: Step[]; metrics: Metric[] }) {
  return (
    <div>
      <ol aria-label="Tuning steps">
        {steps.map((step) => (
          <li key={step.id} data-status={step.status}>
            <span>{step.label}<span className="visually-hidden"> — {step.status}</span></span>
            {step.duration_seconds !== null && <span>{step.duration_seconds.toFixed(1)}s</span>}
            {step.message && <p>{step.message}</p>}
          </li>
        ))}
      </ol>
      <table>
        <thead><tr><th scope="col">Metric</th><th scope="col">Before</th><th scope="col">After</th><th scope="col">Change</th></tr></thead>
        <tbody>
          {metrics.map((m) => {
            const delta = m.before !== null && m.after !== null ? m.after - m.before : null
            const improved = delta === null || m.better === 'neutral' ? null : m.better === 'lower' ? delta < 0 : delta > 0
            return (
              <tr key={m.key}>
                <th scope="row">{m.label}</th><td>{m.before ?? '—'}</td><td>{m.after ?? '—'}</td>
                <td data-improved={improved === null ? undefined : String(improved)}>{delta === null ? '—' : (delta > 0 ? '+' : '') + delta}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}`,
}

const results = []
for (const [name, code] of Object.entries(SOURCES)) {
  const events = []
  let compiled = 0
  babel.transformSync(code, {
    filename: `${name}.tsx`, babelrc: false, configFile: false,
    parserOpts: { plugins: ['typescript', 'jsx'] },
    plugins: [[compiler, { panicThreshold: 'none', logger: { logEvent(_f, e) {
      if (e.kind === 'CompileSuccess') { compiled += 1; return }
      events.push({ kind: e.kind, fn: e.fnName ?? null, reason: e.detail?.reason ?? e.detail?.options?.reason ?? e.reason ?? String(e.detail ?? '') })
    } } }]],
  })
  const bail = events.filter((e) => e.kind !== 'CompileSkip' || e.reason)
  results.push({ source: name, compiled, ok: bail.length === 0, events: bail })
  console.log(`${bail.length ? 'BAIL' : 'OK  '} ${name} (compiled fns: ${compiled})${bail.length ? ' :: ' + JSON.stringify(bail) : ''}`)
}
if (process.argv[2]) writeFileSync(process.argv[2], JSON.stringify(results, null, 1))
