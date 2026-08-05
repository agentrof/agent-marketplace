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

1. Apply the active host contract. Work only from a newly started session at
   the project git root. Resolve the PMO launcher at
   `${AGENT_MARKETPLACE_HOME:-${AGENTROF_HOME:-$HOME/.agentrof}/agent-marketplace}/bin/pmo_cli.py`; missing launcher means
   stop and repair the PMO installation without touching the project.
2. Run `upgrade status --project-root <git-root> --json`. Read its status,
   reasons, blockers, guidance, installed component versions, and fingerprint.
   This read is authoritative; do not infer readiness from plugin UI state.
3. Present the status through one owner gate:
   - current: explain that no upgrade is needed and stop;
   - required ready: recommend preparing the deterministic plan now;
   - required blocked: list every blocker and the ordered, non-destructive
     actions needed to clear it, then stop. For each `blocking_sessions`
     record, ask the owner to close that exact host session. Only after the
     owner confirms it is closed may you run `upgrade session-release
     --session-id <id> --confirm-closed`. For
     `UPGRADE_BRANCH_REQUIRED:<target>`, recommend creating
     `agent-marketplace/upgrade-<UTC compact timestamp>` with the exact
     `git switch -c` command, then starting a fresh session on that branch;
   - recovery required: recommend recovery before every other marketplace
     action and show the recorded run id;
   - restart required: tell the user to close this session and start a fresh
     one before normal work.
4. On approval, run `upgrade plan --project-root <git-root>`. Show the exact
   database transition, component transitions, managed project files, backup
   policy, and plan id. A plan performs no project or database mutation.
5. Ask one final apply gate. On approval, run `upgrade apply --plan-id
   <plan-id>`. Never recreate, edit, or bypass the plan manually. If the source
   fingerprint changed, discard the plan and return to step 2.
6. If apply reports recovery required, run only `upgrade recover --run-id
   <run-id>` after owner approval. Preserve the journal and backup evidence.
7. On success, report the run id, backup location, changed managed surfaces,
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

## Safety Contract

- User code, authored documentation, memory, demos, sketches, secrets,
  environment files, custom CI, and unmanaged instruction content are outside
  upgrade ownership and are never overwritten.
- A dirty checkout, active work order, running task attempt, competing session,
  freeze manifest, unmanaged collision, symbolic-link target, contract drift,
  database integrity failure, stale plan, downgrade, or incomplete prior run
  blocks mutation.
- Repositories with an origin remote apply from an `agent-marketplace/upgrade-*`
  branch. The project remains PR-pending until its configured target branch
  contains the exact managed upgrade identity. A feature-branch commit
  alone never unlocks normal work.
- Database changes run through ordered, checksummed migrations against a
  candidate copy while a writer lock protects the source. The live migration
  commits as one transaction only after candidate integrity and foreign-key
  checks pass. Project changes use recorded before-images and rollback on error.
- Skipped releases are valid only when the installed migration catalog contains
  a complete ordered chain from the recorded version to the target version.
- The same canonical plan governs every installed host package. A host version
  mismatch or missing adapter blocks the complete upgrade.
