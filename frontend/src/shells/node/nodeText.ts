/** Words of the Node shell (constants live in `.ts` files, UI_DESIGN §0.3). */
export const NODE_TEXT = {
  shellLabel: 'Node workflow editor',
  fixProblems: 'Fix the problems in the workflow first.',
  needsBackend: 'Needs a newer Img3D backend',
  validateFailedTitle: 'Could not validate the workflow',
  validateFailedText: 'Check the connection and try again.',
  noSelection: 'Select a node to edit it.',
  lockedNode: 'Paused until you approve refinement.',
  importFailed: 'Could not import the workflow',
  imported: 'Workflow imported',
  graphView: 'Graph',
  viewerView: 'Viewer',
  canvas: 'Canvas',
  list: 'List',
} as const

export const RUN_STATE_TEXT = {
  idle: 'Idle',
  queued: 'Queued',
  running: 'Running',
  done: 'Done',
  warning: 'Done with warnings',
  failed: 'Failed',
  skipped: 'Skipped',
  cancelled: 'Cancelled',
  bypassed: 'Bypassed',
  locked: 'Locked',
  unavailable: 'Unavailable',
} as const

/** Status chip glyphs (paired with the text above, never colour alone). */
export const RUN_STATE_GLYPH = {
  idle: '○',
  queued: '◷',
  running: '▶',
  done: '✓',
  warning: '!',
  failed: '✕',
  skipped: '–',
  cancelled: '⊘',
  bypassed: '↷',
  locked: '⏸',
  unavailable: '⊘',
} as const

/** "6 nodes, 2 bypassed, 0 problems, Image-to-3D running 45 %" (canvas `aria-describedby`, list header). */
export function graphSummaryText(
  nodeCount: number,
  bypassed: number,
  problems: number,
  running: { title: string; percent: number | null } | null,
): string {
  const parts = [`${nodeCount} node${nodeCount === 1 ? '' : 's'}`, `${bypassed} bypassed`, `${problems} problem${problems === 1 ? '' : 's'}`]
  if (running) parts.push(`${running.title} running${running.percent === null ? '' : ` ${Math.round(running.percent)} %`}`)
  return parts.join(', ')
}

export function statusText(state: keyof typeof RUN_STATE_TEXT, percent: number | null): string {
  return state === 'running' && percent !== null ? `${RUN_STATE_TEXT.running} ${Math.round(percent)}%` : RUN_STATE_TEXT[state]
}
