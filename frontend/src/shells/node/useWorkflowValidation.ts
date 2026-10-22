/**
 * Displayed issues = client issues ∪ the server report for this revision, deduped (server first, authoritative), plus
 * the warnings of the last load/import (UI_DESIGN §4.3.5). 600 ms after a revision change, when the doc compiles
 * locally and `features.workflow.available`, the server validates it (`workflowStore.validateRemote`); the previous
 * call is aborted and a report for an older revision is discarded by the store.
 */
import { useEffect } from 'react'

import { useWorkflowStore } from '../../store/workflowStore'
import { compileDoc, type GraphIssue, type NodeRegistry, type WorkflowDoc } from '../../workflow/engine/index.ts'
import { boundFromStores, contextFromStores, mergedIssues, type NodeValidationContext } from './bindings'

const REMOTE_DELAY_MS = 600

export interface WorkflowValidationView {
  readonly issues: readonly GraphIssue[]
  readonly errors: number
  readonly warnings: number
}

export function useWorkflowValidation(doc: WorkflowDoc, registry: NodeRegistry, ctx: NodeValidationContext): WorkflowValidationView {
  const serverReport = useWorkflowStore((s) => s.serverReport)
  const loadIssues = useWorkflowStore((s) => s.loadIssues)
  const available = ctx.features.status === 'ready' && ctx.features.workflow.available
  const revision = doc.revision

  useEffect(() => {
    if (!available) return undefined
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      const store = useWorkflowStore.getState()
      if (store.doc.revision !== revision) return
      const out = compileDoc(store.doc, registry, boundFromStores(), contextFromStores())
      if (out.ok) void store.validateRemote(revision, out.request, controller.signal)
    }, REMOTE_DELAY_MS)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [revision, available, registry])

  const issues = [...mergedIssues(doc, registry, ctx, serverReport), ...loadIssues]
  let errors = 0
  for (const issue of issues) if (issue.severity === 'error') errors += 1
  return { issues, errors, warnings: issues.length - errors }
}
