---
name: request
description: The software-team front door for real work. The user states an ask; it is classified as atomic or large, confirmed in one line, and driven to a pull request through the develop flow.
disable-model-invocation: true
---

# Request

Front door for real work: classify, confirm, deliver.

## When to Use
- The user wants working software changed or built: a fix, a feature, a
  module. For pure exploration use sketch; for a sales package use demo.

## Procedure

1. Pre-flight: read workspace/config.json (missing, or without a
   project_key: route to the setup entry and stop). Run the PMO CLI's
   resume-info --project-key <key> (launcher per the develop flow's
   state contract): an active run in this worktree means offer Resume or
   Release; never start a second run here. Resume means: load
   ${CLAUDE_PLUGIN_ROOT}/flows/develop.md, read the run's state
   (resume-info), and continue from the first step that is not done,
   under the full state contract; never redo done steps and never work
   outside the flow.
2. Classify BINARY, in this conversation (this is the product-owner hat
   as instruction text, not an agent spawn; classification must be able
   to question the user, and spawned agents cannot talk to the user):
   - ATOMIC: genuinely minor and analysis-free. Name the tier:
     COSMETIC-ATOMIC changes no behavior (a label, copy, an
     existing-token swap); FIX-ATOMIC changes behavior (a bug fix, a
     rule correction) and runs the develop flow's fix-atomic discipline
     (failing reproduction test first, one reviewer pass). Anything
     touching the data model, the interface contract or the schema is
     NOT atomic; an ask that implies persisted data or a contract change
     (a new form field that must be saved) classifies LARGE up front,
     not at the escape hatch.
   - LARGE: everything else.
   Confirm the route in one line ("Reading this as: <atomic|large>,
   because <reason>. Proceed?") and wait.
3. ATOMIC route: execute the atomic variant in
   ${CLAUDE_PLUGIN_ROOT}/flows/develop.md exactly. Its escape hatch is
   binding: the moment the work touches model, contract or schema, stop
   and re-enter this procedure as LARGE.
4. LARGE route:
   a. Brief precondition: no approved brief for this topic under
      workspace/docs/business-analysis/ means the business-analysis entry
      flow runs first, here, in this conversation.
   b. Spawn software-team-product-owner with the approved brief and its
      bound planning knowledge skill to produce or extend the backlog as
      an epics-and-stories JSON import file (the agent's output
      contract). Use the develop flow's spawn template.
   c. BACKLOG GATE: present the epic and story summary with the coverage
      map; Approve / Request changes / Pause. On approve, load it into
      the PMO database (item import --project-key <key> --json-file
      <file>; the CLI rejects stories with empty scope, exclusions, DoR
      or DoD) and regenerate the committed view (render backlog --out
      workspace/docs/backlog.md).
   d. Story loop: for each ready story in order, execute
      ${CLAUDE_PLUGIN_ROOT}/flows/develop.md end to end. After each
      story's merge checkpoint (which updates the database and re-renders
      the backlog view on the main line), ask whether to continue with
      the next story.
5. All gates are manual; there is no autonomous mode.
