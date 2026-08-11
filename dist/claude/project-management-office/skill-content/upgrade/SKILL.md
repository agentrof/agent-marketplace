---
name: upgrade
description: Safely inspect, plan, apply, or recover an Agent Marketplace upgrade across the PMO backbone, installed teams, database, and managed project surfaces.
exposure: entry
---

# Agent Marketplace Upgrade

Upgrade the installed marketplace contract without overwriting user-owned project content.

## When to Use

- After updating one or more Agent Marketplace plugins.
- When a session reports an `AGENT_MARKETPLACE_UPGRADE_*` status.
- To resume a run that reports recovery required.

## Procedure

1. Apply the active host contract and work from the project git root. Resolve
   the PMO launcher at
   `${AGENT_MARKETPLACE_HOME:-${AGENTROF_HOME:-$HOME/.agentrof}/agent-marketplace}/bin/pmo_cli.py`; missing launcher means
   stop and repair the PMO installation without touching the project.
2. Before any CLI call, present one explicit user choice through the choice gate
   with this canonical, host-neutral copy:
   - question: `Are the upgrade prerequisites complete?`
   - description: `Confirm that marketplace components are updated, active
     PMO work is complete, the repository is clean, and the checkout is on its
     default branch. Approval runs read-only checks and prepares the required
     upgrade branch automatically.`
   - `Ready (Recommended)`: run read-only preflight and prepare the upgrade
     branch only if every check passes;
   - `Cancel`: stop without running an upgrade or branch command.
   Continue only for the explicit `Ready (Recommended)` selection. `Cancel`,
   free-form input, or any other response stops without a command. Do not add
   host or platform names to this canonical copy.
3. Run `upgrade status --project-root <git-root> --json`. Read its status,
   reasons, blockers, guidance, installed component versions, and fingerprint.
   This read is authoritative; do not infer readiness from plugin UI state.
4. Present the status through one owner gate:
   - current: explain that no upgrade is needed and stop;
   - required ready: recommend preparing the deterministic plan now;
   - required blocked: list every blocker and the ordered, non-destructive
     actions needed to clear it, then stop. When the only blocker is
     `UPGRADE_BRANCH_REQUIRED:<target>`, run `upgrade prepare-branch
     --project-root <git-root>`, verify that it reports
     `AGENT_MARKETPLACE_UPGRADE_BRANCH_PREPARED` with an upgrade-ready next
     status, then continue in the same session. Do not run raw branch-creation
     commands. For `UPGRADE_TARGET_REQUIRED:<target>`, show the exact command
     that returns to the target and stop;
   - recovery required: recommend recovery before every other marketplace
     action and show the recorded run id;
   - restart required: tell the user to close this session and start a fresh
     one before normal work.
5. On approval, run `upgrade plan --project-root <git-root>`. If it reports
   `AGENT_MARKETPLACE_UPGRADE_CHOICE_REQUIRED`, show every exact preview and
   present its preserve, discard, and abort options through the choice gate.
   Abort stops without a plan. Otherwise rerun `upgrade plan` with every exact
   `--choice <id>=<option>` selection. Show the database transition, component
   transitions, managed project files, backup policy, resolved choices, and
   plan id. A plan performs no project or database mutation.
6. Ask one final apply gate. On approval, run `upgrade apply --plan-id
   <plan-id>`. Never recreate, edit, or bypass the plan manually. If the source
   fingerprint changed, discard the plan and return to step 2.
7. If apply reports recovery required, run only `upgrade recover --run-id
   <run-id>` after owner approval. Preserve the journal and backup evidence.
8. On success, report the run id, backup location, changed managed surfaces,
   and `AGENT_MARKETPLACE_UPGRADE_COMPLETE_RESTART_REQUIRED`. Stop without
   staging: close the pre-upgrade session and start a fresh session on the same
   upgrade branch. That session must report
   `AGENT_MARKETPLACE_PROJECT_UPGRADE_PR_PENDING`; review `git diff --stat`,
   stage only the plan's `project_files` with `git add -- <exact paths>`, and
   commit exactly `chore: apply Agent Marketplace upgrade`. Push that branch
   and open the project upgrade pull request when the repository has a
   configured remote. Normal marketplace work stays locked on the upgrade
   branch after commit. After merge, update the target branch and start a fresh
   session from that merged revision; only that revision may report current.
9. Contract v3 upgrades inspect `workspace/docs/` as one vault. If the new
   project contract records `vault.status: pending`, install the tracked
   portable gate with the team `vault_gate.py install` verb, run
   `vault_check.py adoption-plan --vault workspace/docs`, and show its exact
   plan hash and every finding. Unknown content is never moved automatically.
   After the owner-approved repair/migration plan is green, run
   `render-navigation`, `render-relations`, the portable full gate, then
   `activate-adoption --project-root <root> --plan-hash <exact-green-hash>`.
   Until activation, `deliver`, `delivery-lanes` and `backlog-plan` remain
   mechanically blocked.

## Safety Contract

- User code, authored documentation, host user companions, `me.md`,
  `profile.md`, nested instruction files, demos, sketches, secrets,
  environment files, and custom CI are outside upgrade ownership and are never
  overwritten. Legacy unmanaged root instructions move only through an
  explicit preserve choice or are dropped only through an explicit discard
  choice.
- A dirty checkout, active PMO work in any project, freeze manifest, unmanaged
  collision, symbolic-link target, contract drift, database integrity failure,
  stale plan, downgrade, or incomplete prior run blocks mutation.
- Repositories with an origin remote apply from an `agent-marketplace/upgrade-*`
  branch prepared by the PMO from the resolved default branch. The project
  remains PR-pending until its configured target branch contains the exact
  managed upgrade identity. A feature-branch commit alone never unlocks normal
  work.
- Database changes run through ordered, checksummed migrations against a
  candidate copy while a writer lock protects the source. The live migration
  commits as one transaction only after candidate integrity and foreign-key
  checks pass. Project changes use recorded before-images and rollback on error.
- Skipped releases are valid only when the installed migration catalog contains
  a complete ordered chain from the recorded version to the target version.
- The same canonical plan governs every installed host package. A host version
  mismatch or missing adapter blocks the complete upgrade.
