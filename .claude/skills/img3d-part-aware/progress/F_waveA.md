# Frontend wave A (F0, F1, F2, F3, F4) — done 2026-10-16 ~22:45 (lean mode: no gate/review agents)

- F1 finished in the first run (progress/F1.md); F0, F2, F3, F4 finished in the resumed run (their reports are in
  the workflow results; key requests: F5 must import form/preference types from lib/forms.ts + lib/uiModes.ts and run
  the sanitize* functions in each persist merge; F5 partStore.loadSet retry rule for ok:false manifests; F11
  workflowStore must drop deleted node ids from every runIndex entry; F11 adds a top-level nodeShell value to
  bundle_baseline.json).
- Gates (all agents + re-checked by the main session): tsc -b 0 errors, oxlint clean, compiler_check 0 events,
  import_boundaries 0 violations, protected_frontend 27 tuner files byte-identical to 8654b90, bundle budget ok
  (initial JS 178.81 kB gz vs 178.45 baseline), unit tests 515/515, ui_modes_mock_smoke 198/198,
  mesh_tuner_mock_smoke 36/36.
- ui_probe BASELINE recorded by F0 in the browser pane from the 8654b90 build (legacy404) at 1280x800, 768x1024,
  390x844. Main session then aligned `test/frontend/review/ui_probe.js` + `unit/ui_probe.test.mjs` with the user-spec
  rule (tuner preserved but locked): `tunerEntries` informational (never subtracted), `EXPECTED_DEFAULT_DELTA[key]
  .{legacy,locked}`, `note:` entries, `fingerprint({skipAdditions})`, `checkUi1(key, 'locked')` lock-frame checks.
