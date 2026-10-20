# Fast Lane Flow

Spawn template: paste `{{constitution}}` into every role prompt.

Sources first, per constitution section 5: every role reads the sources the
task file lists and greps `workspace/docs/` for anything missing; it records
extra sources and reasons and returns `context_findings` to the parent for
user-approved reporting.

Switch `step_timing`: at `recorded`, the coordinator runs
`step_timing.py start --run <run> --step <step> [--budget <id>]` before
each step and `step_timing.py end --run <run> --span <span>` after it,
and records each role spawn with `--kind spawn --parent <step span>
--role <role> --phase reading|writing|review|re_review|waiting`. An end
that returns overrun is reported to the owner at once with its
breakdown, largest contributor and lever. The run ends with
`step_timing.py report --run <run> --write`, as
`skill-content/deliver/references/switch-step_timing-recorded.md`
defines. Switch `step_budgets`: at `enforced`, each budgeted step is
compared with its maximum, the owner's parameter or the package target
in `skill-content/configure/data/step-budgets.json`; a step may finish
well under it, and only an exceeded maximum is reported, never blocking,
as
`skill-content/deliver/references/switch-step_budgets-enforced.md`
defines.
Switch `process_profile`: the governed Delivery path is removed, so this flow
runs at either value. At `fast_lane`, follow
`skill-content/deliver/references/switch-process_profile-fast_lane.md` and
the steps below. Read the value with
`scripts/process_policy.py value --switch process_profile`.

One request becomes one task, one branch and one pull request to the
target branch. Git, the pull request and the project's CI are the protocol:
no Delivery, Fence, Slot, Item, Integration branch, execution plan, path
claim, contract pin or architecture stamp is created or read.

The main session orchestrates. It spawns every role in the foreground, one
call that returns the role's result, and never spawns a delivery
coordinator or a background agent that would report to it.
Each spawn passes the model and effort, or names the agent, that
`model_class.py resolve --role <role> --task <id> --project-root . --host
<host> --json` returns; a step that names a class passes `--class <class>`.

Each step names its `scripts/task_lane.py` verb. The task holds the
request and approval times; GitHub holds the pull request, review, CI and
merge times. Nothing is recorded after the merge.

A task is `build` (code, devops, tests) or `knowledge` (documents only:
analysis, design, architecture records, backlog, contracts). One request that
needs both is two tasks, run side by side. Request-to-merge targets per kind
and size are in `skill-content/deliver/data/targets.json` (knowledge 1/3/5,
max 15 minutes; build 5/15/30, max 60) and `/retrospective` reports each task
against its own.

Knowledge task, the short path: `start --knowledge --size s|m|l`. The request
is the approval: no criteria and no `approve`. The main session
edits the documents itself and spawns the owning role only at size `l`;
then it runs `open-pr`, the review of step 6 only when `review-inputs`
routes a reviewer, and `merge`. Approval stamps are not re-verified at
`fast_lane`: the file on the target branch is in force.

The task's `rigor` sets the verification:

| Rigor | Reviewers | Live tests | Merge |
|---|---|---|---|
| `normal` (default) | one `code-reviewer` | only when the change really affects a live group | auto-merge on green CI |
| `high` | the `code-reviewer` and the `security-reviewer` | every affected live group, always | the owner merges |

Any risk flag in the task's `risks` (`auth`, `secrets`, `data_placement`,
`migration`, `irreversible`) also runs the `security-reviewer` and escalates
the spawns `model_class.py --task` resolves, at either rigor.

Method skills: `skill-content/deliver/data/task-triggers.json` maps paths and
words to the method skill they make mandatory (data model, schema, migration
or SQL to `sql-database-design`, a document store to `nosql-database-design`,
container, compose, chart or cluster files to `docker-compose`, Python
service files to `python-fastapi`, React UI to `react-typescript`); a project
replaces or adds a rule in `.agentrof/task-triggers.json`. Every stack is
accepted; a stack no rule names is worked from the code.

Role routing pulls a team role into a task only where it adds value.
`start` and `approve` match the task's kind, size, named files and words
against `skill-content/deliver/data/role-routing.json` (a project replaces
or adds a rule in `.agentrof/role-routing.json`) and write the hits to the
task's `roles` field as `<role>:<mode>:<step>`; the main session may add one
by hand. The software architect is `require` for a build task that changes a
data model, schema or migration, makes a structural change (new module,
service, package or app, a public interface or contract, a cross-component
dependency) or is size `l`; the other roles are `suggest`. Spawn each routed
role in the foreground with the task, the plan and only the architecture or
solution notes it rests on, within the rule's `time_budget_minutes`, and give
it the rule's `ask`. Record its answer with `role-check --task <id> --role
<role> --verdict go|changes --note "<reasons>"`; apply a `changes` verdict
before writing, and file a finding that is not critical as an issue.
`open-pr` refuses while a `require` role, routed by the task or by the files
its diff changes, has no verdict; a `suggest` role never blocks.

1. Request. Run `start --title "<short title>" --request-file <file>` from
   the project checkout, with the user's request verbatim in the file, and
   `--delivery DLV-###` when the user groups tasks under a Delivery label.
   A task that delivers part of a backlog story adds `--source-story ST-###
   --covers <numbers>`; a standalone task may add `--epic EP-###`.
   It creates and pushes `task/<id>-<slug>` from `origin/main`, writes the
   task skeleton under `workspace/docs/backlog/tasks/` with `requested_at`,
   and proposes `rigor: high` when the request names authentication,
   credentials, secrets, data deletion or another irreversible action.
   It also writes `risks` (each `--risk <flag>` and the flags the request
   hits), `applied_skills` (the method skills the request's words and files
   trigger) and `sources` (each `--source <file>` and each existing doc the
   request names, as `path@<sha256 prefix>`).
2. Task. Fill the task in one pass: the request with its `### Do` list,
   3 to 8 numbered acceptance criteria, each one verifiable property with
   its exact codes and limits, among them a filled `Live wiring:` criterion
   (where the running system serves the change and how it is seen working)
   and a `Docs delta:` criterion (the docs it changes, or none), a `## Tests`
   map with a `- <criterion numbers>: <test>` line for every criterion, and
   `size` s, m or l. Set `rigor: high` yourself when the change touches
   authentication, credentials, secrets, data deletion or an irreversible
   action even if the proposal missed it, and add each risk flag the change
   carries to `risks`. Add every planned file's triggered skill to
   `applied_skills` and every doc the task rests on to `sources`; `approve`
   hashes the sources added. `lint --task <id>` until it passes. Show the task to
   the owner with the proposed rigor; the owner confirms or changes rigor on
   the task. On the owner's go, or under a standing delegation, run
   `approve --task <id>`; it writes `approved_at`. No analysis, solution or
   design stage and no review panel reads the task. A request that needs
   more than 8 criteria is two tasks: say so and start the second after
   the first.
   Then run the role checks `approve` lists under `roles` (Role routing above).
3. Write. Spawn the owning developer role in the foreground with the task,
   the branch, the files it names and every skill in `applied_skills`, which
   it loads before writing. It writes the change and its tests on
   the branch, commits, and runs the project's test command for the suites it
   touched, the command the CI workflow runs on pull requests (`make test`
   unless the project declares another). Architecture records and contracts
   the change needs are edited in the same branch; an architecture record
   revision names the task id as its `--item-ref`.
4. Live tests. At `normal`, run a live group only when the diff touches
   environment code or live test files and the change can really alter what
   that group proves; say which group and why. At `high`, run every live
   group the diff can affect. Otherwise live tests are not part of the story
   and run on demand through the project's live command.
5. Pull request. Run `overlap` and pass its warnings on; they never block.
   Run `open-pr --task <id>`. It marks the task delivered, pushes the
   branch and opens the pull request. Switch `pr_proof`: at `local_record`,
   run `prove --task <id>` first; `open-pr` refuses without its green record
   of a clean HEAD, as
   `skill-content/deliver/references/switch-pr_proof-local_record.md` defines.
6. Review. Run `review-inputs --task <id>`. It routes by the files the
   branch changes: code to `code-reviewer`, `workspace/docs/business-analysis`
   to `analysis-challenger`, `workspace/docs/solution-design` or
   `workspace/docs/system-architecture` to `solution-reviewer`, and adds the
   read-only `security-reviewer` at `high` or on any risk flag. Spawn every
   listed reviewer in the foreground in parallel with the task, the output
   of `gh pr diff <pr>` and the `review-inputs` result. Each checks every
   acceptance criterion against the diff and its tests and returns findings
   with severities; a skill in `missing_skills` that the change did not
   follow is a major finding, and a source in `changed_sources` is checked
   for a change the task now contradicts. The pull request merges with 0
   critical and 0 major findings. The same writer fixes each critical or
   major finding on the branch, and only the reviewer that raised it
   re-reads the fix diff, once; at most one fix round, then the owner
   decides. Trivial minors are fixed in one commit on the branch; only
   out-of-scope minors become one grouped issue in the project's repository
   through `gh issue create`, linked from a pull request comment. A design
   or policy question found in review goes to the owner as a decision, never
   patched by the developer.
7. Merge. Run `merge --task <id> --pr <pr>`. It posts the review pass on
   the pull request. At `normal` it turns on auto-merge, so the pull request
   merges when its required CI checks are green; a red check is fixed on the
   branch like a critical finding. At `high` it leaves the merge to the
   owner: report the pull request and stop.
8. Report the task, the pull request and its merge state. `/retrospective`
   reads the times later.
   Pass the review's counts to `merge` as `--critical <n> --non-critical <n>
   --filed <n>`, and start a task that repairs a defect an earlier task let
   through with `--fixes TK-###`; `/retrospective` reads both as quality.
   When a task needed another pass for a reason no red run, finding or
   verdict shows, record it with `rework --task <id> --category <cause>
   --note "<why>"`. `/retrospective` groups the rework causes; only the
   recurring ones become lessons, memory candidates or process proposals.

The task is done when its pull request is merged. The task file on the target
branch is the durable task record.
