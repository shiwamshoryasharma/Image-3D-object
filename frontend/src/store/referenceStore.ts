/**
 * The current reference set (persisted by id in localStorage) plus in-flight uploads and per-tile errors.
 */
import { create } from 'zustand'

import { ApiError, toApiError } from '../api/client'
import {
  createReferenceSet,
  deleteReferenceImage,
  deleteReferenceSet,
  getReferenceSet,
  setPrimaryImage,
  updateReferenceImage,
  uploadReferenceImage,
} from '../api/references'
import { SINGLE_SLOTS, type ReferenceImage, type ReferenceSet, type ReferenceSlot } from '../api/types'
import { FALLBACK_LIMITS, validateImageFile } from '../lib/imageValidation'
import { SLOT_META, isSingleSlot } from '../lib/slots'
import { STORAGE_KEYS, readString, writeString } from '../lib/storage'
import { useSystemStore } from './systemStore'
import { toast } from './toastStore'

export interface UploadEntry {
  key: string
  slot: ReferenceSlot
  fileName: string
  /** Object URL of the local file for an instant preview. */
  previewUrl: string
  /** 0..100; `null` while waiting in the sequential upload queue. */
  progress: number | null
  /** Existing image this upload replaces (detail replace). */
  replacesImageId: string | null
}

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

interface ReferenceState {
  set: ReferenceSet | null
  status: LoadStatus
  error: ApiError | null
  uploads: UploadEntry[]
  /** Error messages keyed by slot (single slots) or `detail`. */
  tileErrors: Partial<Record<ReferenceSlot, string>>
  busyImageIds: string[]
  creatingSet: boolean

  init: () => Promise<void>
  /** Re-fetch the current set (after reconnecting); recreates it when it no longer exists. */
  refresh: () => Promise<void>
  newSet: () => Promise<void>
  uploadFiles: (files: File[], targetSlot?: ReferenceSlot) => Promise<void>
  replaceImage: (image: ReferenceImage, file: File) => Promise<void>
  removeImage: (imageId: string) => Promise<void>
  setPrimary: (imageId: string) => Promise<void>
  moveImage: (imageId: string, slot: ReferenceSlot) => Promise<void>
  clearTileError: (slot: ReferenceSlot) => void
  /** Make a set returned by the server current (e.g. after `…/candidates/{n}/use`), persisting its id. */
  adoptSet: (next: ReferenceSet) => void
}

let uploadCounter = 0
/** Serializes uploads so the server assigns primary/order deterministically. */
let uploadChain: Promise<void> = Promise.resolve()

function limits() {
  return useSystemStore.getState().system?.limits ?? FALLBACK_LIMITS
}

function detailLimit(set: ReferenceSet | null): number {
  return set?.slot_limits.detail ?? limits().max_detail_images
}

export const useReferenceStore = create<ReferenceState>()((set, get) => {
  /**
   * Apply a set returned by the server, ignoring stale responses: out-of-order responses for the current set,
   * and late responses for a set that is no longer current (e.g. an upload that finished after "New set").
   */
  const applySet = (next: ReferenceSet) => {
    const current = get().set
    if (current && current.id !== next.id) return
    if (current && Date.parse(next.updated_at) < Date.parse(current.updated_at)) return
    set({ set: next, status: 'ready', error: null })
  }

  const setTileError = (slot: ReferenceSlot, message: string | null) =>
    set((state) => {
      const tileErrors = { ...state.tileErrors }
      if (message) tileErrors[slot] = message
      else delete tileErrors[slot]
      return { tileErrors }
    })

  const withBusy = async (imageId: string, action: () => Promise<void>) => {
    set((state) => ({ busyImageIds: [...state.busyImageIds, imageId] }))
    try {
      await action()
    } finally {
      set((state) => ({ busyImageIds: state.busyImageIds.filter((id) => id !== imageId) }))
    }
  }

  const removeUpload = (key: string) =>
    set((state) => {
      const entry = state.uploads.find((u) => u.key === key)
      if (entry) URL.revokeObjectURL(entry.previewUrl)
      return { uploads: state.uploads.filter((u) => u.key !== key) }
    })

  const updateUpload = (key: string, patch: Partial<UploadEntry>) =>
    set((state) => ({ uploads: state.uploads.map((u) => (u.key === key ? { ...u, ...patch } : u)) }))

  /** Ensure we have a set to upload into. */
  const ensureSet = async (): Promise<ReferenceSet | null> => {
    const current = get().set
    if (current) return current
    await get().init()
    return get().set
  }

  /** Validate + enqueue one upload. Resolves when this upload settled. */
  const enqueueUpload = (
    file: File,
    slot: ReferenceSlot,
    options: { makePrimary?: boolean; replacesImageId?: string | null } = {},
  ): Promise<void> => {
    uploadCounter += 1
    const key = `${slot}:${uploadCounter}`
    const entry: UploadEntry = {
      key,
      slot,
      fileName: file.name,
      previewUrl: URL.createObjectURL(file),
      progress: null,
      replacesImageId: options.replacesImageId ?? null,
    }
    set((state) => ({ uploads: [...state.uploads, entry] }))
    setTileError(slot, null)

    const run = async () => {
      try {
        const validation = await validateImageFile(file, limits())
        if (validation) {
          setTileError(slot, validation)
          toast.error('Image rejected', validation)
          return
        }
        const target = await ensureSet()
        if (!target) {
          setTileError(slot, 'No reference set available — the backend may be offline.')
          return
        }
        updateUpload(key, { progress: 0 })
        const response = await uploadReferenceImage(
          target.id,
          { file, slot, makePrimary: options.makePrimary },
          { onProgress: (progress) => updateUpload(key, { progress }) },
        )
        applySet(response.set)
      } catch (error) {
        const apiError = await toApiError(error)
        const message = apiError.isUnreachable ? 'Upload failed — backend unreachable.' : apiError.message
        setTileError(slot, message)
        toast.error(`Upload to ${SLOT_META[slot].label} failed`, message)
      } finally {
        removeUpload(key)
      }
    }

    const next = uploadChain.then(run)
    uploadChain = next.catch(() => undefined)
    return next
  }

  return {
    set: null,
    status: 'idle',
    error: null,
    uploads: [],
    tileErrors: {},
    busyImageIds: [],
    creatingSet: false,

    init: async () => {
      if (get().status === 'loading') return
      set({ status: 'loading', error: null })
      try {
        const savedId = readString(STORAGE_KEYS.referenceSetId)
        if (savedId) {
          try {
            applySet(await getReferenceSet(savedId))
            return
          } catch (error) {
            const apiError = await toApiError(error)
            if (apiError.status !== 404) throw apiError
            // The set expired or was deleted: fall through and create a fresh one.
          }
        }
        const created = await createReferenceSet({})
        writeString(STORAGE_KEYS.referenceSetId, created.id)
        set({ set: created, status: 'ready', error: null })
      } catch (error) {
        set({ status: 'error', error: await toApiError(error) })
      }
    },

    refresh: async () => {
      const current = get().set
      if (!current) {
        await get().init()
        return
      }
      try {
        applySet(await getReferenceSet(current.id))
      } catch (error) {
        const apiError = await toApiError(error)
        if (apiError.status !== 404) return
        // The set vanished on the server (expired / deleted): start a fresh one.
        writeString(STORAGE_KEYS.referenceSetId, null)
        set({ set: null, status: 'idle' })
        await get().init()
      }
    },

    newSet: async () => {
      const previous = get().set
      set({ creatingSet: true })
      try {
        const created = await createReferenceSet({})
        writeString(STORAGE_KEYS.referenceSetId, created.id)
        set({ set: created, status: 'ready', error: null, tileErrors: {} })
        // Remove the previous set only when it is empty (jobs keep their own copies anyway).
        if (previous && previous.images.length === 0) void deleteReferenceSet(previous.id).catch(() => undefined)
        toast.info('New reference set', 'Started an empty reference set.')
      } catch (error) {
        const apiError = await toApiError(error)
        toast.error('Could not create a new set', apiError.message)
      } finally {
        set({ creatingSet: false })
      }
    },

    uploadFiles: async (files, targetSlot) => {
      if (files.length === 0) return
      const current = get().set
      const pending = get().uploads
      const occupied = new Set<ReferenceSlot>([
        ...(current?.images.map((image) => image.slot) ?? []),
        ...pending.map((upload) => upload.slot),
      ])
      const detailUsed =
        (current?.images.filter((image) => image.slot === 'detail').length ?? 0) +
        pending.filter((upload) => upload.slot === 'detail' && !upload.replacesImageId).length
      let detailFree = Math.max(0, detailLimit(current) - detailUsed)
      const freeSingles = SINGLE_SLOTS.filter((slot) => !occupied.has(slot) && slot !== targetSlot)

      const plan: Array<{ file: File; slot: ReferenceSlot }> = []
      let skipped = 0
      files.forEach((file, index) => {
        if (index === 0 && targetSlot && isSingleSlot(targetSlot)) {
          plan.push({ file, slot: targetSlot })
          return
        }
        if (targetSlot === 'detail' && detailFree > 0) {
          plan.push({ file, slot: 'detail' })
          detailFree -= 1
          return
        }
        const slot = targetSlot === 'detail' ? undefined : freeSingles.shift()
        if (slot) plan.push({ file, slot })
        else if (detailFree > 0) {
          plan.push({ file, slot: 'detail' })
          detailFree -= 1
        } else skipped += 1
      })

      if (skipped > 0) {
        const message = `${skipped} image${skipped > 1 ? 's were' : ' was'} not added — all slots are full (detail limit ${detailLimit(current)}).`
        if (targetSlot === 'detail' || plan.length === 0) setTileError('detail', message)
        toast.warning('Some images were skipped', message)
      }
      await Promise.all(plan.map(({ file, slot }) => enqueueUpload(file, slot)))
    },

    replaceImage: async (image, file) => {
      if (isSingleSlot(image.slot)) {
        // Uploading to an occupied single slot replaces it server-side (primary flag carries over).
        await enqueueUpload(file, image.slot)
        return
      }
      // Detail images: upload the new file (keeping primary), then delete the old one.
      const current = get().set
      const detailCount = current?.images.filter((img) => img.slot === 'detail').length ?? 0
      if (detailCount >= detailLimit(current)) {
        await get().removeImage(image.id)
        await enqueueUpload(file, 'detail', { makePrimary: image.is_primary })
        return
      }
      const before = get().set?.images.length ?? 0
      await enqueueUpload(file, 'detail', { makePrimary: image.is_primary, replacesImageId: image.id })
      const after = get().set?.images.length ?? 0
      if (after > before) await get().removeImage(image.id)
    },

    removeImage: async (imageId) => {
      const current = get().set
      if (!current) return
      await withBusy(imageId, async () => {
        try {
          applySet(await deleteReferenceImage(current.id, imageId))
        } catch (error) {
          const apiError = await toApiError(error)
          toast.error('Could not remove image', apiError.message)
        }
      })
    },

    setPrimary: async (imageId) => {
      const current = get().set
      if (!current || current.primary_image_id === imageId) return
      await withBusy(imageId, async () => {
        try {
          applySet(await setPrimaryImage(current.id, imageId))
        } catch (error) {
          const apiError = await toApiError(error)
          toast.error('Could not set primary image', apiError.message)
        }
      })
    },

    moveImage: async (imageId, slot) => {
      const current = get().set
      if (!current) return
      await withBusy(imageId, async () => {
        try {
          applySet(await updateReferenceImage(current.id, imageId, { slot }))
          setTileError(slot, null)
        } catch (error) {
          const apiError = await toApiError(error)
          const message =
            apiError.status === 409 ? `The ${SLOT_META[slot].label} slot is already occupied.` : apiError.message
          toast.error('Could not move image', message)
        }
      })
    },

    clearTileError: (slot) => setTileError(slot, null),

    adoptSet: (next) => {
      writeString(STORAGE_KEYS.referenceSetId, next.id)
      set({ set: next, status: 'ready', error: null, tileErrors: {} })
    },
  }
})

/** Images of a set grouped by slot. */
export function imagesBySlot(set: ReferenceSet | null): Map<ReferenceSlot, ReferenceImage[]> {
  const map = new Map<ReferenceSlot, ReferenceImage[]>()
  for (const image of set?.images ?? []) {
    const list = map.get(image.slot)
    if (list) list.push(image)
    else map.set(image.slot, [image])
  }
  return map
}
