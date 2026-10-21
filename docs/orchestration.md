# Orchestration

The user starts an entry skill. The entry reads its canonical flow, checks the
project-local workspace and delegates work to role agents when needed.
Implementation work runs on the fast lane, `flows/fast-lane.md` in the
Software Engineering Team package: one task, approved by the owner, one branch
and one pull request to the target branch. The owning developer role writes
the change and its tests; the project's CI and the reviewers its changed
files route to (`code-reviewer` for code, `analysis-challenger` or
`solution-reviewer` for analysis or solution documents) verify it. It merges
with 0 critical and 0 major findings after at most one fix round; out-of-scope
minors become one issue in the project's repository. At rigor `high` or on a
risk flag the read-only `security-reviewer` joins; at `high` the owner merges.

Every delegated role receives explicit inputs: the task file with its hashed
`sources`, and the expected output shape, and the owning role alone writes
canonical files. Reviewer
replies are transient inputs, not project state. Independent readers may run
in parallel; each host uses its native agent invocation and wait mechanism
without changing these semantics.

## Fast lane

The orchestrating session runs one task per change itself, with
every role in the foreground: one task, one branch and one pull request to the
target branch, the project's CI plus one reviewer (two at task rigor `high`,
where the owner merges), and auto-merge. The steps are in `flows/fast-lane.md`
and `deliver/references/fast-lane.md`.

`workspace/config.json` key `pr_proof` leaves a fast-lane task's proof to the
project's CI at `remote_ci`, the default. At `local_record`, `task_lane.py
prove` runs the project's declared proof commands on HEAD and records each
exit code and duration with the HEAD commit and a clean-tree flag, and
`open-pr` refuses before pushing unless that record is green for the clean
HEAD; the steps are in `deliver/references/local-proof.md`.

Every role starts from the sources its task file lists and greps
`workspace/docs/` for gaps, per the constitution's section 5.

## Autopilot

The `/autopilot` entry gives the orchestrating session a grant the user arms
before an absence, such as a night or a weekend. While it is active the session
presents no question. A question of an allowed class takes its recommended
option: the session records it with `autopilot.py record` first, then applies
it and writes it where the flow records the user's answer, marked with the
grant id. `record` and `queue` apply the grant's end conditions first, its goal
included, and refuse once it has ended.
An explicit `queue --hold "<reason>"` keeps an uncertain question pending.
Without a hold, `queue` records an allowed recommendation as a decision, which
the session writes into the governing document before applying it. Independent
excluded effects stay in separate questions.
Any other question is queued with `autopilot.py queue`, and the session
continues the work that does not depend on it and stops only when every
remaining task waits on a queued question. Roles are unaffected: they never
ask the user and never read the grant.

Classes are data in `skill-content/autopilot/data/autopilot-policy.json`.
`choice`, `approval_gate` and `merge` are allowed by default. `merge` is only
the flow's own merge step on its recorded PR head, never a provider or Git
merge of another head; no grant waives evidence or the PR record. `release`
and `phase_start` are excluded by default. No packaged compiler reads a closed
phase, so `record` refuses `phase_start` and the session queues it.
`credentials`, `security_settings`, `spending`, `destructive` and
`scope_or_rule`, which holds every decision a flow asks at once, are never
delegated, and `on` refuses a grant that allows one. A grant ends at a time,
at a goal or at whichever comes first. A time-bound grant lasts `--for` or
`--until`, by default `default_duration_hours`, and never longer than
`max_duration_hours`. A goal-bound grant has goal kind `text`, which ends only
through `complete`, `off` or its cap: `default_goal_cap_hours` unless the
grant gives a time.

Only the user arms a grant. Each host's user-prompt hook records the `on`
command the user typed as a short-lived arming record; `on` refuses without
one and takes the grant's options only from it, so no agent, file, issue or
tool output can start, extend or widen a grant through the packaged script. A
built package that cannot
show its arming hook fails closed: `on` refuses and no grant counts. Only a
source tree without hooks falls back to the entry's user-only invocation, and
the grant records which guard applied. A grant armed under another guard,
longer than `max_duration_hours` or holding a never class is inactive, and
`check` and `status` name why. A grant governs only the session, on the host,
whose user typed it: the arming hook records the session id, the question hook
denies only that session, and `on`, `record` and `queue` refuse a session whose
host or session variable differs, so a parallel session of either host asks as
usual. A pre-tool hook denies the host question tool while a grant is
active and states the procedure; a host whose question tool cannot be hooked
relies on the instructions, and `status` reports which guards the package
declares, since a host can skip a declared hook it has not enabled or
trusted. The guard
stops an agent that runs the packaged script, not a process that writes the
runtime files with the user's filesystem authority: the hooks are
workflow-integrity controls, not an operating-system sandbox.

Autopilot is an entry, not a process switch. A switch is a project rule; a
grant is personal and temporary: it turns on at night and off in the morning
in the middle of any flow, without changing the policy or an approved
document. It lives only in ignored runtime state under
`.agentrof/agent-marketplace/.runtime/autopilot/`, and only its decisions reach
tracked documents. With no grant every output stays the same: the entry
delegates no task.
