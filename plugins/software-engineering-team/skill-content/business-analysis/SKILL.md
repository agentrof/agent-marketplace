---
name: business-analysis
description: Interactive business analysis. The analyst persona runs a multi-turn discovery conversation and grows one approved analysis space per topic; the space is the precondition every build and design flow stands on.
disable-model-invocation: true
---

# Business Analysis

Turn an idea into one approved analysis space through conversation:
typed documents, challenged by an adversarial review loop, gated per
domain, all mechanically checked by the space compiler (ba_compile.py,
run through the dispatcher).

## When to Use
- The user has an idea or need that must be understood and decomposed
  before anything is planned, designed or built.

## Procedure

1. Pre-flight.
   - Resolve the PMO CLI and the dispatcher ("$RUN", "$TEAM") per the
     develop flow's state contract; scripts below run as
     "$RUN" run "$TEAM" scripts/<x>.py.
   - Read workspace/config.json when present (output_language governs
     ONLY the body prose of authored .md docs; names and technical
     terms follow terminology_language, default English; ids, structure
     and every other machine artifact stay English).
   - Freeze set: run the PMO CLI's resume-info --project-key <key>
     --json; for each active work order (running or waiting_gate) on
     this topic, collect its story's criterion ids from the database
     (coverage list --project-key <key> --story <WP-##> --json), then
     ba_compile.py resolve --ids
     <them> for the owning docs. Those docs are FROZEN: refuse edits and
     status flips on them (a guard hook backstops this); everything else
     in the space stays editable but must pass check before commit.
   - An existing space for this topic means UPDATE mode: same living
     tree, never a parallel version.
   - Vault stewardship (obsidian-vault skill): run
     "$RUN" run "$TEAM" scripts/vault_check.py check --vault
     workspace/docs --scope business-analysis, passing the freeze set as
     repeated --exclude flags; every finding is this session's repair
     work before the gate (migrate covers the deterministic classes;
     frozen docs surface as named warnings, repaired after release).
   - Session resume (spaces outlive conversations): orient from
     _generated/status.md and _generated/open-questions.md first, then
     read only the target domain's subtree fully, the rest summary-only
     via _generated/index.md. The generated views are the working
     memory; conversation is not.
2. New topic: ba_compile.py init --space
   workspace/docs/business-analysis/<slug> --title "<title>" --code
   <CODE>. The four plain named root files and _generated/ appear;
   run render once.
3. Adopt the business-analyst role IN THIS CONVERSATION (an interactive
   persona, not a spawn; analysis is a dialogue). Read the behavioral
   constitution printed by "$RUN" path "$TEAM" constitution.md and
   honor it; follow the agent constitution printed by
   "$RUN" path "$TEAM" agents/business-analyst.md
   exactly, and load its bound knowledge skills (requirements-analysis
   and obsidian-vault): the space standard and decomposition method
   govern where every fact lands, the questioning techniques and
   non-functional checklist govern the rounds, and the vault law
   governs every file written under the docs tree.
4. Author incrementally, per domain.
   - New docs come from ba_compile.py stub (born compliant with the
     vault frontmatter and nav section; it prints the node's next free
     ids); each title carries its type designation (obsidian-vault Title
     Law), mechanically checked against the config map. Grow domains only
     through approved split proposals: a signal or the compiler's
     advisory "split proposal" warning NOMINATES a split, the owner
     approves it through the AskUserQuestion popup (split now / defer
     with a revisit note) before any split lands; a small topic stays
     one node. A new space or domain hub
     joins maps/business-analysis.md in the same milestone; the tree's
     FIRST content also materializes the map seed from the templates
     and adds the home map line (dynamic home).
   - Question in rounds through the AskUserQuestion popup (at most
     four questions per round, options with tradeoffs, recommended
     first); flush every answer into its owning doc per the
     fact-routing test.
     Ids are table rows; citations are links.
   - After every authoring milestone: check + render. Fix findings
     immediately; a red compile never accumulates.
   - Flip a doc draft -> in_review in the frontmatter; approve only via
     "$RUN" run "$TEAM" scripts/ba_compile.py approve --space <space>
     --doc <rel-path>: the script stamps status and the UTC approved_at
     itself and refuses a doc the checks reject (never hand-write the
     date; the guard hook denies it). approved -> draft reopens rework
     outside the frozen set.
5. CHALLENGE LOOP, per domain, before its gate (and once at space level
   before the space closes when domains exist). Load the challenge-review
   knowledge skill and run its loop: cast lenses and topic experts, spawn
   them fresh-context and read-only, triage every finding into the space
   (covered / fix / assumption / question / rejected), audit the burial
   paths, record the round as reviews/round-<n>-review.md via stub, close it
   locked. Round 1 is mandatory; later rounds run only while blocking
   findings appear, up to the schema's round cap (default 3; a project
   limits override may change it); the record is part of the gate.
6. Gates, in order; before presenting any gate run check --gate approval
   (scoped with --node for a domain), render, and the vault stewardship
   check from step 1; a red compile or a red vault check blocks the
   gate.
   - FOUNDATION gate, once: <slug>-space.md, <slug>-glossary.md,
     <slug>-actors.md and <slug>-budgets.md approved together; the
     approval is asked through the AskUserQuestion popup.
   - DOMAIN gate, per domain as its analysis closes: present
     _generated/status.md and the open-questions board; the user
     approves or defers named questions explicitly through the
     AskUserQuestion popup (deferral is a row status with a revisit
     note, never silence). Approve flips the subtree statuses; commit
     authored plus generated files together.
   - A buildable domain unblocks deliver, sketch and demo for its scope;
     other domains keep analyzing in parallel.
7. Process pulses: at each gate and challenge-round close, append an
   event via the PMO CLI (event append) naming the space, node, round
   and finding counts, so the dashboard shows analysis progress next to
   build progress. Content stays in files; the database gets pulses.
