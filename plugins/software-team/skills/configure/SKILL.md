---
name: configure
description: The single change gate for the machine-managed project config. Interprets a requested change, validates it against the supported enums, shows the impact, and writes only workspace/config.json on approval.
disable-model-invocation: true
---

# Configure

Config changes go through this gate, never through hand edits.

## When to Use
- The project's stack set, test command, source directories or output
  language must change (for example: "add a document store to the
  databases set").

## Procedure

1. Read workspace/config.json; missing: route to the setup entry and
   stop. When present, remind the user the file is machine-managed (its
   managed_by note says so) and this gate is its only supported writer;
   hand edits are unsupported and carry no guarantee of surviving later
   gate writes.
2. Interpret the requested change into concrete key changes.
3. Validate. Enum keys: backend_stack python-fastapi; frontend_stack
   react-typescript; databases a set drawn from sql and nosql, one or
   both, never empty. A value outside the enums is refused honestly with
   the reason: this team ships tested stacks only, and new stacks arrive
   as maintainer releases. Shape keys: test_command a non-empty command
   string; source_dirs a non-empty list of repo-relative paths (absolute
   paths refused); output_language a non-empty language name.
4. Present the impact analysis before writing: which roles' skill
   bindings change (the static role-to-skill map lives in
   ${CLAUDE_PLUGIN_ROOT}/flows/develop.md, step 0; method skills such as
   the architect's architecture skill are static and config-independent),
   what future packages will do differently (for example: adding a
   document store means the architect loads both database skills and
   declares a store per entity), and any migration consequences for
   existing work.
5. On approval, write workspace/config.json and nothing else: this gate
   touches exactly one file. Confirm the diff to the user. On rejection,
   stop; nothing is written.
