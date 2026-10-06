# Mechanical backlog schema migration

Only a package schema repair takes this path. New scope, changed scenarios,
source references, criteria, automation targets or product decisions keep the
normal reviewed revision. The default remains `reviewed_revision`.

After a package upgrade, with `backlog_schema_migration` at `receipt_only` in
the approved Process Policy, the coordinator runs the packaged compiler:

```text
backlog_compile.py plan-schema-migration --docs workspace/docs --source-commit <approved-commit> --from-version <previous-package-version>
```

The source must be an ancestor of HEAD with a complete committed, hash-verified
approval. The compiler recognizes only a leading `unit`, `fixture` or `live`
followed by a parenthesized explanation, a colon or a spaced hyphen; it keeps
that entire old value in `level_reason`. Unknown or ambiguous text needs a normal revision.
The plan contains package versions, the source commit, the full canonical note
inventory, per-file before/after byte and source hashes and package hashes.
It changes only the scenario field lines and compiler hash stamps.

Present the exact receipt and diff to the owner. After approval of its
`owner_approval` hash, run:

```text
backlog_compile.py apply-schema-migration --docs workspace/docs --source-commit <approved-commit> --from-version <previous-package-version> --approve-receipt <owner_approval>
```

No writer or reader pass and no new root review is needed. Review notes,
revision numbers, timestamps, Story bytes and every other authored byte stay
exact. The command rejects unrelated edits, unsafe paths and stale approval,
uses the project maintenance lock, rechecks each replacement and rolls back
its own unchanged postimages on failure. An interrupted attempt can be resumed
with the same source commit and approved hash: each canonical file must still
be its exact before or after image. Pause other backlog editors during apply.

Commit the migrated notes and the tracked JSON receipt below
`backlog/artifacts/schema-migrations/`, together with regenerated views.
An existing Delivery retains its exact execution plan and gate A. Its source
check accepts the old backlog and Test Plan pins only after replaying the
receipt against the committed predecessor and proving the complete current
postimage. Story, Operation, DoD, policy, topology and all other pins remain
strict. The receipt remains required for that compatibility binding even if
the process switch later returns to its default. A content edit after migration
invalidates that binding and needs the normal revision and execution approval.
