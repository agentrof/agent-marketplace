# Greenfield preparation

The greenfield path is intentionally document-first:

```text
setup -> business-analysis -> solution-design -> design-system
       -> experience-design -> backlog-plan
```

Each stage owns its documents, compiler and explicit user gate. Until
`backlog-plan`, its approved Git-tracked documents are the complete stage
state.

Advancing from a completed stage requires its approved subtree, stage map,
vault home and `workspace/config.json` to be tracked, committed and clean.
`preparation_check.py` scopes Git status to those paths. Draft work in the
current stage and unrelated application files do not stop authoring.

The idempotent setup bootstrap creates the workspace contract, project-local
scratch directory and Obsidian payload while preserving authored files. Its
`inspect`, `apply` and `check` commands share one convergence plan.

`backlog-plan` materializes the nested Obsidian tree, assigns one owner and any
supporting roles per story, creates test plans with stable scenarios, runs
mechanical checks, completes exact-set epic and cross-epic reviews, renders
generated views and commits the result. It then reports
`deliver` as the next explicit entry. Delivery lane design is deliberately
outside this preparation contract.
