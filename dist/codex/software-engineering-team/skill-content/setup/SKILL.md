---
name: setup
description: Project bootstrap and environment reconciliation for the software-engineering-team. Attaches a clone to the tracked contract and materializes machine-local runtime and host projections without rewriting user content.
exposure: entry
---

# Setup

Use one public entry for a fresh repository, a new clone, or an environment
that no longer matches the tracked project contract.

## When to Use

- First run after installing the team in a repository.
- First run after clone, pull, host change, or Marketplace component update.
- Recovery from `AGENT_MARKETPLACE_ENVIRONMENT_RECONCILE_REQUIRED`.

## Procedure

Use a choice gate for every owner decision. Recommended options come first and
include tradeoffs. Anchor every path at the resolved git root.

1. Resolve PMO and the marketplace dispatcher through the host contract. A
   repository is required; offer initialization when none exists. Register
   plugin roots and run PMO `ensure` before reading environment state.
2. Run `project environment-status --project-root <root> --json`.
   - `AGENT_MARKETPLACE_CURRENT`: setup is idempotent; report current.
   - `AGENT_MARKETPLACE_FRESH_SETUP_REQUIRED`: continue with fresh setup.
   - `AGENT_MARKETPLACE_ENVIRONMENT_RECONCILE_REQUIRED`: update any named
     older or missing installed component, start a fresh session, then rerun
     setup. When only attach or projection drift remains, run `project attach
     --project-root <root> --workspace <workspace> --json`.
   - `AGENT_MARKETPLACE_RECONCILE_DEFERRED_ACTIVE_WORK`: list every project,
     type, key, and worktree. Start no new managed work. Close the named task,
     plan, or Experience run, or checkpoint the named work order through its
     owning flow, then rerun setup.
   - `AGENT_MARKETPLACE_PROJECT_UPGRADE_CHOICE_REQUIRED`: present `Upgrade
     Project (Recommended)` and `Cancel`. Upgrade uses the existing upgrade
     entry; setup never silently upgrades or downgrades the project.
   - Unsupported, corrupt, identity-conflicting, or contract-drift state stops
     fail-closed.
3. For fresh setup, run `setup_check.py preflight --project-root <root>
   --workspace <name> --json` before the first write. A foreign workspace gets
   an owner-chosen alternative lower-kebab name used consistently.
4. Materialize both portable host instructions, the active machine-local host
   projection, and memory templates through the host contract with `--scope
   all`. User-owned
   companions, `me.md`, and `profile.md` are seeded only when missing.
5. Reconcile `.gitignore` by marker ownership. Preserve every user line and
   write the product contract's runtime and host-projection root ignores,
   substituting `{{project_local_ignores}}` with one anchored ignore per root,
   followed by the workspace test and vault rules. A missing
   half-marker or duplicate marker fails closed. Verify each root with
   `git check-ignore --no-index` and reject any force-added file below it.
6. Create only missing top-level structure: apps; environment; demos; sketches;
   and docs with maps, business-analysis, solution-design,
   system-architecture, design-system/pages, and experience-design. Add
   `.gitkeep` only to empty tracked directories. Runtime paths are lazy.
7. Build `<workspace>/config.json` interactively. Detect stack values before
   asking for gaps. Require commands, repo-relative source directories,
   output_language, terminology_language, and scale. Ask `project_origin`
   explicitly and write it only through `project_config.py set-origin`.
8. Materialize the base vault payload without overwriting existing Obsidian
   values. Reconcile owner-approved output-language designations through
   `vault_check.py reconcile-designations`.
9. Register PMO with `project register --key <key> --name <name> --team
   software-engineering-team --stamp-config <config> --project-root <root>
   --workspace <name>`. Registration preserves top-level `project_key` and
   atomically writes nested project contract v5 with its hash.
10. Install the tracked portable gate with `vault_gate.py install
    --project-root <root>`. The output is
    `.github/agentrof/vault-gate.pyz`.
11. Materialize CI through `references/ci-bootstrap.md`, replacing
    `{{test_command}}`, `{{audit_command}}`, and `{{env_command}}`, then close
    in order: config check; PMO identity; contract hash; host instructions; local
    projection; effective ignore and force-add checks; vault renders; portable
    gate; preparation status; setup check; and an empty setup preview.
12. Run `project attach --project-root <root> --workspace <name> --json` and
    require `AGENT_MARKETPLACE_CURRENT`. Start a fresh session so local agents,
    hooks, instructions, and `contract_sha256_at_start` load together.
13. For greenfield print `business-analysis -> solution-design -> design-system
    -> experience-design -> backlog-plan`. For existing projects print the
    scoped `deliver` route.
