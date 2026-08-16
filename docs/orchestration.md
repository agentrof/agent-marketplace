# Orchestration

The user starts an entry skill. The entry reads its canonical flow, checks the
project-local workspace and delegates read-only challenges to role agents when
needed. Writers are serialized; independent readers may run in parallel.

Preparation is a linear, user-gated sequence. Each stage commits its approved
documents before the next stage begins. The backlog compiler is the only
machine that derives backlog indexes. All durable changes are ordinary Git
changes in the project workspace.

Host adapters preserve semantics: Claude uses namespaced agents and
`AskUserQuestion`; Codex uses project-local agents and `request_user_input`.
Neither host requires another plugin.
