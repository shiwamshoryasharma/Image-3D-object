/**
 * UI modes and preferences (UI_DESIGN.md §3.1, §3.5): the four modes, their metadata, the `?ui=` override, the
 * persisted preference sanitizer and the resolution rules for every "auto" value.
 *
 * There is no "auto" UI mode: the user asked for four modes with Default as the default (§12 Q-U1); the suggested
 * mode is only a badge in App settings.
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import type { ViewportMetrics } from './viewport.ts'

export const UI_MODES = Object.freeze(['default', 'node', 'mobile', 'tablet'] as const)
export type UiMode = (typeof UI_MODES)[number]

export const DENSITY_PREFERENCES = Object.freeze(['auto', 'comfortable', 'touch'] as const)
export type DensityPreference = (typeof DENSITY_PREFERENCES)[number]

export const MOTION_PREFERENCES = Object.freeze(['system', 'reduce'] as const)
export type MotionPreference = (typeof MOTION_PREFERENCES)[number]

export const NODE_VIEW_PREFERENCES = Object.freeze(['auto', 'canvas', 'list'] as const)
export type NodeViewPreference = (typeof NODE_VIEW_PREFERENCES)[number]

export const LINK_STYLES = Object.freeze(['bezier', 'smoothstep', 'straight'] as const)
export type LinkStyle = (typeof LINK_STYLES)[number]

export type Density = 'comfortable' | 'touch'
export type MotionAttribute = 'system' | 'reduce'
export type NodeView = 'canvas' | 'list'

/** Persisted `img3d.preferences` v1 (written by `store/preferencesStore.ts`). */
export interface Preferences {
  uiMode: UiMode
  density: DensityPreference
  motion: MotionPreference
  node: { view: NodeViewPreference; minimap: boolean; snapToGrid: boolean; linkStyle: LinkStyle }
  dismissedHints: string[]
}

export const DEFAULT_PREFERENCES: Readonly<Preferences> = Object.freeze({
  uiMode: 'default',
  density: 'auto',
  motion: 'system',
  node: Object.freeze({ view: 'auto', minimap: true, snapToGrid: true, linkStyle: 'bezier' }),
  dismissedHints: [],
})

export interface UiModeMeta {
  /** Card label in App settings. */
  readonly label: string
  /** Spoken name ("Mobile UI"): announcements, the shell error boundary, the About section. */
  readonly name: string
  /** One-line description on the App settings card. */
  readonly description: string
}

export const UI_MODE_META: Readonly<Record<UiMode, UiModeMeta>> = Object.freeze({
  default: { label: 'Default', name: 'Default UI', description: 'The classic layout: panels around the 3D viewer.' },
  node: { label: 'Node', name: 'Node UI', description: 'A node graph of the pipeline, with a list view for small screens.' },
  mobile: { label: 'Mobile', name: 'Mobile UI', description: 'Full-screen viewer with a floating bottom bar and sheets.' },
  tablet: { label: 'Tablet', name: 'Tablet UI', description: 'Floating side rail in landscape, bottom bar in portrait.' },
})

const MAX_HINTS = 50
const MAX_HINT_CHARS = 64

type Json = Record<string, unknown>

const isObject = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value)

function member<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value) ? (value as T) : undefined
}

export function isUiMode(value: unknown): value is UiMode {
  return member(value, UI_MODES) !== undefined
}

/** `?ui=default|node|mobile|tablet` (case-insensitive; the last `ui` parameter wins; anything else → `null`). */
export function parseUiOverride(search: string): UiMode | null {
  const values = new URLSearchParams(search).getAll('ui')
  if (values.length === 0) return null
  return member(values[values.length - 1].trim().toLowerCase(), UI_MODES) ?? null
}

/**
 * Keeps only values that are members of their tuples; anything corrupt or unknown is left out, so the store's
 * `merge` falls back to the default for it (a broken key can never select a non-existent mode).
 */
export function sanitizePreferences(raw: unknown): Partial<Preferences> {
  if (!isObject(raw)) return {}
  const out: Partial<Preferences> = {}
  const uiMode = member(raw.uiMode, UI_MODES)
  if (uiMode) out.uiMode = uiMode
  const density = member(raw.density, DENSITY_PREFERENCES)
  if (density) out.density = density
  const motion = member(raw.motion, MOTION_PREFERENCES)
  if (motion) out.motion = motion
  if (isObject(raw.node)) {
    const d = DEFAULT_PREFERENCES.node
    const n = raw.node
    out.node = {
      view: member(n.view, NODE_VIEW_PREFERENCES) ?? d.view,
      minimap: typeof n.minimap === 'boolean' ? n.minimap : d.minimap,
      snapToGrid: typeof n.snapToGrid === 'boolean' ? n.snapToGrid : d.snapToGrid,
      linkStyle: member(n.linkStyle, LINK_STYLES) ?? d.linkStyle,
    }
  }
  if (Array.isArray(raw.dismissedHints)) {
    const hints: string[] = []
    for (const hint of raw.dismissedHints) {
      if (typeof hint === 'string' && hint && hint.length <= MAX_HINT_CHARS && !hints.includes(hint)) hints.push(hint)
    }
    out.dismissedHints = hints.slice(0, MAX_HINTS)
  }
  return out
}

/**
 * The state inside a zustand persist envelope `{state, version}` (the raw `localStorage` value, e.g. from a
 * `storage` event); `null` for `null`, invalid JSON or a missing `state`. The `try` lives here so components have none.
 */
export function readPersistedState(raw: string | null): unknown {
  if (raw === null) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!isObject(parsed) || !('state' in parsed) || parsed.state === undefined) return null
  return parsed.state
}

/** `override ?? pref` when that shell has landed (`ready`), else Default. */
export function resolveUiMode(pref: UiMode, override: UiMode | null, ready: ReadonlySet<UiMode>): UiMode {
  const candidate = override ?? pref
  return ready.has(candidate) ? candidate : 'default'
}

type ScreenMetrics = Pick<ViewportMetrics, 'width' | 'height' | 'coarse'>

/** Phone-sized: narrow, or a touch screen whose short side is under 520 px (phones in landscape). */
const isPhoneSized = (v: ScreenMetrics): boolean => v.width < 600 || (v.coarse && Math.min(v.width, v.height) < 520)

/** "Suggested for this screen" badge (never applied automatically). */
export function suggestUiMode(v: ScreenMetrics): UiMode {
  if (isPhoneSized(v)) return 'mobile'
  if (v.coarse && v.width < 1366) return 'tablet'
  return 'default'
}

/** Mobile and Tablet force touch density; otherwise the preference, `auto` following the pointer. */
export function resolveDensity(pref: DensityPreference, mode: UiMode, coarse: boolean): Density {
  if (mode === 'mobile' || mode === 'tablet') return 'touch'
  if (pref === 'touch') return 'touch'
  if (pref === 'comfortable') return 'comfortable'
  return coarse ? 'touch' : 'comfortable'
}

/** `data-motion` value; CSS also honours `prefers-reduced-motion` for `system`. */
export function resolveMotion(pref: MotionPreference): MotionAttribute {
  return pref === 'reduce' ? 'reduce' : 'system'
}

/** Node editor view: explicit preference, else the list on phone-sized screens and the canvas elsewhere. */
export function resolveNodeView(pref: NodeViewPreference, v: ScreenMetrics): NodeView {
  if (pref === 'canvas' || pref === 'list') return pref
  return isPhoneSized(v) ? 'list' : 'canvas'
}
