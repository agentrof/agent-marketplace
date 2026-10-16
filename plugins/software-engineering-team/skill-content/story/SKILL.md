---
name: story
description: Fast-lane story entry. Turns one free-text request into a story card, one branch and one pull request that auto-merges on green CI and one reviewer pass.
exposure: entry
---

# Story

One request to one merged pull request, timed end to end.

Before delegating a role, follow the packaged `templates/task-input-contract.md`
and derive its inputs with `scripts/task_inputs.py --entry story`.

## When to Use

- The project's Process Policy sets `process_profile` to `fast_lane` and the
  user asks for a change in free text.

## Invocation

`/story <request>` starts one story. `/story resume FL-###` continues one
from its card and branch; `/retrospective` reads the times.

## Procedure

1. Pre-flight: read `workspace/config.json` (missing: route to the setup entry
   and stop) and `scripts/process_policy.py value --switch process_profile`.
   At `standard`, stop and route to `/requirement` as `flows/story.md` says.
2. Read `flows/story.md` completely and run its steps in order, from the
   project checkout, with the request verbatim.
3. Report the card, its rigor, the pull request and its merge state, or the
   exact blocker.
