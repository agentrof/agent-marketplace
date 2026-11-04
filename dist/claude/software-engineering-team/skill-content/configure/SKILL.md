---
name: configure
description: Change explicit project settings, execution profiles or proof commands through their owning writer.
exposure: entry
---

# Configure

## Outcome

A minimal validated setting change with its effect visible.

## When to use

Use when the user requests a project configuration or model-profile change.

## Inputs and authority

Read `workspace/config.json`, the host contract and the requested change. Keep
technology decisions in project design records. Use the host's absolute Python
and installed script paths for machine-owned writes.

## Method

1. Inspect the effective setting and its source. Use `project_config.py classes`
   for the execution profiles. Explain availability and capability limitations.
2. Preview ordinary settings with `project_config.py set --dry-run --json`.
   Change a compute profile with `set-class`. Resolve material cost, permission
   or behavioral changes under the user's existing authority.
3. Apply through the owning writer, run `project_config.py check`, and reconcile
   only the affected host projection. Never change global preferences silently.

## Evidence and completion

Show the setting delta and validation result. Record requested and resolved model
settings separately from host-observed execution.

## Conditional references

- [Configuration contract](references/config-contract.md) defines owned settings.
- [Execution profiles](references/role-models.md) explains model selection.
