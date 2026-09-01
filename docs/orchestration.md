# Orchestration

The user starts an entry skill. The entry reads its canonical flow, checks the
project-local workspace and delegates read-only challenges to role agents when
needed. Writers are serialized; independent readers may run in parallel.

Every delegated review has three explicit boundaries: the orchestrator names
the complete input file set and expected output shape before invocation; it
waits for all readers in that review layer; then the owning persona alone
triages findings and writes canonical files. Reviewer replies are transient
inputs, not project state. A later review layer starts only after the prior
layer's writes and deterministic checks are green. Each host uses its native
agent invocation and wait mechanism without changing these semantics.

Requirement Flow is a linear, user-gated sequence. Each required stage commits
its approved documents before the next stage begins. The backlog compiler is
the only machine that derives backlog indexes. All durable changes are
ordinary Git changes in the project workspace.

Host adapters preserve semantics: Claude uses namespaced agents and
`AskUserQuestion`; Codex uses project-local agents and `request_user_input`.
Neither host requires another plugin.
