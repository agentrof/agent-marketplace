# Fast Lane Flow

Spawn template: paste `{{constitution}}` into every role prompt.

Sources first, per constitution section 5: every role reads the sources the
task file lists and greps `workspace/docs/` for anything missing.

Tasks, links and parallel lanes follow
`skill-content/deliver/references/fast-lane.md`.

One request becomes one task, one branch and one pull request. Each step names
its `scripts/task_lane.py` verb. The main session only orchestrates: large
reads and proofs go to spawned roles, every role runs in the foreground, and
nobody writes into a tree while a verification run or a delegated role reads
it. Each spawn passes what `model_class.py resolve --role <role> --task <id>
--project-root . --host <host> --json` returns (`--class <class>` when a step
names one).

A task is `build` (code, devops, tests) or `knowledge` (documents only); a
request that needs both is two tasks run side by side. Request-to-merge
targets are in `skill-content/deliver/data/targets.json`. Knowledge task:
`start --knowledge --size s|m|l`; the request is the approval. The main
session edits the documents, spawning the owning role only at size `l`, then
runs `open-pr`, step 6 when `review-inputs` routes a reviewer, and `merge`.

| Rigor | Reviewers | Live tests | Merge |
|---|---|---|---|
| `normal` (default) | `code-reviewer` | only when the change affects a live group | auto-merge on green CI |
| `high` | `code-reviewer` and `security-reviewer` | every affected live group | the owner, or auto under a delegation |

Rigor is `high` for authentication, credentials, secrets, tenant data, money,
data deletion or an irreversible action. Any flag in `risks` (`auth`,
`secrets`, `data_placement`, `migration`, `irreversible`) also adds the
`security-reviewer` and escalates the resolved spawns.

Method skills: `skill-content/deliver/data/task-triggers.json` (project rules
in `.agentrof/task-triggers.json`) makes a method skill mandatory for the
paths and words it matches; a stack no rule names is worked from the code.
Role routing: `start` and `approve` match the task against
`skill-content/deliver/data/role-routing.json` (project rules in
`.agentrof/role-routing.json`) and write `roles` as `<role>:<mode>:<step>`.
The software architect is `require` for a data model, schema or migration
change, a structural change or size `l`; other roles are `suggest`. Spawn
each routed role with the task, the plan and only the notes it rests on,
within the rule's `time_budget_minutes` and with its `ask`; the architect
applies the record changes its pre-check calls for in the same spawn. Record
the answer with `role-check --task <id> --role <role> --verdict go|changes
--note "<reasons>"` and apply `changes` before writing. `open-pr` refuses
while a `require` role has no verdict.

1. Request. `start --title "<short title>" --request-file <file>` with the
   request verbatim (`--source-story ST-### --covers <numbers>` or `--epic
   EP-###` when they apply). It pushes
   `task/<id>-<slug>` from `origin/main` and writes the skeleton with
   `requested_at`, a proposed rigor, `risks`, `applied_skills` and `sources`
   as `path@<sha256 prefix>`.
2. Task. Fill it in one pass: the `### Do` list; 3 to 8 numbered criteria,
   each one verifiable property with exact codes and limits, including a
   `Live wiring:` and a `Docs delta:` criterion; a `## Tests` line per
   criterion; `size`; rigor, risks, triggered skills and sources. `lint
   --task <id>` until it passes, show the task with its rigor, and on the
   owner's go or a standing delegation run `approve --task <id>`. Approval
   never blocks defining: define the next task meanwhile, or the owner
   pre-approves a batch. More than 8 criteria is two tasks; split a size `l`
   task by layer or deployable, or run its writers in parallel on disjoint
   files. Then run the routed role checks.
3. Write. Spawn the owning developer with the task, the branch, its files and
   every `applied_skills` skill, loaded before writing. It writes the change
   and its tests (app tests inside the app, shared suites under `tests_root`),
   records and contracts the change needs, commits, and runs the CI test
   command for the suites it touched.
4. Live tests. At `normal`, run a live group only when the diff can alter what
   it proves, and say which and why; at `high`, every affected group. Each
   worktree that runs live checks gets its own environment namespace.
5. Pull request. Run `overlap` (warnings only), then `open-pr --task <id>`.
   When `workspace/config.json` sets `pr_proof` to `local_record`, run
   `prove --task <id>` first, as `skill-content/deliver/references/local-proof.md` defines.
6. Review. Spawn every reviewer `review-inputs --task <id>` lists, in
   parallel, with the task, `gh pr diff <pr>` and its result. Each checks
   every criterion against the diff and tests; an unfollowed skill in
   `missing_skills` is major, and a source in `changed_sources` is checked for
   a contradiction. Merge needs 0 critical and 0 major. The same writer fixes
   them on the branch and only the raising reviewer re-reads once; after one
   fix round the owner decides. Trivial minors are fixed in one commit;
   out-of-scope minors become one grouped issue (`gh issue create`). A design
   or policy question goes to the owner, never patched by the developer.
7. Merge. CI runs the full suite on the branch merged with current main;
   registries the change touches are updated in the same pull request. Run
   `merge --task <id> --pr <pr> --critical <n> --non-critical <n> --filed
   <n>`: at `normal` it auto-merges on green CI, and a red check is fixed like
   a critical finding. At `high` the owner merges unless an active delegation
   allows `merge`, then add `--delegated`. A green pull request never waits on
   a chat message: report one left to the owner with its expected finish
   time.
8. Report the task, the pull request and its merge state. A task repairing an
   earlier task's defect starts with `--fixes TK-###`; extra passes no red
   run or finding shows are recorded with `rework --task <id> --category
   <cause> --note "<why>"` for `/retrospective`.

The task is done when its pull request is merged; the task file on the target
branch is the durable record.
