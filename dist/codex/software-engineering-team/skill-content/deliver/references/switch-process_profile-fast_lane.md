# Process profile: fast lane

At Process Policy `process_profile` `fast_lane`, every change reaches the
target branch through one task and its own pull request,
`flows/fast-lane.md`.

A task is the smallest unit of delivery, merged by one PR; it may derive from
a story (source_story, covers) or stand alone.

## Entries

- `/deliver`: one task, the owner's approval on the task, then branch, code
  and tests, review and merge.
- `/requirement` stays for a large, ambiguous or multi-part request. It
  clarifies the scope with the owner, runs the business-analysis,
  solution-design, design-system or experience-design stage only when the
  request needs it, then splits the work into tasks, each started with
  `task_lane.py start` and run as an independent `/deliver`, side by side. Each
  task names the documents it rests on as wikilinks in a `## Sources`
  section. No backlog revision and no plan approval follow.
- `/requirement ST-###` splits one approved backlog story into 3 to 6
  linked tasks. `task_lane.py criteria --story ST-###` lists the story's
  numbered criteria, its test plan, the tasks already open for it and a
  warning when the story changed on the target branch after an open task was
  requested; report that warning to the owner before splitting. Each task is
  started with `start --source-story ST-### --covers <numbers>` and gets the
  story's epic; the tasks together cover every criterion, each criterion once
  where possible.

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
- A change to an approved document is a knowledge task (`/deliver`, `start
  --knowledge`): no stage entry, review panel, revision or rebind. Approval
  stamps, package hashes and cross-package bindings (Experience
  `application@rN`, landscape and backlog input refs) are not re-verified at
  `fast_lane`.
- The stage entries keep their full method for new analysis or design a
  request needs. Their reviews run one reader in
  a single pass and only a critical finding blocks; every other finding
  becomes an issue. Their approved documents reach the target branch by a
  plain pull request, with no Fence or source handoff.
- `/backlog-plan`, `/delivery-plan`, `/execution-plan` and the governed
  Delivery entry `deliver-governed`, all hidden from the user's menu, start
  no new work: tell the user the fast lane is on and to use `/deliver` or
  `/requirement`, and stop. A Delivery opened before the switch finishes
  under the values it pinned; no Delivery flow owns this switch, so changing
  it never forces a Delivery's plan revision.
- Setup, configure, retrospective and autopilot entries are unchanged.

## Contracts, Definition of Done and Governance

- The Verification and Environment Contracts, the Definition of Done and
  the Delivery Governance change by plain pull request; the file on the
  target branch is in force. No revision number, `source_hash` or
  `approved_at_utc` stamp, `constrained_by` citation or Verification to
  Environment pairing is required, and approval never waits for a Delivery's
  owner gates.
- The Definition of Done needs no fixed Commands, Evidence Rules or Quality
  Gates headings.
- Governance is optional: `governance.md` may be deleted, and without it
  Items activate one at a time.
- No Item, Delivery or runner pins a contract or Definition of Done hash; each
  reads the text in force.

## Task path

- A task lives under `workspace/docs/backlog/tasks/`, type `task`,
  checked by `scripts/task_lane.py lint` only. The
  backlog compiler, epic and root reviews and backlog revisions never read it.
- Delivery is one branch and one pull request to the target branch. Nothing
  reserves a Slot, starts an Item, publishes or approves an execution plan,
  claims paths, pins or pairs Operation contracts, or reopens an Item.
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
- The main session runs every role in the foreground; no delivery
  coordinator is spawned.
- An architecture record revision names the task id as its item reference:
  `architecture_compile.py` accepts an active or delivered task as the owner
  at `fast_lane` and stamps no Item receipt.
- Times: the task holds `requested_at` and `approved_at`; the pull request,
  review comment, CI and merge times are read from GitHub by
  `/retrospective`. Nothing is written after the merge.
- The CI workflow that runs the project's tests on pull requests is
  rendered once with `operation_compile.py render-ci --output
  .github/workflows/tests.yml` from the approved Verification Contract's
  test command, committed by a task, and made a required check.
