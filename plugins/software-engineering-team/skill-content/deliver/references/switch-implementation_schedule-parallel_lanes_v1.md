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
2. Then the lanes run. Start every lane that waits for no producer together,
   as the host contract says. A lane rendered `(after <producers>)` consumes
   those lanes' seams: start it as soon as every producer it names has
   finished, without waiting for any other lane.
3. After the last lane finishes the coordinator commits once, then freezes the
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
- A lane makes no Git writes: no add, commit, stash, checkout, restore, reset,
  merge, rebase or branch change. That includes `git add -N`, because the lanes
  share one Git index and two index writers at once fail on its lock. A lane
  reports each file it creates, and the coordinator runs `git add -N <path>`
  for it, one Git command at a time, so that tools that read tracked files,
  such as a test harness that builds fixture projects from `git ls-files`, see
  it.
- A change needed in another lane's scope goes to the coordinator as a message
  naming the file, the change and the seam. The coordinator hands it to the
  owning lane; the requesting lane continues with work that does not depend on
  it.
- A lane reports the paths it changed or created, the seams it coded against,
  the tests it ran and the environment it ran them with, and its open requests.

## Test isolation

Lanes run tests in the same worktree at the same time, so every test process
gets an explicit environment:

- The interpreter search paths `PYTHONPATH`, `PYTHONHOME` and `NODE_PATH` are
  unset or point only inside the Item worktree. Never inherit them from the
  coordinator's, a gate's or a hook's process: a value that names another
  checkout makes fixture runs import that checkout's code. `lane-run` enforces
  this for the commands it runs: it drops every entry of these variables that
  resolves outside the Item worktree, a relative entry against the command's
  working directory and a link through its target, keeps every entry inside
  the worktree, unsets a variable left with none and reports the dropped
  entries as `dropped_search_paths`.
- Caches, coverage data, build output and scratch go to a lane-private,
  Git-ignored directory.
- Before trusting a result, confirm that the modules under test resolve inside
  the Item worktree.

Environment verbs that start, seed or stop the approved Environment Contract's
services, the approved full test command, and every vault or compiler write run
one at a time, and a lane asks the coordinator for each of them. The
coordinator runs the full test command in the Item worktree with
`scripts/delivery_verification.py --worktree <item-root> lane-run --delivery
DLV-### --story <story> --role <lane-role> --kind test`, and an environment
verb with `--kind environment --verb down|up|seed|logs|url [--value
<approved-identifier>]`, then hands the lane the output file the result names.
Each such command holds the Item's environment lock, which QA's `run` and
`environment` take as well. While another command holds it, the command
refuses with `DELIVERY_ENVIRONMENT_BUSY` and names the holder: run it again
once the holder finishes. A holder that died loses the lock with its process;
the next command reports it as `interrupted_holder`, and after an interrupted
environment verb the environment goes down before anything trusts it again.

## Commit and freeze

When every lane has finished, the coordinator lists the changed and new paths.
Each must lie inside the scope of the one lane that reported it. A path that no
lane reported, or that lies outside its reporter's scope, is a lane scope
violation: resolve it with the owning lane before committing. Then the
coordinator commits the combined change once and freezes the candidate.

## Host loss

Lane work stays uncommitted until that commit, so after a host loss it exists
only in the Item worktree, as a sequential writer's uncommitted work does. On
the host that holds the worktree, run `scripts/delivery_git.py lane-status
--delivery DLV-### --story <story>`. Its observations name, against the
worktree's committed head, each lane's changed paths inside its approved scope
(`lane:<role>`), the lanes with work, the changed paths outside every lane
scope, the worktree head and this host's writer receipt state. While that
receipt is `verified`, resume in the worktree without takeover: restart each
lane without work from its prompt and each lane with work from its reported
paths.

Takeover never discards lane work silently. While the Item worktree holds
uncommitted lane work, `takeover-item` refuses with `DELIVERY_WORKTREE_UNSAFE`,
names each lane's paths and the choice: keep the work, finish its lanes and
commit it as the coordinator on this host, which its verified writer receipt
allows; or discard it with the `git reset --hard` and `git clean -fd` commands
the refusal names, then take over. A host without a verified writer receipt
cannot publish the work, so there only discarding remains, after copying out
any path to keep.
