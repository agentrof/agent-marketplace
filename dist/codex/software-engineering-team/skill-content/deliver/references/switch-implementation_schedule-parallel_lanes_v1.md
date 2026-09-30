# Parallel implementation lanes: execution

These instructions apply while the project's Process Policy sets switch
`implementation_schedule` to `parallel_lanes_v1`, and only to an Item whose
approved plan declares `implementation_schedule: parallel_lanes_v1`. Any other
Item runs its implementation roles one after another in their approved order.

## What stays the same

The Item keeps one worktree, one Item ref, one Slot and one writer receipt
epoch. A lane has no worktree, branch, Slot or commit of its own. The
coordinator is the only committer. `push-item`, integration, Code Review, QA,
the evidence records and every Delivery verb keep their rules.

## Phases

Follow the phases the Execution Plan's Role Sequences render for the Item.

1. The Software Architect, when the Item has one, runs alone first.
2. The lanes run by seam order. Start every lane of a phase together, as the
   host contract says. A lane that a seam names as consumer starts only after
   its producer's lane has finished.
3. After the last lane phase the coordinator commits once, then freezes the
   candidate; Code Review and QA follow the Item's verification schedule.

## Seam specification first

Before any lanes that share an interface start, a seam specification fixes that
interface at code level: exact function, endpoint or command signatures, data
shapes, error codes, and the landing order of the lanes' changes. The Software
Architect writes it in the Item's claimed architecture record, unless an
approved contract the Item claims already fixes it. Every lane prompt names it,
and each lane codes against it from its first step. Lanes that share an
interface never start without it. A lane that finds the specification wrong
stops that part of its work and reports it; only the architect revises it.

## Lane rules

- Derive each lane's inputs with `task_inputs.py --entry deliver --role <role>`
  and the Item record as input. Its `write_scope` holds that lane's approved
  scope only.
- A lane writes only inside its scope and never edits another lane's files.
- A lane makes no Git writes: no commit, stash, checkout, restore, reset, merge,
  rebase or branch change. The one exception is `git add -N <path>` for a file
  the lane created inside its scope, because tools that read tracked files,
  such as a test harness that builds fixture projects from `git ls-files`, do
  not see an untracked file.
- A change needed in another lane's scope goes to the coordinator as a message
  naming the file, the change and the seam. The coordinator hands it to the
  owning lane; the requesting lane continues with work that does not depend on
  it.
- A lane reports the paths it changed or created, the seams it coded against,
  the tests it ran and the environment it ran them with, and its open requests.

## Test isolation

Lanes run tests in the same worktree at the same time, so every test process
gets an explicit environment:

- Interpreter search paths such as `PYTHONPATH` and `NODE_PATH` are unset or
  point only inside the Item worktree. Never inherit them from the
  coordinator's, a gate's or a hook's process: a value that names another
  checkout makes fixture runs import that checkout's code.
- Caches, coverage data, build output and scratch go to a lane-private,
  Git-ignored directory.
- Before trusting a result, confirm that the modules under test resolve inside
  the Item worktree.

Environment verbs that start, seed or stop the approved Environment Contract's
services, the approved full test command, and every vault or compiler write run
one at a time: a lane asks the coordinator, which runs them serially, and a
lane that needs the environment waits for it.

## Commit and freeze

When every lane has finished, the coordinator lists the changed and new paths.
Each must lie inside the scope of the one lane that reported it. A path that no
lane reported, or that lies outside its reporter's scope, is a lane scope
violation: resolve it with the owning lane before committing. Then the
coordinator commits the combined change once and freezes the candidate.

## Host loss

Lane work stays uncommitted until that commit, so after a host loss it exists
only in the Item worktree, as a sequential writer's uncommitted work does.
Resume the Item in that worktree, compare each lane's scope with the committed
head to see where each lane stood, and restart only the lanes that had not
finished. Takeover refuses a worktree with uncommitted changes, so it cannot
discard lane work.
