# Img3D Codex entry point

Project root: E:/14-OCT-2026/Img3D. Created 2026-10-29.

## Starting a session

Read [MEMORY.md](MEMORY.md), then the skill relevant to the request below. Read
[memory/status.md](memory/status.md) before resuming milestone work, and
[memory/project-map.md](memory/project-map.md) when locating an implementation.

This entry point and the skills are stored inside .codex at the user's request.
Do not assume .codex/AGENTS.md is automatically loaded for the repository root,
or that .codex/skills is automatically included in a session's skill catalog.
A future session can bootstrap with:
"Read E:/14-OCT-2026/Img3D/.codex/AGENTS.md and follow its project guidance."
These are saved project files, not a claim of automatic or cross-chat memory.
No global skill installation or repository-root instruction change was made.

## Skills

| Request | Read |
|---|---|
| Understand the project, resume work, reconcile older notes | [img3d-project-context](skills/img3d-project-context/SKILL.md) |
| Change backend APIs, jobs, workers, inference or parts | [img3d-backend](skills/img3d-backend/SKILL.md) |
| Change UI modes, state, viewer or workflow editor | [img3d-frontend](skills/img3d-frontend/SKILL.md) |
| Run tests, validate generation, use sample images | [img3d-validation](skills/img3d-validation/SKILL.md) |
| Diagnose packages, embedded Python, CUDA or model loading | [img3d-environment](skills/img3d-environment/SKILL.md) |

Read only the relevant skills and sections of the existing .claude references.
Those files contain extensive prior work; preserve them rather than regenerating
the architecture, scaffold, model environment or implementation.

## Scope and persistent user preferences

- Act as a direct, technical senior engineer. Inspect code before proposing changes.
- Make the smallest practical change; reuse existing components and dependencies.
- Model-generation and validation input images come from test/images.
- All testing scripts, configuration, caches, logs, builds and outputs stay under test.
- Generate no images unless the user explicitly requests them.
- This setup request does not authorize resuming a milestone, enabling tuning,
  installing packages, downloading weights, committing or publishing changes.
- Save Codex-specific knowledge here; keep executable testing helpers under test.
- Preserve unrelated working changes, including existing .claude updates.

## Maintaining these notes

Update the small relevant memory file when evidence or user decisions change.
Record the date, source, and verification level. Distinguish source inspection,
historical reports, and tests actually run. Do not retain secrets or private
account/network details here; consult the existing local private notes only
when the task requires them.
