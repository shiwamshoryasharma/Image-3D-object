/**
 * Shared input form for every UI mode (UI_DESIGN §3.3; persisted as `img3d.input` v1): the source switch
 * (image / prompt) and the prompt form. The persist `merge` runs the pure sanitizers of `lib/forms.ts`.
 * `promptError` (not persisted) is the inline prompt-field error of a rejected create call (§2.11).
 */
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import type { ImageGenerationRequest } from '../api/partAwareTypes.ts'
import {
  DEFAULT_PROMPT_FORM,
  promptFormFromRequest,
  sanitizeInputState,
  type InputSource,
  type PromptForm,
} from '../lib/forms.ts'
import { STORAGE_KEYS } from '../lib/storage'

export type { InputSource, PromptFlow, PromptForm } from '../lib/forms.ts'

interface InputState {
  source: InputSource
  prompt: PromptForm
  /** Inline error under the prompt field (422 on `prompt`); cleared when the prompt text changes or a run starts. */
  promptError: string | null
  setSource: (source: InputSource) => void
  updatePrompt: (patch: Partial<PromptForm>) => void
  resetPrompt: () => void
  /** "Reuse prompt" / "Regenerate from prompt": unknown or invalid values fall back to the defaults. */
  loadPrompt: (request: ImageGenerationRequest) => void
  setPromptError: (message: string | null) => void
}

const defaultPrompt = (): PromptForm => ({ ...DEFAULT_PROMPT_FORM })

export const useInputStore = create<InputState>()(
  persist(
    (set) => ({
      source: 'image',
      prompt: defaultPrompt(),
      promptError: null,
      setSource: (source) => set((state) => (state.source === source ? state : { source })),
      updatePrompt: (patch) =>
        set((state) => {
          const changed = (Object.keys(patch) as (keyof PromptForm)[]).some((key) => state.prompt[key] !== patch[key])
          if (!changed) return state
          const promptChanged = patch.prompt !== undefined && patch.prompt !== state.prompt.prompt
          return { prompt: { ...state.prompt, ...patch }, promptError: promptChanged ? null : state.promptError }
        }),
      resetPrompt: () => set({ prompt: defaultPrompt(), promptError: null }),
      loadPrompt: (request) =>
        set((state) => ({ prompt: promptFormFromRequest(request, state.prompt.flow), promptError: null })),
      setPromptError: (promptError) => set((state) => (state.promptError === promptError ? state : { promptError })),
    }),
    {
      name: STORAGE_KEYS.input,
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ source: state.source, prompt: state.prompt }),
      merge: (persisted, current) => ({ ...current, ...sanitizeInputState(persisted) }),
    },
  ),
)
