# Rework causes

Why a fast-lane task did not finish in one pass, and which causes keep
coming back.

## Events

`task_lane.py report` collects these per task:

- each red workflow run before the merge and each re-run attempt; the first
  3 red runs of a task also read `gh run view --log-failed` for the job, the
  first failing test id and the first error line, truncated, with every line
  that looks like a credential dropped;
- each blocking (critical) review finding `merge --critical <n>` recorded;
- each role `changes` verdict;
- each commit pushed after the pull request opened that no earlier red run,
  `changes` verdict or blocking finding explains; a `Merge ...` commit always
  counts.

A cause only a person or agent knows is recorded with `task_lane.py rework
--task TK-### --category <id> --note "<cause>"`. With `--ref <ref>`, a ref the
report lists such as `commit:abc1234`, it classifies that event instead of
adding one. Both land in the task's `## Rework` section with their time.

## Causes

Each event takes the first category of `data/rework-causes.json` whose hint,
a case-insensitive regex, matches its text; no match is `unclassified` and the
report lists it with its ref so a person can classify it. Each category has an
owner: `project`, `plugin` or `process`. A project replaces or adds a category,
or overrides a setting, in `.agentrof/rework-causes.json`.

## Groups

Over the latest `recurring.window_tasks` (30) tasks with known rework, the
scope and the kept history together, events group by category and, for a
failing test, by test id. A group is `recurring` at `min_events` (3) events in
`min_tasks` (2) tasks; a test failing in 2 or more tasks is always a recurring
repeat offender. Everything else is `rare` and only listed. Each merged task's
raw events are kept once as a `task_rework` history row and classified again
on every run, so a taxonomy change reclassifies history; `--rescan` reads them
from GitHub again.

## Lessons, memory and levers

- `report --write-lessons` updates the tracked lessons file, by default
  `workspace/memory/lessons-learnt.md` (`lessons.path`), in place: one entry
  per recurring group with its cause, rule, owner, status and evidence. An
  edited Rule line is kept. An open entry with no event in
  `fixed_after_tasks` (10) tasks turns fixed and reopens when it recurs. At
  most `max_open` (20) open entries are written and as many fixed ones kept,
  the oldest fixed dropped first. The history file is never touched.
- At most `promote_max` (5) project-owned recurring rules, most events first,
  are marked `Promote: memory` and printed as one-line candidates. Nothing is
  written to any memory store; the main session decides.
- Process- and plugin-owned recurring groups become proposals
  `RETRO-<KIND>-REWORK-<CAUSE>` with evidence, the rework minutes they cost
  (first red or blocking event to merge) and a verify line; they rank with the
  time, cost and quality levers and file through `file-issues`.
