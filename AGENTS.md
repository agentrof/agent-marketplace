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
