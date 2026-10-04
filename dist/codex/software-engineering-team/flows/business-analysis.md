# Business Analysis Flow

Spawn template: paste `{{constitution}}`, exact input/output paths, review
lens and `SELF-CHECK` into every reviewer prompt.

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
   Switch `source_decision_gate`: at `one_gate_when_drafted`, a decision that
   changes approved documents and whose recommendation needs no owner input
   is drafted and reviewed first and approved as exact content in one owner
   gate, as
   `skill-content/business-analysis/references/switch-source_decision_gate-one_gate_when_drafted.md`
   defines.
4. Requirement mode binds the returned receipt. Manual mode returns the exact
   BA package receipt and suggests `/solution-design`; it does not run it.
