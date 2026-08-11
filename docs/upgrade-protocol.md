# Upgrade protocol

Agent Marketplace Upgrade is the single compatibility entry for Project
Management Office and every installed team. Claude and Codex use the same
engine, plan, database, project UUID, migration ledger, and result. Host
adapters change only the native question and project-instruction surfaces.

## User sequence

1. Update the installed marketplace plugins with the host's normal plugin
   update mechanism.
2. Finish or release active PMO work across every project and leave the project
   checkout clean on its resolved default branch.
3. Invoke `/project-management-office:upgrade` on Claude or
   `$project-management-office:upgrade` on Codex.
4. Approve the host-neutral prerequisite gate. Status then runs read-only. When
   the default-branch requirement is its only blocker, PMO creates the
   `agent-marketplace/upgrade-*` branch and continues in the same session.
5. Review status. If ready, approve plan creation, inspect the exact plan, and
   approve apply separately.
6. After apply, close the session. In a fresh session on the same branch,
   review, commit and push only the planned files, then open the upgrade pull
   request. Normal marketplace work remains locked until the target branch
   contains that revision and a fresh session starts there.

Skipping stable releases is supported when the installed migration catalogs
contain a continuous step-by-step chain from the recorded contract to the
target contract. Missing steps, reversed versions, host catalog drift, and
downgrades fail closed.

## Status model

| Status | Meaning | Mutation policy |
|---|---|---|
| `AGENT_MARKETPLACE_CURRENT` | Runtime, database, and project agree. | Normal work allowed. |
| `AGENT_MARKETPLACE_UPGRADE_REQUIRED_READY` | Change is required and preflight is clear. | Upgrade commands only. |
| `AGENT_MARKETPLACE_UPGRADE_REQUIRED_BLOCKED` | A safety condition prevents apply. | No mutation; clear blockers first. |
| `AGENT_MARKETPLACE_UPGRADE_APPLY_READY` | A fingerprint-bound plan awaits approval. | Exact plan apply or stop. |
| `AGENT_MARKETPLACE_UPGRADING` | The maintenance lock has an owner. | No competing mutation. |
| `AGENT_MARKETPLACE_UPGRADE_RECOVERY_REQUIRED` | A durable journal is incomplete. | Recorded recovery only. |
| `AGENT_MARKETPLACE_UPGRADE_COMPLETE_RESTART_REQUIRED` | Apply completed against pre-upgrade session context. | Fresh session required. |
| `AGENT_MARKETPLACE_PROJECT_UPGRADE_PR_PENDING` | Managed changes are not yet on the configured target branch. | Exact review, commit, push and PR operations only. |

`PROJECT_CONTRACT_DRIFT` identifies a marker-owned field or block changed
outside its owner. It is never repaired silently. A dirty checkout, active PMO
work in any project, frozen work, path collision, symbolic link, missing
adapter, package provenance failure, cross-host version mismatch, insufficient
disk, database integrity error, stale plan, or downgrade also blocks apply.

Package provenance covers authored distribution files exactly. Claude may add
host-owned `.in_use/<pid>` or `.in_use/<pid>.tmp.<8-hex>` cache markers after
installation. Only regular, non-symlink marker files with an empty body or the
matching bounded `pid`/`procStart` JSON shape are excluded. The same path on
another host, a nested marker, an unknown filename, malformed content, a PID
mismatch, or any other unlisted file remains a provenance blocker.

Session readiness still guards normal marketplace mutations, but session
records do not participate in upgrade readiness. Upgrade safety derives from
global PMO work state, repository preflight, migration journals and writer
locks.

The PMO-owned `upgrade prepare-branch --project-root <git-root>` command runs
only when `UPGRADE_BRANCH_REQUIRED:<target>` is the sole blocker. It creates a
UTC-named branch from the resolved default branch, verifies the unchanged base
HEAD, and recomputes status. Raw branch creation is not an upgrade exception.

## Writer boundary

The upgrader may write only:

- the PMO database through a writer-locked candidate validation and
  transactional live migration;
- the global host-aware plugin registry, locks, plans, journals, and backups;
- `.agentrof/agent-marketplace/project.json` in the consuming project;
- declared machine-owned fields in the team config;
- Agent Marketplace marker blocks in host instruction files;
- Agent Marketplace-owned native project-agent files.

It never overwrites user code, authored documentation, memory, demos, sketches,
secrets, environment files, custom CI, unmarked instruction text, or an
unmanaged file collision. Config updates preserve unknown and user-owned keys.
Instruction adapters replace only their exact marker block. Project files have
before-images in the journal and are restored if their phase fails.

All global runtime state is rooted at `AGENT_MARKETPLACE_HOME` when set,
otherwise at `${AGENTROF_HOME:-$HOME/.agentrof}/agent-marketplace`. The PMO
database is `pmo.db`; logs, sessions, locks, plugin roots, plans, journals and
backups remain inside that product root.

After apply, the pre-upgrade session admits no finalization work. A fresh
session on the same upgrade branch admits only read-only diff inspection, exact
planned-file staging, the fixed upgrade commit, its branch push and pull-request
creation while `AGENT_MARKETPLACE_PROJECT_UPGRADE_PR_PENDING` is active. Other
marketplace mutations remain locked. A feature-branch commit does not clear the
status. Normal work resumes only when the configured target branch's project
state contains the exact managed upgrade identity. Descendant work branches then
remain current instead of re-entering PR-pending state.

## Database safety and recovery

The engine acquires the database writer lock before taking an online backup and
separate candidate. Each migration step has an immutable id, source and target
schema, checksum, component version, and source fingerprint. The candidate must
pass its full chain, foreign-key check, integrity check, PMO content stamp, and
writer-epoch installation before the same migration commits to the live database
as one transaction. The schema migration ledger is exactly-once evidence, not a
reason to skip checksum validation.

The journal advances by durable phases. Failure before database commit or
project application rolls back without entering recovery. Once project
application begins, any failure enters recovery even when the adapter restores
its before-images or no database schema migration was needed. This conservative
boundary covers partial adapter writes and the following identity sync. Failure
after that boundary preserves maintenance mode, backup, before-images, and run id.
The recovery command resumes only that recorded plan. It does not invent a new
chain or delete evidence.

The upgrade lock records its host and process id. Recovery reclaims it only
when the owner belongs to the same host and the operating system proves that
process no longer exists. A live, foreign-host or unreadable owner remains
fail-closed.

## If plugins were updated but upgrade was not run

Session hooks and team PreToolUse guards recompute status from disk. Read-only
diagnostics and the upgrade entry remain available. Normal marketplace Write,
Edit, patch, and shell mutations stop with the exact status and guidance. This
lock is mechanical and does not depend on the agent remembering a warning.
