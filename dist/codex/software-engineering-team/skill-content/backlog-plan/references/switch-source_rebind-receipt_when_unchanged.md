# Source Rebind Receipt

These are the instructions of process switch `source_rebind` at
`receipt_when_unchanged`. A task binds this file only when the project's
Process Policy selects that value; at the default, `reviewed_revision`,
every revision reviews every epic again as the flow describes. Where this
file and the flow differ on which epics a revision reviews, this file
governs.

## When it applies

Only a revision whose backlog root differs from its approved predecessor in
nothing but its input bindings, its Requirement and its lifecycle fields
takes this path. Stories, test plans and epic notes may change; each change
impacts its epic. A changed root body or another root field, a removed
source family, a source whose approval Git does not hold, a binding whose
hash moved while none of its authored documents changed (its changed files
are named), a relation graph gap that touches the backlog or a revision that
rebinds no source is refused, and the revision takes the standard path. A
newly bound source family counts each of its documents as changed.

## Steps

1. Run `begin-revision` with the new bindings as the flow describes, and let
   the Product Owner make any story or test plan change the new sources need.
2. Plan the receipt from the last approved backlog commit:

   ```text
   backlog_compile.py plan-source-rebind --docs workspace/docs --source-commit <approved-commit>
   ```

   The plan is read-only. It names each changed binding with its before and
   after approval commits, changed source documents and changed row ids, and
   each epic as `reviewed` with every reason or `reused` with its approved
   review. A note is impacted when its content changed, it cites a changed
   source document or row id, the relation closure of a changed source
   reaches it, a changed upstream note adds or drops a typed edge to it, a
   dependency edge or its membership changed, or it implements a changed
   Requirement. A reused epic's approved review is impacted the same way.
   An authored source note's status is content; only a package's anchor
   note loses its approval stamps. A changed document's id and aliases count
   as changed ids. While the sources only gain notes and rows, the Solution
   landscape counts by its added rows: a Components or Engagements row, a
   Target delta or a Transition step names its decisions and engagements as
   the changed targets, and a note constrained by the landscape is not
   impacted through it. A modified, reordered or removed row or step, a
   change outside those rows or an edit to an existing source note passes
   the change through the landscape to every note it constrains, and a
   Solution change that edits an existing note or row is never mechanical.
   An impact on an epic note, its reused review or a root reader finding
   impacts every story and test plan of that epic.
3. Run `backlog_compile.py record-source-rebind-root-review --docs
   workspace/docs --source-commit <approved-commit>`. It writes the root
   round's structural sections, every epic in `related_to`, every
   cross-epic edge in `dependency_refs` and a `Source Rebind` section naming
   the receipt hash and predecessor. A later change to any backlog note or
   source stales the receipt: every manifest and the approval refuse until
   the plan and the root round are recorded again.
4. Open the next round of each `reviewed` epic with `stub-epic <epic-slug>
   --new-review`. One fresh `backlog-reviewer` per reviewed epic reads its
   manifest: the impacted stories and test plans in full, every other story
   as its hash-bound summary, and the source diff in `check.source_rebind`.
   No reader runs for a `reused` epic, and its manifest is refused.
5. One fresh scoped `backlog-reviewer` reads the root manifest: the impacted
   and citing stories, the changed source documents, the source diff and the
   `check.backlog_graph` summary. It reports every story whose behavior
   depends on a changed source although the receipt reuses its epic. Each
   such finding moves that epic to review: record it again with
   `--review-epic <EPIC-ID>` on steps 2 and 3, then review that epic as in
   step 4. The Product Owner writes Cross-Epic Overlap, Deferred Criteria,
   Findings and Verdict from the reader's return.
6. Run the flow's pre-approval check, then present the receipt and its
   `owner_approval` hash to the owner. After the owner approves that exact
   hash, run:

   ```text
   backlog_compile.py apply-source-rebind --docs workspace/docs --source-commit <approved-commit> --approve-receipt <owner_approval>
   ```

   It writes the receipt below `backlog/artifacts/source-rebinds/`, runs the
   ordinary atomic approval and seals the approved package hash into the
   receipt. The approval replays the receipt and refuses unless each
   reviewed epic has an approved round of this revision and each reused
   epic's latest review is its approved review byte for byte. Plain
   `approve` refuses a round with a `Source Rebind` section.
7. When the Process Policy also selects `dependent_rebind_gate`
   `with_source` and the source gate approved this backlog rebind, a
   receipt that `plan-source-rebind` reports `mechanical` (every epic
   reused, no cited story, no Requirement change, no root reader finding, a
   Solution change that only adds notes and landscape rows) needs no second
   owner gate: run step 6 with the planned hash and
   `--source-gate`, which refuses any other receipt. Every other receipt
   goes to the owner as step 6 describes.

Commit the approved backlog and the sealed receipt together. The receipt
stays tracked evidence: a Delivery that pinned the predecessor proves its
pin through it, also after the switch returns to its default.

## Measurement

The project owner measures outside every task; no role acts on it. Per
source-rebind revision, record the reader passes, the wall clock from
`begin-revision` to approval, the epics reused and reviewed, every root
reader finding that moved an epic to review and every Delivery execution
re-approval it avoided. Replay the revision as a frozen reviewed revision and
compare the findings.
