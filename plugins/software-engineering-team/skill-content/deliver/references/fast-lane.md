# Fast lane: tasks and lanes

Every change reaches the target branch through one task and its own pull
request, `flows/fast-lane.md`. A task is the smallest unit of delivery; it may
derive from a story (`source_story`, `covers`) or stand alone.

## Parallel tasks

- A large or multi-part request is split into tasks, each started with
  `task_lane.py start` and run as an independent `/deliver`, side by side.
- `start` creates the task branch in its own git worktree (the JSON result
  names it as `worktree`) and never switches the current checkout, so
  parallel agents can share one checkout; `--switch` opts back in.
- Parallel tasks run only on disjoint files, with each task's file ownership
  written in its `### Do` list; work on a shared area is sequenced.
  `task_lane.py overlap` warns about open pull requests that touch the same
  files and never refuses.
- Generated files are rendered again after a merge, never resolved by hand.
  Bring main into a branch with a merge, never a rebase or force-push.

## Task links

- A task may name `epic` (EP-###), and `source_story` (ST-###) with `covers`,
  the numbers of that story's criteria the task delivers. Links point from
  the task to the story only; no story, epic or test plan is edited.
- A task with no story is standalone, for fixes, docs, configuration and
  technical debt; it may still name an epic.
- A task's `roles` field lists the team roles routed to it as
  `<role>:require|suggest:pre_check|author`, and its `## Role Checks` section
  holds each recorded verdict as `- <time> <role> go|changes: <note>`.
- `task_lane.py board` derives the board on every run: tasks grouped by epic
  and story, a story delivered once merged tasks cover every one of its
  criteria, and the open tasks whose story changed. `--write` keeps it in the
  git-ignored `.agentrof/retrospective/board.md`, so parallel tasks never
  conflict on it.

## Task path

- A task lives under `workspace/docs/backlog/tasks/`, type `task`, checked by
  `scripts/task_lane.py lint` only.
- The Verification and Environment documents under `workspace/docs/operation/`
  change by plain pull request; the file on the target branch is in force.
- The CI workflow that runs the project's tests on pull requests is copied
  once from the packaged `templates/ci-tests.yml` to
  `.github/workflows/tests.yml` with the project's test command and
  `tests_root` from `workspace/config.json`, committed by a task, and made a
  required check.
