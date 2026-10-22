import { X } from 'lucide-react'
import { useId, useState, type KeyboardEvent } from 'react'

import type { SegProfile, SegSubject } from '../../api/partAwareTypes.ts'
import { useFeatures } from '../../hooks/useFeatures'
import { licenceNotices } from '../../lib/featureFlags.ts'
import { SEG_PROFILE_LABELS, SEG_SUBJECT_LABELS } from '../../lib/featureMeta.ts'
import { EXTRA_TERM_MAX_CHARS, normalizeExtraTerms } from '../../lib/forms.ts'
import { reasonClause } from '../../lib/generatePlan.ts'
import { usePartOptionsStore } from '../../store/partOptionsStore'
import { Field } from './Field'
import { LicenseNotice } from './LicenseNotice'
import styles from './PartOptionsSection.module.css'

export interface SegmentationOptionsContentProps {
  active?: boolean
  headingLevel?: 2 | 3
  compact?: boolean
}

/**
 * Subject / Detector profile / Also look for + licence notices (UI_DESIGN §6.2). Used inside `PartOptionsSection`
 * (`mode="all"`) and alone by the Node Segmentation inspector. Every option and reason comes from `/features`.
 */
export function SegmentationOptionsContent(_props: SegmentationOptionsContentProps) {
  const features = useFeatures()
  const form = usePartOptionsStore((s) => s.form)
  const update = usePartOptionsStore((s) => s.update)
  const [draft, setDraft] = useState('')
  const subjectId = useId()
  const profileId = useId()
  const termsId = useId()
  const termsHintId = useId()

  const seg = features.segmentation
  const maxTerms = seg.maxExtraTerms
  const profileInfo = (id: SegProfile) => seg.profiles.find((p) => p.id === id) ?? null
  const selectedProfile = form.profile === 'auto' ? null : profileInfo(form.profile)
  const realistic = profileInfo('realistic')
  const notices = realistic?.available ? licenceNotices(features) : []
  const emphasised = form.subject === 'realistic_human' || form.profile === 'realistic'
  const atLimit = form.extraTerms.length >= maxTerms

  const addTerm = () => {
    const next = normalizeExtraTerms([...form.extraTerms, draft], maxTerms)
    if (next.length !== form.extraTerms.length) update({ extraTerms: next })
    setDraft('')
  }

  const onTermKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      if (draft.trim()) addTerm()
    } else if (event.key === 'Backspace' && draft === '' && form.extraTerms.length > 0) {
      event.preventDefault()
      update({ extraTerms: form.extraTerms.slice(0, -1) })
    }
  }

  return (
    <div className={styles.disclosure}>
      {!seg.available && <p className={styles.reason}>Segmentation is unavailable: {reasonClause(seg)}.</p>}
      <div className={styles.pair}>
        <Field label="Subject" htmlFor={subjectId}>
          <select
            id={subjectId}
            className={styles.select}
            value={form.subject}
            disabled={!seg.available}
            onChange={(event) => update({ subject: event.target.value as SegSubject })}
          >
            {seg.subjects.map((subject) => (
              <option key={subject} value={subject}>
                {SEG_SUBJECT_LABELS[subject]}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Detector profile"
          htmlFor={profileId}
          hint={selectedProfile && !selectedProfile.available ? reasonClause(selectedProfile) : undefined}
        >
          <select
            id={profileId}
            className={styles.select}
            value={form.profile}
            disabled={!seg.available}
            onChange={(event) => update({ profile: event.target.value as SegProfile })}
          >
            <option value="auto">{SEG_PROFILE_LABELS.auto}</option>
            {seg.profiles.map((profile) => (
              <option key={profile.id} value={profile.id} disabled={!profile.available && form.profile !== profile.id}>
                {SEG_PROFILE_LABELS[profile.id]}
                {profile.available ? '' : ` (unavailable: ${reasonClause(profile)})`}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field
        label="Also look for"
        htmlFor={termsId}
        aside={`${form.extraTerms.length}/${maxTerms}`}
        hint={
          <span id={termsHintId}>
            Press Enter to add a term (up to {EXTRA_TERM_MAX_CHARS} characters), Backspace to remove the last one.
          </span>
        }
      >
        <div className={styles.chips}>
          {form.extraTerms.map((term) => (
            <span key={term} className={styles.chip}>
              {term}
              <button
                type="button"
                className={styles.chipRemove}
                aria-label={`Remove ${term}`}
                disabled={!seg.available}
                onClick={() => update({ extraTerms: form.extraTerms.filter((t) => t !== term) })}
              >
                <X size={12} aria-hidden />
              </button>
            </span>
          ))}
          <input
            id={termsId}
            className={styles.chipInput}
            value={draft}
            maxLength={EXTRA_TERM_MAX_CHARS}
            disabled={!seg.available || atLimit}
            placeholder={atLimit ? 'Limit reached' : 'e.g. held object'}
            aria-describedby={termsHintId}
            enterKeyHint="enter"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onTermKey}
            onBlur={() => {
              if (draft.trim()) addTerm()
            }}
          />
        </div>
      </Field>

      {notices.map((notice) => (
        <LicenseNotice key={notice.modelId} notice={notice} emphasised={emphasised} />
      ))}
    </div>
  )
}
