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
     prose; file names, ids, Status lines and commits stay English.
   - Grounding, mechanical: when the engagement cites analysis content,
     run ${CLAUDE_PLUGIN_ROOT}/scripts/ba_compile.py check --space
     workspace/docs/business-analysis/<slug> --gate approval --node
     <domain> per cited domain; nonzero routes to business-analysis. No
     space at all is allowed ONLY for pre-analysis groundwork: the
     engagement doc is then flagged ungrounded-by-analysis and its
     decisions cite requirements as assumptions to re-verify.
   - Mint the engagement slug here: kebab-case topic name; a reopened
     topic appends -2, -3, never reuses a closed slug.
   - An existing tree means UPDATE mode: same living tree, never a
     parallel version. First run births it from the landscape-docs
     reference's birth skeletons, verbatim. On a project with
     pre-existing systems, baselining precedes engagement one: write
     Current from the repository and the user's account, minted as one
     dated baseline decision record the Components rows cite.
   - Session resume (the tree outlives conversations): orient from the
     landscape and decision-log summaries and the engagements whose
     status is open, then read only the active engagement's docs fully.
     The tree is the working memory; conversation is not.
   - Staleness sweep, every session: compare each live record's revisit
     trigger and cited budget values (decision-log Summary index)
     against the current analysis space; a breached trigger or changed
     budget opens a re-evaluation engagement citing the record before
     any new topic proceeds.
2. Adopt the solution-architect role IN THIS CONVERSATION (an
   interactive persona, not a spawn; solution design is a debate). Read
   the behavioral constitution at ${CLAUDE_PLUGIN_ROOT}/constitution.md
   and honor it; follow the agent constitution at
   ${CLAUDE_PLUGIN_ROOT}/agents/solution-architect.md exactly, and load
   its bound knowledge skill (solution-architecture): its dimension set
   governs every evaluation, its doc contract governs where every
   verdict lands.
3. Work per engagement.
   - One engagement doc per topic: engagements/<slug>.md framing the
     question, the touched components, the cited requirements and
     constraints, then the options matrix and the verdict.
   - Debate in rounds with the user; every accepted verdict lands as a
     decision record (alternatives, tradeoffs, exit path,
     sustainability judgment; supersede, never edit) and the landscape
     updates to match. Nothing decided lives only in conversation.
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
   Round 1 is mandatory; further rounds run only while blocking
   findings appear, cap 3. A landscape-scoped round (the lenses over
   landscape.md and the live decisions, not one engagement) runs on
   user demand or when fold-in reveals a contradiction.
5. SOLUTION gate, per engagement.
   - Mechanical half first: artifact_check on every touched doc per the
     doc contract;
     ${CLAUDE_PLUGIN_ROOT}/scripts/landscape_check.py --tree
     workspace/docs/solution-design exit 0 (link, status and id
     integrity); ${CLAUDE_PLUGIN_ROOT}/scripts/ba_compile.py resolve
     --space <space> --ids <every id the engagement cites>, nonzero
     blocks the gate; all rounds recorded.
   - Present: each verdict with its strongest rejected alternative,
     sustainability judgments, cited constraints, impacts on in-flight
     or planned work (read resume-info --project-key <key> --json and
     name affected stories; invalidated scope routes to the product
     owner for re-slicing, never silently), deferred findings for the
     owner's ruling.
   - Approve / Request changes / Pause. On approve: stamp the
     engagement via ${CLAUDE_PLUGIN_ROOT}/scripts/landscape_check.py
     --tree <tree> --stamp-engagement <slug> --status approved (the
     script writes the UTC date and re-checks the tree; never type the
     date), land deferred questions in its Verdict with a revisit note
     (deferral is a recorded row, never silence), fold the outcome into
     landscape.md and decision-log.md (the engagement is the study; the
     landscape is the living truth) and commit the tree.
6. Process pulses: at each round close and gate close, append an event
   via the PMO CLI (event append) naming the engagement, round and
   finding counts; the CLI is the launcher at
   "${AGENTROF_HOME:-$HOME/.agentrof}/bin/pmo_cli.py", per the develop
   flow's state contract. Content stays in files; the database gets
   pulses.
7. HARD SCOPE LIMIT: this entry writes ONLY under
   workspace/docs/solution-design/. Requirements gaps route to
   business-analysis, implementation to request, stack changes to
   configure, per-story design to the develop flow.
