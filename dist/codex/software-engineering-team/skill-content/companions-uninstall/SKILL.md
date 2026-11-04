---
name: companions-uninstall
description: Remove explicitly selected supported companions from the chosen hosts and verify their removal.
exposure: entry
project_scope: external
---

# Companions Uninstall

## Outcome

Removes the selected companion packages and their managed integration settings.
Removing Graphify also removes its CLI and git hooks. Project marker files
(`.impeccable/`) and graph output remain project-owned.
Normal setup and upgrades do not remove or install companions.

## When to use

- The owner explicitly requests removal of named companions or all companions.

## Inputs and authority

Use the explicit removal request to identify packages and hosts. Preserve user-owned project files.

## Method

1. Resolve the requested companions and hosts. Ask only if the removal scope
   is unclear. Existing explicit removal authorization is sufficient.
2. Run, with the active host runtime's absolute Python and the installed
   package script's absolute path:

   ```text
   <absolute-python> <absolute-package-scripts>/setup_project.py companions uninstall --companion <id> --project-root <root>
   ```

   Repeat `--companion <id>` for several companions, or use `--all` only when
   removal of all companions was requested. Add `--host <name>` for one host.
   A host CLI that ships inside a desktop app
   may not be on `PATH`; put its folder on `PATH` first.
3. Show the table the command prints: one row per companion, its type
   (plugin, skill, cli, hook), its host and `removed` as `yes` or `NO`. A `NO`
   row is reported as failed with its name; never call a partial removal done.
4. Report the verified removals and any remaining failed rows. Reinstallation
   is a separate explicit selection through `companions apply --companion <id>`.

## Evidence and completion

Report only removals confirmed by the host and list any failed rows.

## Conditional references

Use the installed companion catalog for package identities and cleanup ownership.
