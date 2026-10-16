/*
 * UI probe for the browser pane (UI_DESIGN.md §9, §10.5, §10.6; invariant UI1). Paste this whole file into
 * `javascript_tool`: it installs `window.img3dProbe` and evaluates to `img3dProbe.report()` for the current page.
 *
 *   img3dProbe.report({ mode: 'mobile', baseline: '390x844' })  → the §9 JSON report (+ fingerprint, UI1 verdict)
 *   img3dProbe.fingerprint()                                    → ordered landmark / heading / button entries
 *   img3dProbe.recordBaseline()                                 → {fingerprint, tunerEntries} of the current page
 *   img3dProbe.checkUi1('1280x800')                             → UI1 on a legacy404 backend (tuner as today)
 *   img3dProbe.checkUi1('1280x800', 'locked')                   → UI1 on `full` (tuner rendered but locked)
 *
 * Fingerprint entries, in document order, from rendered elements outside `[aria-hidden="true"]`, `[inert]`, closed
 * dialogs and `[data-a11y-announcer]` regions; standalone numbers in names become `#` (live numbers never break UI1):
 *   `landmark:<role>[:<name>]`  banner, navigation, main, complementary, contentinfo, search, region/form (named only)
 *   `heading:<level>:<name>`    h1–h6 and role=heading
 *   `button:<name>`             button, role=button, input[type=button|submit|reset|image], summary (disabled too)
 *   `note:<text>`               role=note (today's UI has none, so the recorded BASELINE has none)
 * `fingerprint({skipAdditions: true})` also ignores every subtree marked `data-ui-addition` (part-aware cards).
 *
 * BASELINE was recorded by F0 from TODAY's UI (the clean 8654b90 build, mock `--features legacy404 --engine-ready
 * --seed-jobs 0`, a fresh origin (empty localStorage), no job opened, page reloaded at each size, browser pane, fine
 * pointer) at the three baseline viewports; compare under the same conditions. The fingerprint INCLUDES the tuner
 * (the user's spec: preserve the existing Default UI; tuning is only locked). `tunerEntries` is information only and
 * is never subtracted: every entry inside the closest `section` of `#tuning-title` (the "Auto Tune" panel,
 * TuningCard) plus every button whose name starts with "Blender runtime" (BlenderRuntimeStatus). Re-record only
 * from that same build:
 *   copy(JSON.stringify(img3dProbe.recordBaseline()))   at 1280×800, 768×1024 and 390×844, then paste below.
 *
 * UI1 (applyDelta): passes when (1) the current fingerprint without the `add` entries equals the baseline fingerprint
 * without the `remove` entries (same order), and (2) each `add` entry occurs exactly once. The `locked` variant also
 * requires every control inside each `[data-tuner-lock="locked"]` frame to be disabled, the frame's fieldset to have
 * aria-disabled="true" + a non-empty aria-describedby, and `#tuning-title` to exist exactly once.
 */
(() => {
  // ---- recorded baseline (see header) --------------------------------------------------------------------------
  const SLOT = (name) => `button:${name} slot — empty. Press Enter to browse, or drop / paste an image.`
  const REFERENCES = [
    'landmark:region:References', 'heading:2:References', 'button:New set', ...['Front', 'Back', 'Left', 'Right', 'Face Front', 'Face Side'].map(SLOT),
    'heading:3:Detail images', 'button:Add detail images (# remaining). Press Enter to browse, or drop / paste images.',
  ]
  const VIEWER = [
    'landmark:region:3D viewer', 'button:Reset camera', 'button:Save screenshot (PNG)', 'button:Fullscreen', 'button:Wireframe overlay', 'button:Grid',
    'button:Contact shadows', 'button:Studio lighting', 'button:Auto-rotate',
  ]
  const SETTINGS = ['landmark:region:Settings', 'heading:2:Settings', 'button:Reset', 'button:Pick a random seed', 'button:50K', 'button:200K', 'button:500K', 'button:1M', 'button:Advanced sampling']
  const CARDS = ['landmark:region:Progress', 'heading:2:Progress', 'landmark:region:Model', 'heading:2:Model', 'landmark:region:Auto Tune', 'heading:2:Auto Tune']
  const HEADER = ['landmark:banner', 'button:Engine status: Model ready. Show details', 'button:Blender runtime: Blender #. Show details']
  const GENERATE = 'button:Generate 3D model Ctrl ↵'
  const TUNER = ['button:Blender runtime: Blender #. Show details', 'landmark:region:Auto Tune', 'heading:2:Auto Tune']
  /** Recorded 2026-10-16 from the 8654b90 build (390×844 is emulated by the pane as a touch phone: coarse pointer). */
  const BASELINE = {
    '1280x800': { fingerprint: [...HEADER, 'button:History', 'landmark:main', ...REFERENCES, ...VIEWER, ...SETTINGS, ...CARDS, GENERATE], tunerEntries: TUNER },
    '768x1024': { fingerprint: [...HEADER, 'button:History', 'landmark:main', ...REFERENCES, ...SETTINGS, GENERATE, ...VIEWER, ...CARDS], tunerEntries: TUNER },
    '390x844': { fingerprint: [...HEADER, 'landmark:main', ...REFERENCES, GENERATE, 'landmark:navigation:Sections'], tunerEntries: [TUNER[0]] },
  }

  const VIEWPORTS = Object.fromEntries(Object.keys(BASELINE).map((key) => [key, key.split('x').map(Number)]))

  const TUNER_LOCK_NOTICE = 'Paused until you approve the part-aware milestone'
  /** UI1: the Default UI = today's UI (tuner included) plus the App settings gear (+ P21 button); `locked` also has
   *  exactly one lock notice. Nothing is ever removed. */
  const EXPECTED_DEFAULT_DELTA = Object.fromEntries(Object.keys(BASELINE).map((key) => {
    const width = VIEWPORTS[key][0]
    const add = ['button:App settings', ...(width >= 1280 && width < 1440 ? ['button:GPU memory details'] : [])]
    return [key, { legacy: { remove: [], add }, locked: { remove: [], add: [...add, `note:${TUNER_LOCK_NOTICE}`] } }]
  }))

  /** UI1 comparison of a current fingerprint with a baseline (pure; also used by the unit test). */
  function applyDelta(baseline, delta, current) {
    const removed = new Set(delta.remove)
    const added = new Set(delta.add)
    const expected = baseline.fingerprint.filter((entry) => !removed.has(entry))
    const actual = current.filter((entry) => !added.has(entry))
    const counts = Object.fromEntries(delta.add.map((entry) => [entry, current.filter((item) => item === entry).length]))
    let firstDifference = null
    for (let i = 0; i < Math.max(expected.length, actual.length); i++) {
      if (expected[i] !== actual[i]) {
        firstDifference = { index: i, expected: expected[i] ?? null, actual: actual[i] ?? null }
        break
      }
    }
    const addedOnce = Object.values(counts).every((count) => count === 1)
    return { pass: firstDifference === null && addedOnce, firstDifference, addCounts: counts, expectedLength: expected.length, actualLength: actual.length }
  }

  /** Collapse whitespace; standalone numbers (`8`, `5.2.2`, `12:30`) become `#`, words like `3D` or `500K` stay. */
  const normalize = (text) => (text ?? '').replace(/\s+/g, ' ').trim().replace(/\b\d+(?:[.,:]\d+)*\b/g, '#')

  const LANDMARK_TAGS = { header: 'banner', nav: 'navigation', main: 'main', aside: 'complementary', footer: 'contentinfo', section: 'region', form: 'form', search: 'search' }
  const LANDMARK_ROLES = new Set(['banner', 'navigation', 'main', 'complementary', 'contentinfo', 'region', 'form', 'search'])
  const BUTTON_INPUTS = new Set(['button', 'submit', 'reset', 'image'])
  const SECTIONING = 'article, aside, main, nav, section'

  function roleOf(el) {
    const explicit = el.getAttribute('role')
    if (explicit) return explicit.split(/\s+/)[0]
    const tag = el.tagName.toLowerCase()
    if (/^h[1-6]$/.test(tag)) return 'heading'
    if (tag === 'button' || tag === 'summary' || (tag === 'input' && BUTTON_INPUTS.has((el.getAttribute('type') ?? '').toLowerCase()))) return 'button'
    if (tag === 'header' || tag === 'footer') return el.parentElement?.closest(SECTIONING) ? null : LANDMARK_TAGS[tag]
    return LANDMARK_TAGS[tag] ?? null
  }

  function textOf(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent
    if (node.nodeType !== Node.ELEMENT_NODE || node.getAttribute('aria-hidden') === 'true') return ''
    if (node.tagName === 'IMG') return node.getAttribute('alt') ?? ''
    if (node.tagName === 'svg') return node.querySelector('title')?.textContent ?? ''
    if (node.hasAttribute('aria-label')) return node.getAttribute('aria-label')
    return [...node.childNodes].map(textOf).join(' ')
  }

  /** A pragmatic accessible name (aria-labelledby → aria-label → value/alt → contents → title). */
  function nameOf(el) {
    const labelledBy = el.getAttribute('aria-labelledby')
    if (labelledBy) {
      const text = labelledBy.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map(textOf).join(' ')
      if (text.trim()) return normalize(text)
    }
    if (el.hasAttribute('aria-label')) return normalize(el.getAttribute('aria-label'))
    if (el.tagName === 'INPUT') return normalize(el.getAttribute('value') ?? el.getAttribute('alt') ?? el.getAttribute('title'))
    const text = normalize([...el.childNodes].map(textOf).join(' '))
    return text || normalize(el.getAttribute('title'))
  }

  function excluded(el) {
    return !!el.closest('[aria-hidden="true"], [inert], [data-a11y-announcer], dialog:not([open]), template')
  }

  function rendered(el) {
    if (typeof el.checkVisibility === 'function') return el.checkVisibility({ visibilityProperty: true, checkVisibilityCSS: true })
    return el.getClientRects().length > 0
  }

  /** Ordered entries with their elements (`{entry, el}`), document order. */
  function fingerprintItems(root = document.body, { skipAdditions = false } = {}) {
    const items = []
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT)
    for (let el = walker.currentNode; el; el = walker.nextNode()) {
      if (el.nodeType !== Node.ELEMENT_NODE) continue
      if (skipAdditions && el.closest('[data-ui-addition]')) continue
      const role = roleOf(el)
      if (!role || excluded(el) || !rendered(el)) continue
      if (LANDMARK_ROLES.has(role)) {
        const name = nameOf(el)
        if ((role === 'region' || role === 'form') && !(el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby'))) continue
        const label = el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby') ? name : ''
        items.push({ entry: `landmark:${role}${label ? `:${label}` : ''}`, el })
      } else if (role === 'heading') {
        const level = Number(el.getAttribute('aria-level')) || Number(/^H([1-6])$/.exec(el.tagName)?.[1]) || 2
        items.push({ entry: `heading:${level}:${nameOf(el)}`, el })
      } else if (role === 'button') {
        items.push({ entry: `button:${nameOf(el)}`, el })
      } else if (role === 'note') {
        items.push({ entry: `note:${normalize(el.textContent)}`, el })
      }
    }
    return items
  }

  const fingerprint = (opts = {}) => fingerprintItems(document.body, opts).map((item) => item.entry)

  function recordBaseline() {
    const items = fingerprintItems()
    const tuningSection = document.getElementById('tuning-title')?.closest('section') ?? null
    const tunerEntries = items.filter(({ entry, el }) => (tuningSection && tuningSection.contains(el)) || entry.startsWith('button:Blender runtime')).map((item) => item.entry)
    return { fingerprint: items.map((item) => item.entry), tunerEntries }
  }

  /** Lock-frame checks of the `locked` variant (UI_DESIGN §3.9, §10.5). */
  function lockFrames() {
    const frames = [...document.querySelectorAll('[data-tuner-lock="locked"]')]
    const problems = []
    for (const frame of frames) {
      const enabled = [...frame.querySelectorAll('button, input, select, textarea')].filter((el) => !el.matches(':disabled'))
      if (enabled.length) problems.push(`enabled control in lock frame: ${enabled.map((el) => nameOf(el)).join(', ')}`)
      const fieldset = frame.matches('fieldset') ? frame : frame.querySelector('fieldset')
      const describedBy = fieldset?.getAttribute('aria-describedby') ?? ''
      const description = describedBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? '').join(' ').trim()
      if (fieldset?.getAttribute('aria-disabled') !== 'true') problems.push('lock fieldset without aria-disabled="true"')
      if (!description) problems.push('lock fieldset without a non-empty aria-describedby')
    }
    const titles = document.querySelectorAll('#tuning-title').length
    if (titles !== 1) problems.push(`#tuning-title count ${titles} (expected 1)`)
    return { frames: frames.length, problems }
  }

  /** UI1. `legacy`: legacy404 backend, tuner exactly as today. `locked`: `full` backend, part-aware additions skipped. */
  function checkUi1(key, variant = 'legacy') {
    const baseline = BASELINE[key]
    if (!baseline) return { pass: null, reason: `no BASELINE recorded for ${key}` }
    if (variant !== 'locked') return applyDelta(baseline, EXPECTED_DEFAULT_DELTA[key].legacy, fingerprint())
    const result = applyDelta(baseline, EXPECTED_DEFAULT_DELTA[key].locked, fingerprint({ skipAdditions: true }))
    const lock = lockFrames()
    return { ...result, pass: result.pass && lock.frames > 0 && lock.problems.length === 0, lock }
  }

  // ---- §9 cell checks ------------------------------------------------------------------------------------------
  const INTERACTIVE = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="tab"], [role="switch"], [role="checkbox"], [role="radio"], [role="slider"], [role="menuitem"], [role="option"], [tabindex]:not([tabindex="-1"])'
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'

  function visualBox(el) {
    const style = getComputedStyle(el)
    if (style.visibility === 'hidden' || Number(style.opacity) === 0) return null
    const rect = el.getBoundingClientRect()
    if (rect.width < 2 && rect.height < 2) return null // visually hidden (sr-only) or collapsed
    return rect
  }

  function describe(el) {
    const id = el.id ? `#${el.id}` : ''
    const testId = el.getAttribute('data-testid') ? `[data-testid="${el.getAttribute('data-testid')}"]` : ''
    return `${el.tagName.toLowerCase()}${id}${testId} "${nameOf(el).slice(0, 60)}"`
  }

  function smallTargets(minimum) {
    const out = []
    for (const el of document.querySelectorAll(INTERACTIVE)) {
      if (excluded(el) || !rendered(el)) continue
      if (el.tagName === 'A' && getComputedStyle(el).display === 'inline' && el.closest('p, li')) continue // WCAG 2.5.8 inline exception
      const box = visualBox(el)
      if (!box) continue
      if (box.width < minimum || box.height < minimum) out.push({ el: describe(el), width: Math.round(box.width), height: Math.round(box.height) })
    }
    return out
  }

  function fixedAncestor(el) {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const position = getComputedStyle(node).position
      if (position === 'fixed' || position === 'sticky') return node
    }
    return null
  }

  /** Focusable elements inside the viewport whose centre is covered by fixed/sticky chrome they do not belong to. */
  function obscured() {
    const out = []
    for (const el of document.querySelectorAll(FOCUSABLE)) {
      if (excluded(el) || !rendered(el)) continue
      const box = visualBox(el)
      if (!box) continue
      const x = box.left + box.width / 2
      const y = box.top + box.height / 2
      if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue
      const top = document.elementFromPoint(x, y)
      if (!top || top === el || el.contains(top) || top.contains(el)) continue
      const chrome = fixedAncestor(top)
      if (chrome && !chrome.contains(el)) out.push({ el: describe(el), coveredBy: describe(chrome) })
    }
    return out
  }

  const visible = (selector) => {
    const el = document.querySelector(selector)
    return !!el && !excluded(el) && rendered(el) && !!visualBox(el)
  }

  function report(options = {}) {
    const params = new URLSearchParams(location.search)
    const mode = options.mode ?? params.get('ui') ?? 'default'
    const key = options.baseline ?? `${innerWidth}x${innerHeight}`
    const coarse = matchMedia('(pointer: coarse)').matches
    const minimum = mode === 'mobile' || mode === 'tablet' ? 48 : coarse ? 44 : null
    const nodePane = document.querySelector('.react-flow')
    const viewer = document.querySelector('section[aria-label="3D viewer"]')
    const viewerHeight = viewer ? Math.round(viewer.getBoundingClientRect().height) : null
    const toolbarOverflow = viewer ? [...viewer.querySelectorAll('[role="radiogroup"], [role="group"], [role="toolbar"]')].filter((el) => el.scrollWidth > el.clientWidth).map(describe) : []
    const small = minimum ? smallTargets(minimum) : []
    const hidden = obscured()
    const canvasCount = document.querySelectorAll('canvas').length
    const checks = {
      noHorizontalScroll: document.documentElement.scrollWidth <= innerWidth,
      targets: minimum === null ? null : small.length === 0,
      notObscured: hidden.length === 0,
      // UI5: one R3F canvas while a model is open; null (not applicable) while the viewer shows its empty state.
      oneCanvas: canvasCount === 1 ? true : canvasCount === 0 ? null : false,
      mainContent: !!document.getElementById('main-content'),
      uiModeAttribute: document.documentElement.getAttribute('data-ui-mode') === mode,
      sourceSwitch: visible('[data-testid="source-switch"]'),
      promptInput: visible('#prompt-input'),
      referenceDropzone: visible('[data-testid="reference-dropzone"]'),
      viewerTall: viewerHeight === null ? null : viewerHeight >= 220,
      toolbarFits: toolbarOverflow.length === 0,
    }
    return {
      url: location.href,
      viewport: { width: innerWidth, height: innerHeight, coarse, devicePixelRatio },
      mode,
      htmlAttributes: { uiMode: document.documentElement.getAttribute('data-ui-mode'), density: document.documentElement.getAttribute('data-density'), motion: document.documentElement.getAttribute('data-motion') },
      checks,
      details: {
        scrollWidth: document.documentElement.scrollWidth,
        nodeCanvasPane: !!nodePane,
        targetMinimum: minimum,
        smallTargets: small,
        obscured: hidden,
        canvasCount,
        viewerHeight,
        toolbarOverflow,
      },
      fingerprint: fingerprint(),
      ui1: mode === 'default' && BASELINE[key] !== undefined ? checkUi1(key, options.ui1Variant ?? 'legacy') : null,
    }
  }

  const probe = { BASELINE, EXPECTED_DEFAULT_DELTA, TUNER_LOCK_NOTICE, applyDelta, fingerprint, recordBaseline, checkUi1, report }
  globalThis.img3dProbe = probe
  return typeof document === 'undefined' ? probe : probe.report()
})()
