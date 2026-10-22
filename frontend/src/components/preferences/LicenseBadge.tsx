import type { LicenseOut } from '../../api/partAwareTypes.ts'
import { LICENSE_BADGE_TEXT } from '../../lib/featureMeta.ts'
import { Tag } from '../common/Tag'
import { LICENSE_TONES } from './preferencesText'

/** Commercial-use class of a model licence ("Permissive", "Non-commercial", …); text, never colour alone. */
export function LicenseBadge({ license }: { license: LicenseOut }) {
  return (
    <Tag tone={LICENSE_TONES[license.commercial]} title={license.name}>
      {LICENSE_BADGE_TEXT[license.commercial]}
    </Tag>
  )
}
