# Bounded Review Manifests

These are the instructions of process switch `review_manifest_scope` at
`bounded`. A task binds this file only when the project's Process Policy
selects that value; at the default, `transitive`, every review manifest reads
as the flow and role files describe. Where this file and a flow or role file
differ on what an epic reader reads, this file governs.

## Read set

`backlog_review_inputs.py --epic <EP-ID>` reads the value from the Process
Policy for a reader's manifest and records it in the manifest's
`review_manifest_scope` field. The manifest holds:

- the scope: the root backlog, the epic, its stories and test plans;
- the incoming and outgoing dependency closure: every story that dependency
  edges connect to the epic's stories, with its test plan and epic;
- every note that an epic, story or test plan of the scope or closure links
  to in its front matter or body, and every Experience record it cites as
  `<experience>:<ID>@rN`;
- one hop further, the front-matter relations of each such note: its
  front-matter wikilinks, package and Requirement references and cited
  Experience records;
- the epic's review notes.

Nothing else is expanded. A note two hops out is read without its links, and a
story reached through a link is read without its test plan or dependency
closure. The root backlog and the review notes are read as notes two hops out
unless an epic, story or test plan above links to them. A source finding in
a backlog note the manifest does not read, a `stub-epic` or `stub-story`
placeholder included, is listed in `check.scaffold_findings` and does not fail
it. The root manifest and a
writer's manifest keep the transitive read set, and backlog approval still
checks the whole backlog.

## Review

- Give the reader the manifest and every path it names, as the flow says. The
  closure's shared contract and source context arrives through the links and
  relations above; context behind a linked note's body links belongs to the
  root review, which reads the complete package.
- A reader that needs evidence outside the manifest reports the note and why
  it needs it, as its role says, and never infers the note's content. Add
  each reported note to that reader's task with `--input`, rerun that reader,
  and keep the request with the review's inputs: the switch's metric counts
  it.
- Recompute the manifest with `--expected-hash` as the flow says. Its hash
  binds every note it reads and the story identities and dependency edges that
  reach the dependency closure, so an edge that reaches a story read only
  through a link leaves it fresh. The value shapes the read set and is
  recorded in the manifest, so a review taken under the other value is stale.

## Measurement

The project owner measures outside every task; no role acts on it. For each
epic review pass, record the manifest's path count, JSON bytes and build
seconds beside those of the transitive manifest of the same epic and backlog
revision, the reader's wall time, its valid findings by severity and its
requests for evidence outside the manifest, and compare them with a transitive
shadow review of the same epic and revision. The registry's promotion rule
judges them over at least 5 review passes across at least 2 backlog revisions.
