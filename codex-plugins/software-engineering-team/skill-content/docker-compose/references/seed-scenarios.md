# Seed Scenarios

Deterministic, named data states for the running environment. The runner and the registry live under workspace/environment/seed/ and are devops-owned; the domain content each scenario loads is application code, owned by that application's developer.

## Registry Shape

workspace/environment/seed/scenarios.json declares every scenario:

```json
{
  "scenarios": {
    "baseline": {
      "description": "Realistic core-process graph for live verification.",
      "loaders": [
        {"app": "api", "entry": "seed.load", "args": ["baseline"]}
      ]
    },
    "empty": {
      "description": "Schema only; no domain rows.",
      "loaders": []
    }
  }
}
```

- Keys are snake_case; the scenario name is the single argument callers pass to the `seed` verb.
- A scenario not in the registry does not exist: the runner refuses unknown names instead of guessing.

## Reserved Names

- `baseline`: a realistic, minimal-but-complete graph exercising the core processes; the QA live protocol seeds it before the surface walk.
- `demo`: curated data for a stakeholder walkthrough of the real application; richer narrative, same determinism rules.
- `empty`: schema and reference data only. The default `up` scenario is project policy, recorded in the contract document.

Projects add scenarios freely (an error-state graph, a load-shaped set); each one enters the registry and the contract document's catalog.

## Determinism Rules

- Fixed identifiers and fixed timestamps: the same scenario yields byte-identical domain data on every run. Generated randomness is allowed only behind a fixed seed recorded in the scenario.
- Safe to re-run: loading a scenario into an already-seeded environment converges to the same state (wipe-then-load or idempotent upserts; loader's choice, converging result mandatory).
- Referentially intact: a scenario loads as a consistent graph in dependency order; a scenario that leaves orphaned rows or dangling references is a defect even if every insert succeeded.

## The Runner-Loader Boundary

The runner (devops-owned) resolves the scenario in the registry and executes each listed loader inside its application's container, passing the scenario name. The loader (application-owned) knows the domain: factories, ordering, integrity. This boundary keeps domain knowledge out of the environment prefix and environment mechanics out of the applications.

When a story adds domain entities that the reserved scenarios must cover, the owning developer extends the loader in the same story; the devops engineer only wires new loaders into the registry.
