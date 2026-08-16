---
name: sketch
description: Design exploration for thinking. Produces genuinely divergent mock-data directions for a topic, refines the pick, and keeps the result as a committed seed for later demo or development work.
exposure: entry
---

# Sketch

Pure design exploration: no code, no sales package, just directions.

## When to Use
- The user wants to SEE options for a screen or module before committing
  to anything: "how could this look", "give me a few directions".

## Procedure

1. Pre-flight: read `workspace/config.json` (missing: route to the setup entry
   and stop). Resolve all referenced flow and skill files from the installed
   Software Engineering Team package.
2. Preconditions, in order:
   a. Approved brief for the topic; missing: run the business-analysis
      entry flow first, then continue.
   b. Design master at workspace/docs/design-system/MASTER.md (a vault
      note; the obsidian-vault skill owns its docs-tree law); missing:
      stop, say "no design system yet", route the user into the
      design-system entry, and continue here once it exists.
3. Read the packaged `flows/design.md` and execute it in sketch mode:
   directions in one self-contained preview under
   workspace/sketches/<slug>/, direction pick, refinement rounds,
   handshake.
4. Commit the approved preview under workspace/sketches/<slug>/. It is a
   durable seed: demo can expand it, deliver can implement it.
