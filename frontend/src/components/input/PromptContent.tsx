import { Info } from 'lucide-react'
import { useId, type ClipboardEvent } from 'react'

import type { PromptPreset, PromptStyle } from '../../api/partAwareTypes.ts'
import { useFeatures, useFeaturesStatus } from '../../hooks/useFeatures'
import { FALLBACK_LIMITS, selectableT2IModels } from '../../lib/featureFlags.ts'
import { LICENSE_BADGE_TEXT, PROMPT_PRESET_LABELS, PROMPT_STYLE_LABELS } from '../../lib/featureMeta.ts'
import type { PromptFlow } from '../../lib/forms.ts'
import { PLAN_TEXT, reasonClause } from '../../lib/generatePlan.ts'
import { useInputStore } from '../../store/inputStore'
import { selectReviewGeneration, useRunStore } from '../../store/runStore'
import { SegmentedControl } from '../common/SegmentedControl'
import { Switch } from '../common/Switch'
import { Field } from '../settings/Field'
import { SeedControl } from '../settings/SeedControl'
import { ColdStartNotice } from './ColdStartNotice'
import styles from './Input.module.css'

export interface PromptContentProps {
  active?: boolean
  headingLevel?: 2 | 3
  compact?: boolean
}

type StyleChoice = PromptStyle | 'none'

/** Control characters the server rejects (DESIGN §4.4.2); `\n` and `\t` are kept, `\r\n` becomes `\n`. */
function stripControl(text: string): string {
  let out = ''
  for (const ch of text.replace(/\r\n?/g, '\n')) {
    const code = ch.charCodeAt(0)
    if (ch === '\n' || ch === '\t' || (code >= 0x20 && code !== 0x7f)) out += ch
  }
  return out
}

/**
 * Prompt form (UI_DESIGN §5.2) bound to `inputStore.prompt`, driven by `/features.pipeline.prompt_to_3d`: presets,
 * styles, sizes, models and limits all come from the backend. The flow decides the plan (§2.9): review first
 * (default, never disabled) or straight to 3D (needs workflows).
 */
export function PromptContent(_props: PromptContentProps) {
  const features = useFeatures()
  const status = useFeaturesStatus()
  const form = useInputStore((s) => s.prompt)
  const promptError = useInputStore((s) => s.promptError)
  const updatePrompt = useInputStore((s) => s.updatePrompt)
  const lastSeed = useRunStore((s) => selectReviewGeneration(s)?.options.seed ?? null)
  const promptId = useId()
  const counterId = useId()
  const errorId = useId()
  const flowNoteId = useId()
  const sizeId = useId()
  const modelId = useId()
  const negativeId = useId()

  const { prompt, workflow } = features
  const maxChars = prompt.maxChars || FALLBACK_LIMITS.promptMaxChars
  const length = form.prompt.length
  const nearLimit = length >= maxChars * 0.9
  const tooLong = form.prompt.trim().length > maxChars
  const error = promptError ?? (tooLong ? PLAN_TEXT.promptTooLong(maxChars) : null)
  const models = selectableT2IModels(features)
  const sizeValue = form.size ? `${form.size[0]}x${form.size[1]}` : ''
  const oneShotBlocked = !workflow.available
  const flowNote = oneShotBlocked ? PLAN_TEXT.oneShotNeedsWorkflows(workflow) : null

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const text = event.clipboardData.getData('text')
    const clean = stripControl(text)
    if (clean === text) return
    event.preventDefault()
    const el = event.currentTarget
    const next = `${el.value.slice(0, el.selectionStart)}${clean}${el.value.slice(el.selectionEnd)}`
    updatePrompt({ prompt: next.slice(0, maxChars) })
  }

  return (
    <div className={styles.root}>
      <ColdStartNotice />
      {status === 'loading' && <p className={styles.note}>Checking prompt-to-3D availability…</p>}
      {status === 'ready' && !prompt.available && (
        <p className={styles.warningNote}>
          <Info size={14} aria-hidden />
          <span>Prompt-to-3D is unavailable: {reasonClause(prompt)}.</span>
        </p>
      )}

      <div className={styles.stack}>
        <Field label="Describe the character or object" htmlFor={promptId}>
          <textarea
            id={promptId}
            className={styles.textarea}
            value={form.prompt}
            maxLength={maxChars}
            enterKeyHint="go"
            rows={4}
            aria-invalid={error ? true : undefined}
            aria-describedby={`${counterId}${error ? ` ${errorId}` : ''}`}
            onPaste={onPaste}
            onChange={(event) => updatePrompt({ prompt: event.target.value })}
          />
          <div className={styles.counterRow}>
            {error ? (
              <p id={errorId} className={styles.error}>
                {error}
              </p>
            ) : (
              <span />
            )}
            <span
              id={counterId}
              className={`${styles.counter} ${nearLimit ? styles.counterNear : ''}`}
              aria-live={nearLimit ? 'polite' : 'off'}
            >
              {length} / {maxChars}
            </span>
          </div>
        </Field>
      </div>

      {prompt.presets.length > 0 && (
        <Field
          label="Preset"
          hint={form.preset === 'raw' ? 'Your text is sent unchanged (no 3D-friendly template).' : undefined}
        >
          <SegmentedControl<PromptPreset>
            label="Preset"
            size="sm"
            value={form.preset}
            options={prompt.presets.map((preset) => ({ value: preset, label: PROMPT_PRESET_LABELS[preset] }))}
            onChange={(preset) => updatePrompt({ preset })}
          />
        </Field>
      )}

      {prompt.styles.length > 0 && (
        <Field label="Style">
          <SegmentedControl<StyleChoice>
            label="Style"
            size="sm"
            value={form.style ?? 'none'}
            options={[
              { value: 'none', label: 'None' },
              ...prompt.styles.map((style) => ({ value: style, label: PROMPT_STYLE_LABELS[style] })),
            ]}
            onChange={(style) => updatePrompt({ style: style === 'none' ? null : style })}
          />
        </Field>
      )}

      <Switch
        label="Part-friendly prompt"
        description="Ask for clearly separated hair, clothes and accessories"
        checked={form.partHint}
        onChange={(partHint) => updatePrompt({ partHint })}
      />

      <Field label="Flow" hint={flowNote ? <span id={flowNoteId}>{flowNote}</span> : undefined}>
        <SegmentedControl<PromptFlow>
          label="Flow"
          size="sm"
          value={form.flow}
          options={[
            { value: 'review', label: 'Review image first' },
            {
              value: 'one_shot',
              label: 'Straight to 3D',
              disabled: oneShotBlocked && form.flow !== 'one_shot',
              title: flowNote ?? 'Generate the image and the 3D model in one run',
            },
          ]}
          onChange={(flow) => updatePrompt({ flow })}
        />
      </Field>

      {form.flow === 'review' && prompt.maxImages > 1 && (
        <Field label="Candidates" hint="Images generated for you to review before building the 3D model.">
          <SegmentedControl<number>
            label="Candidates"
            size="sm"
            value={Math.min(form.numCandidates, prompt.maxImages)}
            options={Array.from({ length: Math.min(4, prompt.maxImages) }, (_, index) => ({
              value: index + 1,
              label: String(index + 1),
            }))}
            onChange={(count) => updatePrompt({ numCandidates: count as 1 | 2 | 3 | 4 })}
          />
        </Field>
      )}

      <details className={styles.disclosure}>
        <summary>Advanced</summary>
        <SeedControl
          seed={form.seed}
          randomize={form.randomizeSeed}
          lastSeed={lastSeed}
          onSeedChange={(seed) => updatePrompt({ seed })}
          onRandomizeChange={(randomizeSeed) => updatePrompt({ randomizeSeed })}
        />
        {prompt.sizes.length > 0 && (
          <Field label="Size" htmlFor={sizeId}>
            <select
              id={sizeId}
              className={styles.select}
              value={sizeValue}
              onChange={(event) => {
                const match = prompt.sizes.find(([w, h]) => `${w}x${h}` === event.target.value)
                updatePrompt({ size: match ? [match[0], match[1]] : null })
              }}
            >
              <option value="">Preset default</option>
              {prompt.sizes.map(([w, h]) => (
                <option key={`${w}x${h}`} value={`${w}x${h}`}>
                  {w} × {h}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Model" htmlFor={modelId}>
          <select
            id={modelId}
            className={styles.select}
            value={form.modelId ?? ''}
            onChange={(event) => updatePrompt({ modelId: event.target.value || null })}
          >
            <option value="">Backend default</option>
            {models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.display_name} · {LICENSE_BADGE_TEXT[model.license.commercial]}
              </option>
            ))}
          </select>
        </Field>
        {prompt.supportsNegativePrompt && (
          <Field label="Negative prompt" htmlFor={negativeId}>
            <textarea
              id={negativeId}
              className={styles.textarea}
              value={form.negativePrompt}
              maxLength={prompt.negativeMaxChars || FALLBACK_LIMITS.negativeMaxChars}
              rows={2}
              onChange={(event) => updatePrompt({ negativePrompt: event.target.value })}
            />
          </Field>
        )}
      </details>
    </div>
  )
}
