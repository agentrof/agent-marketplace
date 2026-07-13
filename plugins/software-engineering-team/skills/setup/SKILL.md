---
name: setup
description: Idempotent project bootstrap for the software-engineering-team. Creates the workspace skeleton, materializes templates, and builds the machine-managed config interactively. Safe to re-run; never breaks what exists.
disable-model-invocation: true
---

# Setup

Stand the project up: workspace, templates, config. Run it in an empty
folder or an existing repository; it completes gaps and breaks nothing.

## When to Use
- First thing after installing the plugin in a project.
- Any time the workspace skeleton might be incomplete.

## Procedure

1. Git check: resolve the project git root and anchor everything there.
   No repository: offer to initialize one; the team cannot work without
   git (every story ends in a pull request). Sub-directory invocation
   still anchors at the root.
2. Workspace collision: a foreign workspace/ directory at the root: ask
   for an alternative name and use it consistently, substituting it for
   workspace/ in every materialized template content and skeleton path
   (the .gitignore work-orders rule, the CLAUDE.md import, source_dirs). The
   team's own layout is recognized by a config.json carrying the
   managed_by note, or by the docs/ plus memory/ pair; a directory
   matching neither is foreign. Record the chosen name as a one-line
   note in the project CLAUDE.md once step 3 has materialized it.
3. Materialize templates from ${CLAUDE_PLUGIN_ROOT}/templates/, only
   where missing (idempotency: never overwrite an existing file, only
   add):
   - .gitignore: create from the template, or append only its missing
     lines (the work-orders/ ignore rule is mandatory).
   - CLAUDE.md: create from the template with the memory import.
   - workspace/memory/: ask "do you have personal rule files (a me.md
     and a profile.md, or a directory containing them)?"; given paths
     are copied verbatim per file, otherwise me.md and profile.md come
     from the templates.
4. Create the skeleton, only missing parts: workspace/apps/,
   workspace/docs/business-analysis/, workspace/docs/system-architecture/,
   workspace/docs/design-system/pages/, workspace/demos/,
   workspace/sketches/, workspace/environment/, workspace/work-orders/.
   Git does not track empty directories: drop a .gitkeep in each folder
   created empty
   (work-orders/ is gitignored and needs none). Topic analysis spaces
   inside workspace/docs/business-analysis/ are created by the
   business-analysis entry, never by setup.
5. PMO backbone: resolve the PMO CLI (the launcher at
   "${AGENTROF_HOME:-$HOME/.agentrof}/bin/pmo_cli.py"; missing means
   look up the project-management-office entry in
   $HOME/.claude/plugins/installed_plugins.json, use its install path plus
   scripts/pmo_cli.py, and run its sync-launcher subcommand once; no
   entry there means the plugin is missing: stop and tell the user to
   reinstall this plugin, the dependency brings project-management-office
   in). Run init-db, then register the
   project: project register --key <kebab project name> --name "<name>"
   --team software-engineering-team --stamp-config workspace/config.json (stamps
   project_key into the config; idempotent). Every flow resolves the
   project by that key.
6. Build workspace/config.json interactively. Its first key is always
   "managed_by": "software-engineering-team plugin; change only through the
   configure entry". An existing config.json is never re-interviewed:
   only missing keys are asked for and added. Detect from project
   manifests first (a pyproject.toml or requirements.txt implies the
   python backend; a package.json depending on react implies the
   typescript frontend), ask only for gaps: backend_stack (supported:
   python-fastapi), frontend_stack (supported: react-typescript),
   databases (set drawn from sql, nosql; one or both, never empty),
   test_command (the one command that runs the whole suite; point it at
   a script or make target when several stacks must run),
   mutation_command (the mutation-testing runner for the stacks, with a
   {{changed_files}} placeholder for per-story scope; the QA gate
   requires it on code stories and treats its absence as a blocking
   finding; verify it by running it once on one file before writing the
   config, and in a project with no code yet record it as unverified so
   the first code story's QA gate performs the verification),
   environment_stack (supported: docker-compose; detect from an existing
   compose file under workspace/environment/), env_command (one entry
   point implementing the verbs up, down, seed <scenario>, logs and
   url <service>; contract in the environment stack skill; verify by
   running up then down once, and in a project with no environment yet
   record it as unverified so the first environment-impacting story's QA
   gate performs the verification), source_dirs, output_language
   (default English). A stack outside the supported set is refused
   honestly: this team ships tested stacks only, and new stacks arrive
   as maintainer releases.
7. Continuous integration: no PR-triggered test workflow in the
   repository's CI directory (for GitHub, .github/workflows/): offer to
   add one from ${CLAUDE_PLUGIN_ROOT}/templates/ci-tests.yml,
   substituting every placeholder. {{test_command}} takes the configured
   test command. {{audit_command}} takes one audit command per
   configured stack, anchored at that stack's lockfile: python-fastapi
   gets pip-audit against the backend's requirements or lock file,
   react-typescript gets npm audit --audit-level=high run in the
   frontend app directory; two stacks chain with &&. {{env_command}}
   takes the configured environment command; include the
   environment_smoke job only when an up-then-down probe passes right
   now, otherwise omit it (never ship a dead placeholder) and note that
   re-running setup after the first environment story appends it: a
   workflow missing the smoke job counts as a gap once the probe passes.
   Keep the dependency-audit job, and route any advisory it raises
   through the request entry as a fix-atomic lockfile bump.
8. Close with the summary and pointers: start with business-analysis for
   the first topic, design-system before any screen work, then request.
