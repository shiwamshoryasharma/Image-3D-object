/**
 * One slot row (UI_DESIGN §4.3.3), ComfyUI style: the React Flow handle is a dot on the node edge — filled when a
 * link is attached, a ring when free — next to the type glyph and the visible label (WCAG 1.4.1: never colour
 * alone). Under a coarse pointer the hit area grows to 44 px (`flow.module.css`).
 */
import { Handle, Position } from '@xyflow/react'
import type { CSSProperties } from 'react'

import { PORT_META, type PortType } from '../../../workflow/engine/types.ts'
import styles from './flow.module.css'

export interface PortHandleProps {
  side: 'input' | 'output'
  portId: string
  label: string
  /** The output type, or the first accepted type of an input. */
  portType: PortType
  required?: boolean
  connected: boolean
}

export function PortHandle({ side, portId, label, portType, required = false, connected }: PortHandleProps) {
  const meta = PORT_META[portType]
  const input = side === 'input'
  const color = { '--port-color': `var(${meta.token})` } as CSSProperties
  return (
    <div className={`${styles.port} ${input ? styles.portIn : styles.portOut}`} data-port-type={portType} style={color}>
      <Handle
        id={portId}
        type={input ? 'target' : 'source'}
        position={input ? Position.Left : Position.Right}
        className={styles.handle}
        data-connected={connected ? 'true' : undefined}
        aria-label={`${input ? 'Input' : 'Output'} ${label} (${meta.label})${connected ? ', connected' : ''}`}
      />
      <span className={styles.portGlyph} aria-hidden="true">
        {meta.glyph}
      </span>
      <span className={styles.portLabel}>
        {label}
        {input && required && !connected ? (
          <>
            <span className={styles.required} aria-hidden="true">
              {' *'}
            </span>
            <span className="visually-hidden"> (required)</span>
          </>
        ) : null}
      </span>
    </div>
  )
}
