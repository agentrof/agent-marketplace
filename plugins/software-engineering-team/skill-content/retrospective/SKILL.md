---
name: retrospective
description: Reports fast-lane story times, request-to-merge average, median and max, each story's step breakdown, the slowest stories and numbered improvement proposals; opens an issue per proposal only on the user's approval.
exposure: entry
model_invocable: true
---

# Retrospective

Where the story time went, and what to cut next.

Load the `obsidian-vault` skill before writing a retrospective note under the
docs tree; its vault law is authoritative.

Before delegating a role, follow the packaged `templates/task-input-contract.md`
and derive its inputs with `scripts/task_inputs.py --entry retrospective`.

## When to Use

- The user, or an agent reporting progress, wants the measured story times.
- The user approves proposals from an earlier retrospective.

## Invocation

- `/retrospective`: the latest 20 stories.
- `/retrospective DLV-###`: the stories whose card names that Delivery.
- `/retrospective --since 7d`: the stories requested in the window (`<n>d` or `<n>h`).
- `--export csv|json` also rebuilds `.agentrof/retrospective/stories.<ext>`,
  a derived, git-ignored file.

## Procedure

1. From the project checkout, after `git fetch` and on the target branch, run
   `scripts/story_lane.py report` with `--delivery DLV-###`, `--since <window>`
   and `--export <format>` as asked. The story cards give the request and
   approval times; the pull request, review, CI and merge times are read from
   GitHub at report time.
2. Return its text as it is: story count, request-to-merge average, median and
   max, the per-story steps (card, write, ci, review, merge_wait; ci and
   review run side by side), the slowest stories with their largest step and
   3 to 5 numbered proposals with their evidence. Add nothing it does not
   measure; an empty scope says so.
3. For a Delivery scope the user may keep the summary: `--write-report` writes
   `workspace/docs/delivery/retrospectives/dlv-###-retrospective.md`, which a
   story or docs pull request commits.
4. Issues: only when the user approves proposals by number or id in this
   conversation, run `scripts/story_lane.py file-issues --ids <list>` with the
   same scope flags. It opens one issue per proposal in the repository of the
   checkout's `origin` remote, with the measured evidence, and returns the
   links. An agent that calls this entry reports only and never files.
