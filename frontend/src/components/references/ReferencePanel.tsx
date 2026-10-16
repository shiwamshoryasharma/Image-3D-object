import { FolderPlus, Images, Info, Plus, RefreshCw, Star } from 'lucide-react'
import { useEffect } from 'react'
import { useDropzone } from 'react-dropzone'

import { SINGLE_SLOTS, type ReferenceSlot } from '../../api/types'
import { FALLBACK_LIMITS } from '../../lib/imageValidation'
import { SLOT_META, acceptAttribute, formatList } from '../../lib/slots'
import { imagesBySlot, useReferenceStore } from '../../store/referenceStore'
import { selectCapabilities, useSystemStore } from '../../store/systemStore'
import { Button } from '../common/Button'
import { EmptyState } from '../common/EmptyState'
import { Panel } from '../common/Panel'
import { Spinner } from '../common/Spinner'
import { ReferenceTile } from './ReferenceTile'
import styles from './ReferencePanel.module.css'

function AddDetailTile({ accept, disabled, remaining }: { accept: string; disabled: boolean; remaining: number }) {
  const uploadFiles = useReferenceStore((s) => s.uploadFiles)
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: (files) => void uploadFiles(files, 'detail'),
    multiple: true,
    noDragEventsBubbling: true,
    useFsAccessApi: false,
    disabled,
  })
  return (
    <div
      {...getRootProps({
        role: 'button',
        className: `${styles.addTile} ${isDragActive ? styles.addTileActive : ''} ${disabled ? styles.addTileDisabled : ''}`,
        'aria-label': `Add detail images (${remaining} remaining). Press Enter to browse, or drop / paste images.`,
        'data-reference-tile': 'true',
      })}
    >
      <input {...getInputProps()} accept={accept} aria-label="Choose detail images" />
      <Plus size={18} aria-hidden />
      <span>{isDragActive ? 'Drop' : 'Add'}</span>
    </div>
  )
}

/**
 * Reference set editor: six single-slot tiles + a detail grid. Dropping several files on the panel fills
 * empty slots in order, then the detail grid. Pasting an image anywhere (outside inputs) does the same.
 */
export function ReferencePanel({ active = true }: { active?: boolean }) {
  const referenceSet = useReferenceStore((s) => s.set)
  const status = useReferenceStore((s) => s.status)
  const loadError = useReferenceStore((s) => s.error)
  const uploads = useReferenceStore((s) => s.uploads)
  const tileErrors = useReferenceStore((s) => s.tileErrors)
  const creatingSet = useReferenceStore((s) => s.creatingSet)
  const uploadFiles = useReferenceStore((s) => s.uploadFiles)
  const newSet = useReferenceStore((s) => s.newSet)
  const init = useReferenceStore((s) => s.init)
  const limits = useSystemStore((s) => s.system?.limits) ?? FALLBACK_LIMITS
  const capabilities = useSystemStore(selectCapabilities)
  const reachable = useSystemStore((s) => s.reachable)

  const accept = acceptAttribute(limits.allowed_mime_types)
  const bySlot = imagesBySlot(referenceSet)
  const occupied = new Set<ReferenceSlot>(referenceSet?.images.map((image) => image.slot) ?? [])
  const detailImages = bySlot.get('detail') ?? []
  const detailUploads = uploads.filter((upload) => upload.slot === 'detail' && !upload.replacesImageId)
  const detailLimit = referenceSet?.slot_limits.detail ?? limits.max_detail_images
  const detailRemaining = Math.max(0, detailLimit - detailImages.length - detailUploads.length)
  const disabled = !referenceSet || reachable === false
  const primary = referenceSet?.images.find((image) => image.is_primary) ?? null
  const imageCount = referenceSet?.images.length ?? 0

  const panelDrop = useDropzone({
    onDrop: (files) => void uploadFiles(files),
    noClick: true,
    noKeyboard: true,
    noPaste: true,
    multiple: true,
    useFsAccessApi: false,
    disabled,
  })

  // Paste anywhere (not into a text field / focused tile) → fill empty slots.
  useEffect(() => {
    if (!active || disabled) return
    const onPaste = (event: ClipboardEvent) => {
      if (event.defaultPrevented) return
      const target = event.target as HTMLElement | null
      if (target?.closest('input, textarea, select, [contenteditable="true"], [data-reference-tile]')) return
      const files = Array.from(event.clipboardData?.files ?? []).filter((file) => file.type.startsWith('image/'))
      if (files.length === 0) return
      event.preventDefault()
      void useReferenceStore.getState().uploadFiles(files)
    }
    document.addEventListener('paste', onPaste)
    return () => document.removeEventListener('paste', onPaste)
  }, [active, disabled])

  const subtitle = referenceSet
    ? `${imageCount} image${imageCount === 1 ? '' : 's'}${primary ? ` · primary: ${SLOT_META[primary.slot].label}` : ''}`
    : 'Reference set'

  return (
    <Panel
      id="references"
      title="References"
      icon={<Images size={16} />}
      subtitle={subtitle}
      actions={
        <Button
          size="sm"
          variant="ghost"
          icon={<FolderPlus size={14} />}
          loading={creatingSet}
          disabled={disabled && reachable === false}
          onClick={() => void newSet()}
          title="Start a new, empty reference set"
        >
          New set
        </Button>
      }
    >
      {!referenceSet && status !== 'error' && (
        <div className={styles.loading}>
          {reachable === false ? (
            <EmptyState
              compact
              icon={<Images size={18} />}
              title="References unavailable"
              description="The backend is offline. Your reference set will load when it reconnects."
            />
          ) : (
            <>
              <Spinner size={18} />
              <span>Loading reference set…</span>
            </>
          )}
        </div>
      )}

      {!referenceSet && status === 'error' && (
        <EmptyState
          compact
          tone="danger"
          icon={<Images size={18} />}
          title="Could not load references"
          description={loadError?.message}
          action={
            <Button size="sm" icon={<RefreshCw size={14} />} onClick={() => void init()}>
              Try again
            </Button>
          }
        />
      )}

      {referenceSet && (
        <div
          {...panelDrop.getRootProps({
            className: `${styles.dropArea} ${panelDrop.isDragActive ? styles.dropAreaActive : ''}`,
          })}
        >
          <input {...panelDrop.getInputProps()} accept={accept} tabIndex={-1} aria-hidden />

          {capabilities?.max_reference_images === 1 && (
            <p className={styles.note}>
              <Info size={14} aria-hidden />
              <span>
                The current engine generates from the <Star size={11} className={styles.inlineStar} aria-label="primary" />{' '}
                primary image only. Other references are stored with each job for multi-reference generation.
              </span>
            </p>
          )}

          {panelDrop.isDragActive && <p className={styles.dropBanner}>Drop images to fill empty slots in order, then details</p>}

          <div className={styles.slots}>
            {SINGLE_SLOTS.map((slot) => (
              <ReferenceTile
                key={slot}
                slot={slot}
                image={bySlot.get(slot)?.[0]}
                upload={uploads.find((upload) => upload.slot === slot)}
                error={tileErrors[slot]}
                occupiedSlots={occupied}
                detailFull={detailRemaining === 0}
                accept={accept}
                disabled={disabled}
              />
            ))}
          </div>

          <div className={styles.detailHeader}>
            <h3 className={styles.detailTitle}>Detail images</h3>
            <span className={styles.detailCount}>
              {detailImages.length}/{detailLimit}
            </span>
          </div>
          <div className={styles.detailGrid}>
            {detailImages.map((image) => (
              <ReferenceTile
                key={image.id}
                slot="detail"
                image={image}
                upload={uploads.find((upload) => upload.replacesImageId === image.id)}
                occupiedSlots={occupied}
                detailFull={detailRemaining === 0}
                accept={accept}
                disabled={disabled}
                showSlotLabel={false}
              />
            ))}
            {detailUploads.map((upload) => (
              <ReferenceTile
                key={upload.key}
                slot="detail"
                upload={upload}
                occupiedSlots={occupied}
                detailFull={detailRemaining === 0}
                accept={accept}
                disabled
              />
            ))}
            {detailRemaining > 0 && <AddDetailTile accept={accept} disabled={disabled} remaining={detailRemaining} />}
          </div>
          {tileErrors.detail && (
            <p className={styles.detailError} role="alert">
              {tileErrors.detail}
            </p>
          )}

          <p className={styles.footnote}>
            {formatList(limits.allowed_mime_types)} · up to {limits.max_upload_mb} MB · min {limits.min_image_side}px.
            Drop several files on the panel to fill slots in order; paste with Ctrl+V.
          </p>
        </div>
      )}
    </Panel>
  )
}
