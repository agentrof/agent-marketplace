---
name: setup
description: Idempotent project bootstrap for the software-team. Creates the workspace skeleton, materializes templates, and builds the machine-managed config interactively. Safe to re-run; never breaks what exists.
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
   git (every package ends in a pull request). Sub-directory invocation
   still anchors at the root.
2. Workspace collision: a foreign workspace/ directory at the root: ask
   for an alternative name and use it consistently, substituting it for
   workspace/ in every materialized template content and skeleton path
   (the .gitignore runs rule, the CLAUDE.md import, source_dirs). The
   team's own layout is recognized by a config.json carrying the
   managed_by note, or by the docs/ plus memory/ pair; a directory
   matching neither is foreign. Record the chosen name as a one-line
   note in the project CLAUDE.md once step 3 has materialized it.
3. Materialize templates from ${CLAUDE_PLUGIN_ROOT}/templates/, only
   where missing (idempotency: never overwrite an existing file, only
   add):
   - .gitignore: create from the template, or append only its missing
     lines (the runs/ ignore rule is mandatory).
   - CLAUDE.md: create from the template with the memory import.
   - workspace/memory/: ask "do you have personal rule files (a me.md
     and a profile.md, or a directory containing them)?"; given paths
     are copied verbatim per file, otherwise me.md and profile.md come
     from the templates.
4. Create the skeleton, only missing parts: workspace/apps/,
   workspace/docs/business-analysis/, workspace/docs/system-architecture/,
   workspace/docs/design-system/pages/, workspace/demos/,
   workspace/sketches/, workspace/runs/. Git does not track empty
   directories: drop a .gitkeep in each folder created empty (runs/ is
   gitignored and needs none).
5. Build workspace/config.json interactively. Its first key is always
   "managed_by": "software-team plugin; change only through the
   configure entry". An existing config.json is never re-interviewed:
   only missing keys are asked for and added. Detect from project
   manifests first (a pyproject.toml or requirements.txt implies the
   python backend; a package.json depending on react implies the
   typescript frontend), ask only for gaps: backend_stack (supported:
   python-fastapi), frontend_stack (supported: react-typescript),
   databases (set drawn from sql, nosql; one or both, never empty),
   test_command (the one command that runs the whole suite; point it at
   a script or make target when several stacks must run), source_dirs,
   output_language (default English). A
   stack outside the supported set is refused honestly: this team ships
   tested stacks only, and new stacks arrive as maintainer releases.
6. Continuous integration: no PR-triggered test workflow in the
   repository's CI directory (for GitHub, .github/workflows/): offer to
   add one from ${CLAUDE_PLUGIN_ROOT}/templates/ci-tests.yml with the
   configured test command substituted for its placeholder.
7. Close with the summary and pointers: start with business-analysis for
   the first topic, design-system before any screen work, then request.
