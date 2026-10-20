---
name: retrospective
description: Reports fast-lane task times, cost and quality, request-to-merge average, median and max against target, each task's steps and waits with their owner, the slowest tasks and numbered improvement proposals; acts on a proposal only on the user's approval.
exposure: entry
---

# Retrospective

Where the task time went, who owns each loss, and what to cut next.

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
  a derived, git-ignored file with the time, cost and quality columns.

## Procedure

1. From the project checkout, after `git fetch` and on the target branch, run
   `scripts/task_lane.py report` with `--delivery DLV-###`, `--since <window>`,
   `--deep` and `--export <format>` as asked. The tasks give the request and
   approval times; the pull request, review, CI and merge times come from
   GitHub; agent count, models, effort, tokens, dollars and the active
   minutes come from the project's host session transcripts. A spawned agent
   whose description or first prompt names exactly one task id is that
   task's; else a record on a `task/...` branch is that task's; else the
   main session and any other record fall in every task window that holds
   them, split evenly. What no task claims is reported as unattributed.
   Prices come from `data/pricing.json` (USD per 1M tokens per model, with
   its date, sources and verification note; fast-mode calls cost the model's
   `fast_multiplier`); a project overrides or adds a model in
   `.agentrof/pricing.json` with the same `models` shape. A model without a
   price counts tokens only and is listed as unpriced. Each run appends every
   merged task's cost and waits once to `.agentrof/retrospective/history.jsonl`
   and later runs read that row first, so cost survives the host's transcript
   cleanup. `--rescan` prices every task from the transcripts again and
   appends the result as its newest row; earlier rows stay. The history is
   append-only measurement: nothing deletes or rewrites it without the
   owner's approval. Each summary row keeps a fingerprint of the price
   table, and a changed one is flagged as a cost-method change.
   Quality comes beside them: the review counts `merge --critical <n>
   --non-critical <n> --filed <n>` writes into its pass comment; the CI first
   pass and the red runs before green from the branch's workflow runs (one
   `gh api` call per pull request; an external status check is not seen); an
   escaped defect is each later task whose `fixes: TK-###` names the task; a
   story's coverage is the share of its criteria the delivered tasks cover.
   The findings and CI figures are kept once per merged task as a
   `task_quality` history row and read first; `--rescan` reads them again.
   Rework asks why a task did not finish in one pass: follow
   [rework causes](references/rework.md) for its events, causes, grouping
   and the lessons file.
2. Return its text as it is:
   - task count; request-to-merge average, median and max with a verdict
     against the target, the baseline (the previous retrospective of the same
     scope, or none) and the average step breakdown;
   - per task the steps (define, write, ci, review, merge_wait; ci and review
     run side by side, the larger counts) and the unaccounted minutes, so they
     add up to the total; a missing step or cost is shown as missing with its
     reason; the waits (agent work, CI wait, review wait, owner
     wait, idle) and the cost: dollars, tokens (input, cache write, cache
     read, output), spawns with the startup context each one began with (its
     first prompt: input plus cache tokens) and the main session's part;
   - per kind and for all merged tasks: total dollars, dollars, tokens and
     spawns per task, average startup context per spawn, dollars by model and
     by class (author, review, fix, security, main), the main-session share,
     and the previous retrospective's dollars and tokens per task;
   - per task its quality: critical, non-critical and filed findings, CI first
     pass with its red runs, the tasks that fixed it and its story coverage;
     per kind critical and non-critical findings per task, the filed share,
     the CI first-pass rate, red runs per task, the escaped defect rate and
     coverage per story, and the previous retrospective's quality figures;
   - per kind the role checks: how many, on how many tasks, and their minutes
     inside the write step, each counted from approval or the previous check;
   - per task its rework events and minutes, per kind the one-pass rate, the
     recurring and rare rework groups, unclassified events and memory
     candidates;
   - the slowest tasks with their steps and verdict against their size target;
   - per kind the size targets beside the per-size medians calibrated from the
     scope and the kept history (shown at 5 or more tasks of a size);
   - up to 5 numbered proposals, one per cause across kinds, only from kinds
     with at least 5 merged tasks; time, cost (startup context, spawns
     per task, xhigh effort on review or fix, main-session share) and quality
     levers (CI first pass under 80%, any escaped defect, critical findings on
     over 20% of reviewed tasks) and recurring process- or plugin-owned rework
     causes (`RETRO-REWORK-<CAUSE>`) ranked together by the share of the task's
     minutes, dollars or tasks they address, each with its evidence per kind, cause owner (project, plugin, host
     tool, owner or process), expected gain and how to verify it.
   Add nothing it does not measure; an empty scope says so.
3. For a Delivery scope the user may keep the summary: `--write-report` writes
   `workspace/docs/backlog/retrospectives/dlv-###-retrospective.md`, which a
   task or docs pull request commits.
4. `--write-lessons` updates the tracked lessons file as
   [rework causes](references/rework.md) describes; it never writes memory.
5. Act only when the user approves proposals by number or id in this
   conversation: run `scripts/task_lane.py file-issues --ids <list>` with the
   same scope flags. A plugin-caused proposal is appended once to the pending
   upstream table of `.agentrof/plugin-ledger.md`, never filed upstream. Any
   other proposal goes to the repository of the checkout's `origin` remote: an
   existing issue, open or closed, that carries its proposal id gets a comment
   with the new evidence; otherwise one issue is opened. Return the links and
   ledger rows. An agent that calls this entry reports only and never files.
