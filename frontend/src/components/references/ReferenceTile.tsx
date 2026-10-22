import { ArrowRightLeft, EllipsisVertical, ImageOff, ImagePlus, Replace, Sparkles, Star, Trash, X } from 'lucide-react'
import { useState } from 'react'
import { useDropzone } from 'react-dropzone'

import { referenceThumbnailUrl } from '../../api/references'
import { REFERENCE_SLOTS, type ImageSourceOut, type ReferenceImage, type ReferenceSlot } from '../../api/types'
import { useFeatures } from '../../hooks/useFeatures'
import { formatBytes } from '../../lib/format'
import { effectiveSource } from '../../lib/generatePlan.ts'
import { SLOT_META, isSingleSlot } from '../../lib/slots'
import { useInputStore } from '../../store/inputStore'
import { useNavigationStore } from '../../store/navigationStore'
import { useReferenceStore, type UploadEntry } from '../../store/referenceStore'
import { useRunStore } from '../../store/runStore'
import { IconButton } from '../common/Button'
import { Menu, type MenuItem } from '../common/Menu'
import { Spinner } from '../common/Spinner'
import styles from './ReferenceTile.module.css'

export interface ReferenceTileProps {
  /** Slot this tile represents (for detail images: `detail`). */
  slot: ReferenceSlot
  image?: ReferenceImage
  upload?: UploadEntry
  error?: string
  /** Slots currently occupied (for the move menu). */
  occupiedSlots: ReadonlySet<ReferenceSlot>
  detailFull: boolean
  accept: string
  disabled?: boolean
  /** Show the slot name label (single slots). */
  showSlotLabel?: boolean
}

/** Display name of a reference image (label, else original filename, else a generic name). */
function imageName(image: ReferenceImage): string {
  return image.label ?? image.original_filename ?? 'Untitled image'
}

function describeImage(image: ReferenceImage): string {
  return `${image.original_filename ?? imageName(image)} — ${image.width}×${image.height}px · ${formatBytes(image.size_bytes)}${image.has_alpha ? ' · alpha' : ''}`
}

/**
 * "From prompt" chip of an image adopted from a text-to-image candidate (UI_DESIGN §5.5, `source.kind === 't2i'`).
 * While that generation is the one in the review panel (and prompt-to-3D is available) the chip links there: it
 * switches the input to the prompt and reveals the review card. Otherwise it is a plain label. A part-aware
 * addition (never rendered for a legacy backend, which sends no `source`).
 */
function FromPromptChip({ source }: { source: ImageSourceOut }) {
  const features = useFeatures()
  const linked = useRunStore((s) => s.reviewGenerationId === source.image_generation_id)
  const title = `Generated from a prompt (seed ${source.seed})`
  if (!linked || effectiveSource('prompt', features) !== 'prompt') {
    return (
      <span className={styles.promptChip} title={title} data-ui-addition="from-prompt">
        <Sparkles size={11} aria-hidden />
        <span className={styles.promptChipText}>From prompt</span>
      </span>
    )
  }
  return (
    <button
      type="button"
      className={`${styles.promptChip} ${styles.promptChipLink}`}
      title={`${title}: show the generated images`}
      aria-label="From prompt: show the generated images"
      data-ui-addition="from-prompt"
      onClick={() => {
        useInputStore.getState().setSource('prompt')
        useNavigationStore.getState().requestReveal('review')
      }}
    >
      <Sparkles size={11} aria-hidden />
      <span className={styles.promptChipText}>From prompt</span>
    </button>
  )
}

/**
 * One reference tile: drag & drop, click/keyboard to browse, paste when focused, thumbnail, upload progress,
 * primary star, replace / move / remove menu and inline validation errors.
 */
export function ReferenceTile({
  slot,
  image,
  upload,
  error,
  occupiedSlots,
  detailFull,
  accept,
  disabled = false,
  showSlotLabel = true,
}: ReferenceTileProps) {
  const uploadFiles = useReferenceStore((s) => s.uploadFiles)
  const replaceImage = useReferenceStore((s) => s.replaceImage)
  const removeImage = useReferenceStore((s) => s.removeImage)
  const setPrimary = useReferenceStore((s) => s.setPrimary)
  const moveImage = useReferenceStore((s) => s.moveImage)
  const clearTileError = useReferenceStore((s) => s.clearTileError)
  const busy = useReferenceStore((s) => (image ? s.busyImageIds.includes(image.id) : false))
  const [thumbFailed, setThumbFailed] = useState<string | null>(null)

  const meta = SLOT_META[slot]
  const filled = !!image

  const handleFiles = (files: File[]) => {
    if (files.length === 0) return
    if (image && !isSingleSlot(image.slot)) {
      const [first, ...rest] = files
      void replaceImage(image, first)
      if (rest.length) void uploadFiles(rest, 'detail')
      return
    }
    void uploadFiles(files, slot)
  }

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop: handleFiles,
    multiple: true,
    noClick: filled,
    noKeyboard: filled,
    noDragEventsBubbling: true,
    useFsAccessApi: false,
    disabled,
  })

  const moveItems: MenuItem[] = image
    ? REFERENCE_SLOTS.filter((target) => target !== image.slot).map((target) => {
        const occupied = isSingleSlot(target) && occupiedSlots.has(target)
        const full = target === 'detail' && detailFull
        return {
          id: `move-${target}`,
          label: SLOT_META[target].label,
          icon: <ArrowRightLeft size={14} />,
          disabled: occupied || full,
          hint: occupied ? 'occupied' : full ? 'full' : undefined,
          onSelect: () => void moveImage(image.id, target),
        }
      })
    : []

  const rootProps = getRootProps({
    className: [
      styles.tile,
      filled ? styles.filled : styles.empty,
      isDragActive ? styles.dragActive : '',
      error ? styles.hasError : '',
      image?.is_primary ? styles.primary : '',
      disabled ? styles.disabled : '',
    ]
      .filter(Boolean)
      .join(' '),
    // Empty tiles act as buttons (Enter/Space opens the file dialog); filled tiles stay focusable so a focused
    // tile accepts pasted images (replace).
    role: filled ? 'group' : 'button',
    ...(filled ? { tabIndex: 0 } : {}),
    'aria-label': filled
      ? `${meta.label} reference: ${imageName(image)}${image.is_primary ? ' (primary)' : ''}. Drop or paste an image to replace.`
      : `${meta.label} slot — empty. Press Enter to browse, or drop / paste an image.`,
    'data-reference-tile': 'true',
  })

  const showUpload = !!upload
  const thumbnailUrl = image ? referenceThumbnailUrl(image) : null

  return (
    <div className={styles.wrapper}>
      <div {...rootProps}>
        <input {...getInputProps()} accept={accept} aria-label={`Choose an image for ${meta.label}`} />

        {image && thumbnailUrl && thumbFailed !== thumbnailUrl && !showUpload && (
          <img
            key={image.id}
            className={styles.thumb}
            src={thumbnailUrl}
            alt={`${meta.label} reference`}
            title={describeImage(image)}
            loading="lazy"
            decoding="async"
            draggable={false}
            onError={() => setThumbFailed(thumbnailUrl)}
          />
        )}
        {image && thumbFailed === thumbnailUrl && !showUpload && (
          <div className={styles.placeholder} title={describeImage(image)}>
            <ImageOff size={20} aria-hidden />
            <span>Preview unavailable</span>
          </div>
        )}

        {showUpload && (
          <div className={styles.uploading}>
            <img className={styles.localPreview} src={upload.previewUrl} alt="" draggable={false} />
            <div className={styles.uploadOverlay}>
              {upload.progress === null ? (
                <span className={styles.uploadLabel}>Queued…</span>
              ) : (
                <>
                  <span className={styles.uploadLabel}>{Math.round(upload.progress)}%</span>
                  <div className={styles.uploadTrack} aria-hidden>
                    <div className={styles.uploadFill} style={{ width: `${upload.progress}%` }} />
                  </div>
                </>
              )}
              <span className="visually-hidden" role="status">
                Uploading {upload.fileName}
              </span>
            </div>
          </div>
        )}

        {!image && !showUpload && (
          <div className={styles.emptyContent}>
            <ImagePlus size={20} aria-hidden className={styles.emptyIcon} />
            <span className={styles.emptyLabel}>{meta.label}</span>
            <span className={styles.emptyHint}>Drop · paste · click</span>
          </div>
        )}

        {image && showSlotLabel && !showUpload && <span className={styles.slotBadge}>{meta.short}</span>}
        {image && !showSlotLabel && !showUpload && (
          <span className={styles.slotBadge} title={imageName(image)}>
            {imageName(image)}
          </span>
        )}
        {image?.source?.kind === 't2i' && !showUpload && <FromPromptChip source={image.source} />}

        {busy && (
          <div className={styles.busy}>
            <Spinner size={18} label="Updating" />
          </div>
        )}

        {isDragActive && <div className={styles.dropHint}>{filled ? 'Drop to replace' : 'Drop to add'}</div>}
      </div>

      {image && !showUpload && (
        <>
          <button
            type="button"
            className={`${styles.star} ${image.is_primary ? styles.starActive : ''}`}
            aria-pressed={image.is_primary}
            aria-label={image.is_primary ? 'Primary image' : `Set ${meta.label} as primary image`}
            title={image.is_primary ? 'Primary image (used for generation)' : 'Set as primary'}
            disabled={busy || disabled}
            onClick={() => void setPrimary(image.id)}
          >
            <Star size={14} fill={image.is_primary ? 'currentColor' : 'none'} />
          </button>
          <div className={styles.menu}>
            <Menu
              label={`${meta.label} image actions`}
              icon={<EllipsisVertical size={14} />}
              disabled={busy || disabled}
              sections={[
                {
                  items: [
                    {
                      id: 'replace',
                      label: 'Replace…',
                      icon: <Replace size={14} />,
                      onSelect: open,
                    },
                    {
                      id: 'primary',
                      label: image.is_primary ? 'Primary image' : 'Set as primary',
                      icon: <Star size={14} />,
                      disabled: image.is_primary,
                      onSelect: () => void setPrimary(image.id),
                    },
                  ],
                },
                { title: 'Move to', items: moveItems },
                {
                  items: [
                    {
                      id: 'remove',
                      label: 'Remove',
                      icon: <Trash size={14} />,
                      danger: true,
                      onSelect: () => void removeImage(image.id),
                    },
                  ],
                },
              ]}
            />
          </div>
        </>
      )}

      {error && (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          <IconButton
            label="Dismiss error"
            icon={<X size={12} />}
            size="sm"
            className={styles.errorClose}
            onClick={() => clearTileError(slot)}
          />
        </div>
      )}
    </div>
  )
}
