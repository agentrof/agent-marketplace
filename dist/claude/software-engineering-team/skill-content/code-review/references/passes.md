# The Two Passes in Depth

Fixed order on every cycle: correctness, then conformance. Security belongs
to the `security-reviewer` (see the review skill). Before any pass, build
the complete changed-file inventory (no file skipped) and plan the order:
core business logic first, then the interface layer, then
infrastructure and configuration, then tests, then documentation. Stack
checklists loaded from the bound stack skills extend each pass; they never
replace it.

## Verification

DO use the project's operation documents and existing verification
evidence, such as the pull request's CI results. Keep verification read-only;
never stage or change another role's candidate. For Agent Marketplace package PRs, use `make check-local` and
`make verify-local` against the owner's exact staged candidate, plus at most
the single test named in a finding. If that candidate is unavailable, report
missing evidence. Run a full test module, `make check`, or a `--full` local gate
only to reproduce a CI failure already reported on the exact PR head, and
cite that failure with the reproduction command. Pull request CI owns the
full suite and required platform and host checks; bind its results to the
exact PR head before claiming readiness.

## Pass 1: Correctness

The change does what it claims, for all inputs, on all paths.

### Logic

- [ ] The implementation matches the stated intent of the change; trace the
      main path end to end rather than pattern-matching on names
- [ ] Conditionals cover all branches; no impossible or unreachable branch,
      no inverted comparison
- [ ] Loops terminate; off-by-one bounds checked at both ends
- [ ] State mutations happen in the intended order; no read-modify-write
      race on shared state
- [ ] Concurrency: shared resources guarded, no await/async result ignored,
      no fire-and-forget with observable consequences

### Edge Cases

- [ ] Empty, null, and missing inputs handled explicitly
- [ ] Boundary values: zero, negative, maximum size, empty string,
      duplicate entries
- [ ] Collections: behavior defined for zero, one, and many elements
- [ ] Time and encoding: timezone-aware timestamps, unicode-safe string
      handling, no naive date arithmetic
- [ ] Identity source: tenant, user and actor read from the server-side
      session, never from the request body or client-set headers
- [ ] Membership filter: list and read paths filter by the caller's
      membership or role, not only by the tenant boundary
- [ ] Bounds: page sizes, upload sizes, counts and loops over input have
      an upper limit
- [ ] Deadline: one deadline, set at request arrival, bounds every
      outbound call the request makes
- [ ] Idempotency: every re-runnable step is idempotent; a create takes a
      client idempotency key (same body replays, different body is 409)

### Error Propagation

- [ ] Every failure path is handled or deliberately propagated; nothing
      swallowed silently
- [ ] Exception handlers catch specific types; no bare catch-all that hides
      unrelated failures
- [ ] Errors are transformed correctly at each layer boundary: internal
      details never leak outward, context is never lost inward
- [ ] Failure responses use correct status codes and a consistent error
      shape
- [ ] Resources released on all paths, including the failure path
      (connections, file handles, locks)

### Type Safety

- [ ] Public interfaces fully typed; no untyped escape hatches on exported
      functions
- [ ] Validation models match the data they claim to validate; optionality
      and nullability declared honestly
- [ ] No silent coercion across a boundary (string to number, loose
      equality, implicit truthiness on possibly-zero values)

## Pass 2: Conformance

The change against the approved design documents, which are the source of
truth. The reviewer verifies conformance; it does not re-design.

State structural findings in the same coupling and cohesion vocabulary the
architect designs with, defined in
[design-qualities](../../software-architecture/references/design-qualities.md).
A finding that names the coupling grade a change introduces, or the cohesion
it breaks, can be argued against the design documents; a finding phrased in
ad hoc structural terms cannot.

### Contract

- [ ] Endpoints, payloads, and status codes match the declared interface
      contract exactly: names, shapes, optionality
- [ ] Data access matches the declared schema: fields, types, constraints,
      relationships; no field invented in code that the schema does not
      declare
- [ ] Configuration read in code matches the declared environment
      specification; no undeclared variable; no hard-coded variation
      point: enums, thresholds, formats, taxonomies and policy values
      are declared data (config or schema) even when the spec does not
      name them configurable
- [ ] File placement follows the declared structure; layer separation
      respected (no interface layer reaching past the service layer into
      storage)
- [ ] Recorded decisions honored; a change that contradicts a decision is
      a finding even when the code works

### Docs Delta

- [ ] The README and the interface and architecture records the change
      makes stale are updated in the same pull request
- [ ] No new helper duplicates an existing one the change could have used

### Ownership Map

- [ ] Every write in the diff targets data the writing module owns per the
      ownership map
- [ ] No second writer introduced for a field that already has an owner
- [ ] Any copy of data owned elsewhere is declared, with a named sync path
      and staleness tolerance; an undeclared denormalized copy of a mutable
      field is an escalation, not a fix-loop finding
- [ ] Cross-module calls go through declared interfaces, not through
      another module's internals or its tables

### Dependencies

- [ ] Import chains acyclic; no new circular dependency
- [ ] Cross-module interfaces consistent on both sides of every call
- [ ] No unused or duplicated dependency added

### Architectural Impact Rating

Every conformance finding carries one rating:

- **High:** violates a contract, ownership boundary, or recorded decision;
  correcting it changes an interface or the data model. Candidate for
  escalation to the architecture owner.
- **Medium:** deviates from declared structure or layering but is
  correctable locally without touching any contract.
- **Low:** cosmetic drift from conventions; MINOR severity, never blocks.


## Production reachability

Before judging a new function, command or record reader, trace each new
symbol from a real production entry point and each record to its producer. A
test-only caller is not wiring. Resolve callbacks, imports, dynamic paths and
external producers explicitly. Report missing wiring in the current task so it
does not become later unplanned work.
