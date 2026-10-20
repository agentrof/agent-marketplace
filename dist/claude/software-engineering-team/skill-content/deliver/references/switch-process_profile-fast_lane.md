# Process profile: fast lane

At Process Policy `process_profile` `fast_lane`, every change reaches the
target branch through one task and its own pull request,
`flows/fast-lane.md`.

A task is the smallest unit of delivery, merged by one PR; it may derive from
a story (source_story, covers) or stand alone.

## Entries

- `/deliver`: one task, the owner's approval on the task, then branch, code
  and tests, review and merge.
- A large or multi-part request is split into tasks, each started with
  `task_lane.py start` and run as an independent `/deliver`, side by side.
  Each task names the documents it rests on as wikilinks in a `## Sources`
  section.

## Task links

- A task may name `epic` (EP-###), and `source_story` (ST-###) with `covers`,
  the numbers of that story's criteria the task delivers. Links point from
  the task to the story only; no story, epic or test plan is edited.
- A task's `roles` field lists the team roles routed to it as
  `<role>:require|suggest:pre_check|author`, and its `## Role Checks` section
  holds each recorded verdict as `- <time> <role> go|changes: <note>`.
- A task with no story is standalone, for fixes, docs, configuration and
  technical debt; it may still name an epic.
- `task_lane.py board` derives the board on every run: tasks grouped by epic
  and story, a story delivered once merged tasks cover every one of its
  criteria, and the open tasks whose story changed. `--write` keeps it in the
  git-ignored `.agentrof/retrospective/board.md`; nothing commits it, so
  parallel tasks never conflict on it.
- A change to a knowledge document is a knowledge task (`/deliver`, `start
  --knowledge`); the vault gate checks schema, links and structure of the
  touched trees, in parallel.
- Vault, setup, configure, retrospective and autopilot entries are unchanged.

## Contracts

- The Verification and Environment documents under `workspace/docs/operation/`
  change by plain pull request; the file on the target branch is in force.

## Task path

- A task lives under `workspace/docs/backlog/tasks/`, type `task`,
  checked by `scripts/task_lane.py lint` and the vault law only.
- A task is one branch and one pull request to the target branch.
  `task_lane.py overlap` warns about open pull requests that touch the same
  files and never refuses.
- Verification is the project's CI on the pull request head plus one
  `code-reviewer` reading the diff. Only a critical finding blocks; every
  other finding is filed as an issue in the project's repository. Auto-merge
  lands the pull request once CI is green and the reviewer passed.
- The task's `rigor` is `normal` by default. At `high`, proposed for
  authentication, credentials, secrets, data deletion and irreversible
  actions and confirmed by the owner on the task, the read-only
  `security-reviewer` joins, every affected live group runs and the owner
  merges.
- At rigor `normal`, live tests run only when the diff touches environment
  code or live test files and the change can alter what a live group
  proves; otherwise they run on demand.
- The main session runs every role in the foreground.
- Times: the task holds `requested_at` and `approved_at`; the pull request,
  review comment, CI and merge times are read from GitHub by
  `/retrospective`. Nothing is written after the merge.
- The CI workflow that runs the project's tests on pull requests is copied
  once from the packaged `templates/ci-tests.yml` to
  `.github/workflows/tests.yml` with the project's test command, committed by
  a task, and made a required check.
