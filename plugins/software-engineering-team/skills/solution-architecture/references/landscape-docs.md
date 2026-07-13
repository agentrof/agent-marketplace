# Landscape Docs

The solution tree's contract. One living tree per project at workspace/docs/solution-design/; engagements study, the landscape records. All docs are checkable with artifact_check against the mandated sections below, and the tree-level rules with landscape_check.

## The Tree

| path | owns | mandated sections |
|---|---|---|
| landscape.md | the one living truth: current and target landscape, the transition between them, components with build-buy-integrate verdicts, topology, method choices | Summary, Current, Target, Transition, Components |
| decision-log.md | append-only solution decisions: alternatives, tradeoffs, exit path, sustainability judgment, revisit trigger; supersede, never edit | Summary, Decisions |
| engagements/<slug>.md | one study per invocation topic: framing, options matrix, verdict | Summary, Framing, Options, Verdict |
| reviews/<slug>-round-<n>.md | one challenge round: findings table plus dispositions | Summary, Findings |

## Birth Skeletons (first run copies these verbatim)

- landscape.md: `# Landscape`, `## Summary` (one paragraph plus the Engagements index table: slug, status, minted decision ids), `## Current`, `## Target`, `## Transition`, `## Components` (table: component, verdict, decision, engagement, status).
- decision-log.md: `# Decision Log`, `## Summary` (the index table below), `## Decisions`.
- engagements/<slug>.md: `# <Title>`, `## Summary` (first body line `Status: open`), `## Framing`, `## Options`, `## Verdict`.
- reviews/<slug>-round-<n>.md: `# Round <n>: <slug>`, `## Summary`, `## Findings` (table: lens, finding, severity, disposition).

Greenfield first run: Current states "Nothing built yet" plus any inherited constraints (hosting, organizational platforms); Target and Transition start empty and grow only from decision records.

## landscape.md

- The Summary carries the Engagements index table (slug, status line copy, minted decision ids), updated at every fold-in; session resume enters here.
- **Current** describes what exists today, honestly, including components the team did not choose and would not choose again.
- **Target** describes the decided destination, each delta traceable to a decision record by id. When a Target delta ships, fold it into Current and drop it from Target in the fold-in that records the shipping.
- **Transition** orders the deltas from Current to Target: each step cites its owning decision and names its precondition; the planner reads it when sequencing stories. A non-empty gap between Current and Target with an empty Transition is a defect.
- **Components** is the table the planner and the software architect read: component, build-buy-integrate verdict, owning decision (linked), engagement (linked), status (decided / adopted / retiring). When a record is superseded, its rows re-point to the successor in the same fold-in; a retired component's row moves to a Retired subsection, never deleted.
- The landscape never carries reasoning; reasoning lives in the engagement study and the decision record it points to. The landscape states outcomes.

## decision-log.md

- Records follow the software-architecture skill's decision-records mechanics: Y-statement for routine calls, full record for structural ones; superseded records stay in place with a pointer forward, never edited.
- Each record is a bare-id heading: `## SD-001`, with the title as the record's first line (`**Title:** <title>`). Never put the title in the heading: the anchor `#sd-001` must resolve in repository viewers and heading-text resolvers alike, and title text in the heading breaks the slug.
- Id allocation: the next id is one past the highest `## SD-` heading in decision-log.md, computed by scan immediately before appending. Ids are assigned only at append time; engagement drafts cite `pending` until then.
- Lifecycle: records land at verdict-accept with `**Status:** proposed`; the gate's approve flips them to `accepted`, request-changes flips to `rejected` or supersedes. The fold-in finalizes statuses; it never first-writes records.
- Solution records carry, as first-class fields: the exit path, the sustainability judgment, the requirement ids and budget links they rest on (so "which decisions rest on this requirement" is answerable from this file alone), and a revisit trigger: one named condition that reopens the decision (a cited budget or scale threshold crossed, a pricing or licensing term changed, a date reached). A record with no condition that could invalidate it states why.
- The Summary is the index: one row per record (id linked, title, status, territory, revisit trigger, open named risks), maintained with every append. Session resume, the gate and the staleness sweep read only this table; a record missing from it is invisible and therefore a defect.

## engagements/

- The slug names the topic, kebab-case, minted at the entry's pre-flight; a reopened topic appends -2, -3, never reuses a closed slug.
- The Summary's first body line is exactly `Status: open`, `Status: approved YYYY-MM-DD`, `Status: parked YYYY-MM-DD: <reason>`, or `Status: superseded by <slug>`. Dated Status lines are written only by the stamp mode (`landscape_check.py --tree <tree> --stamp-engagement <slug> --status approved|parked|open [--reason ...]`): the script stamps the UTC date and re-checks the tree; a hand-typed date is denied by the guard hook and a future date fails the check. Session resume greps the line, reads open engagements fully and skips parked ones. Parked covers an owner pause, a spike in flight and topics ruled not-now; a parked engagement reopens through the stamp mode's `--status open`, with the note dated from `now --date`.
- Deferred questions and named risks live under Verdict, each with a revisit note; silence is never a deferral.
- An engagement is append-only once its gate closes; a reopened topic gets a new engagement citing the old one.
- The `ungrounded-by-analysis` flag, when present, sits in the Summary and is removed only by re-verification against an approved analysis domain.

## reviews/

- One file per challenge round: the findings table (lens, finding, severity) and the disposition per finding (fix with the landing doc, reject with the one-line reason, defer with the gate note).
- Landscape-scoped rounds are `reviews/landscape-round-<n>.md`, numbered in their own sequence.
- Round files are locked at round close; the gate presentation cites them.

## Linking Rules (the tree is a graph)

Every cross-document citation is a relative markdown link, never a bare id; bare ids are invisible to repository viewers and graph tools, links are edges.

- A decision citation is the id text linked to its bare-id anchor: target `../decision-log.md#sd-001` from an engagement, `decision-log.md#sd-001` from the landscape.
- Every decision record links back to the engagement that minted it, and forward to the record superseding it when one exists.
- The landscape's Components rows link both the owning decision and the engagement study; a review round links its engagement; an engagement links the analysis docs it cites with relative paths (`../../business-analysis/<slug>/...`).
- Standard relative links only (no wiki-link syntax): they render and resolve in repository viewers, editors and graph tools alike.

The result reads as one navigable web: engagement to decisions to landscape and back, reviews hanging off their engagement, analysis grounding one hop away.

## Fold-In Rules (gate close)

1. Engagement verdict accepted: proposed records flip to accepted, the landscape updates to match (Components, Target, Transition, the Engagements index), the engagement doc is stamped and finalized.
2. Consistency check, mechanical: artifact_check green on every touched doc, and landscape_check exit 0 over the tree (every Components decision link targets an existing, non-superseded record; every Target delta cites a record; every engagement carries a valid status line; ids are unique).
3. Commit the tree together (engagement, records, landscape, review rounds); the tree is the deliverable, the conversation is not.
