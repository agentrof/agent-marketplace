---
name: deliver
description: Fast-lane delivery entry. Turns one free-text request into a task, one branch and one pull request that auto-merges on green CI and one reviewer pass.
exposure: entry
---

# Deliver

One request to one merged pull request, timed end to end.

## When to Use

- The user asks for a change in free text.

## Invocation

`/deliver <request>` starts one task. `/deliver resume TK-###` continues one
from its task and branch; `/retrospective` reads the times.

Run each task in its own fresh, short session started on the workhorse model
the host contract names; a skill cannot switch the session model. On another
model, tell the owner once and continue.

## Procedure

1. Pre-flight: read `workspace/config.json` (missing: route to the setup entry
   and stop).
2. Read `constitution.md`, which binds this session too, `flows/fast-lane.md`
   and [fast-lane](references/fast-lane.md) completely and run the flow's steps in order, from the project checkout,
   with the request verbatim. Read [local proof](references/local-proof.md)
   only when `workspace/config.json` sets `pr_proof` to `local_record`.
3. Report the task, its rigor, the pull request and its merge state, or the
   exact blocker.
