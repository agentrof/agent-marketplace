# Local proof before a pull request

Read this only when `workspace/config.json` sets `pr_proof` to
`local_record`. At the default, `remote_ci`, the project's CI proves the
change after the push.

A project whose checks run on one machine, with no remote CI, otherwise opens
and merges pull requests whose tests nobody ran.

## Rules

1. The proof commands come from `.agentrof/local-proof.json`, `{"commands":
   ["<command>", ...]}`, each run by the shell from the project root, `{base}`
   replaced by the target branch. Without the file the command is
   `make test`. A check the project needs for some
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

The record is scratch, never a durable record; the pull request body keeps
the proof.
