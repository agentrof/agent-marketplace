# Agent Marketplace

Read `memory/me.md` and follow it before editing this repository.

## Working in this repository

- Read `docs/architecture.md` for invariants and `docs/authoring.md` before changing plugin content.
- Keep host-neutral canonical sources under `plugins/`; host manifests, contracts and overlays live under `platforms/`.
- Generate all registered host distributions with `python3 tools/build_distributions.py`.
- Never edit `dist/` by hand.
- Create components with `tools/scaffold.py`; do not hand-copy them.
- Derived README counts are maintained by `make counts`, never by hand.
- Run `make check` before committing; one validation error fails CI.

## Maintainer automation

- Follow `docs/maintainer-automation-protocol.md` for repository issue and
  release work. This protocol is separate from the shipped project Delivery
  flow.
- For an issue task, analyze root cause, challenge the solution, assess every
  affected host and operating system, implement and verify, open a PR, then
  stop before merge until the user approves.
- An explicit request to start a release authorizes the complete release flow
  for the unambiguous PR set selected in the active task, including green
  feature and release PR merges and cleanup. It never authorizes unrelated PRs
  or bypassing a failed gate.
- Finish a release only after remote and local refs are audited, selected
  merged branches are removed, the active branch is `main`, and the worktree is
  clean.
