---
name: configure
description: The single change gate for project configuration and durable workflow settings.
exposure: entry
---

# Configure

Before delegating a role, follow the packaged `templates/task-input-contract.md`
and derive its inputs with `scripts/task_inputs.py --entry configure`.
Use the owning compiler's exact scope and source bindings.

## When to Use

- Changing language, an Operation Contract, Delivery Governance, the
  Definition of Done, or a process switch (`process`).
- Changing a tier's model or reasoning effort, or the tier a role runs on,
  in this project (`models`).
- Repairing a local workspace contract after an intentional user change.

## Procedure

1. Read `workspace/config.json`. If missing, route to setup. Never hand-edit
   machine-managed keys.
   Read `flows/operation.md` for either Operation target,
   `flows/delivery-governance.md` for Governance and
   `references/process-policy.md` for `process` before durable changes.
   Read `references/role-models.md` for `models`.
2. Read `references/config-contract.md`. Identify the owning document or
   compiler before proposing a change. Stack, database and component method
   choices belong to accepted Solution Design decisions, never config.
   Invoke every machine-owned writer with the active host runtime's exact
   absolute Python executable and the installed package script's absolute
   path. Never use a bare Python command name, `/usr/bin/env` indirection or
   direct shebang execution for these writes; shell aliases and functions make
   those forms unverifiable and therefore guard-only.
3. For language, use `project_config.py set --dry-run --json`. For
   `operation verification`, dispatch `operation_compile.py`; for
   `operation environment`, dispatch the same compiler with `--kind
   environment`; for `governance`, use `delivery_governance.py`; for DOD, use
   its Delivery compiler; for `process`, use `process_policy.py`. Never
   hand-edit their lifecycle fields. For `models`, use `project_config.py
   tiers`, `set-tier` and `set-role-tier`.
4. For a language change, run `project_config.py set --dry-run --json` and
   present only the config delta. Existing authored titles are not translated
   or rewritten by a config change.
5. On approval, run the owning writer, `project_config.py check` and the
   scoped vault gate. A new
   Governance revision must be applied through `delivery_git.py
   apply-governance`; do not start, claim or resume an Item while that Fence
   handoff is held. On rejection, write nothing.
6. Confirm the exact Git diff. Configuration changes update only their
   declared project-local surfaces.
