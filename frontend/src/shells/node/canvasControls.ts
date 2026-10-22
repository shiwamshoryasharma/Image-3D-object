/**
 * The mounted canvas registers its view commands here, so the toolbar and the shortcuts (outside the lazy canvas
 * chunk, which alone imports @xyflow) can fit the view. A no-op while the List view is shown.
 */
let fitHandler: (() => void) | null = null

export function registerCanvasFit(handler: (() => void) | null): void {
  fitHandler = handler
}

export function fitCanvas(): void {
  fitHandler?.()
}
