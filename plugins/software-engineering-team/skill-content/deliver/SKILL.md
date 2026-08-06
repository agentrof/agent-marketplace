---
name: deliver
description: The software-engineering-team front door for real work. The user states an ask; it is classified as atomic or large, confirmed in one line, and driven to a pull request through the develop flow.
exposure: entry
---

# Deliver

Front door for real work: classify, confirm, deliver.

## When to Use
- The user wants working software changed or built: a fix, a feature, a
  module. For pure exploration use sketch; for a sales package use demo.

## Procedure

1. Pre-flight: read workspace/config.json (missing, or without a
   project_key: route to the setup entry and stop). Resolve the PMO CLI
   and the dispatcher ("$RUN", "$TEAM") per the develop flow's state
   contract and run the idempotent ensure. Run resume-info
   --project-key <key>: an active work order whose worktree field matches
   THIS resolved git root means offer Resume or Release through the
   choice gate; never start a second work order here. Active orders in OTHER worktrees are parallel
   lanes coordinated by the delivery-lanes entry: name them and continue.
   Resume means: load the flow printed by "$RUN" path "$TEAM"
   flows/develop.md, read the
   work order's state (resume-info), and continue from the first step
   that is not done, under the full state contract; never redo done
   steps and never work outside the flow.
   Run `preparation_check.py route --project-root <root> --intent deliver
   --json`. A greenfield project with an incomplete preparation stage must
   stop at the exact entry named by the result; do not guess or compress the
   sequence. An unclassified upgraded project routes to configure. Existing
   projects continue through the scoped preparation below.
   Story fast path: an ask naming an existing backlog story ("deliver:
   deliver WP-03"; verify the row with item list) is LARGE by
   construction and was already sliced and approved at the backlog gate:
   skip classification and the brief precondition, confirm through the
   choice gate ("Delivering WP-03 <title>." with proceed /
   adjust options), and execute the flow printed by "$RUN" path "$TEAM"
   flows/develop.md for that story (its step 0.5 readiness gate still
   guards).
2. Classify BINARY, in this conversation (this is the product-owner hat
   as instruction text, not an agent spawn; classification must be able
   to question the user, and spawned agents cannot talk to the user):
   - ATOMIC: genuinely minor and analysis-free. Name the tier:
     COSMETIC-ATOMIC changes no behavior (a label, copy, an
     existing-token swap); FIX-ATOMIC changes behavior (a bug fix, a
     rule correction) and runs the develop flow's fix-atomic discipline
     (failing reproduction test first, one reviewer pass). Anything
     touching the data model, the interface contract, the schema or the
     environment's service or store set is NOT atomic; an ask that
     implies persisted data or a contract change (a new form field that
     must be saved) classifies LARGE up front, not at the escape hatch.
   - LARGE: everything else.
   Confirm the route through a choice gate ("Reading this
   as: <atomic|large>, because <reason>." with proceed / reclassify
   options) and wait.
3. ATOMIC route: execute the atomic variant in the flow printed by
   "$RUN" path "$TEAM" flows/develop.md exactly. Its escape hatch is
   binding: the moment the work touches model, contract, schema or the
   environment's service or store set, stop and re-enter this procedure
   as LARGE.
4. LARGE route:
   a. Scope the request against the existing project. Run a scoped Business
      Analysis check, then a solution-impact pass over the approved landscape.
      Re-enter solution-design only for a landscape decision; per-story
      architecture remains in develop. Run an Experience Design delta when UI
      behavior or an existing journey, flow or screen contract changes. Every
      omitted stage is justified by a clean mechanical impact result.
   b. Load `flows/backlog-planning.md` in `feature` mode. The product-owner,
      compiler and backlog-reviewer produce and gate the bounded feature plan.
      The execution set contains only feature stories and user-approved
      unfinished transitive prerequisites. Active and completed contracts are
      frozen.
   c. Apply through PMO `backlog-plan apply`, then explicitly activate the
      target release. Backlog approval alone is not activation. Read back the
      approved execution set from PMO; legacy `item import` is not a structural
      writer for managed programs.
   d. Story loop: for each story in that approved execution set and order, execute the flow
      printed by "$RUN" path "$TEAM" flows/develop.md end to end. After each
      story's merge checkpoint (which updates the database on the main
      line), ask through a choice gate whether to continue
      with the next story.
5. All gates are manual; there is no autonomous mode.
