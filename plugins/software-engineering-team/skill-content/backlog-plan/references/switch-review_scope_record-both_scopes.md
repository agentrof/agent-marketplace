# Review Scope Record

These are the instructions of process switch `review_scope_record` at
`both_scopes`. A task binds this file only when the project's Process Policy
selects that value; at the default, `off`, no epic review measures its read
set. Where this file and the flow differ on what an epic review records, this
file governs.

The record measures; it never changes what a reader reads. Each reader still
receives the manifest of the `review_manifest_scope` value in force, and the
root manifest, writer manifests and backlog approval are unchanged.

## Manifest sizes

`backlog_review_inputs.py --epic <EP-ID>` derives the reader's manifest as
the flow says, then derives the same epic's manifest under the other
`review_manifest_scope` value from the same sources. The manifest names
`review_scope_record: both_scopes` and carries `scope_sizes`:

- `read`: the value the reader reads under;
- `transitive` and `bounded`: each read set's `files`, `source_bytes` (the
  bytes of every path it names) and `manifest_bytes` (the manifest's JSON as
  printed);
- `transitive_budget`, only when the owner set the switch's
  `transitive_source_bytes` parameter: that budget and `over`, whether the
  transitive read set's source bytes exceed it.

The sizes are facts of the sources, so `source_hash` binds them and the
`--expected-hash` recheck is unchanged.

## Before the readers start

When `transitive_budget.over` is true and the value in force is
`transitive`, stop before any reader of that epic starts. Show the owner the
two sizes and ask one choice-gate question: keep `transitive` for this
revision, or change `review_manifest_scope` to `bounded` through
`/configure process` and derive the manifests again. Recommend `bounded`
when the transitive read set is several times the bounded one. A changed
policy stales every manifest derived before it, so no reader may start on an
earlier manifest.

## Record

The coordinator keeps one tracked JSON Lines file,
`<workspace>/measurements/review-scope.jsonl`, beside the docs vault, commits
it with the backlog revision, and appends to it:

1. When an epic reader's manifest is derived for dispatch: rerun the same
   command with `--record`, which appends the epic, the manifest's
   `source_hash` and its `scope_sizes`.
2. After the review's findings are in the review note, or in the claim
   record that calibration reads: run
   `backlog_review_inputs.py --docs <workspace>/docs --epic <EP-ID> --scope-findings --record`,
   adding `--findings <claim record>` when the review note keeps no
   `Returned Findings` yet. It lists each blocking finding, after calibration
   where one ruled, with the notes it cites and those outside the bounded read
   set, and appends that list.

`--record <file>` takes another file only inside the workspace and outside
the docs vault, and the command refuses a file Git ignores: setup's managed
`.gitignore` ignores `.agentrof/` and the host directories, so a record there
would never reach the commit, and the vault gate refuses a non-markdown file
inside the vault.

A record row is measurement data, never review evidence: no reader gets it,
and no review note cites it. Never delete or rewrite a row without the
owner's approval.

## Measurement

The project owner reads the record outside every task; no role acts on it.
Over several backlog revisions, compare the blocking findings that cite a
note outside the bounded read set with the reader time and bytes bounded
saves. The registry's promotion rule judges the record, and the
`review_manifest_scope` default is a separate owner decision made on it.
