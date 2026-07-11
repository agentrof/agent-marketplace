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

1. Pre-flight: read workspace/config.json (missing: route to the setup
   entry and stop). Check workspace/runs/ for a run with status running
   or waiting_gate: offer Resume or Archive; never start a second run.
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
      bound planning knowledge skill to produce or extend
      workspace/docs/backlog.md (first large job creates it). Use the
      develop flow's spawn template.
   c. BACKLOG GATE: present the backlog summary and the coverage map;
      Approve / Request changes / Pause.
   d. Package loop: for each ready package in order, execute
      ${CLAUDE_PLUGIN_ROOT}/flows/develop.md end to end. After each
      package's merge checkpoint, update the backlog on the main line and
      ask whether to continue with the next package.
5. All gates are manual; there is no autonomous mode.
