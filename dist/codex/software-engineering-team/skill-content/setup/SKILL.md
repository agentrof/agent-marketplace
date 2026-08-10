---
name: setup
description: Fresh, idempotent project bootstrap for the software-engineering-team. Creates the workspace and machine-managed contract only when no project key or prior contract exists; keyed projects use Agent Marketplace Upgrade.
exposure: entry
---

# Setup

Stand up a fresh project contract without overwriting user content. This entry
is not an upgrade path.

## When to Use

- First run after installing the team in a repository.
- Rerun inside the still-unkeyed bootstrap window to complete missing setup.

## Procedure

Use a choice gate for every owner decision. Recommended options come first and
include tradeoffs. Anchor every path at the resolved git root.

1. Resolve PMO and the marketplace dispatcher through the host contract. A
   repository is required; offer initialization when none exists. Register
   plugin roots and run the idempotent PMO ensure when launchers are missing.
2. Run `setup_check.py preflight --project-root <root> --workspace <name>
   --json` before the first write. Fresh setup requires a matching or absent
   team config, no `project_key`, no project contract and no foreign
   managed-team trace. A keyed config or old contract routes to Agent
   Marketplace Upgrade and stops. A foreign `workspace/` gets an owner-chosen
   alternative name used consistently in paths and templates.
3. Materialize host instructions and memory templates only where missing.
   User-supplied `me.md` and `profile.md` are copied verbatim. Materialize the
   base vault payload with `vault_check.py materialize-payload --vault
   <workspace>/docs`; existing `.obsidian` values are never overwritten.
4. Reconcile `.gitignore` by marker ownership. Preserve every user line and
   old unmarked ignore. Create or idempotently replace exactly:

   ```text
   # agent-marketplace:software-engineering-team:gitignore:start
   .agentrof/agent-marketplace/.runtime/
   <workspace>/junit-*.xml
   <workspace>/docs/.obsidian/*
   !<workspace>/docs/.obsidian/app.json
   !<workspace>/docs/.obsidian/appearance.json
   !<workspace>/docs/.obsidian/core-plugins.json
   !<workspace>/docs/.obsidian/graph.json
   !<workspace>/docs/.obsidian/types.json
   !<workspace>/docs/.obsidian/snippets/
   !<workspace>/docs/.obsidian/snippets/**
   <workspace>/docs/.obsidian/workspace.json
   <workspace>/docs/.obsidian/workspace-mobile.json
   <workspace>/docs/.trash/
   # agent-marketplace:software-engineering-team:gitignore:end
   ```

   A missing half-marker or duplicate marker fails closed.
5. Create only missing top-level structure: apps; environment; demos; sketches;
   and docs with maps,
   business-analysis, solution-design, system-architecture, design-system/pages
   and experience-design. Add `.gitkeep` only to empty tracked directories.
   Setup never creates `.agentrof/agent-marketplace/.runtime/`; backlog-plan
   and work-order flows create their own runtime paths lazily. Setup never
   creates analysis topics, experience programs or releases. The Experience
   Design map is born with its first program.
6. Build `<workspace>/config.json` interactively. The first key is
   `team_id: software-engineering-team`. Detect stack values before asking for
   gaps. Supported enums remain python-fastapi, react-typescript, sql/nosql and
   docker-compose. Require test, mutation and environment commands, repo-relative
   source directories, output_language, terminology_language and scale.
   Scale is small, medium, large, x-large, xx-large or enterprise.
7. Ask `project_origin` explicitly as `greenfield` or `existing`; never infer
   it. Set it only with `project_config.py set-origin --config <config>
   --origin <choice>`, because direct edits are hook-denied. Configure may
   change it before program, backlog or delivery state exists. It is immutable
   afterward. Upgrade writes `unclassified`, which must be classified before
   delivery.
8. Present one output-language designation per vault taxonomy type, including
   experience, program, release, journey, flow-set and screen. Write the
   approved map only through `vault_check.py reconcile-designations`; direct
   config edits are hook-denied. Unsupported stacks or colliding designations
   stop honestly.
9. Register PMO with `project register --key <key> --name <name> --team
   software-engineering-team --stamp-config <config> --project-root <root>
   --workspace <name>`. Registration stamps `project_key`, creates the project
   UUID and project contract version 4, records the vault policy 5 active
   state, and records managed-surface hashes.
10. Install the repository-portable gate with `vault_gate.py install
    --project-root <root>`. The resulting tracked executable is
    `.agentrof/agent-marketplace/checks/vault-gate.pyz`; both hosts and CI run
    the exact same gate from that path.
11. Materialize CI through `references/ci-bootstrap.md`, replacing
    `{{test_command}}`, `{{audit_command}}` and `{{env_command}}` before
    writing.
12. Close in order: `project_config.py check`; PMO registration; contract
    version; host instructions and generated project agents; gitignore markers;
    `vault_check.py render-navigation`; `vault_check.py render-relations`;
    the portable `vault-gate.pyz check --project-root <root> --json`;
    `preparation_check.py status --project-root <root>
    --json`; `setup_check.py check`; then rerun the setup preview and require an
    empty diff. Pre-existing vault degradation routes to organize-docs; setup
    authored findings are setup bugs.
13. For greenfield print `business-analysis -> solution-design -> design-system
    -> experience-design -> backlog-plan`. For existing projects print the
    scoped `deliver` route.
