# Requirement Flow

Spawn template: paste `{{constitution}}` into every role prompt. Load the
`obsidian-vault` skill before reading or writing the docs tree; its vault law is
authoritative.

This flow is the host-neutral sequence behind `/requirement` and the four
stage-by-stage entries. Durable truth is the Requirement record and the
approved stage packages under `workspace/docs/`; no runtime session or hidden
workflow mode is created.

## Sequence

1. Parse the public argument. Free text creates a new local Requirement,
   `REQ-###` selects exactly one record, and a bare invocation never fuzzy
   resumes an old record.
2. Compile and present Intent, Outcome/Acceptance, Scope/Non-goals,
   Evidence/Constraints and the four-row Stage Impact matrix.
3. Require Requirement approval before expensive stage work. The compiler
   owns the UTC approval stamp, semantic source hash and status tag.
4. Run only `required` stages in dependency order. `reuse` resolves to an
   approved current package; `not_applicable` has no evidence refs and keeps a
   concrete rationale. Every stage entry checks the same prerequisites.
5. Recheck the Requirement after every stage handoff. Semantic impact changes
   invalidate approval and require a new approval before downstream work.
6. Run Backlog Planning only when all required stage packages are current and
   the compiler-owned incorporation predicate is still open. An approved
   `resolved_no_change` Requirement is the only legal no-backlog terminal.

## User outcomes

Normal gates expose Approve, Request changes and Stop for now. Request changes
keeps the current draft and findings; Stop performs no mutation and leaves the
same exact resume command. Discard, Withdraw and Supersede are state-derived
exceptions and are never inferred from a generic rejection.

## Handoff

Backlog approval is committed through the project's ordinary Git policy. Only
after the exact Requirement and backlog revision reach target may Delivery
Planning consume them. Requirement Flow never creates Delivery branches,
worktrees, slots, PRs or Release Management state.
