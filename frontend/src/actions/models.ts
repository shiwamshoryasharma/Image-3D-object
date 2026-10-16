/**
 * Model warm-up / free memory (DESIGN §6.1; UI_DESIGN §3.8, §5.4). Models are read-only otherwise: no install UI.
 * Layer (§1.4): stores, api, lib; no React.
 */
import { errorText } from '../lib/errorText.ts'
import { announce } from '../store/announcerStore'
import { useFeaturesStore } from '../store/featuresStore'
import { toast } from '../store/toastStore'

/** "Warm up now": `POST /models/{id}/load` (202, state `loading`), then a features refresh. */
export async function warmUpModel(modelId: string): Promise<boolean> {
  const error = await useFeaturesStore.getState().loadModel(modelId)
  if (error) {
    toast.error('Could not warm up the model', errorText(error))
    return false
  }
  announce('Loading the model')
  return true
}

/** "Free memory": `POST /models/{id}/unload`, then a features refresh. */
export async function freeModelMemory(modelId: string): Promise<boolean> {
  const error = await useFeaturesStore.getState().unloadModel(modelId)
  if (error) {
    toast.error('Could not free the model memory', errorText(error))
    return false
  }
  announce('Model memory freed')
  return true
}
