import { Box, GalleryVerticalEnd, ImagePlus, Puzzle, Settings2, Sparkles } from 'lucide-react'

import type { FeatureId } from '../../lib/featureMeta.ts'

/** Feature id → its icon, so every shell shows the same glyph next to the same `FEATURE_META` words. Decorative. */
export function FeatureIcon({ id, size = 22 }: { id: FeatureId; size?: number }) {
  switch (id) {
    case 'create':
      return <ImagePlus size={size} aria-hidden />
    case 'viewer':
      return <Box size={size} aria-hidden />
    case 'parts':
      return <Puzzle size={size} aria-hidden />
    case 'library':
      return <GalleryVerticalEnd size={size} aria-hidden />
    case 'appSettings':
      return <Settings2 size={size} aria-hidden />
    case 'generate':
      return <Sparkles size={size} aria-hidden />
  }
}
