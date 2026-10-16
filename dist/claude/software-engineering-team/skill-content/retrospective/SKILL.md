---
name: retrospective
description: Reports fast-lane task times and cost, request-to-merge average, median and max against target, each task's steps and waits with their owner, the slowest tasks and numbered improvement proposals; acts on a proposal only on the user's approval.
exposure: entry
---

# Retrospective

Where the task time went, who owns each loss, and what to cut next.

Load the `obsidian-vault` skill before writing a retrospective note under the
docs tree; its vault law is authoritative.

Before delegating a role, follow the packaged `templates/task-input-contract.md`
and derive its inputs with `scripts/task_inputs.py --entry retrospective`.

## When to Use

- The user, or an agent reporting progress, wants the measured task times.
- The user approves proposals from an earlier retrospective.

## Invocation

- `/retrospective`: the latest 20 tasks.
- `/retrospective DLV-###`: the tasks that name that Delivery.
- `/retrospective --since 7d`: the tasks requested in the window (`<n>d` or `<n>h`).
- `--deep` adds reading waste: files read 5 or more times and tool results
  over 200 KB.
- `--export csv|json` also rebuilds `.agentrof/retrospective/tasks.<ext>`,
  a derived, git-ignored file.

## Procedure

1. From the project checkout, after `git fetch` and on the target branch, run
   `scripts/task_lane.py report` with `--delivery DLV-###`, `--since <window>`,
   `--deep` and `--export <format>` as asked. The tasks give the request and
   approval times; the pull request, review, CI and merge times come from
   GitHub; agent count, models, effort and tokens per role and the active
   minutes come from the project's host session transcripts, matched
   to a task by its branch, else by its time window.
2. Return its text as it is:
   - task count; request-to-merge average, median and max against the target
     (15 minutes on average, 50 at most for a large task) and the previous
     retrospective of the same scope;
   - per task the steps (define, write, ci, review, merge_wait; ci and review
     run side by side), the waits (agent work, CI wait, review wait, owner
     wait, idle) and the cost;
   - the slowest tasks with their largest step;
   - 3 to 5 numbered proposals, each with its evidence, cause owner (project,
     plugin, host tool, owner or process), expected gain and how to verify it.
   Add nothing it does not measure; an empty scope says so.
3. For a Delivery scope the user may keep the summary: `--write-report` writes
   `workspace/docs/delivery/retrospectives/dlv-###-retrospective.md`, which a
   task or docs pull request commits.
4. Act only when the user approves proposals by number or id in this
   conversation: run `scripts/task_lane.py file-issues --ids <list>` with the
   same scope flags. A plugin-caused proposal is appended once to the pending
   upstream table of `.agentrof/plugin-ledger.md`, never filed upstream. Any
   other proposal goes to the repository of the checkout's `origin` remote: an
   existing issue, open or closed, that carries its proposal id gets a comment
   with the new evidence; otherwise one issue is opened. Return the links and
   ledger rows. An agent that calls this entry reports only and never files.
