---
name: organize-docs
description: Repair and curate the tracked project-local Obsidian documentation vault.
exposure: entry
---

# Organize Docs

Restore the tracked `workspace/docs/` vault to the Obsidian policy. This entry
repairs documentation only.

## When to Use

- The owner asks for a full naming, link, map, graph, or payload repair pass.
- Setup or a docs-producing flow reports a vault finding.
- A scoped repair is larger than one document and should be reviewed together.

## Procedure

1. Read `workspace/config.json` and confirm that the single team owns it. Read
   the `obsidian-vault` skill completely before writing.
2. Run the read-only inventory:

   ```text
   vault_check.py check --vault workspace/docs --json
   vault_check.py normalize --vault workspace/docs --rename --dry-run --json
   ```

   Run the packaged `vault_check.py`.

3. Present the rename and designation plan. Preserve user-owned prose and ask
   for approval before applying bulk renames or designation changes.
4. Apply deterministic repairs through `vault_check.py normalize` and
   `reconcile-designations`. Curate ambiguous titles, aliases, maps, and home
   links in the current session. All authored backlog, epic, story, review and
   test-plan content remains ordinary Markdown under Git.
5. Render generated views and run a full check. Every residual must name its
   file and reason.

## Hard Scope

- Durable documentation remains inside the tracked project vault. Local
  scratch state stays disposable.
- Never rewrite approved meaning without an explicit owner decision.
- Do not create or modify delivery artifacts; backlog preparation ends at the
  approved Markdown backlog and its test plans.
