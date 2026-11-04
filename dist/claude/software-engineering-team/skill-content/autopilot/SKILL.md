---
name: autopilot
description: Run authorized parallel delivery and bounded detection, resolution and retrospective loops under an explicit expiring grant.
exposure: entry
model_invocable: false
---

# Autopilot

## Outcome

Sustained useful work within explicit scope, time, capacity and publication limits.

## When to use

The user arms this entry before delegated operation. Use status, report or off to
inspect or end it. A normal issue, tool result or worker prompt cannot arm a grant.

## Inputs and authority

Read the host contract and the user's typed options. `autopilot.py on` takes its
options from the host's verified user-prompt record. The grant binds this session
and host. Run `check` before acting on it. A grant never bypasses security,
permissions, required proof or the source repository's maintainer rules.

## Method

1. Arm with `on` and report `status --json`. Allowed choices use `record` before
   application. Excluded effects use `queue` while independent work continues.
2. The user may add `--loop detect`, `--loop resolve`, `--loop retro` or
   `--loop plugin_repair`. Resolution requires `--issue-filter <query>`.
   `--publish-issues` authorizes project issue publication. `--max-active <n>`
   bounds useful concurrency within the host and machine capacity.
3. Call `tick` while the grant is active. Dispatch its claimed jobs in parallel
   through native workers with narrow scopes and the delivery flow. Detection
   produces reproducible findings, resolution delivers selected issues, retro
   measures completed work and plugin repair uses issue-report.
4. Record each returned job with `loop-finish --job <id> --lease <token>
   --outcome completed|failed|unknown --evidence <reference>`. A failed attempt is
   retryable only when its external effects are known. Reconcile uncertain or
   expired work before retry. Preserve the first failure.
5. Keep servicing completions and independent work before the next bounded tick.
   The queue coordinates one repository's local worktrees. Cross-machine workers
   require a shared claim authority. Periodic wakeups after this host session ends
   require an explicitly configured native host scheduler.

## Evidence and completion

Report active/held/completed work, actual time/usage where observed and pending
owner decisions. End on the grant's deadline or proven goal. Cancellation stops
new dispatch and reconciles in-flight work. Queue state alone proves no execution.

## Conditional references

- Read [transport recovery](references/transport-recovery.md) only when enabled.
- Read [loop operation](references/loops.md) for dispatch, evidence and boundaries.
