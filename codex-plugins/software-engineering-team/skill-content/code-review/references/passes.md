# The Three Passes in Depth

Fixed order on every cycle: correctness, then conformance, then security.
Before any pass, build the complete changed-file inventory (no file skipped)
and plan the order: security-critical files first (auth, middleware, config
with secrets), then core business logic, then the interface layer, then
infrastructure and configuration, then tests, then documentation. Stack
checklists loaded from the bound stack skills extend each pass; they never
replace it.

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

## Pass 3: Security

Runs on every cycle, even when the diff looks unrelated to security.

### Authentication and Authorization

- [ ] Authentication required on every non-public entry point; the
      protected-by-default stance holds
- [ ] Authorization checked before every action, not just at login;
      object-level access verified (no ID-guessing across tenants or users)
- [ ] Token validation complete: signature, expiry, audience; no trust in
      client-supplied identity claims
- [ ] Role and permission checks server-side; no privilege decision made
      from client input
- [ ] Session and logout semantics: state cleared, tokens invalidated

### Input Validation

- [ ] All user input validated at the boundary: type, length, range,
      format; allowlist over blocklist
- [ ] Database queries parameterized; no string-built queries anywhere
- [ ] Output encoded for its context; no raw interpolation into markup,
      shell, or query languages
- [ ] File uploads restricted by size and type; path traversal blocked on
      any user-influenced path
- [ ] Deserialization of untrusted data constrained to expected shapes

### Data Protection

- [ ] No hardcoded secrets, keys, or credentials; secrets come from the
      environment or a secret store
- [ ] Passwords hashed with a modern adaptive algorithm; never logged,
      never returned
- [ ] Sensitive data and personal information absent from logs, error
      messages, and URLs
- [ ] Transport encryption enforced for sensitive flows; request size
      limits present

### Common Vulnerability Classes

- [ ] Injection (query, command, template) ruled out at every sink
- [ ] Cross-site scripting: output escaping and content-type headers
- [ ] Cross-site request forgery protection on state-changing operations
- [ ] No dynamic code execution on user-influenced input
- [ ] Rate limiting on public and authentication endpoints
- [ ] Error responses terse in production; no stack traces or internal
      paths to callers
- [ ] New dependencies checked for known vulnerabilities

### Security Pass Closure

End the pass with a short attack-surface summary: entry points touched by
the change, which are protected versus public, and the data entry points
introduced. A security finding that implicates the approved auth design
follows the escalation route, same as conformance.
