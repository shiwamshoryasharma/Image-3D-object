---
name: img3d-project-context
description: Orient or resume work in the Img3D project, locate existing implementation, and reconcile Codex memory with dated Claude handoffs and accepted milestone decisions. Use for project navigation or resuming Img3D work.
---

# Img3D project context

Read [durable memory](../../MEMORY.md) and, when resuming implementation,
[status](../../memory/status.md). Use [the project map](../../memory/project-map.md)
to locate code before changing it.

## Resolve context economically

- Read the requested area's code, its tests and only the matching .claude
  contract/design sections. Use rg for section headings before reading large docs.
- The newest dated block in
  [RESUME.md](../../../.claude/skills/img3d-part-aware/RESUME.md) is the latest
  handoff. Its older task table and the public README roadmap can be stale.
- Read [DECISIONS.md](../../../.claude/skills/img3d-part-aware/DECISIONS.md)
  before choosing part policy, providers, gates or tuning scope.
- Existing progress reports are evidence of prior work; do not infer current
  passing behavior merely from file presence or a historical test count.
- Do not relaunch historical multi-agent workflows as a side effect of reading
  their documents. Follow the current request and current delegation rules.

## Preserve project boundaries

Use the backend, frontend, validation or environment skill for that area.
Keep original reconstruction and reference behavior intact. Part-awareness is
additive. Tuning stays paused pending milestone approval; documentation setup
does not grant that approval or authorize resuming implementation.

Before editing, inspect current Git status and preserve unrelated changes.
The initial 2026-10-29 inspection found a pre-existing modified RESUME.md.

If the user requests publishing, read
[the existing publishing skill](../../../.claude/skills/img3d-repo-publishing/SKILL.md)
for ignore/privacy/identity/license requirements. Do not publish input images,
models, runtimes, secrets, private prompts or machine-local notes.

## Maintain useful memory

Keep durable rules in ../../MEMORY.md, navigation in ../../memory/project-map.md,
and dated checkpoints in ../../memory/status.md. Update only facts established
by source inspection, actual checks or explicit user decisions. Label historical
results and unresolved assumptions. Link detailed .claude work rather than
copying whole design documents. Test helpers and reports belong under test,
not inside skill directories.
