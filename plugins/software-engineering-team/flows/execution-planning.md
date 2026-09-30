# Execution Planning Flow

Spawn template: paste `{{constitution}}` into every role prompt.

`/execution-plan DLV-###` consumes one scope-approved Delivery. The compiler
stores topology on Item records and renders the Execution Plan as an exact
aggregate. It validates dependencies, cycles, path and contract claims, role
sequence, verification strategy and current source hashes before the user
approves the plan.

New Items explicitly declare `verification_schedule: parallel_snapshot_v1`.
Implementation roles retain their approved order; independent Code Review
and QA then run against one frozen candidate before the owner write barrier.
`role_sequence` keeps the complete role inventory. An absent schedule in an
older approved Item means `sequential_v1` without rewriting or rehashing it.
Changing a schedule uses the normal execution revision, approval and publication
path; unknown schedules are rejected.
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
