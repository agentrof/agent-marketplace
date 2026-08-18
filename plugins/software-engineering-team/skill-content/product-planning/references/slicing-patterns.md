# Slicing Patterns

How to cut an approved brief into stories. The unit throughout is
the story: one review unit, independently revertable, a backlog entry
with its own Definition of Done. It is a Delivery Item; nothing here
assumes iterations or estimates.

Every worked example slices the same generic brief: at Acme Corp, members
submit expense reports, approvers approve or reject them, and finance
exports approved reports. Acceptance criteria are cited as AC-n, business
rules as BR-###.

## Pattern 1: Split by Workflow Step

Cut where the actor or the artifact's state changes; each step is one
observable capability.

Before (fails the too-big test):

- WP-01 Expense reporting end to end: submit, decide, export.

After:

- WP-01 Submit a report: a member creates and submits; the report reaches
  the submitted state (AC-1).
- WP-02 Decide a report: an approver approves or rejects; the decision and
  its reason are visible to the member (AC-2, BR-011).
- WP-03 Export approved reports: finance pulls approved reports as a file
  (AC-3).

Each story crosses every layer it needs and is demonstrable alone. WP-02
depends on WP-01 only because a report must exist before it can be
decided; that is a real edge, not chain slicing.

DO cut at the state transitions named in the brief's process analysis.
DON'T cut inside a transition: a "validate submission" story is a layer
in disguise, nothing new is observable when it lands.

## Pattern 2: Split by Business-Rule Variation

Ship the base rule first; each variation becomes its own story.

Before:

- WP-02 Decide a report with all approval rules: BR-011 base approval,
  BR-012 auto-approval under a threshold, BR-013 delegation while the
  approver is absent.

After:

- WP-02 Decide a report, base rule only (BR-011). Scope states: does NOT
  include auto-approval or delegation.
- WP-04 Auto-approve under threshold (BR-012): observable as a submitted
  report skipping the approver queue.
- WP-05 Delegate decisions (BR-013): observable as the delegate seeing and
  deciding the absent approver's queue.

DO write the excluded variations into the base story's "does NOT
include" scope line, so the coverage map still traces BR-012 and BR-013 to
a story instead of losing them. DON'T split below one whole rule: half a
business rule has no verifiable Definition of Done.

## Pattern 3: Split by Data Variation

Ship the simplest data shape first; each harder shape is its own story.

Before:

- WP-01 Submit a report covering every expense shape: single currency,
  foreign currency, attached receipts.

After:

- WP-01 Submit a single-currency report (AC-1).
- WP-06 Foreign-currency expenses (BR-021 conversion rule): observable as
  a correctly converted total on the report.
- WP-07 Receipt attachments (AC-5): observable as an attachment the
  approver can open from the report.

DO pick the shape the walking skeleton needs as the base. DON'T label the
base story "temporary": its Definition of Done must still hold unchanged
after the variation stories land.

## Pattern 4: Split by Interface Subset

When one surface serves several actor needs, ship the operation subsets as
separate stories.

Before:

- WP-03 Reporting surface: create, submit, list, filter, export.

After:

- Create and submit already live in WP-01 (the capture subset).
- WP-08 Find reports: list and filter for approvers (management subset,
  AC-4).
- WP-03 Export approved reports (export subset, AC-3).

DO keep each subset a whole capability for one actor. DON'T split by verb
alone: "the list endpoint" without the screen that shows it is a
horizontal slice wearing a subset's name.

## Pattern 5: Spike, Last Resort Only

When a story cannot be sliced or ordered because a question is open
(feasibility, an external system's actual behavior), cut a spike story.

- A spike's Definition of Done is the answered question, written into the
  backlog's open questions section; when the answer is architectural, flag
  it for the architect's decision log. Never code.
- State the question in the story title, size it to one review unit, and
  give every story it unblocks a dependency edge onto it.
- If reading the brief or asking the owner could answer the question, do
  that instead; a spike that one question could replace is waste.

## The Too-Big Test

Split the story when any of these holds:

- It exceeds one review unit: a reviewer cannot verify it in one pass, or
  it bundles more than one concern.
- Its Definition of Done cites criteria from more than one workflow step,
  or a base rule plus its variations.
- Its scope sentence needs "and" between two capabilities.

## The Too-Small Test

The slice is not a story when:

- Its Definition of Done cites no acceptance criterion and no BR-###:
  nothing observable proves it done.
- It only becomes verifiable after a sibling lands, which means the cut
  created a dependency instead of a capability.

## Merge Rules

- Merge a too-small slice into the smallest story that makes it
  observable, never into a grab-bag.
- Merge two stories only when they cite the same brief criterion and
  neither is verifiable alone; propose the merge at a checkpoint (splits
  and merges need project decision authority approval, never happen silently).
- Never merge across concerns to shorten the backlog; two concerns stay
  two stories even when both are small.

## Anti-Pattern Gallery

| Anti-pattern | Smell in the backlog | Fix |
|---|---|---|
| Horizontal layer | "Build the schema", "all endpoints" | Re-slice by workflow step; each story carries its own slice of every layer |
| Plumbing story | "Project setup" with no criterion cited | Fold the setup into the walking skeleton story |
| Grab-bag | "Misc fixes and polish" | One concern per story; the cosmetic tail becomes named stories |
| Dependent chain | Each story only verifiable after the next | Re-cut at state transitions; remove or reverse the edges |
| Interface shell first | A screen story with no behavior behind it | Make the screen the thin end of a vertical slice |
| Iceberg base | "Basic submit" silently hiding most of the brief's rules | Move each named rule to a variation story; the scope line lists exclusions |
