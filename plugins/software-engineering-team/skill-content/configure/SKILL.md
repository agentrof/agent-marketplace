---
name: configure
description: The single change gate for machine-managed project configuration. Use when changing stack, commands, source directories, origin, scale, limits, output language, terminology language, or document type designations.
exposure: entry
---

# Configure

Route every config change through this gate. Never hand-edit
`workspace/config.json`.

## When to Use

- Use when changing the project's stack, commands, source directories,
  origin, scale, limits, language policy, parallelism, or designations.

## Procedure

1. Read `workspace/config.json`. If missing, route to setup and stop. Run PMO
   `resume-info --project-key <key>`; refuse while a work order is `running` or
   `waiting_gate` because it owns a config snapshot. Remind the owner that the
   file is machine-managed and this gate is its only supported writer.
2. Interpret the request as concrete key changes. Read
   [config-contract.md](references/config-contract.md) completely before
   validating or presenting impact. Refuse unsupported values with the
   contract's exact reason.
3. Handle `project_origin` only through its owner. Before registration run
   `project_config.py set-origin`. After a project contract exists run PMO
   `project classify-origin --project-key <key> --project-root <root> --origin
   <choice>` so config, fingerprint, and audit state move together.
4. Present the contract's impact analysis before writing. Ask apply or reject
   through the choice gate, putting the impact and tradeoff in the option
   descriptions. Designation changes use the dry-run plan from
   `vault_check.py reconcile-designations`; present locked and residual records
   exactly as specified in the reference.
5. On approval, execute designation changes through
   `reconcile-designations`, never Write/Edit. For every other supported key,
   update `workspace/config.json` and no other file. Run the required checks,
   show new findings, and confirm the exact diff. On rejection, write nothing.
