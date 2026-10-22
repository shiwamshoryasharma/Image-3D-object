import { Download } from 'lucide-react'

import { Panel } from '../common/Panel'
import { ExportContent } from './ExportContent'

/** Downloads as a card (`Panel id="export"` + `ExportContent`), for layouts that show them on their own (§6.7). */
export function ExportPanel() {
  return (
    <div data-ui-addition="export">
      <Panel id="export" title="Export" icon={<Download size={16} />}>
        <ExportContent headingLevel={3} />
      </Panel>
    </div>
  )
}
