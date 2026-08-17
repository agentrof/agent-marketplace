# Requirement Flow

Requirement Flow is intentionally document-first and request-scoped:

```text
setup -> requirement -> required stages -> backlog-plan -> delivery-plan
```

Each required stage owns its documents, compiler and explicit user gate. Until
`backlog-plan`, its approved Git-tracked documents are the complete stage
state. Reused and not-applicable stages carry their evidence and rationale in
the Requirement record.

Advancing from a completed stage requires its approved subtree, stage map,
vault home and `workspace/config.json` to be tracked, committed and clean.
`requirement_route.py` scopes Git status to those paths. Draft work in the
current stage and unrelated application files do not stop authoring.

The idempotent setup bootstrap creates the workspace contract, project-local
scratch directory and Obsidian payload while preserving authored files. Its
`inspect`, `apply` and `check` commands share one convergence plan.

`backlog-plan` materializes the nested Obsidian tree, assigns one owner and any
supporting roles per story, creates test plans with stable scenarios, runs
mechanical checks, completes exact-set epic and cross-epic reviews, renders
generated views and commits the result. It then reports
`deliver` as the next explicit entry. Release Management remains outside this
contract.
