# Backlog Planning Flow

The flow turns approved product knowledge into a versioned, project-local
backlog. Its canonical state is Markdown under `workspace/docs/backlog/`.

Spawn template: paste `{{constitution}}` into every role prompt. Load the
`obsidian-vault` skill before touching the docs tree; its policy is
authoritative.

## 0. Preconditions

- Requirement Flow has approved the request impact matrix. Every stage marked
  `required` is approved/current, every `reuse` target is valid, and every
  `not_applicable` row has its concrete rationale.
- A feature, defect or technical intake carries the exact approved source,
  issue or decision evidence selected by that impact matrix.
- The user explicitly starts the backlog entry and reviews each authored
  package.
- Requirement state comes only from the tracked documents and their checks.

## 1. Materialize the backlog tree

Run the packaged backlog compiler:

```text
backlog_compile.py init --docs <workspace>/docs
```

The root contains `backlog.md` and `reviews/`. Each epic is a folder with an
`epic.md`, `reviews/`, and `stories/`. Each story folder contains exactly
`story.md` and `test-plan.md`. Membership is derived from the path.

## 2. Author stories

The Product Owner authors each `story.md` with exactly these sections:

```text
User Value
Scope
Non-Goals
Implementation Responsibilities
Acceptance
Dependencies
Delivery Notes
```

Every story has one `owner_role` and may have `supporting_roles`. The owner is
accountable for the integrated story result. Every listed supporting role must
have a concrete contribution in `Implementation Responsibilities`; the owner
cannot be repeated there as a supporting role. Store team role identifiers,
never people or runtime identities.

Every story declares `work_kind: feature|defect|technical`. Its upstream links
are required when the Requirement impact matrix says those outputs constrain
the story. A defect or technical story may use approved or accepted `related_to`
source, issue or decision evidence when those feature-stage outputs are not
applicable. This is scoped intake evidence, not a replacement for traceability
when a stage applies.

The backlog normally scopes only the criteria and evidence explicitly selected
by its stories and root review. Add canonical `analysis_scopes` to
`backlog.md` (`<space>` or `<space>#domains/<path>`) only when the whole named
approved BA scope must receive an exact covered-or-deferred disposition.

Use vault-absolute wikilinks for `criterion_refs`, `experience_refs`,
`derives_from`, `depends_on`, `uses_design` and `constrained_by`. Criterion
links target the approved owning note and carry its BA registry-qualified
criterion or rule identity as the alias.
Dependency links target stories, and every dependency has a reason in the
`Dependencies` section. List position is not dependency evidence.

## 3. Author test plans

The QA Engineer and Business Analyst co-author the sibling `test-plan.md`.
Every scenario has a stable `<story-id>-TS-###` heading and this shape:

```markdown
## ST-001-TS-001

- category: happy-path
- target: api
- automation: required
- automation_target: tests/api/test_accounts.py::test_register_account
- source_refs:
  - [[business-analysis/accounts/acceptance/account-access-acceptance|accounts:AC-ACC-001]]
- Given: the preconditions are satisfied
- When: the story action occurs
- Then: the observable outcome is correct
```

`automation` is `required` or `manual`; `required` needs an
`automation_target`. The target records intended delivery work and need not
exist yet. Every scenario has non-empty `source_refs`. Feature scenarios cite
only their story's declared criteria. Defect and technical scenarios may cite
their story's declared criteria and approved `related_to` evidence. Every
declared planning source appears in at least one scenario.
The `Coverage Classes` table contains exactly `empty`, `boundary`,
`invalid-input`, `authorization`, `duplicate-concurrent`, `failure` and
`adjacent-regression`. Each row is `covered` with existing scenario IDs or
`not_applicable` with no scenario IDs and a concrete reason. The union of all
`covered` rows equals the story's exact scenario set; one scenario may cover
multiple classes, but none may remain unclassified. A missing class, unknown
scenario, orphan scenario or unexplained exclusion fails the compiler.

The Requirement trace ends at planned verification:

```text
criterion or rule -> scenario -> automation target
```

Executable tests, execution results, story completion and release readiness
belong to delivery.

## 4. Challenge and render

The Product Owner finishes the candidate package, then the active orchestrator
runs the packaged compiler before spawning any reviewer:

```text
backlog_compile.py check --docs <workspace>/docs --render --json
```

For each epic, build an explicit reviewer input containing the exact epic,
story and test-plan paths plus the expected `derives_from` and `verifies`
target sets. Invoke one fresh `backlog-reviewer` per epic. Independent epic
reviewers may run in parallel because they are read-only. Wait for every epic
reviewer to return before any writer action.

The Product Owner is the single writer: it triages the returned findings,
repairs source documents, and writes each designated epic review note. An epic
review uses `derives_from` for its owning epic and `verifies` for the exact
child story and test-plan set. Its body covers scope, slicing, criteria, test
design, intra-epic dependencies, role ownership, findings and verdict. Re-run
the compiler after these serialized writes.

Only after every epic package and review is green, invoke one fresh
`backlog-reviewer` with the root backlog, every epic and the exact expected
`derives_from` and `related_to` sets. Wait for its return. The Product Owner
then writes the root review note and any source fixes. The root review covers
cross-epic overlap, dependency direction, cycles, release ordering, shared
contracts, deferred criteria, global test coverage, findings and verdict.

Use the current host's agent invocation and wait mechanism; no host-specific
command is canonical. Reviewer responses are input, never durable state. If a
blocking finding remains, rerun only the affected reviewer after the Product
Owner's fix, then re-run the compiler. Continue until both review layers are
approved.

`Deferred Criteria` is a structured table with `criterion_ref`, `owner_role`,
`reason` and `revisit_trigger`; `owner_role` is exactly `product_owner`.
`criterion_ref` is an escaped-table,
vault-absolute wikilink to the approved owning BA note, for example
`[[business-analysis/erp/domains/inventory/rules/stock-rules\|erp:BR-INV-002]]`.
The compiler derives every active AC and BR in every approved BA registry when
the Requirement impact matrix includes that scope and requires the exact
universe to be covered by one or more story `criterion_refs`, or represented
once in this table, never both. A shared criterion may support multiple stories
when the delivery slices are distinct. Otherwise unrelated historical BA
remains out of scope unless `backlog.md` explicitly declares `analysis_scopes`;
a declared scope receives the same exact treatment. Unknown and uncovered
values fail. Every non-deferral review lens uses an
`Evidence [<section>]:` line with a resolvable vault note and a separate
`Conclusion [<section>]:` line. Long
generic approvals such as `the package was reviewed`, `looks good` or
`no findings` fail.

## 5. User approval and handoff

After the user approves the exact diff, run the packaged compiler atomically:

```text
backlog_compile.py approve --docs <workspace>/docs
backlog_compile.py check --docs <workspace>/docs --approved --render --json
```

Approval stamps the package, root backlog, epics, reviews and test plans while
stories remain `planned`. Commit `workspace/docs/backlog/` and the updated
`workspace/config.json` in the same project change. Report the package hash
and exact generated views. Stop and route to `delivery-plan`; do not create Delivery
state in this flow.

Human-facing authored titles use the project's configured document-type
designations. The root backlog and root review use the capitalized designation
as their complete title; user-authored epic/story/test-plan bases append their
type designation. Stable type keys, paths, IDs, CLI messages, registry JSON and
the disposable generated board, dependency and coverage view labels remain
English machine vocabulary.
