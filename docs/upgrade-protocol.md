# Upgrade protocol

An Agent Marketplace upgrade is an idempotent package refresh followed by a
project-local setup and check. The consuming repository's authored Markdown is
the source of truth.

1. Build and validate both host distributions from the same source snapshot.
2. Preview changes to package-owned instructions, hooks, vault payload and
   compiler-owned views.
3. Preserve authored documents and user-owned companion files. Apply only the
   selected managed-file changes.
4. Preserve configured designation wording and retired-value history. Setup
   may add defaults for newly shipped document types, but never replaces an
   existing project-selected designation.
5. Rerun the packaged setup command against the canonical `workspace/` path.
   The compatibility `--workspace workspace` argument is accepted; every other
   workspace value and every second managed vault is rejected. Repeated setup with the same package and
   project inputs must produce no authored-file diff.
6. Run the portable vault gate and every compiler for a subtree that exists,
   including the approved-integrity check when the backlog is approved.
7. Review and commit the exact tracked diff, then start a fresh host session so
   the refreshed skills and hooks load.

The project-local `.agentrof/agent-marketplace/.runtime/` directory is
disposable and never participates in compatibility decisions. A refresh may
recreate it without changing preparation state.

## Version and build identity

- A `.changes/*.json` file declares release impact. The release workflow is the
  only writer that bumps `versions.json`; host manifests expose that semantic
  plugin version.
- Each generated package carries `.agent-marketplace-package.json` with a
  deterministic snapshot `build_id`, source provenance and file hashes. This
  metadata verifies the package; it is not project state.
- Setup never copies a package version or build ID into project configuration,
  and upgrade never compares an old project build ID with a new one. There is
  therefore no compatibility lock, migration chain or durable upgrade ledger.
- `doc_type_designation_history` is not an upgrade ledger. It contains only
  retired project-selected display values used to find stale titles after an
  explicit designation rename.
