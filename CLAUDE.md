# Agent Marketplace, Repo Rules

1. **Purpose**: generic Claude Code marketplace of agents and skills, built and maintained through six primitive skills.
2. **Language**: artifacts are English. User chat may match the user's language.
3. **Caveman**: terse, bullets, no filler, no preambles, no trailing summaries.
4. **No assumptions**: never invent facts, paths, ids, or data. Ask the user when uncertain.
5. **No em dash**: never use the U+2014 character in any output.
6. **No hardcoded paths**: every path in an artifact is repo-root-relative; scripts derive root from `__file__` or env var.
7. **Layout**: `.claude/agents/<id>.md` for subagents; `.claude/skills/<id>/` for skills; `.run/<uuid>/` for execution workspaces (gitignored).
8. **Agent shape**: required frontmatter `name`, `description`; optional `tools`, `model`. Single file.
9. **Skill shape**: required files `SKILL.md`, `README.md`, `manifest.yaml`. Required folders `scripts/`, `assets/`, `references/`, `examples/` (empty kept via `.gitkeep`).
10. **Folder semantics**: `scripts/` = invoked code; `assets/` = copied verbatim; `references/` = loaded into context; `examples/` = human study.
11. **Manifest**: `id`, `kind`, `version`, `description` required; `tags`, `depends_on` optional. No `provides`, no typed inputs/outputs in Phase 1.
12. **Dependencies**: every mutation runs deterministic mechanical scans (dep, content, path) plus advisory semantic review. Impact report saved to `.run/<uuid>/artifacts/impact-report.md` and shown in chat. User approval required before any write.
13. **Run workspace**: every agent or skill invocation that produces work creates `.run/<uuid4>/` with `META.md` (frontmatter `task_id`, `component`, `kind`, `started`, `ended`, `status` plus body sections `## Inputs`, `## Outputs`, `## Artifacts`) and an `artifacts/` subfolder. Chat opens "Task `<short>` started" and closes "Task `<short>` done".
14. **Deep primitives epistemic loop**: `create-skill`, `create-agent`, `update-skill`, `update-agent` run an iterative multi-persona reasoning loop before writing. Fixed intensity 10 iterations, 10 questions, 10 personas, 10 critics, early-exit on consensus. No tiers. Loop output persists under `.run/<uuid>/artifacts/iter-N/`. Simulation-based; true parallel diversity is Phase 2.
15. **Primitives provided**: `create-agent`, `create-skill`, `list-agents`, `list-skills`, `update-agent`, `update-skill`.
16. **Adding entries**: invoke `create-skill` or `create-agent` in dialog; the skill asks for missing inputs, runs the epistemic loop, presents synthesized design for approval, then writes to `.claude/`.
17. **Scope**: Phase 1 is Claude-only and ships only the six primitives plus rules. Orchestration, inter-component messaging, workflow chaining, multi-IDE shims, registry, delete operations, runner-based parallelism, and example user-domain components are deferred.
