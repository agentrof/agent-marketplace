# Agent Marketplace

Read `memory/me.md` and follow it before editing this repository.

## Working in this repository

- Read `docs/architecture.md` for invariants and `docs/authoring.md` before changing plugin content.
- Keep canonical plugin sources under `plugins/`; generate Codex distributions with `python3 tools/sync_skill_surfaces.py` followed by `python3 tools/build_codex_plugins.py`.
- Never edit `claude-skills/`, `codex-skills/`, or `codex-plugins/` by hand.
- Create components with `tools/scaffold.py`; do not hand-copy them.
- Derived README counts are maintained by `make counts`, never by hand.
- Run `make check` before committing; one validation error fails CI.
