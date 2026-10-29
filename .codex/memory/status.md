# Img3D checkpoint and evidence

## Latest task: generation-quality fix (2026-10-29)

The user's subsequent fix request authorized targeted export changes. See
[generation-quality.md](generation-quality.md) for the implementation, real anime/
cat/house artifacts, 261 CPU + 4 native GPU passing checks, and remaining learned
model limitations. The part-aware milestone and tuner approval status are
unchanged. The setup-only snapshot below is historical.

## Earlier setup snapshot

Recorded 2026-10-29. This task inspected documentation and source, checked Git
metadata and the embedded interpreter, and created Codex knowledge files.
It did not execute application tests, run inference or approve a milestone.

## Directly checked before these additions

- Git HEAD: 12a4786, 2026-10-22, "Part-aware milestone (WIP): parts layer,
  prompt-to-3D, workflows, four UI modes".
- Existing working change: .claude/skills/img3d-part-aware/RESUME.md. Preserve it.
- No .codex directory existed before this setup.
- Embedded backend Python: 3.14.2, sys.flags.isolated = 1, PyYAML available.
- Eight PNG inputs exist under test/images/ALICE and ANIME_CHARACTER_01.
- Part-aware backend packages, provider implementations, parts asset code,
  run/workflow services and four frontend shells exist.
- Progress reports S0-S11 and F0-F12 (including split reports) exist.
  No F13.md report was present in the inspected progress directory.
- backend/config/settings.py defaults mesh_tuning_enabled to false and
  persist_generation_intermediates to false.
- Model directories exist under backend/.model, including the original engine,
  auxiliary, segmentation and text-to-image roots. Weight completeness and GPU
  readiness were not verified.

## Latest historical checkpoint (2026-10-22; not rerun here)

The dated update block at the top of
[RESUME.md](../../.claude/skills/img3d-part-aware/RESUME.md) reports:

- Backend suites: 743 passed, 4 skipped.
- Frontend unit tests: 534 passed; UI-mode mock smoke: 198/198; tuner mock:
  36/36; compiler/import/protected-file/bundle gates passed.
- S10 real semantic-parts smoke passed with face-weighted cut consistency
  about 0.987/0.988 on ALICE/anime at eight views.
- S11 real prompt -> image -> 3D -> parts workflow completed in about 1146 s.
- Commit 12a4786 was pushed; F13 verification/docs, the final review and browser
  verification of all four modes remained next, followed by user approval.

Treat those numbers as historical claims backed by the referenced reports.
Re-check current results for the requested change instead of declaring them
the present test baseline.

## Accepted decisions to preserve

From [DECISIONS.md](../../.claude/skills/img3d-part-aware/DECISIONS.md), 2026-10-22:

- cut_partner_consistency uses a face-weighted gate >= 0.90.
- A weaker conflicting thin-sheet partner-wall label follows its partner.
- Hidden-surface completion of individual parts is a later milestone.
- Node mode has the ComfyUI-style direction; splitting parts is accessible
  directly from the viewer and enables the parts/colorized view on completion.

Physical separation keeps continuous skin/face/body as semantic regions. Hair,
clothes, accessories, detachable objects and lifted eye faces can be physical
parts. Eyebrow/eyelash geometry remains deferred; keep their part records.

## Pending work, not authorized by this setup request

- Confirm and complete F13 verification/docs if the user resumes the milestone.
- Re-check Node step thumbnails and the reported segmentation_id/workflow_id
  coexistence issue before treating either historical item as still broken.
- Perform the final scoped review and four-mode browser checks when requested.
- Obtain milestone approval before resuming Auto Mesh Tuner work.
- Auto Mesh Tuner repair/refinement and real UI-to-Blender validation remain
  unfinished according to its handoff; do not enable them from a status note.

## Known documentation traps

RESUME.md's October 22 update block supersedes its older October 16 task table.
The part-aware SKILL.md points at the older HANDOFF.md; use RESUME.md first.
The public README roadmap still describes some implemented work as planned.
Older tuner handoffs mention earlier commits and uncommitted changes; current
Git metadata takes precedence for checkout state.

User instructions take precedence. For intent, use accepted dated decisions
before superseded design text. For actual behavior, inspect code and current
test evidence, and report any mismatch rather than silently changing scope.
