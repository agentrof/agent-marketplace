---
name: sketch
description: Design exploration for thinking. Produces genuinely divergent mock-data directions for a topic, refines the pick, and keeps the result as a committed seed for later demo or development work.
disable-model-invocation: true
---

# Sketch

Pure design exploration: no code, no sales package, just directions.

## When to Use
- The user wants to SEE options for a screen or module before committing
  to anything: "how could this look", "give me a few directions".

## Procedure

1. Pre-flight: read workspace/config.json (missing: route to the setup
   entry and stop). Dispatcher for plugin files:
   RUN="${AGENTROF_HOME:-$HOME/.agentrof}/bin/agentrof_run.py" and
   TEAM=software-engineering-team.
2. Preconditions, in order:
   a. Approved brief for the topic; missing: run the business-analysis
      entry flow first, then continue.
   b. Design master at workspace/docs/design-system/MASTER.md (a vault
      note; the obsidian-vault skill owns its docs-tree law); missing:
      stop, say "no design system yet", route the user into the
      design-system entry, and continue here once it exists.
3. Execute the flow printed by "$RUN" path "$TEAM" flows/design.md in
   sketch mode:
   directions in one self-contained preview under
   workspace/sketches/<slug>/, direction pick, refinement rounds,
   handshake.
4. Commit the approved preview under workspace/sketches/<slug>/. It is a
   durable seed: demo can expand it, deliver can implement it.
