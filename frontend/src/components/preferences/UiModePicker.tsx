import { useId, useState } from 'react'

import { useResolvedUiMode } from '../../hooks/useResolvedUiMode'
import { useViewport } from '../../hooks/useViewport'
import { suggestUiMode, UI_MODE_META, UI_MODES, type UiMode } from '../../lib/uiModes.ts'
import { useNavigationStore } from '../../store/navigationStore'
import { usePreferencesStore } from '../../store/preferencesStore'
import { RadioCardGroup, type RadioCardOption } from '../common/RadioCardGroup'
import { Tag } from '../common/Tag'
import type { ModeActions } from './preferencesText'
import styles from './preferences.module.css'

/**
 * "UI style" (UI_DESIGN §3.8): four radio cards. Selecting first awaits `prepareMode(mode)` (spinner + `aria-busy`
 * on the card), then saves the mode, so the new shell never suspends after the switch; a failed load leaves the
 * mode unchanged. Modes not in this build are disabled. A `?ui=` / session override is explained and cleared by an
 * explicit choice.
 */
export function UiModePicker({ prepareMode, prefetchMode }: ModeActions) {
  const labelId = useId()
  const preference = usePreferencesStore((s) => s.uiMode)
  const setUiMode = usePreferencesStore((s) => s.setUiMode)
  const override = useNavigationStore((s) => s.sessionUiOverride)
  const setOverride = useNavigationStore((s) => s.setSessionUiOverride)
  const ready = useNavigationStore((s) => s.readyModes)
  const current = useResolvedUiMode()
  const suggested = suggestUiMode(useViewport())
  const [pending, setPending] = useState<UiMode | null>(null)
  const [failed, setFailed] = useState<UiMode | null>(null)

  const select = (mode: UiMode) => {
    if (pending || (mode === preference && override === null)) return
    setPending(mode)
    setFailed(null)
    prepareMode(mode).then(
      () => {
        setPending(null)
        setOverride(null)
        setUiMode(mode)
      },
      () => {
        setPending(null)
        setFailed(mode)
      },
    )
  }

  const options: RadioCardOption<UiMode>[] = UI_MODES.map((mode) => {
    const included = ready.has(mode)
    const meta = UI_MODE_META[mode]
    const hasChips = mode === current || mode === suggested || !included || failed === mode
    return {
      value: mode,
      label: meta.label,
      description: meta.description,
      media: <span className={`${styles.schematic} ${styles[`schematic_${mode}`]}`} aria-hidden />,
      meta: hasChips && (
        <span className={styles.chips}>
          {mode === current && <Tag tone="accent">Current</Tag>}
          {mode === suggested && <Tag tone="info">Suggested for this screen</Tag>}
          {!included && <Tag>Not included in this build</Tag>}
          {failed === mode && (
            <span className={styles.cardError} role="alert">
              Couldn&apos;t load this UI style. Try again.
            </span>
          )}
        </span>
      ),
      disabled: !included,
      busy: pending === mode,
    }
  })

  return (
    <div className={styles.field}>
      <h4 id={labelId} className={styles.fieldLabel}>
        UI style
      </h4>
      <RadioCardGroup
        value={pending ?? current}
        options={options}
        onChange={select}
        labelledBy={labelId}
        onPrefetch={prefetchMode}
        className={styles.modeCards}
      />
      {override !== null && (
        <p className={styles.hint}>
          This tab uses the {UI_MODE_META[override].name} for this session. Your saved choice (
          {UI_MODE_META[preference].label}) applies next time; picking a style here applies it now.
        </p>
      )}
    </div>
  )
}
