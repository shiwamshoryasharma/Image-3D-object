import { GenerateAction } from './GenerateAction'

export interface GenerateCardProps {
  variant?: 'card' | 'bar'
}

/**
 * Generate / Cancel action with disabled reasons and the engine-loading note (Default card and mobile bar). A thin
 * wrapper over the shared `GenerateAction` (UI_DESIGN §4.1) with the same DOM and CSS as before; Ctrl/Cmd+Enter is
 * registered once in `app/GlobalShortcuts.tsx`, and "switch to the viewer after submit" is a reveal request mapped by
 * the Default shell.
 */
export function GenerateCard({ variant = 'card' }: GenerateCardProps) {
  return <GenerateAction variant={variant} />
}
