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
   model_overrides reserved schema room for future model pinning:
   consumed by nothing yet (the source alias enum stands), so any
   requested value is declined with that reason;
   doc_type_designations a map of each taxonomy type-kebab to its
   rendered designation string, minted from the canonical English table
   into output_language. It and its doc_type_designation_history ledger
   are machine-managed with ONE writer, the checker's
   reconcile-designations verb (the write-time hook denies every other
   Write/Edit of these keys). Validate a requested value: key a known
   taxonomy type, value a non-empty output_language phrase, refused
   when fold-equal to another type's current designation.
4. Present the impact analysis before writing: which roles' skill
   bindings change (the static role-to-skill map lives in the develop
   flow, step 0, printed by "$RUN" path "$TEAM" flows/develop.md, the
   dispatcher per its state contract; method skills such as
   the architect's architecture skill are static and config-independent),
   what future packages will do differently (for example: adding a
   document store means the architect loads both database skills and
   declares a store per entity), and any migration consequences for
   existing work. A terminology_language change governs newly authored
   names only; existing names, glossary rows and merged code are never
   renamed, state this fork whenever the key changes on a project with
   authored content. The apply / reject decision is asked through the
   AskUserQuestion popup (impact summary in the option descriptions).
   For a designation change (or an output_language change, which
   re-renders the canonical table from the obsidian-vault skill's
   metadata into the new language and passes every pair), the impact
   analysis IS the verb's plan (the title law it transitions is that
   skill's):
   "$RUN" run "$TEAM" scripts/vault_check.py reconcile-designations
   --vault workspace/docs --set <type>=<value> ... --dry-run --json.
   Render the popup from that plan: apply / reject / adjust wording,
   with the before -> after retitles and alias sweeps in the
   descriptions; when locked_skipped is non-empty, a SECOND question:
   relabel the locked records (PMO-audited, title and H1 only,
   recommended) or leave them as named check warnings. manual and
   blocked entries are presented as named residuals, never as
   approvable options.
5. On approval: designation keys execute through the verb, never a
   Write (run it without --dry-run, adding --include-locked per the
   locked answer and --actor configure); it writes config.json (map
   plus ledger in one write), transitions every affected title and H1,
   sweeps byte-equal link aliases, re-renders the generated views and
   appends one PMO audit event per relabeled locked record. Close with
   a full check: green, or every residual named. Every OTHER key:
   write workspace/config.json and nothing else, this gate touches
   exactly one file. Confirm the diff to the user. On rejection, stop;
   nothing is written.
