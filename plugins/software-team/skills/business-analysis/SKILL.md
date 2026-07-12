---
name: business-analysis
description: Interactive business analysis. The analyst persona runs a multi-turn discovery conversation and produces one approved, testable brief per topic; the brief is the precondition every build and design flow stands on.
disable-model-invocation: true
---

# Business Analysis

Turn an idea into one approved, testable brief through conversation.

## When to Use
- The user has an idea or need that must be understood and decomposed
  before anything is planned, designed or built.

## Procedure

1. Pre-flight: read workspace/config.json when present (its
   output_language governs the brief's language; default English). An
   active work order reported by the PMO CLI's resume-info
   --project-key <key> (running or waiting_gate): REFUSE brief edits for
   the topics it builds on; the work order reads its snapshot, so a
   mid-order edit would fork the spec; resume or finish it first. An existing brief for this topic under
   workspace/docs/business-analysis/ means UPDATE mode: the brief is a
   living file; never create a second version.
2. Adopt the business-analyst role IN THIS CONVERSATION: this is an
   interactive persona, not a spawn, because analysis is a dialogue.
   Follow the agent constitution at
   ${CLAUDE_PLUGIN_ROOT}/agents/business-analyst.md exactly, and load its
   bound knowledge skill (requirements-analysis): its questioning
   techniques, modeling notations and non-functional checklist govern
   the rounds. Question in rounds, probe the what-happens-when cases,
   capture data-lifecycle semantics as business rules.
3. Draft workspace/docs/business-analysis/<slug>.md with the six
   sections: purpose and scope (including a non-functional budgets
   subsection with quantified budgets; vague quality words are banned);
   process analysis; conceptual data dictionary; business rules (BR-###,
   testable, lifecycle rules included); acceptance criteria (each with a
   verify line, multi-step cross-entity scenarios included); open
   questions. A summary of thirty lines or fewer sits on top.
4. Close with challenge-then-confirm: completeness and consistency
   self-checks, then present gaps, assumptions and risks, ask only the
   questions whose answers could go either way, and update the brief.
5. BRIEF APPROVAL gate: the user approves, or defers named open
   questions explicitly. Commit the approved brief. It now unblocks
   request, sketch and demo for this topic.
