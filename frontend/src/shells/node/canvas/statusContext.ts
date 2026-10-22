/**
 * Run status for the custom nodes (UI_DESIGN §4.3.12): never through `nodes`, so status chips and the inline
 * progress bar update on every `workflow.updated` / `job.updated` without touching the doc revision.
 */
import { createContext } from 'react'

import type { NodeStatusView } from '../../../workflow/engine/types.ts'

export const NodeStatusContext = createContext<Readonly<Record<string, NodeStatusView>>>({})

/** Error/warning count per node id (issue badge on the node header). */
export const NodeIssueContext = createContext<Readonly<Record<string, number>>>({})
