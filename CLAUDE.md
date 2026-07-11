# Load

@memory/me.md

## Rules

memory/me.md. Read and follow.

## Working in this repo

- This is the Agent Marketplace source. Read [docs/architecture.md](docs/architecture.md) for the invariants and [docs/authoring.md](docs/authoring.md) before touching plugin content.
- Run `make check` before committing; CI runs the same gates and one finding is red.
- Derived counts are never hand-written; `make counts` maintains the README marker block.
- Create new components with `tools/scaffold.py`, never by hand-copying.
