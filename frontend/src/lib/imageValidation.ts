/**
 * Client-side pre-validation of reference images against `/system.limits`. The backend re-validates
 * everything (it decodes with Pillow); this only gives fast, friendly feedback.
 */
import type { SystemLimits } from '../api/types'
import { MIME_EXTENSIONS, formatList } from './slots'

/** Limits used before `/system` responds (contract defaults §7.2). */
export const FALLBACK_LIMITS: SystemLimits = {
  max_upload_mb: 25,
  allowed_mime_types: ['image/png', 'image/jpeg', 'image/webp'],
  max_detail_images: 8,
  min_image_side: 64,
  slots: ['front', 'back', 'left', 'right', 'face_front', 'face_side', 'detail'],
}

function inferMimeType(file: File): string {
  if (file.type) return file.type.toLowerCase()
  const name = file.name.toLowerCase()
  for (const [mime, extensions] of Object.entries(MIME_EXTENSIONS)) {
    if (extensions.some((ext) => name.endsWith(ext))) return mime
  }
  return ''
}

async function readDimensions(file: File): Promise<{ width: number; height: number } | null> {
  if (typeof createImageBitmap !== 'function') return null
  try {
    const bitmap = await createImageBitmap(file)
    const size = { width: bitmap.width, height: bitmap.height }
    bitmap.close()
    return size
  } catch {
    return null
  }
}

/** Returns an error message, or `null` when the file looks acceptable. */
export async function validateImageFile(file: File, limits: SystemLimits): Promise<string | null> {
  const mime = inferMimeType(file)
  if (!limits.allowed_mime_types.includes(mime)) {
    return `"${file.name}" is not a supported image. Use ${formatList(limits.allowed_mime_types)}.`
  }
  const maxBytes = limits.max_upload_mb * 1024 * 1024
  if (file.size > maxBytes) {
    return `"${file.name}" is ${(file.size / 1024 / 1024).toFixed(1)} MB — the limit is ${limits.max_upload_mb} MB.`
  }
  if (file.size === 0) return `"${file.name}" is empty.`
  // If the browser cannot decode it (e.g. a very large image), let the backend make the final call.
  const size = await readDimensions(file)
  if (size && Math.min(size.width, size.height) < limits.min_image_side) {
    return `"${file.name}" is ${size.width}×${size.height}px — each side must be at least ${limits.min_image_side}px.`
  }
  return null
}
