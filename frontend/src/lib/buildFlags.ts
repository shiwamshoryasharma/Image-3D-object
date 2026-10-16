/**
 * Build-time flags (impure: reads `import.meta.env`; never imported by a pure module, UI_DESIGN §1.5).
 *
 * The only build flag of the part-aware milestone is the refinement preview of the Node palette (§4.3.2, §4.3.11).
 * There is deliberately **no** tuner build flag: the Auto Tune UI is locked or unlocked at runtime by
 * `/features.mesh_tuning.enabled` (`hooks/useTunerLocked.ts`, §3.9).
 */

/** `VITE_FEATURE_REFINEMENT_PREVIEW === 'true'`: list the locked refine node group in the Node palette (default off). */
export const REFINEMENT_PREVIEW: boolean = import.meta.env.VITE_FEATURE_REFINEMENT_PREVIEW === 'true'
