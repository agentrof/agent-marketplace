---
name: companions-uninstall
description: Remove every optional companion from the hosts in one run and prove each is gone; the team plugin itself stays.
exposure: entry
project_scope: external
---

# Companions Uninstall

Removes the optional third-party companions that setup installs: the plugins,
the skills, the graphify CLI and its git hooks, and the deny rules, environment
and disable block that `companions apply` wrote. The team plugin itself is
never touched. Project marker files (`.diagram-design`, `.impeccable/`,
`workspace/docs/.graphifyignore`) and graphify output stay: they are project
files.

## When to Use

- The owner wants a clean slate before running setup again.
- A companion install is broken and a reinstall must start from nothing.

## Procedure

1. Ask the owner to confirm: this removes software from the user's hosts.
   Name the hosts (all, unless one is asked for).
2. Run, with the active host runtime's absolute Python and the installed
   package script's absolute path:

   ```text
   <absolute-python> <absolute-package-scripts>/setup_project.py companions uninstall --project-root <root>
   ```

   Add `--host <name>` for one host. A host CLI that ships inside a desktop app
   may not be on `PATH`; put its folder on `PATH` first.
3. Show the table the command prints: one row per companion, its type
   (plugin, skill, cli, hook), its host and `removed` as `yes` or `NO`. A `NO`
   row is reported as failed with its name; never call a partial removal done.
4. Run `companions check` once more. Every row must read not installed; this
   is the proof beyond the command's own table.
5. Report the next step: run setup to install again, in a fresh session.
