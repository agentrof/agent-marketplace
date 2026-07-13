---
name: business-analysis
description: Interactive business analysis. The analyst persona runs a multi-turn discovery conversation and grows one approved analysis space per topic; the space is the precondition every build and design flow stands on.
disable-model-invocation: true
---

# Business Analysis

Turn an idea into one approved analysis space through conversation:
typed documents, challenged by an adversarial review loop, gated per
domain, all mechanically checked by the compiler at
${CLAUDE_PLUGIN_ROOT}/scripts/ba_compile.py.

## When to Use
- The user has an idea or need that must be understood and decomposed
  before anything is planned, designed or built.

## Procedure

1. Pre-flight.
   - Read workspace/config.json when present (output_language governs
     all document prose; structure stays English per the space
     standard; default English).
   - Freeze set: run the PMO CLI's resume-info --project-key <key>
     --json; for each active work order (running or waiting_gate) on
     this topic, collect its story's criterion ids from the coverage map
     in workspace/docs/backlog.md, then ba_compile.py resolve --ids
     <them> for the owning docs. Those docs are FROZEN: refuse edits and
     status flips on them (a guard hook backstops this); everything else
     in the space stays editable but must pass check before commit.
   - An existing space for this topic means UPDATE mode: same living
     tree, never a parallel version.
   - Session resume (spaces outlive conversations): orient from
     _generated/status.md and _generated/open-questions.md first, then
     read only the target domain's subtree fully, the rest summary-only
     via _generated/index.md. The generated views are the working
     memory; conversation is not.
2. New topic: ba_compile.py init --space
   workspace/docs/business-analysis/<slug> --title "<title>" --code
   <CODE>. The five root files and _generated/ appear; run render once.
3. Adopt the business-analyst role IN THIS CONVERSATION (an interactive
   persona, not a spawn; analysis is a dialogue). Follow the agent
   constitution at ${CLAUDE_PLUGIN_ROOT}/agents/business-analyst.md
   exactly, and load its bound knowledge skill (requirements-analysis):
   its space standard and decomposition method govern where every fact
   lands; its questioning techniques and non-functional checklist govern
   the rounds.
4. Author incrementally, per domain.
   - New docs come from ba_compile.py stub (born compliant; it prints
     the node's next free ids). Grow domains only on the decomposition
     reference's split signals; a small topic stays one node.
   - Question in rounds; flush every answer into its owning doc per the
     fact-routing test. Ids are table rows; citations are links.
   - After every authoring milestone: check + render. Fix findings
     immediately; a red compile never accumulates.
   - Flip a doc draft -> in_review -> approved only when check reports
     zero errors naming it; stamp approved_at. approved -> draft reopens
     rework outside the frozen set.
5. CHALLENGE LOOP, per domain, before its gate (and once at space level
   before the space closes when domains exist). Load the challenge-review
   knowledge skill and run its loop: cast lenses and topic experts, spawn
   them fresh-context and read-only, triage every finding into the space
   (covered / fix / assumption / question / rejected), audit the burial
   paths, record the round as reviews/round-<n>.md via stub, close it
   locked. Round 1 is mandatory; rounds 2-3 run only while blocking
   findings appear; the record is part of the gate.
6. Gates, in order; before presenting any gate run check --gate approval
   (scoped with --node for a domain) and render; a red compile blocks
   the gate.
   - FOUNDATION gate, once: space.md, glossary.md, actors.md,
     budgets.md approved together.
   - DOMAIN gate, per domain as its analysis closes: present
     _generated/status.md and the open-questions board; the user
     approves or defers named questions explicitly (deferral is a row
     status with a revisit note, never silence). Approve flips the
     subtree statuses; commit authored plus generated files together.
   - A buildable domain unblocks request, sketch and demo for its scope;
     other domains keep analyzing in parallel.
7. Process pulses: at each gate and challenge-round close, append an
   event via the PMO CLI (event append) naming the space, node, round
   and finding counts, so the dashboard shows analysis progress next to
   build progress. Content stays in files; the database gets pulses.
