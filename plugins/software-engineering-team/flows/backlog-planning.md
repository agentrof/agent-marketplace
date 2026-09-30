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
- Manual mode may start from a strict-current BA scope, solution landscape,
  Design System MASTER, globally current `application@rN` and zero or more
  living Experience process packages without a Requirement. Zero process
  receipts are valid only when the application is the verified empty
  application; otherwise the supplied set must exactly equal its process set.
  In manual mode the root package records `Input Package Coverage` rather than
  Requirement Coverage.
- A feature, defect or technical intake carries the exact approved source,
  issue or decision evidence selected by that impact matrix.
- The user explicitly starts the backlog entry and reviews each authored
  package.
- Requirement state, when present, comes only from the tracked documents and
  their checks.

## 1. Materialize the backlog tree

Run the packaged backlog compiler:

```text
backlog_compile.py init --docs <workspace>/docs --planning-mode requirement --requirement-ref REQ-###
```

For a manual chain, initialize with the exact approved input package refs:

```text
backlog_compile.py init --docs <workspace>/docs --planning-mode manual \\
  --input-ref "business-analysis/<space>/space" \\
  --input-ref "[[solution-design/landscape|Solution landscape]]" \\
  --input-ref "[[design-system/MASTER|Design Master]]" \\
  --input-ref "application@r4" \\
  --input-ref "checkout@r3"
```

The compiler validates the three strict-current upstream package families plus
the globally current Experience application and its selected process receipt
set, then renders `backlog/_generated/input-package-coverage.md`. The view lists
each bound package with its stage and receipt hash, whether the package
resolver still finds that receipt strict-current, and how many stories cite the
package. Requirement
mode instead records `requirement_ref: REQ-###`; stories carry
`implements: REQ-###`, and the complete Requirement Stage Results receipt set
is required before approval.

By default both modes pin the same four input families in compiler-owned
`input_bindings`, and the compiler rejects any binding that is no longer
strict-current. In Requirement mode, a stage the Requirement changes or reuses
binds exactly its Stage Results receipt. A stage the Requirement marks
`not_applicable` binds the exact approved package named with `--input-ref`
(allowed only for such stages) or, at `begin-revision`, the previous
revision's binding, carried forward. A carried binding whose package has since
advanced fails preflight until it is rebound with `--input-ref`, so a package
the Requirement does not touch can never drift unnoticed.

An explicit `headless-v1` exception is available only for a technical or defect
Requirement whose visual stages are `not_applicable`. Pass
`--absent-input design-system` and/or `--absent-input experience-design` to
`init` or `begin-revision` for each genuinely absent family. The compiler still
requires current approved BA and Solution receipts, an explicit Solution
topology containing only CLI, worker or scheduler build components, no authored
Backlog links into the omitted family, and complete Git history proving that
the family never contained package files. An existing or previously bound
package cannot become absent by deleting it. Manual and feature planning keep
the complete input contract. Every new revision must explicitly repeat and
revalidate its absence flags; the compiler records `input_contract` and
`absent_input_stages` and shows the omission in Input Package Coverage. Existing
approved Delivery snapshots retain their original verified boundary when the
project later gains visual packages.

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

Use exact `<experience>:<ID>@rN` values such as `checkout:SCR-001@r2` for
`experience_refs`. Each value must resolve in a pinned current process receipt
for the pinned `application@rN`.
Use vault-absolute wikilinks for `criterion_refs`,
`derives_from`, `depends_on`, `uses_design` and `constrained_by`. Criterion
links target the approved owning note and carry its BA registry-qualified
criterion or rule identity as the alias.
Dependency links target stories, and every dependency has a reason in the
`Dependencies` section. List position is not dependency evidence.

## 3. Author test plans

The QA Engineer and Business Analyst contribute scenario proposals for the
sibling `test-plan.md`. The Product Owner remains the sole canonical backlog
writer and persists their reviewed contributions after both readers finish.
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

Switch `review_panels`: at `lens_panel`, review panel `backlog_epic` and review
panel `backlog_root` replace this section's epic and root reviewers, as
`skill-content/challenge-review/references/switch-review_panels-lens_panel.md`
defines. Switch `mechanical_pass_tier`: at `mechanical`, a Product Owner pass
that only applies the fixes returned findings name, and the compiler commands
of this section and section 5, run as
`skill-content/challenge-review/references/switch-mechanical_pass_tier-mechanical.md`
defines.
Switch `review_loop`: at `blocking_delta`, this section's review loops
follow
`skill-content/challenge-review/references/switch-review_loop-blocking_delta.md`.
Switch `story_size_budget`: at `propose_split`, `check --json` also reports
each story's size against the owner-set limits, and before the first epic
review manifest the Product Owner proposes a split for each story over budget,
which the owner accepts or keeps, as
`skill-content/product-planning/references/switch-story_size_budget-propose_split.md`
defines.

### Recovery that removes only operating-system metadata

An approved backlog may reuse its existing epic review evidence when the only
upstream change is an approved application successor from the explicit
schema-v4 artifact recovery path. The recovery proof must show no added,
changed or meaningfully removed artifact: the successor inventory equals the
predecessor inventory minus the exact schema-policy metadata rows. Process
receipt refs and hashes, BA, Solution, Design System and any Requirement
semantic hash must remain identical.

Before rebinding, record the committed predecessor HEAD and the complete path
and byte inventory of every epic, story, test plan and historical review.
After rebinding, historical reviews, stories and test plans remain byte-exact.
Each epic retains its authored source, `approved_at_utc` and `source_hash`;
only its renderer-owned inverse-relation block may change to reflect the new
root review's existing epic targets. The full vault gate must prove that block
is the exact generated projection. Authored relation, dependency, role,
coverage and scenario sets remain identical. Only the backlog root's receipt
bindings/lifecycle and a fresh root review may otherwise change. Run the full
compiler and vault gates. A fresh root reviewer independently verifies these conditions
and the exact recovery delta before ordinary user approval, atomic approval
and commit. Reused epic reviews retain their original bytes, stamps and hashes;
do not create new epic approval claims. Any missing proof or meaningful delta
returns to the normal review flow below. This exception does not apply to
other application-only revisions or general artifact loss.

The Product Owner finishes the candidate source documents. Review notes may
still contain their initialized placeholders: requiring completed reviews
before their readers run would prevent the first review. For each epic, run
the packaged read-only manifest helper; it validates source, coverage and
dependency inputs while leaving review-completion checks to the final gate.
An epic's manifest fails on a `stub-epic` or `stub-story` placeholder only in
a path it names and lists the others in `check.scaffold_findings`, so a
finished epic's review can start while another epic's writer still works. The
root manifest and approval still need every source finished:

```text
backlog_review_inputs.py --docs <workspace>/docs --epic <EP-ID>
```

Give one fresh `backlog-reviewer` the returned manifest and every named path:
the root backlog, that epic, its child stories and test plans, and the incoming
and outgoing dependency closure with shared contract/source context. Include
the expected `derives_from` and `verifies` sets and any `unparsed_link_sources`:
approved upstream notes whose link text does not parse, which the reader checks
for a missed source instead of failing dispatch. The manifest is disposable
review input, never a second backlog or approval record. Unresolved closure
fails before dispatch; evidence outside the manifest requires an expanded
input set. Independent epic reviewers may run in parallel against unchanged
inputs. Wait for every epic reviewer to return before any writer action.
Recompute each manifest with `--expected-hash <source_hash>` before accepting
its findings for the writer. Changed inputs require a fresh affected review;
never use a stale manifest to justify omitting a dependency.

Readers audit source membership against the manifest's expected relation sets.
Empty draft review fields and placeholder prose await the writer and do not
by themselves request source changes. Malformed or incorrect nonempty review
declarations and stale completed evidence offered for the current candidate
remain findings. The final compiler requires the exact written relation sets
and complete review prose before approval.

The Product Owner is the single writer: it triages the returned findings,
repairs source documents, and writes each designated epic review note. An epic
review uses `derives_from` for its owning epic and `verifies` for the exact
child story and test-plan set. Its body covers scope, slicing, criteria, test
design, intra-epic dependencies, role ownership, findings and verdict. Run
`backlog_compile.py check --docs <workspace>/docs --json` after these serialized
writes and resolve all source and completed epic-review findings. Only the
still-unwritten root review's completion findings remain pending until its
reader returns; they do not authorize ignoring any source finding. An entry
in `advisories` names an empty last section of a story approved before the
compiler read that section above the navigation; it never fails the check,
and the Product Owner fills the section whenever that story is revised.

Only after every epic package and review is green, run
`backlog_review_inputs.py --docs <workspace>/docs --root`. Invoke one fresh
`backlog-reviewer` with that manifest: the root backlog, every epic, every
story and every test plan, its declared context, any `unparsed_link_sources`
and the exact expected `derives_from` and `related_to` sets. Wait for its
return. Recompute the root
manifest with `--expected-hash <source_hash>` before accepting its findings;
a changed input requires a fresh affected review. The Product Owner then
writes the root review note and any source fixes. The root review covers
cross-epic overlap, dependency direction, cycles, delivery sequencing, shared
contracts, deferred criteria, global test coverage, findings and verdict. After
the root review is authored, run the full
`backlog_compile.py check --docs <workspace>/docs --render --json` and scoped
vault gate. Both must pass before the package can be offered for approval.

Use the current host's agent invocation and wait mechanism; no host-specific
command is canonical. Reviewer responses are input, never durable state.
Severity, dispositions and re-review scope follow the Review findings section
of `skill-content/product-planning/references/structured-records.md`: only a
critical or major finding blocks, and the Product Owner preserves every
returned severity. After a blocking fix or disproof, regenerate the affected
manifest and rerun only the affected reviewer with those findings, any cited
evidence and the changed paths, which are the manifest files whose `sha256`
changed. It confirms each finding is closed and reviews the changed text with
its dependency context. Then re-run the compiler. A writer's assertion that
the fix is complete does not replace that recheck. A minor finding never
blocks or starts another round: fix it only in a pass that already carries a
blocking fix, otherwise record it in the review note's
`Accepted Minor Findings` section. Continue until both review layers are
approved; no extra clean round is required when no blocking finding remains.

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

`Accepted Minor Findings` is optional in any review note. When present it is a
structured table with `finding`, `owner_role`, `reason` and `revisit_trigger`;
`finding` cites the affected vault note with an escaped-table wikilink and
`owner_role` is `product_owner`, `qa_engineer` or `business_analyst`. The
compiler validates every row. A critical or major finding never enters this
section; it closes only through a fix or disproof that the re-review confirms.

## 5. User approval and handoff

After the user approves the exact diff, run the packaged compiler atomically:

```text
backlog_compile.py approve --docs <workspace>/docs
backlog_compile.py check --docs <workspace>/docs --approved --render --json
```

Approval stamps the package, root backlog, epics, reviews and test plans while
stories remain `planned`. Existing valid unchanged source approvals retain
their timestamps, hashes and bytes; previously approved reviews are immutable
and changes require a new review round. Setup's managed `.gitattributes` rule
checks `workspace/docs/` out without line-ending conversion, which keeps these
bytes exact on Windows. Commit `workspace/docs/backlog/` and the updated
`workspace/config.json` in the same project change. Report the package hash
and exact generated views. A newer approved Experience application receipt,
including one caused by an application-only revision, makes the backlog input
non-current. Revise and reapprove the backlog against the new receipt before
further handoff even when its process receipts did not change. Stop and route
to `delivery-plan`; do not create Delivery state in this flow.

Human-facing authored titles are direct, natural labels in the project's
output language. Stable type keys, paths, IDs, CLI messages, registry JSON and
the disposable generated board, dependency and coverage view labels remain
English machine vocabulary.
