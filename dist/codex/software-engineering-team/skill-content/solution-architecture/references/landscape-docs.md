# Landscape Docs

The solution tree's contract. One living tree per project at workspace/docs/solution-design/; engagements study, the landscape records. All docs are checkable with artifact_check against the mandated sections below, and the tree-level rules with landscape_check.

## The Tree

| path | owns | mandated sections |
|---|---|---|
| landscape.md | the one living truth: current and target landscape, the transition between them, components with build-buy-integrate verdicts, topology, method choices | Summary, Current, Target, Transition, Components |
| decisions/<kebab-slug>-decision.md | one solution decision per note: alternatives, tradeoffs, exit path, sustainability judgment, revisit trigger; supersede, never edit | frontmatter contract in the skeleton below |
| decision-log.md | GENERATED index of decisions/ (marker first line; rendered, never authored) | none: rendered |
| engagements/<slug>.md | one study per invocation topic: framing, options matrix, verdict | Summary, Framing, Options, Verdict |

The subtree carries a map duty: every doc birth or retirement updates
maps/solution-design.md in the same session.

## Birth Skeletons (first run copies these verbatim)

- landscape.md: `# Landscape`, `## Summary` (one paragraph plus the Engagements index table: slug, status, minted decision ids), `## Current`, `## Target`, `## Transition`, `## Components` (table: component, verdict, decision, engagement, status).
- engagements/<slug>.md: `# <Title>`, `## Summary` (first body line `Status: open`), `## Framing`, `## Options`, `## Verdict`.
- decision-log.md has NO skeleton: the first `render-decisions` births it.
- decisions/<kebab-slug>-decision.md, the decision-note skeleton (empty
  supersedes/superseded_by keys are omitted entirely; decided_at exists
  only once stamp-decision writes it):

```markdown
---
type: decision
title: <title>
status: proposed
owner_role: solution_architect
territory: <decision territory>
revisit_trigger: <one named condition>
engagement: "[[solution-design/engagements/<slug>]]"
tags:
  - doc/decision
  - status/proposed
aliases:
  - SD-007
---

# <title>

<Y-statement or full record body>

## Baglantilar <!-- sec: nav -->
[[maps/solution-design|Solution Design]] -
[[solution-design/engagements/<slug>|engagement]] -
[[solution-design/landscape|Landscape]]
```

First-run landscape: Current states "Nothing built yet" plus any inherited constraints (hosting, organizational platforms); Target and Transition start empty and grow only from decision records.

## landscape.md

- The Summary carries the Engagements index table (slug, status line copy,
  minted decision ids), updated at every fold-in; every invocation reads this
  current summary before changing the tree.
- **Current** describes what exists today, honestly, including components the team did not choose and would not choose again.
- **Target** describes the decided destination, each delta traceable to a decision record by id. When a Target delta ships, fold it into Current and drop it from Target in the fold-in that records the shipping.
- **Transition** orders the deltas from Current to Target: each step cites its owning decision and names its precondition; the planner reads it when sequencing stories. A non-empty gap between Current and Target with an empty Transition is a defect.
- **Components** is the table the planner and the software architect read: component, build-buy-integrate verdict, owning decision (linked), engagement (linked), status (decided / adopted / retiring). When a record is superseded, its rows re-point to the successor in the same fold-in; a retired component's row moves to a Retired subsection, never deleted.
- The landscape never carries reasoning; reasoning lives in the engagement study and the decision record it points to. The landscape states outcomes.

## decisions/ and the generated index

- Records follow the software-architecture skill's decision-records mechanics: Y-statement for routine calls, full record for structural ones; superseded notes stay in place with a pointer forward, never edited.
- One note per decision at decisions/<kebab-slug>-decision.md, with a title-matching H1 and one `SD-###` frontmatter alias. The id never enters the filename, title or H1.
- Id allocation: the next id is scan-max+1 over the `SD-###` aliases, computed immediately before the note is born. Ids are assigned only at note birth; engagement drafts cite `pending` until then. Duplicate id numbers are caught at every gate, and render-decisions refuses to render over them.
- Lifecycle: notes are born at verdict-accept with `status: proposed`; the gate's approve flips them to `accepted`, request-changes to `rejected` or superseded. Every status change goes ONLY through the vault checker's stamp-decision verb, which writes status, the UTC decided_at, the tag mirror and both ends of the supersede chain in one operation; a hand-typed stamp is guard-denied. The fold-in finalizes statuses; it never first-writes notes.
- Solution notes carry, as first-class fields: the exit path, the sustainability judgment, the requirement ids and budget block-ids they rest on (so "which decisions rest on this requirement" is answerable from the note alone), and a revisit trigger: one named condition that reopens the decision (a cited budget or scale threshold crossed, a pricing or licensing term changed, a date reached). A note with no condition that could invalidate it states why.
- decision-log.md is the GENERATED index (marker first line), rendered by
  `scripts/vault_check.py render-decisions --vault workspace/docs` after any
  decision write. It is never authored or hand-edited. Entries and gates read
  this index; a re-render heals a stale view.

## engagements/

- The slug names the topic in kebab-case and remains stable for that engagement.
- The Summary's first body line is exactly `Status: open`, `Status: approved YYYY-MM-DD`, `Status: parked YYYY-MM-DD: <reason>`, or `Status: superseded by <slug>`. Dated Status lines are written only by the stamp mode (`landscape_check.py --tree <tree> --stamp-engagement <slug> --status approved|parked|open [--reason ...]`): the script stamps the UTC date and re-checks the tree; a hand-typed date is denied by the guard hook and a future date fails the check. Parked covers an owner pause, a spike in flight and topics ruled not-now; a parked engagement reopens through the stamp mode's `--status open`, with the note dated from `now --date`.
- Deferred questions and named risks live under Verdict, each with a revisit note; silence is never a deferral.
- The current engagement may be amended through the same checks and approval
  gate. Git history carries prior prose; create a distinct engagement only
  when the topic or decision scope is genuinely different.
- The `ungrounded-by-analysis` flag, when present, sits in the Summary and is removed only by re-verification against an approved analysis domain.

## Linking Rules (the tree is a graph)

Every cross-document citation is a vault-absolute wikilink with the bare id or a noun phrase as its alias, never a bare id alone; bare ids draw no edges, wikilinks are the graph. Relative markdown links between vault docs are hook-denied; they remain only for targets outside the docs tree.

- A decision citation in prose aliases the id:

```markdown
minted [[solution-design/decisions/order-event-distribution-decision|SD-007]]
```

- The landscape's Components rows link both the owning decision and the engagement study; Engagements index and Target delta cells cite the same way. In any table cell the alias pipe is escaped:

```markdown
| event distribution | integrate | [[solution-design/decisions/order-event-distribution-decision\|SD-007]] | [[solution-design/engagements/order-event-distribution\|order-event-distribution]] | decided |
```

- Every decision note links back to the engagement that minted it (the quoted `engagement` frontmatter wikilink) and forward through the quoted supersede-chain keys when a successor exists; stamp-decision writes both ends.
- An engagement cites decisions and analysis docs as wikilinks too: `[[business-analysis/shop/domains/orders/rules/order-events|BR-ORD-014]]`.
- Budgets are cited via block ids: `[[business-analysis/shop/budgets#^event-volume|volume budget]]`; heading anchors are banned vault-wide.

The result reads as one navigable web: engagement to decisions to landscape
and back, with analysis grounding one hop away.

## Fold-In Rules (gate close)

1. Engagement verdict accepted: proposed notes flip to accepted through stamp-decision only, the landscape updates to match (Components, Target, Transition, the Engagements index), the engagement doc is stamped and finalized.
2. After any decision write, re-render the index with the packaged `vault_check.py render-decisions --vault workspace/docs`.
3. Consistency check, mechanical: artifact_check green on every touched doc; landscape_check exit 0 over the tree (every Components decision link targets an existing, non-superseded note; every Target delta cites a note; every engagement carries a valid status line; ids are unique); and the packaged vault check scoped to this subtree (`vault_check.py check --vault workspace/docs --scope solution-design`).
4. Commit the tree together (engagement, `decisions/`, the rendered
   `decision-log.md`, landscape and `maps/solution-design.md`). The corrected
   final tree is the deliverable; challenger replies are not.
