---
name: configure
description: The single change gate for the machine-managed project config. Interprets a requested change, validates it against the supported enums, shows the impact, and writes only workspace/config.json on approval.
disable-model-invocation: true
---

# Configure

Config changes go through this gate, never through hand edits.

## When to Use
- The project's stack set, test, mutation or environment command, source
  directories, output language or terminology language must change (for
  example: "add a document store to the databases set").

## Procedure

1. Read workspace/config.json; missing: route to the setup entry and
   stop. An active work order reported by the PMO CLI's resume-info
   --project-key <key> (running or waiting_gate): REFUSE the change and
   point at the work order; it reads its config snapshot, so a
   mid-order change would fork the spec.
   When present, remind the user the file is machine-managed (its
   managed_by note says so) and this gate is its only supported writer;
   hand edits are unsupported and carry no guarantee of surviving later
   gate writes.
2. Interpret the requested change into concrete key changes.
3. Validate. Enum keys: backend_stack python-fastapi; frontend_stack
   react-typescript; environment_stack docker-compose; databases a set
   drawn from sql and nosql, one or both, never empty. A value outside
   the enums is refused honestly with the reason: this team ships tested
   stacks only, and new stacks arrive as maintainer releases. Shape
   keys: test_command and mutation_command non-empty command strings;
   env_command a non-empty command string naming one entry point that
   implements the verbs up, down, seed <scenario>, logs and
   url <service> (contract in the environment stack skill); source_dirs
   a non-empty list of repo-relative paths (absolute paths refused);
   output_language a non-empty language name (scope: ONLY the body
   prose of authored .md documents); terminology_language a non-empty
   language name, default English (scope: names, technical terms, code
   and comments, commit messages and PR bodies; the machine layer, file
   names, keys, ids, CLI output, always stays English); max_parallel an
   optional positive integer (the delivery-lanes flow's lane-proposal
   cap; absent means 3; this gate is its only writer);
   doc_type_designations a map of each taxonomy type-kebab to its
   rendered designation string, minted from the canonical English table
   into output_language, machine-managed and, like the language axes,
   changed only through this gate, a change re-checking every vault title.
4. Present the impact analysis before writing: which roles' skill
   bindings change (the static role-to-skill map lives in
   ${CLAUDE_PLUGIN_ROOT}/flows/develop.md, step 0; method skills such as
   the architect's architecture skill are static and config-independent),
   what future packages will do differently (for example: adding a
   document store means the architect loads both database skills and
   declares a store per entity), and any migration consequences for
   existing work. A terminology_language change governs newly authored
   names only; existing names, glossary rows and merged code are never
   renamed, state this fork whenever the key changes on a project with
   authored content. The apply / reject decision is asked through the
   AskUserQuestion popup (impact summary in the option descriptions).
5. On approval, write workspace/config.json and nothing else: this gate
   touches exactly one file. Confirm the diff to the user. On rejection,
   stop; nothing is written.
