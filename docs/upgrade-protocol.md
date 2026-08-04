# Upgrade protocol

Agent Marketplace Upgrade is the single compatibility entry for Project
Management Office and every installed team. Claude and Codex use the same
engine, plan, database, project UUID, migration ledger, and result. Host
adapters change only the native question and project-instruction surfaces.

## User sequence

1. Update the installed marketplace plugins with the host's normal plugin
   update mechanism.
2. Finish or release active work orders and task attempts. Close other
   Agentrof sessions and leave the project checkout clean.
3. Start a new session at the project git root.
4. Invoke `/project-management-office:upgrade` on Claude or
   `$project-management-office:upgrade` on Codex.
5. Review status. If ready, approve plan creation, inspect the exact plan, and
   approve apply separately.
6. After success, close the session and start another fresh session before
   normal marketplace work.

Skipping stable releases is supported when the installed migration catalogs
contain a continuous step-by-step chain from the recorded contract to the
target contract. Missing steps, reversed versions, host catalog drift, and
downgrades fail closed.

## Status model

| Status | Meaning | Mutation policy |
|---|---|---|
| `AGENTROF_CURRENT` | Runtime, database, and project agree. | Normal work allowed. |
| `AGENTROF_UPGRADE_REQUIRED_READY` | Change is required and preflight is clear. | Upgrade commands only. |
| `AGENTROF_UPGRADE_REQUIRED_BLOCKED` | A safety condition prevents apply. | No mutation; clear blockers first. |
| `AGENTROF_UPGRADE_APPLY_READY` | A fingerprint-bound plan awaits approval. | Exact plan apply or stop. |
| `AGENTROF_UPGRADING` | The maintenance lock has an owner. | No competing mutation. |
| `AGENTROF_UPGRADE_RECOVERY_REQUIRED` | A durable journal is incomplete. | Recorded recovery only. |
| `AGENTROF_UPGRADE_COMPLETE_RESTART_REQUIRED` | Apply completed against pre-upgrade session context. | Fresh session required. |
| `PROJECT_UPGRADE_PR_PENDING` | Managed project changes await a review commit and pull request. | Exact planned git paths only. |

`PROJECT_CONTRACT_DRIFT` identifies a marker-owned field or block changed
outside its owner. It is never repaired silently. A dirty checkout, active or
frozen work, competing session, path collision, symbolic link, missing adapter,
package provenance failure, cross-host version mismatch, insufficient disk,
database integrity error, stale plan, or downgrade also blocks apply.

## Writer boundary

The upgrader may write only:

- the PMO database through a writer-locked candidate validation and
  transactional live migration;
- the global host-aware plugin registry, locks, plans, journals, and backups;
- `.agentrof/project.json` in the consuming project;
- declared machine-owned fields in the team config;
- Agentrof marker blocks in host instruction files;
- Agentrof-owned native project-agent files.

It never overwrites user code, authored documentation, memory, demos, sketches,
secrets, environment files, custom CI, unmarked instruction text, or an
unmanaged file collision. Config updates preserve unknown and user-owned keys.
Instruction adapters replace only their exact marker block. Project files have
before-images in the journal and are restored if their phase fails.

After apply, the guard admits only read-only diff inspection, exact planned-file
staging, and the fixed upgrade commit while `PROJECT_UPGRADE_PR_PENDING` is
active. Other marketplace mutations remain locked. Once that commit changes the
project revision, the upgrade pull request can be opened through the normal host
workflow. Normal work resumes only from the merged revision in a fresh session.

## Database safety and recovery

The engine acquires the database writer lock before taking an online backup and
separate candidate. Each migration step has an immutable id, source and target
schema, checksum, component version, and source fingerprint. The candidate must
pass its full chain, foreign-key check, integrity check, PMO content stamp, and
writer-epoch installation before the same migration commits to the live database
as one transaction. The schema migration ledger is exactly-once evidence, not a
reason to skip checksum validation.

The journal advances by durable phases. Failure before database commit rolls
back without entering recovery. Failure after commit preserves maintenance
mode, backup, before-images, and run id. The recovery command resumes only that
recorded plan. It does not invent a new chain or delete evidence.

## If plugins were updated but upgrade was not run

Session hooks and team PreToolUse guards recompute status from disk. Read-only
diagnostics and the upgrade entry remain available. Normal marketplace Write,
Edit, patch, and shell mutations stop with the exact status and guidance. This
lock is mechanical and does not depend on the agent remembering a warning.
