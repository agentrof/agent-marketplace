---
name: configure
description: The single change gate for project configuration and document-type designations.
exposure: entry
---

# Configure

## When to Use

- Changing stack, commands, scale, language, limits, `max_parallel` or
  designations.
- Repairing a local workspace contract after an intentional user change.

## Procedure

1. Read `workspace/config.json`. If missing, route to setup. Never hand-edit
   machine-managed keys.
2. Read `references/config-contract.md`. Identify each requested field's
   current consumer and state the behavioral impact before proposing a write.
3. For ordinary fields, run `project_config.py set --dry-run --json`. Delivery
   fields remain optional before delivery activation, but configured values
   must already be valid.
4. For designation or language changes, run `vault_check.py
   reconcile-designations --dry-run --json`, present the complete title, H1,
   alias impact, then request approval. Language changes do
   not silently translate existing designation values.
5. On approval, run the owning writer, `project_config.py check`,
   `vault_check.py check-designations` and the scoped vault gate. On rejection,
   write nothing.
6. Confirm the exact Git diff. Configuration changes update only their
   declared project-local surfaces.
