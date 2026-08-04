# Migration authoring

Every compatibility change is declared before its implementation. The source
of truth is `plugins/<component>/migrations/manifest.json`; generated host
packages must carry byte-identical copies.

## Contract shape

Each manifest names the component and separately declares database and project
contract surfaces. A surface has a baseline, current version, and an ordered
list of one-version steps. A component without a database uses `null` for that
surface. Step ids are permanent kebab and numeric identifiers, and each checksum
is `sha256:` plus the digest of the immutable runner file bytes.

```json
{
  "schema_version": 1,
  "component": "example-team",
  "database": null,
  "project_contract": {
    "baseline": 1,
    "current": 2,
    "steps": [
      {
        "id": "example-team.project.1-2",
        "from": 1,
        "to": 2,
        "checksum": "sha256:<digest>",
        "runner": "scripts/example_upgrade.py:migrate_1_to_2"
      }
    ]
  }
}
```

## Admission rules

- Never edit or reuse a released migration id. Add the next ordered step.
- Every step is forward-only, idempotent under recovery, deterministic, and
  transactional at its declared boundary.
- Data conversion is explicit. Renames, splits, merges, normalization, table
  moves, and value remapping preserve every source value or stop with a named
  finding. Destructive loss requires a separate product decision and is not an
  automatic migration.
- Database runners operate on the candidate only. They record row counts and
  conversion results in `schema_migrations`, then pass integrity and foreign-key
  checks before swap.
- Project runners declare exact owned surfaces and a three-way ownership rule.
  Marker-owned content may change. Unmanaged collisions and unexpected hash
  drift stop without overwrite.
- Host adapters may render host instruction and native agent files, but all
  migration decisions, ordering, data rules, and status semantics stay in the
  canonical component.
- A schema or managed-surface change includes forward, skipped-release,
  interruption, retry, downgrade, drift, collision, and cross-host parity tests.
- Scaffolding a new team creates the baseline manifest and shared adapters. A
  team cannot ship without a complete contract even when it has no migration.

The distribution builder rejects a missing manifest, a broken chain, an invalid
checksum shape, or a chain that does not reach `current`. Upgrade runtime also
compares catalogs from every installed host before it opens a candidate.
