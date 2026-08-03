---
name: setup
description: Idempotent project bootstrap for the software-engineering-team. Creates the workspace skeleton, materializes templates, and builds the machine-managed config interactively. Safe to re-run; never breaks what exists.
exposure: entry
---

# Setup

Stand the project up in an empty folder or existing repository: complete gaps without overwriting user content.

## When to Use
- First thing after installing the plugin in a project.
- Any time the workspace skeleton might be incomplete.

## Procedure

Every decision uses a choice gate (recommended option first, tradeoffs in
descriptions); enum keys offer supported values, while commands and paths
offer detected candidates plus free-form input.

Apply the active host contract's pre-flight, project-instruction materialization,
and restart rules. `{{workspace}}` means the chosen workspace directory name.

1. Git check: resolve the project git root and anchor everything there.
   No repository: offer to initialize one; the team cannot work without
   git (every story ends in a pull request). Then resolve the
   backbone: the launcher
   PMO="${AGENTROF_HOME:-$HOME/.agentrof}/bin/pmo_cli.py" and the
   dispatcher RUN="${AGENTROF_HOME:-$HOME/.agentrof}/bin/agentrof_run.py"
   with TEAM=software-engineering-team. Launcher missing: bootstrap per
   the control-tower entry's launcher discovery, run `ensure` once,
   and register both plugin roots when the hooks have not ("$RUN"
   register --plugin <name> --root <install path>).
2. Workspace collision: a foreign workspace/ directory at the root:
   ask for an alternative name and use it consistently, substituting
   it in every materialized template content and skeleton path (the
   .gitignore work-orders rule, host instructions, source_dirs).
   The team's layout is recognized by config.json's managed_by note or
   the docs/ plus memory/ pair; anything else is foreign. Record the
   chosen name through the active host contract once step 3 materializes
   project instructions.
3. Materialize templates from the directory printed by
   "$RUN" path "$TEAM" templates, only where missing (idempotency: never overwrite an existing file, only
   add):
   - .gitignore: create from the template, or append only its missing
     lines (the work-orders/ ignore rule is mandatory).
   - Project instructions through the active host contract, including
     the memory import or managed instruction block it defines.
   - workspace/memory/: ask "do you have personal rule files (a me.md
     and a profile.md, or a directory containing them)?"; given paths
     are copied verbatim per file, otherwise me.md and profile.md come
     from the templates.
   - workspace/docs/ vault payload from the template directory's
     vault/ subtree, per file, only where missing: home and the
   .obsidian payload with plugins/ copied recursively (the vault app
     asks a one-time trust prompt to enable them). Subtree map seeds
     are born with their trees by the tree-birthing entries, never by
   setup; the obsidian-vault skill owns their law.
4. Create the skeleton, only missing parts: workspace/apps/,
   workspace/docs/business-analysis/, workspace/docs/solution-design/,
   workspace/docs/system-architecture/, workspace/docs/maps/,
   workspace/docs/design-system/pages/, workspace/demos/,
   workspace/sketches/, workspace/environment/, workspace/work-orders/.
   Git does not track empty directories: drop a .gitkeep in each empty
   folder created (work-orders/ is gitignored and needs none). Topic
   analysis spaces inside workspace/docs/business-analysis/ are created
   by the business-analysis entry, never by setup.
5. PMO backbone: with the CLI resolved in step 1, register the
   project: project register --key <kebab project name> --name "<name>"
   --team software-engineering-team --stamp-config workspace/config.json (stamps
   project_key into the config; idempotent). Every flow resolves the
   project by that key.
6. Build workspace/config.json interactively; first key always
   "managed_by": "software-engineering-team plugin; change only through
   the configure entry". An existing config.json is never re-interviewed:
   missing keys are asked and added. Detect from manifests first
   (pyproject.toml or requirements.txt: the python backend; a package.json
   depending on react: the typescript frontend), ask only the gaps:
   backend_stack (supported: python-fastapi), frontend_stack (supported:
   react-typescript), databases (from sql, nosql; one or both, never
   empty), test_command (one command for the whole suite; a script or make
   target when several stacks run), mutation_command (the mutation-testing
   runner, {{changed_files}} placeholder; required on code stories,
   absence a blocking finding; verify by one one-file run, or in a no-code
   project record unverified for the first code story's QA gate),
   environment_stack (supported: docker-compose; detect from a compose
   file in workspace/environment/), env_command (one entry point for the
   verbs up, down, seed <scenario>, logs, url <service>; contract in the
   environment stack skill; verify by one up-then-down, or record
   unverified for the first environment story's QA gate), source_dirs,
   output_language (scope: .md body prose) and terminology_language
   (names, technical terms, code, comments, commits, PR bodies; the
   machine layer always stays English), both defaulting English, always
   written out; scale (optional; absent means small): one popup
   question "Expected project scale?" with four options: small
   (Recommended: today's thresholds, feature- or single-team-scale
   analysis), medium (3x volume thresholds, one extra nesting level),
   large (9x, two extra levels) and "larger than large" (opens the
   follow-up); when "larger than large" is chosen, a second question
   offers exactly x-large (45x), xx-large (225x) and enterprise
   (1125x); write the chosen value even when it is small (the config
   self-documents). Then mint
   doc_type_designations: render the canonical table (obsidian-vault
   metadata) into output_language, one per taxonomy type, for owner
   review, and write it through the checker's reconcile-designations
   verb (one --set per type): the designation keys and their history
   ledger are hook-guarded with the verb as sole writer. An unsupported
   stack is refused honestly: tested stacks only.
7. Continuous integration: follow `references/ci-bootstrap.md`. Materialize
   the CI template only after replacing `{{test_command}}`,
   `{{audit_command}}`, and `{{env_command}}`; never ship a dead placeholder.
8. Close: run "$RUN" run "$TEAM" scripts/vault_check.py check
   --vault workspace/docs. Fresh tree: any finding is a setup bug.
   Existing tree: findings in setup-authored files are setup bugs;
   pre-existing content findings are named as vault degradation and
   routed to the organize-docs entry, the on-demand full-vault
   reorganization (scoped stewardship at each docs gate still repairs
   its own subtree). Then the pointers: business-analysis first,
   solution-design for foundations, design-system before screen work,
   then deliver.
