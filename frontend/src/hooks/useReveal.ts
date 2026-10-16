/**
 * Subscribe a shell to `navigationStore.requestReveal(target)` (UI_DESIGN §3.4). The handler runs from the store
 * subscription (an effect), never during render, and always sees the latest props through `useEffectEvent`.
 */
import { useEffect, useEffectEvent } from 'react'

import { useNavigationStore, type RevealTarget } from '../store/navigationStore'

export function useReveal(handler: (target: RevealTarget) => void): void {
  const onReveal = useEffectEvent((target: RevealTarget) => handler(target))
  useEffect(
    () =>
      useNavigationStore.subscribe((state, previous) => {
        if (state.reveal && state.reveal.seq !== previous.reveal?.seq) onReveal(state.reveal.target)
      }),
    [],
  )
}
