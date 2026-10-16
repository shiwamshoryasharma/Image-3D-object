// PROBE (test-only): which component/hook patterns babel-plugin-react-compiler (frontend's installed version)
// compiles vs. bails out on. Mirrors test/frontend/review/compiler_check.mjs settings.
// Usage: node test/mesh_tuner/probe/frontend/compiler_patterns.mjs [outJson]
import { createRequire } from 'node:module'
import { writeFileSync } from 'node:fs'

const FRONTEND = 'E:/14-OCT-2026/Img3D/frontend'
const require = createRequire(`${FRONTEND}/package.json`)
const babel = require('@babel/core')
const compiler = require.resolve('babel-plugin-react-compiler')

const HEADER = `import { useEffect, useEffectEvent, useMemo, useRef, useState, useCallback } from 'react'\nimport { create } from 'zustand'\nconst useStore = create(() => ({ a: 1, items: [3, 1, 2], run: async () => true }))\n`

const CASES = {
  try_finally_async_handler: `export function C() {
    const [busy, setBusy] = useState(false)
    const go = async () => { setBusy(true); try { await useStore.getState().run() } finally { setBusy(false) } }
    return <button onClick={() => void go()}>{busy ? 'x' : 'y'}</button>
  }`,
  try_catch_await_handler: `export function C() {
    const [err, setErr] = useState(null)
    const go = async () => { try { await useStore.getState().run() } catch (e) { setErr(String(e)) } }
    return <button onClick={() => void go()}>{err}</button>
  }`,
  try_catch_with_conditional_in_try: `export function C() {
    const [err, setErr] = useState(null)
    const go = async () => { try { const ok = await useStore.getState().run(); setErr(ok ? null : 'no') } catch (e) { setErr(e instanceof Error ? e.message : 'x') } }
    return <button onClick={() => void go()}>{err}</button>
  }`,
  promise_chain_handler: `export function C() {
    const [p, setP] = useState(undefined)
    const go = () => { setP(null); return useStore.getState().run().then(() => setP(100)).catch(() => undefined).then(() => setP(undefined)) }
    return <button onClick={() => void go()}>{String(p)}</button>
  }`,
  store_action_async_in_store: `export function C() {
    const run = useStore((s) => s.run)
    return <button onClick={() => void run()}>go</button>
  }`,
  read_ref_current_in_render: `export function C() {
    const ref = useRef(0)
    return <span>{ref.current}</span>
  }`,
  write_ref_current_in_render: `export function C({ v }) {
    const ref = useRef(0)
    ref.current = v
    return <span>{v}</span>
  }`,
  set_state_in_render_unconditional: `export function C() {
    const [a, setA] = useState(0)
    setA(1)
    return <span>{a}</span>
  }`,
  derived_state_reset_on_prop_change: `export function C({ url }) {
    const [prev, setPrev] = useState(url)
    const [mode, setMode] = useState('tuned')
    if (prev !== url) { setPrev(url); setMode('tuned') }
    return <button onClick={() => setMode(mode === 'tuned' ? 'original' : 'tuned')}>{mode}</button>
  }`,
  mutate_store_array_in_render: `export function C() {
    const items = useStore((s) => s.items)
    items.sort()
    return <span>{items.join(',')}</span>
  }`,
  sort_copy_in_render: `export function C() {
    const items = useStore((s) => s.items)
    const sorted = [...items].sort((x, y) => x - y)
    return <span>{sorted.join(',')}</span>
  }`,
  nested_component_definition: `export function C() {
    function Inner() { return <i>x</i> }
    return <Inner />
  }`,
  use_effect_event: `export function C({ on }) {
    const fire = useEffectEvent(() => on())
    useEffect(() => { const t = setInterval(() => fire(), 1000); return () => clearInterval(t) }, [])
    return null
  }`,
  use_memo_manual: `export function C({ xs }) {
    const total = useMemo(() => xs.reduce((a, b) => a + b, 0), [xs])
    return <span>{total}</span>
  }`,
  use_callback_missing_dep: `export function C({ x }) {
    const f = useCallback(() => x + 1, [])
    return <span>{f()}</span>
  }`,
  local_mutation_object_build: `export function C({ rows }) {
    const out = {}
    for (const r of rows) out[r.k] = r.v
    return <span>{Object.keys(out).length}</span>
  }`,
  switch_return_in_component: `export function C({ s }) {
    switch (s) { case 'a': return <i>a</i>; case 'b': return <b>b</b>; default: return null }
  }`,
  document_listener_effect_popover: `export function C() {
    const [open, setOpen] = useState(false)
    const ref = useRef(null)
    useEffect(() => {
      if (!open) return
      const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
      document.addEventListener('pointerdown', onDown)
      return () => document.removeEventListener('pointerdown', onDown)
    }, [open])
    return <div ref={ref}><button onClick={() => setOpen((v) => !v)}>t</button></div>
  }`,
  hook_with_try_finally: `export function useThing() {
    const [busy, setBusy] = useState(false)
    const go = async () => { setBusy(true); try { await Promise.resolve() } finally { setBusy(false) } }
    return { busy, go }
  }`,
  for_of_with_break: `export function C({ steps }) {
    let firstPending = null
    for (const s of steps) { if (s.status === 'pending') { firstPending = s.id; break } }
    return <span>{firstPending}</span>
  }`,
}

const results = []
for (const [name, body] of Object.entries(CASES)) {
  const events = []
  let compiled = 0
  let thrown = null
  try {
    babel.transformSync(HEADER + body, {
      filename: `${name}.tsx`, babelrc: false, configFile: false,
      parserOpts: { plugins: ['typescript', 'jsx'] },
      plugins: [[compiler, { panicThreshold: 'none', logger: { logEvent(_f, e) {
        if (e.kind === 'CompileSuccess') { compiled += 1; return }
        events.push({ kind: e.kind, fn: e.fnName ?? null,
          reason: e.detail?.reason ?? e.detail?.options?.reason ?? e.reason ?? String(e.detail ?? e.data ?? '') })
      } } }]],
    })
  } catch (error) { thrown = String(error?.message ?? error).split('\n')[0] }
  const bail = events.filter((e) => e.kind !== 'CompileSkip' || e.reason)
  results.push({ case: name, compiled, ok: bail.length === 0 && !thrown, events: bail, thrown })
}
for (const r of results) console.log(`${r.ok ? 'OK  ' : 'BAIL'} ${r.case}${r.events.length ? ' :: ' + r.events.map((e) => `${e.kind}: ${e.reason}`).join(' | ') : ''}${r.thrown ? ' :: THROWN ' + r.thrown : ''}`)
const out = process.argv[2]
if (out) writeFileSync(out, JSON.stringify(results, null, 1))
