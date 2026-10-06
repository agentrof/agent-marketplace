# Business Analysis Flow

Spawn template: paste `{{constitution}}`, exact input/output paths, review
lens and `SELF-CHECK` into every reviewer prompt.

Vault first, in the order the constitution's section 5 sets: every role
queries the vault with the packaged `vault_query.py` verbs first, then
follows machine indexes and generated views, typed frontmatter, relation
blocks and wikilinks, then maps, and runs a targeted search only for a gap;
when the tools give too little or look wrong it uses its own methods and
records that it did. This flow starts from `home.md`,
`maps/_generated/relation-status.md`,
`maps/_generated/cross-subtree-matrix.md` and
`maps/_generated/stale-relations.md`, the space's `_generated/registry.json`,
`_generated/status.md` and `_generated/open-questions.md`, and
`maps/_generated/uncovered-analysis.md`.
Switch `context_pack`: at `role_digest`, a spawned role receives its pack from
`context_pack.py build --entry <entry> --role <role> --mode <mode>
--project-root <root>` instead of the full required reads; it reads a named
source in full only when the pack does not cover a case, and records that read
and why, as
`skill-content/challenge-review/references/switch-context_pack-role_digest.md`
defines.
Switch `step_timing`: at `recorded`, the coordinator runs `step_timing.py
start --run <run> --step <step> [--budget <id>]` before each step and
`step_timing.py end --run <run> --span <span>` after it, and records each role
spawn with `--kind spawn --parent <step span> --role <role> --phase
reading|writing|review|re_review|waiting`. An end that returns overrun is
reported to the owner at once with its breakdown, largest contributor and
lever. The run ends with `step_timing.py report --run <run> --write`, as
`skill-content/challenge-review/references/switch-step_timing-recorded.md`
defines.
Switch `step_budgets`: at `enforced`, each budgeted step is compared with its
maximum, the owner's parameter or the package target in
`skill-content/configure/data/step-budgets.json`; a step may finish well under
it, and only an exceeded maximum is reported, never blocking, as
`skill-content/challenge-review/references/switch-step_budgets-enforced.md`
defines.

Read this complete flow before `/business-analysis` changes durable state.
An exact `REQ-###` is Requirement mode; no Requirement argument is manual
mode. Manual mode never reads, creates or binds Requirement state.

1. Run the BA package resolver preflight. Requirement mode first confirms the
   router action is `business-analysis`; manual mode opens a fresh selected
   analysis space without implicit Requirement association.
2. `business-analyst` is the only writer. Spawn `analysis-challenger` as a
   read-only reviewer for the complete space and `domain-expert` only for an
   explicitly named domain. Each prompt includes exact paths, review lens,
   output contract and `SELF-CHECK`.
   Switch `calculation_examples`: at `required`, every calculation rule
   carries its formula or a worked example, and the challenger reports a
   missing one as a major finding, as
   `skill-content/requirements-analysis/references/switch-calculation_examples-required.md`
   defines.
   Switch `reader_waves`: at `all_at_once`, every reader of a review or recheck
   wave starts at once, after every finished worker is closed, as
   `skill-content/challenge-review/references/switch-reader_waves-all_at_once.md`
   and the host contract define.
   Switch `review_scope`: at `impact_closure`, `ba_compile.py check` runs
   before any reader is spawned; `task_inputs.py` derives each reader's inputs
   with `--changed <note>` and `--base <approved commit>`, and a re-check
   given `--findings` derives only the fix delta; every role reads the
   change's impact closure, each approved, unchanged note outside it only as
   its hash-bound summary, reads beyond it when unsure and records why, a
   writer fixes a reported graph gap through its owning compiler and
   recomputes the closure, and a confirmation re-review reads only the fix's
   delta, as
   `skill-content/challenge-review/references/switch-review_scope-impact_closure.md`
   defines.
   Switch `review_fanout`: at `per_unit`, a review of more than one changed
   analysis document spawns one reader per unit in one message and then one
   aggregator for the cross-unit checks no compiler enforces, as
   `skill-content/challenge-review/references/switch-review_fanout-per_unit.md`
   defines.
3. Render, move each ready draft through `ba_compile.py enter-review --space
   <space> --doc <relative-doc>`, close individual document gates with `approve`,
   then run `approve-package`. Review entry changes only document status and its
   tag mirror; it does not approve, stamp a date, render or publish a receipt.
   Compiler approval plus a committed package are required before handoff.
   An open package revision is `package_status: draft`: repeat
   `begin-revision` for every approved or superseded document that joins the
   same revision, then approve every gate-blocking document before closing it.
   Git history is the audit baseline; the workflow stores no revision marker
   or recovery receipt.
   Switch `dependent_rebind_gate`: at `with_source`, the gate that approves a
   change to an approved package also shows and approves the mechanical
   rebinds of the Experience packages it makes stale, from
   `experience_compile.py source-impact`, as
   `skill-content/business-analysis/references/switch-dependent_rebind_gate-with_source.md`
   defines.
   Switch `source_decision_gate`: at `one_gate_when_drafted`, a decision that
   changes approved documents and whose recommendation needs no owner input
   is drafted and reviewed first and approved as exact content in one owner
   gate, as
   `skill-content/business-analysis/references/switch-source_decision_gate-one_gate_when_drafted.md`
   defines.
4. Requirement mode binds the returned receipt. Manual mode returns the exact
   BA package receipt and suggests `/solution-design`; it does not run it.
