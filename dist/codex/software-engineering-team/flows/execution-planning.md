# Execution Planning Flow

Spawn template: paste `{{constitution}}` into every role prompt.

Vault first, per constitution section 5: every role starts from the default `project_reading` plan.
Batch-read its units, using the frozen context reader for frozen candidates. When context is insufficient, wrong or unavailable, use manual search, reads and relationship discovery
on the role's initiative or parent direction; record sources and reasons, rebind evidence, preserve gates and return `context_findings` to the parent for user-approved reporting.
For gaps, use `vault_query.py`, machine indexes and generated views, typed frontmatter, relation blocks and wikilinks, then maps and targeted search.
Fallback navigation with a shell can use `home.md`, `maps/_generated/relation-status.md`,
`maps/_generated/cross-subtree-matrix.md` and
`maps/_generated/stale-relations.md`, `maps/delivery.md` and each Item's
`path_claims`, `contract_claims`, `waits_for` and `dependency_bindings`.
Switch `context_pack`: at `role_digest`, a spawned role receives its
pack from `context_pack.py build --entry <entry> --role <role> --mode
<mode> --project-root <root>` instead of the full required reads; it
reads a named source in full only when the pack does not cover a case,
and records that read and why, as
`skill-content/challenge-review/references/switch-context_pack-role_digest.md`
defines. Switch `step_timing`: at `recorded`, the coordinator runs
`step_timing.py start --run <run> --step <step> [--budget <id>]` before
each step and `step_timing.py end --run <run> --span <span>` after it,
and records each role spawn with `--kind spawn --parent <step span>
--role <role> --phase reading|writing|review|re_review|waiting`. An end
that returns overrun is reported to the owner at once with its
breakdown, largest contributor and lever. The run ends with
`step_timing.py report --run <run> --write`, as
`skill-content/challenge-review/references/switch-step_timing-recorded.md`
defines. Switch `step_budgets`: at `enforced`, each budgeted step is
compared with its maximum, the owner's parameter or the package target
in `skill-content/configure/data/step-budgets.json`; a step may finish
well under it, and only an exceeded maximum is reported, never blocking,
as
`skill-content/challenge-review/references/switch-step_budgets-enforced.md`
defines.

`/execution-plan DLV-###` consumes one scope-approved Delivery. The compiler
stores topology on Item records and renders the Execution Plan as an exact
aggregate. It validates dependencies, cycles, path and contract claims, role
sequence, verification strategy and current source hashes before the user
approves the plan: `delivery_compile.py check-plan --delivery DLV-###`, with
each `--reopen` the approval will name, reports every refusal
`approve-execution` would raise, from the same checks, and writes nothing.
Show the plan to the user only once it passes; return each finding to the
topology or contract writer first.
Switch `owner_gates`: at `two_fixed_gates`, the owner decides the scope, the
plan and every Operation or Governance change it needs together in gate A, whose
approval is also the go for Item start, as
`skill-content/deliver/references/switch-owner_gates-two_fixed_gates.md`
defines.
Switch `delivery_path`: at `light_when_eligible`, an eligible Delivery's Item
topology comes from a topology-only pass inside `/delivery-plan` and is
approved in its one owner gate, while `/execution-plan DLV-###` stays the path
for a plan revision and for a Delivery that left the light path, as
`skill-content/execution-plan/references/switch-delivery_path-light_when_eligible.md`
defines.

New Items explicitly declare `verification_schedule: parallel_snapshot_v1`.
Implementation roles retain their approved order; independent Code Review
and QA then run against one frozen candidate before the owner write barrier.
`role_sequence` keeps the complete role inventory. An absent schedule in an
older approved Item means `sequential_v1` without rewriting or rehashing it.
Changing a schedule uses the normal execution revision, approval and publication
path; unknown schedules are rejected.
Switch `implementation_schedule`: at `parallel_lanes_v1`, Items declare lane
scopes and seams and their implementation roles run in parallel lanes, as
`skill-content/execution-plan/references/switch-implementation_schedule-parallel_lanes_v1.md`
defines.
For parallel verification, approved test and environment commands must work
from an independent checkout containing tracked files. Confirm dependency
provisioning in those commands or an explicitly supplied fixed environment;
the runner does not copy the writer's ignored dependency directories. Revise
and approve an unsuitable Operation Contract before starting the Item.

Approval is offline. `publish-execution-plan` is the only later network writer;
it creates no Item worktree, slot or product-code branch. Item claims begin only
after the published plan is verified remotely. Each approval lists the earlier
approvals it supersedes. Publication refuses with `DELIVERY_PLAN_SUPERSEDED` a
plan that differs from the Integration's when its approval does not list the
Integration's, and a pinned Operation contract that is neither the
Integration's approved revision nor a later one; take the Delivery package and
the Operation contracts from the Integration, then revise inside
`begin-plan-revision`.
Switch `provisional_claims`: at `during_plan_revision`, while its own
plan-revision barrier is held the coordinator records a provisional claim of a
path the draft adds to an active Item, and `finish-plan-revision` or
`abort-plan-revision` promotes, orphans or withdraws it, as
`skill-content/deliver/references/switch-provisional_claims-during_plan_revision.md`
defines.

Every executable Item binds the current approved Verification Contract during
approval. Set `runtime_required: true` only when the Item genuinely needs a
live service environment; that Item then also binds the approved Environment
Contract. An Operation revision that no Item pins reaches the Delivery through
the target branch; record it there, then run `refresh-target`. A later hash
drift blocks start, resume, reopen and takeover until a new execution plan is
approved. Re-approval refreshes every Item's Story and
Test Plan pins and the Delivery's backlog and Definition of Done pins from the
current approved sources. A sealed Item keeps the Operation bindings its
evidence was produced against unless the approval names it with `--reopen`;
that Item is rebound to the current contracts, stays integrated, and can then
be reopened. Publication keeps a sealed Item's review and verification records
as the Integration holds them, and keeps its Item record unless the approval
started from that sealed record, so take the sealed records from the
Integration before approving a change to a sealed Item.

Switch `execution_planning`: at `single_source_bundle`, every execution-planning
fact is written once, in the section that
`skill-content/execution-plan/data/fact-ownership.json` names, and every other
document links that section; `software-architect` is the only Item topology
writer and the only architecture decision record writer, and
`delivery-coordinator` is the only User Decisions writer, beside the Operation
contract writers. The revised contracts and the Item topology take one bundle
review, and a revised contract that no Item pins is recorded on the target
branch and brought in with `refresh-target` before publication, as
`skill-content/execution-plan/references/switch-execution_planning-single_source_bundle.md`
defines; the Software Architect follows
`skill-content/software-architecture/references/switch-execution_planning-single_source_bundle.md`.
Switch `review_panels`: at `lens_panel`, review panel `execution_bundle` replaces
the bundle's counterpart readers, as
`skill-content/challenge-review/references/switch-review_panels-lens_panel.md`
defines.
Switch `reader_waves`: at `all_at_once`, every reader of a review or recheck
wave starts at once, after every finished worker is closed, as
`skill-content/challenge-review/references/switch-reader_waves-all_at_once.md`
and the host contract define.
Switch `review_scope`: at `impact_closure`, `delivery_compile.py check-plan`
runs before any reader is spawned and readers read the changed Items' closure
over their claims and bindings; `task_inputs.py` derives each reader's inputs
with `--changed <note>` and `--base <approved commit>`, and a re-check given
`--findings` derives only the fix delta; every role reads the change's impact
closure, each approved, unchanged note outside it only as its hash-bound
summary, reads beyond it when unsure and records why, a writer fixes a reported
graph gap through its owning compiler and recomputes the closure, and a
confirmation re-review reads only the fix's delta, as
`skill-content/challenge-review/references/switch-review_scope-impact_closure.md`
defines.
Switch `review_fanout`: at `per_unit`, a review of more than one changed Item
or revised contract spawns one reader per unit in one message and then one
aggregator for the cross-unit checks no compiler enforces, as
`skill-content/challenge-review/references/switch-review_fanout-per_unit.md`
defines.
Switch `review_levels`: at `concurrent_when_independent`, the bundle's
contract reviews start beside its Item topology review when neither cites the
other's open findings, and approval still waits for every level, as
`skill-content/challenge-review/references/switch-review_levels-concurrent_when_independent.md`
defines.

Approval, and every re-approval, also refuses until a committed workflow will
run on the Delivery PR, because the final merge needs a green provider check.
When none exists, offer the one that `operation_compile.py render-ci`
materializes; it counts once it is committed and pushed to the target branch.
A project whose checks come from outside its workflows instead declares that
source in the approved Verification Contract; approval then requires no
workflow and reports that the declared provider must still turn the Delivery
PR's checks green. `skill-content/setup/references/ci-bootstrap.md` defines
which workflows, refs and declarations count.

Every Item also declares `architecture_impact: required|not_applicable`, its
exact Solution component refs, requested architecture record kinds and a
reason. A required impact places `software_architect` first in the Item role
sequence. The Software Architect uses `architecture_compile.py` only after the
Item is claimed/active; the Item carries the compiler-stamped
`architecture_delta_hash` into verification and integration.
