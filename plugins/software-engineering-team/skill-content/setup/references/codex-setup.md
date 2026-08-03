# Codex Setup Adapter

Apply these rules only on Codex:

1. Run setup only in Code or Default mode. In Plan mode, stop without
   writing and ask the user to switch modes.
2. Before any write, require `AGENTROF_HOOKS_ACTIVE` in session context.
   If absent, ask the user to inspect and trust both plugins with `/hooks`,
   then start a new task.
3. If PMO is absent, stop with this exact remediation:
   `codex plugin add project-management-office@agent-marketplace`.
4. Map each Claude `AskUserQuestion` popup to one concise, option-preserving,
   turn-ending question. Resume from durable PMO/workspace state.
5. After regular templates exist, run `"$RUN" run "$TEAM"
   scripts/generate_codex_project.py --project-root <git-root> --workspace
   <chosen-workspace-name>`.
6. The generator owns only its marked `AGENTS.md` block and Agentrof-marked
   `.codex/agents/*.toml`. Stop on an unmanaged same-name file.
7. Finish by telling the user that custom agents load at a fresh task/session
   boundary and that they must start one before another team entry.
