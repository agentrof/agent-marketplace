# Local proof before a pull request

Read this only when `workspace/config.json` sets `pr_proof` to
`local_record`. At the default, `remote_ci`, the project's CI proves the
change after the push.

A project whose checks run on one machine, with no remote CI, otherwise opens
and merges pull requests whose tests nobody ran.

## Rules

1. The proof commands come from `local_proof` in `workspace/config.json`,
   `["<command>", ...]`, each run by the shell from the project root, `{base}`
   replaced by the target branch. Without it the command is `make test`. A check the project needs for some
   changes only, such as live evidence for the affected live groups, is one
   more command that exits non-zero when that evidence is missing for HEAD.
2. After the last commit of the change, run `task_lane.py prove --task <id>`.
   It runs every command, even after a red one, and writes
   `.agentrof/agent-marketplace/.runtime/proof/<id>.json`: the HEAD commit, a
   clean-tree flag and each command's exit code, start and end time, seconds
   and, for a red command, a short output tail. It exits non-zero when any
   command is red or a tracked file differs from HEAD.
3. `open-pr` refuses before it pushes when the record is missing, ran on
   another commit, ran on a dirty tree, holds a red command, or a tracked file
   changed since. Fix the cause, commit, and run `prove` again; never edit the
   record.
4. On success `open-pr` adds a `## Local proof` section to the pull request
   body: the proved HEAD and each command with its result and seconds. The
   delivered-status commit `open-pr` adds on top touches only the task file.

## Proof by impact

`local_proof_map` in `workspace/config.json` is an ordered list of rules
`{"paths": [glob, ...], "commands": [...] | "none": true, "reason": "..."}`;
`*` in a glob spans folders and matching ignores case. `prove` takes the files changed since the target
branch and the first rule that matches each one. It runs the union of those
rules' commands (`none` adds no command). One changed file no rule matches, no
map, or no changed file selects the whole `local_proof`: an uncovered path never
skips a test. A task whose record declares a `risks` value, or cannot be read,
selects the whole `local_proof` too. The record holds `selection` (`impact`, `full`, `train` or
`deferred`) and why, and the `## Local proof` section repeats them. `prove
--full` ignores the map. `proof_runner`, when set, is a command that runs each
proof command as `<runner> sh -c '<command>'`, such as a machine queue.

## Merge train

`prove --deferred` records that the change's proof is the batch proof.
`task_lane.py train` batches reviewed pull requests behind one proof:

- `train add --task --pr [--critical --non-critical --filed --detail --delegated]`
  joins the open batch; it needs a valid proof record of the change and no
  critical finding. A change that needs the full proof must have run it or
  deferred it. A pull request added after `train prove` started joins the next batch.
- `train prove [--batch n] [--dry-run]` seals the batch, merges every member
  into `train/<n>` in its own worktree and runs the full `local_proof` once,
  through `proof_runner`. One batch proof runs at a time. A green batch writes
  a `train` record into each member's own checkout, which satisfies that
  member's `open-pr` proof rule for its recorded HEAD.
- `train merge [--batch n]` merges each member through the unchanged `merge`
  step with its recorded review counts; it refuses a batch that is not green or
  whose target branch moved since the proof.
- `train bisect [--batch n]` finds the member whose merge turns a red batch red.
  A red batch is fixed at the failing test; bisect is for a cause that is not evident.
- `train status` lists the batches. State lives in the main checkout's
  `.agentrof/agent-marketplace/.runtime/train/`.

The record is scratch, never a durable record; the pull request body keeps
the proof.
