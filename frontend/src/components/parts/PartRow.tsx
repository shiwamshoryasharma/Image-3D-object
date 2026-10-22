import { Copy, Download, Eye, EyeOff, Focus, Image as ImageIcon, MoreHorizontal, ZoomIn } from 'lucide-react'
import type { CSSProperties, KeyboardEvent, MouseEvent } from 'react'

import { downloadArtifact } from '../../actions/downloads'
import type { PartDisplayState, PartView } from '../../lib/partsModel.ts'
import { announce } from '../../store/announcerStore'
import { usePartStore } from '../../store/partStore'
import { toast } from '../../store/toastStore'
import { Menu, type MenuItem } from '../common/Menu'
import { selectPartViewerStatus, useViewerRuntime } from '../viewer/viewerRuntime'
import { PartKindBadge } from './PartKindBadge'
import styles from './Parts.module.css'
import { partName, SHARED_LABEL_TEXT } from './partText'

/** Viewer-side states of a row (F8 `selectPartViewerStatus`, UI_DESIGN §6.4/§6.6): the toggles are disabled. */
const NOT_FOUND_TEXT = 'Not found in the 3D file'
const REGION_UNAVAILABLE_TEXT = 'Highlight unavailable for this region'

export interface PartRowProps {
  part: PartView
  /** Analysis the row belongs to (matches the viewer's indexed parts model). */
  analysisId: string
  state: PartDisplayState
  /** 1..12 (`--part-<n>`), 0 = none. */
  colorIndex: number
  colorize: boolean
  hidden: boolean
  isolated: boolean
  hovered: boolean
  /** Roving tab stop of the list. */
  tabStop: boolean
  /** Hierarchy view indentation. */
  depth?: number
  onFocusRow: (id: string) => void
}

const stop = (event: MouseEvent | KeyboardEvent) => event.stopPropagation()

/**
 * One part (UI_DESIGN §6.4): `role="option"` + `aria-selected`; colour swatch (Colorize on), label + side, kind badge
 * (text), faces and confidence, visibility and isolate toggles (`aria-pressed`), overflow menu. Rows whose surface
 * label is shared (§6.1 rule 7) keep their data but cannot be hidden, isolated or highlighted; the same holds for a part
 * the viewer could not find in the 3D file and for a region whose mesh has no region attribute. "Zoom to part" fits
 * the camera to it (viewerRuntime.focusPart).
 */
export function PartRow({ part, analysisId, state, colorIndex, colorize, hidden, isolated, hovered, tabStop, depth = 0, onFocusRow }: PartRowProps) {
  const select = usePartStore((s) => s.select)
  const hover = usePartStore((s) => s.hover)
  const toggleHidden = usePartStore((s) => s.toggleHidden)
  const isolate = usePartStore((s) => s.isolate)
  const setShow = usePartStore((s) => s.setShow)
  const viewerStatus = useViewerRuntime((s) => selectPartViewerStatus(s, analysisId, part.id))
  const name = partName(part)
  const shared = part.highlight === 'shared_label'
  const notFound = viewerStatus === 'not_found'
  const regionUnavailable = viewerStatus === 'highlight_unavailable'
  // Why the hide/isolate toggles are off for this row (null = available).
  const blockedText = shared ? SHARED_LABEL_TEXT : notFound ? NOT_FOUND_TEXT : regionUnavailable ? REGION_UNAVAILABLE_TEXT : null
  const blocked = blockedText !== null
  // Actions that change what the 3D view shows also turn the Parts view on, so the result is visible (F8: the
  // parts model is drawn only while it is on).
  const selectPart = () => {
    setShow(true)
    select(part.id)
  }
  const selected = state === 'selected'

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      selectPart()
    }
  }

  const items: MenuItem[] = [
    {
      id: 'zoom',
      label: 'Zoom to part',
      icon: <ZoomIn size={14} />,
      disabled: notFound,
      hint: notFound ? 'not in the 3D file' : undefined,
      onSelect: () => {
        setShow(true)
        useViewerRuntime.getState().focusPart(part.id)
      },
    },
  ]
  if (part.downloadUrl) {
    const url = part.downloadUrl
    items.push({
      id: 'glb',
      label: 'Download part GLB',
      icon: <Download size={14} />,
      onSelect: () => void downloadArtifact({ kind: 'part_glb', url, fallbackName: `${part.id}.glb`, sizeHint: null }),
    })
  }
  if (part.maskUrl) {
    const url = part.maskUrl
    items.push({
      id: 'mask',
      label: 'Download mask',
      icon: <ImageIcon size={14} />,
      onSelect: () => void downloadArtifact({ kind: 'mask_png', url, fallbackName: `${part.id}_mask.png`, sizeHint: null }),
    })
  }
  items.push({
    id: 'copy',
    label: 'Copy part id',
    icon: <Copy size={14} />,
    onSelect: () =>
      void navigator.clipboard?.writeText(part.id).then(
        () => toast.info('Part id copied', part.id),
        () => toast.error('Could not copy', 'The clipboard is not available.'),
      ),
  })

  const swatch: CSSProperties | undefined =
    colorize && colorIndex > 0 ? { background: `var(--part-${colorIndex})`, borderColor: 'transparent' } : undefined

  return (
    <div
      role="option"
      aria-selected={selected}
      tabIndex={tabStop ? 0 : -1}
      className={`${styles.option} ${hovered ? styles.optionHovered : ''} ${state === 'dimmed' ? styles.dimmed : ''} ${hidden ? styles.hiddenRow : ''}`}
      style={depth > 0 ? { paddingInlineStart: `calc(var(--space-2) + ${depth * 16}px)` } : undefined}
      data-part-id={part.id}
      title={blockedText ?? undefined}
      onClick={selectPart}
      onKeyDown={onKeyDown}
      onFocus={(event) => {
        if (event.target === event.currentTarget) onFocusRow(part.id)
      }}
      onPointerEnter={() => {
        if (!blocked) hover(part.id)
      }}
      onPointerLeave={() => hover(null)}
    >
      <span className={styles.swatch} style={swatch} aria-hidden />
      <span className={styles.main}>
        <span className={styles.label}>
          <span>{name}</span>
          <PartKindBadge kind={part.kind} />
        </span>
        <span className={styles.sub}>
          {part.faces.toLocaleString()} faces
          {part.confidence !== null ? ` · ${Math.round(part.confidence * 100)} %` : ''}
          {shared ? ' · highlight unavailable' : notFound ? ` · ${NOT_FOUND_TEXT}` : regionUnavailable ? ` · ${REGION_UNAVAILABLE_TEXT}` : ''}
        </span>
      </span>
      <span className={styles.rowActions} onClick={stop} onKeyDown={stop} role="presentation">
        <button
          type="button"
          className={styles.iconToggle}
          aria-pressed={hidden}
          aria-label={hidden ? `Show ${name}` : `Hide ${name}`}
          title={blockedText ?? (hidden ? `Show ${name}` : `Hide ${name}`)}
          disabled={blocked}
          tabIndex={tabStop ? 0 : -1}
          onClick={() => {
            setShow(true)
            toggleHidden(part.id)
            announce(`${name} ${hidden ? 'shown' : 'hidden'}`)
          }}
        >
          {hidden ? <EyeOff size={14} aria-hidden /> : <Eye size={14} aria-hidden />}
        </button>
        <button
          type="button"
          className={styles.iconToggle}
          aria-pressed={isolated}
          aria-label={`Isolate ${name}`}
          title={blockedText ?? `Isolate ${name}`}
          disabled={blocked}
          tabIndex={tabStop ? 0 : -1}
          onClick={() => {
            setShow(true)
            isolate(isolated ? null : part.id)
          }}
        >
          <Focus size={14} aria-hidden />
        </button>
        <Menu label={`More actions for ${name}`} icon={<MoreHorizontal size={14} />} sections={[{ items }]} triggerSize="sm" />
      </span>
    </div>
  )
}
