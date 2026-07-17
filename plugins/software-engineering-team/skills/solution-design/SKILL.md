---
name: solution-design
description: Interactive solution design. The solution-architect persona runs a multi-turn working session over the end-to-end landscape and records technology, topology and method decisions the whole team consumes; grounded on the analysis space, challenged per round, gated per engagement.
disable-model-invocation: true
---

# Solution Design

Turn landscape questions into recorded decisions through conversation:
one living solution tree under workspace/docs/solution-design/, an
engagement study per topic, an adversarial challenge round before every
gate, and a decision log the planner and the software architect read.

## When to Use
- The system's foundations need deciding or debating: technologies,
  platforms, products, topology, methods, integration or orchestration
  constructs, sustainability.
- A new ask raises a landscape-level question before (or beside) any
  story work. NOT for per-story design: develop step 1 owns deltas.

## Procedure

1. Pre-flight.
   - Read workspace/config.json. Missing or without a project_key: stop
     and route to the setup entry. output_language governs ONLY .md body
     prose; names and technical terms follow terminology_language
     (default English); file names, ids and Status lines stay English.
   - Grounding, mechanical: when the engagement cites analysis content,
     run ${CLAUDE_PLUGIN_ROOT}/scripts/ba_compile.py check --space
     workspace/docs/business-analysis/<slug> --gate approval --node
     <domain> per cited domain; nonzero routes to business-analysis. No
     space at all is allowed ONLY for pre-analysis groundwork: the
     engagement is flagged ungrounded, decisions cite assumptions.
   - Mint the engagement slug here: kebab-case topic name; a reopened
     topic appends -2, -3, never reuses a closed slug.
   - An existing tree means UPDATE mode, never a parallel version; the
     first run births it from the landscape-docs birth skeletons. With
     pre-existing systems, baselining precedes engagement one: write
     Current from the repository, minted as one dated baseline decision
     note the Components rows cite.
   - Session resume: orient from the landscape, decision index and open
     engagements; read only the active engagement's docs fully.
   - Staleness sweep, every session: compare each live record's revisit
     trigger and cited budgets (the generated decision index) against
     the analysis space; a breach opens a re-evaluation engagement
     citing the record before any new topic.
   - Vault stewardship (obsidian-vault skill): run
     ${CLAUDE_PLUGIN_ROOT}/scripts/vault_check.py check --vault
     workspace/docs --scope solution-design; its findings are this
     session's repair work (migrate covers the deterministic classes).
2. Adopt the solution-architect role IN THIS CONVERSATION (an
   interactive persona, not a spawn; solution design is a debate). Read
   the behavioral constitution at ${CLAUDE_PLUGIN_ROOT}/constitution.md
   and honor it; follow the agent constitution at
   ${CLAUDE_PLUGIN_ROOT}/agents/solution-architect.md exactly, and load
   its bound knowledge skills (solution-architecture and obsidian-vault):
   the dimension set governs every evaluation, the doc contract governs
   where every verdict lands, the vault law governs every docs write.
3. Work per engagement.
   - One engagement doc per topic: engagements/<slug>.md framing the
     question, the touched components, the cited requirements and
     constraints, then the options matrix and the verdict.
   - Debate in rounds; every accepted verdict lands as its own decision
     note under decisions/ (landscape-docs contract; supersede via the
     stamp-decision verb, never edit) and the landscape updates to
     match. Nothing decided lives only in conversation.
   - Build, buy or integrate per component; a verdict that would change
     the configured stack enums routes to the configure entry and the
     maintainer path, never around them.
   - After every milestone run
     ${CLAUDE_PLUGIN_ROOT}/scripts/artifact_check.py --path <doc>
     --require-sections per the doc contract; fix findings immediately.
4. CHALLENGE ROUND, before the gate. Spawn
   software-engineering-team-analysis-challenger fresh-context and read-only, one
   spawn per lens, files only; the four lenses and their spawn shape
   come from the solution architecture skill's challenge-lenses
   reference. Named practitioner questions go to
   software-engineering-team-domain-expert with an explicit expert profile. Triage
   every finding in conversation: fix (doc or record updated), reject
   (one-line reason), defer (named at the gate). Record the round as
   reviews/<slug>-round-<n>.md (findings table plus dispositions).
   Round 1 is mandatory; further rounds only while blocking findings
   appear, cap 3. A landscape-scoped round runs on user demand or when
   fold-in reveals a contradiction.
5. SOLUTION gate, per engagement.
   - Mechanical half first: artifact_check on every touched doc;
     ${CLAUDE_PLUGIN_ROOT}/scripts/landscape_check.py --tree
     workspace/docs/solution-design exit 0;
     ${CLAUDE_PLUGIN_ROOT}/scripts/vault_check.py check --vault
     workspace/docs --scope solution-design exit 0 (freeze set passed
     as --exclude); ${CLAUDE_PLUGIN_ROOT}/scripts/ba_compile.py resolve
     --space <space> --ids <every cited id>, nonzero blocks the gate;
     all rounds recorded.
   - Present: each verdict with its strongest rejected alternative,
     sustainability judgments, cited constraints, impacts on in-flight
     or planned work (read resume-info --project-key <key> --json and
     name affected stories; invalidated scope routes to the product
     owner for re-slicing, never silently), deferred findings for the
     owner's ruling.
   - Approve / Request changes / Pause, asked through the
     AskUserQuestion popup (tradeoffs in the option descriptions). On
     approve: stamp the engagement via
     ${CLAUDE_PLUGIN_ROOT}/scripts/landscape_check.py --tree <tree>
     --stamp-engagement <slug> --status approved (the script writes the
     UTC date; never type it), land deferred questions in the Verdict
     with a revisit note (a recorded row, never silence), fold the
     outcome into landscape.md, re-render the index (render-decisions),
     update the map note, ensure home.md links this tree's map (dynamic
     home: the map seed materializes with the tree's first content) and
     commit the tree.
6. Process pulses: at each round and gate close, append an event via
   the PMO CLI (event append) naming the engagement, round and finding
   counts; the CLI is the launcher at
   "${AGENTROF_HOME:-$HOME/.agentrof}/bin/pmo_cli.py" (develop flow's
   state contract). Content stays in files; the database pulses.
7. HARD SCOPE LIMIT: writes only under workspace/docs/solution-design/,
   plus home and its own map note repair, and vault payload
   materialization (per-file, only where missing). Requirements gaps
   route to business-analysis, implementation to request, stack changes
   to configure, per-story design to the develop flow.
