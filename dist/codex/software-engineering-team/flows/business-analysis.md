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
3. Render, close individual document gates, then run `approve-package`.
   Compiler approval plus a committed package are required before handoff.
   An open package revision is `package_status: draft`: repeat
   `begin-revision` for every approved or superseded document that joins the
   same revision, then approve every gate-blocking document before closing it.
   Git history is the audit baseline; the workflow stores no revision marker
   or recovery receipt.
4. Requirement mode binds the returned receipt. Manual mode returns the exact
   BA package receipt and suggests `/solution-design`; it does not run it.
