# Operation Contract Flow

Spawn template: paste `{{constitution}}`, the exact contract path, accepted
Solution decision bindings, command-safety lens and `SELF-CHECK` into every
reviewer prompt. Load the `obsidian-vault` skill before writing vault truth.

Read this complete flow before `/configure operation verification` or
`/configure operation environment` changes durable state. Operation is
project-global delivery truth under `workspace/docs/operation/`; it is not a
Requirement stage and it does not alter product-stage package hashes.

1. Resolve the selected kind. `qa-engineer` is the only Verification Contract
   writer; `devops-engineer` is the only Environment Contract writer. Both
   record exact accepted/current Solution decision references, repository
   relative workdirs and command semantics.
2. Run `operation_compile.py check --kind <kind> --json`. An approved contract
   changes only after `begin-revision`; a changed or superseded cited Solution
   decision makes the contract unusable until it is revised and re-approved.
3. Spawn the non-writing counterpart as a read-only reviewer when the contract
   crosses test/runtime boundaries. The review prompt includes the exact
   contract path, accepted Solution references, command safety lens and
   `SELF-CHECK`.
4. Approve with `operation_compile.py approve --kind <kind>`. Return the exact
   contract receipt. Do not run a downstream product stage automatically.
