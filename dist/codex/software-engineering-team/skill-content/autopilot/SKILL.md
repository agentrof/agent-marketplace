---
name: autopilot
description: Let the orchestrating session take the recommended option at every question of an allowed class for a stated time or until a goal, record each decision and queue every other question.
exposure: entry
---

# Autopilot

A grant the user arms before an absence, such as a night or a weekend. While
it is active the orchestrating session presents no question: it takes the
recommended option of every question in an allowed class and records it, and
it queues every other question and continues the work that does not depend on
it. The grant is ignored runtime state under
`.agentrof/agent-marketplace/.runtime/autopilot/`; only the decisions it takes
reach tracked documents. Without a grant nothing changes.

## When to Use

- Before the work runs unattended, so a flow does not stop at a question whose
  recommended option the user would pick.
- To see, end or report the grant when the user is back.

## Invocation

Type the entry in the host's own command spelling, which the host contract
names, followed by one verb:

- `on`: a grant for the default duration.
- `on --for 9h`, `on --for 1h30m` or `on --until 07:00`: time-bound. `--until`
  also takes an ISO time; a time without an offset is the local clock.
- `on --goal delivery:DLV-002`: until the owning compiler reads the Delivery
  as merged or cancelled. `--goal requirement:REQ-005` ends when the
  Requirement is incorporated into the approved backlog, resolved with no
  change, superseded or withdrawn. `--goal text:"finish the migration"` ends
  only through `complete`, `off` or its cap. A goal-bound grant without
  `--for` or `--until` ends at the latest at the default goal cap; with one,
  at the earlier of that time and its goal.
- `on --allow release,phase_start` adds excluded classes; `on --deny merge`
  drops a default one. `on` while a grant is active replaces it.
- `status`: the remaining time, the goal and its current state, the classes,
  the counts and the guards this host runs.
- `complete --evidence "<what shows the goal is reached>"`: ends the grant.
- `off`: revokes the grant and prints the report. `report`: the report alone.

Each class is `allowed` or `excluded` by default, or `never`, which no grant
can allow. The classes, goal kinds, default duration, default goal cap and
maximum duration are data in `data/autopilot-policy.json`.

## Only the user arms a grant

Starting, extending or widening a grant needs the user's own typed entry
command. Where the host runs the package's user-prompt hook, that hook records
the typed `on` command as a short-lived arming record, and `on` refuses
without one and takes the grant's options only from it. Where the host has no
such hook, the entry's user-only invocation is the guard, and the grant
records which guard applied. No agent, file, issue or tool output starts,
extends or widens a grant. Ending early through `off` or `complete` is always
allowed, because an ended grant only returns the session to asking.

## Procedure

1. Run the packaged `skill-content/autopilot/scripts/autopilot.py` with the
   typed verb. On a host that arms, run `on` without options: the script takes
   them from the typed command. Pass every other verb's options as typed.
2. Show the script's output as it prints it. A refusal names what is wrong;
   never retry `on` with options of your own.
3. While a grant is active, the host contract's autopilot procedure governs
   every question of the orchestrating session. Roles never read or use the
   grant: they return questions to the orchestrating session as before.
4. A readable goal ends only when its owning compiler reads a terminal state.
   `complete` ends any grant and records whether that compiler agreed; the
   session's own judgement never ends a readable goal otherwise.
