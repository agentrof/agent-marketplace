# Execution Planning Flow

Spawn template: paste `{{constitution}}` into every role prompt.

`/execution-plan DLV-###` consumes one scope-approved Delivery. The compiler
stores topology on Item records and renders the Execution Plan as an exact
aggregate. It validates dependencies, cycles, path and contract claims, role
sequence, verification strategy and current source hashes before the user
approves the plan.

Approval is offline. `publish-execution-plan` is the only later network writer;
it creates no Item worktree, slot or product-code branch. Item claims begin only
after the published plan is verified remotely.

Every executable Item binds the current approved Verification Contract during
approval. Set `runtime_required: true` only when the Item genuinely needs a
live service environment; that Item then also binds the approved Environment
Contract. A later hash drift blocks start, resume, reopen and takeover until a
new execution plan is approved.

Every Item also declares `architecture_impact: required|not_applicable`, its
exact Solution component refs, requested architecture record kinds and a
reason. A required impact places `software_architect` first in the Item role
sequence. The Software Architect uses `architecture_compile.py` only after the
Item is claimed/active; the Item carries the compiler-stamped
`architecture_delta_hash` into verification and integration.
