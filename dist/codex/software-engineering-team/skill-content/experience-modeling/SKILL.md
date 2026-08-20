---
name: experience-modeling
description: Internal knowledge for living, process-owned Experience packages, stable journey/flow/screen/state/transition identities, revisions, coverage, artifacts and deterministic gates.
exposure: internal
---

# Experience Modeling

Treat Experience Design as a living graph owned by a primary Business Analysis
process, not as a deployment release or numbered baseline.

## When to Use

Load for Experience authoring, review and exact Experience references in
backlog planning.

## Core Rules

- Use `experiences/<process-slug>/experience.md`. The slug is the semantic
  primary BA process name; it is never `exp-*`, `EXP-*`, a Requirement,
  release or technical component name.
- One active Experience owns one primary process. Cross-process behavior has
  one owner and exact references from related processes/packages.
- Primary and related BA process references are vault-relative, extensionless
  topology paths. Root and arbitrarily nested domain processes are both valid
  only when BA's compiler classifies the target as an approved process in the
  selected strict-current BA package.
- Use `JRN`, `FLW`, `SCR`, `STA` and `TRN` stable IDs. Cite records as
  `checkout:SCR-001@r2` and packages as `checkout@r3`.
- Update a package in place with `begin-revision`. Changed children increment
  revision and carry `supersedes`; unchanged children retain their IDs and
  revisions. Retired children leave the active registry but remain resolvable
  through `_ledger/` for historical work.
- The root package owns lifecycle approval. Child records use only
  `record_state: active|retired`; child approval fields are forbidden.
- Requirement and manual mode share scope proposal, authoring, challenge and
  approval. Requirement mode adds exact Requirement traceability and binds the
  final receipt set; manual mode never creates Requirement state.
- A scope proposal is a transient JSON payload, not vault history. Its hash
  covers the complete action set, selected BA/Solution/Design receipt hashes,
  canonical primary BA process references and Requirement semantic state. Pass
  the exact proposal file and hash to every lifecycle mutation and approval.
- Generate only with `experience_compile.py`; `_generated/` is disposable and
  `_ledger/` is durable compiler-owned revision/alias truth.
- Artifacts live in the owning package, are network-free, declare only known
  IDs and carry the rendered registry hash. Never persist reviewer rounds or
  history documents.
- Mechanical checks cannot be waived. Fresh read-only reviewers challenge
  semantic coverage; the UX Designer fixes canonical records or records a real
  assumption/open question before rerunning affected lenses.
