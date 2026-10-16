---
name: deliver
description: Fast-lane delivery entry. Turns one free-text request into a story card, one branch and one pull request that auto-merges on green CI and one reviewer pass.
exposure: entry
---

# Deliver

One request to one merged pull request, timed end to end.

Before delegating a role, follow the packaged `templates/task-input-contract.md`
and derive its inputs with `scripts/task_inputs.py --entry deliver`.

## When to Use

- The project's Process Policy sets `process_profile` to `fast_lane` and the
  user asks for a change in free text.
- `/deliver DLV-###` resumes a governed Delivery through the hidden
  `deliver-governed` entry.

## Invocation

`/deliver <request>` starts one story. `/deliver resume FL-###` continues one
from its card and branch; `/retrospective` reads the times.

## Procedure

1. Pre-flight: read `workspace/config.json` (missing: route to the setup entry
   and stop) and `scripts/process_policy.py value --switch process_profile`.
   At `governed`, or with an exact `DLV-###` argument for a Delivery opened
   before the switch, read `skill-content/deliver-governed/SKILL.md`
   completely and follow it as this entry; it is the governed Delivery path.
2. Read `flows/fast-lane.md` completely and run its steps in order, from the
   project checkout, with the request verbatim.
3. Report the card, its rigor, the pull request and its merge state, or the
   exact blocker.
