# Upgrade protocol

An Agent Marketplace upgrade is a package replacement followed by one
convergent project refresh. The consuming repository's authored Markdown and
configuration remain the source of truth.

1. Build and validate both host distributions from the same source snapshot.
2. Run `setup_project.py inspect --project-root <root> --json`. This is a
   read-only, pre-mutation plan over the workspace contract, policy-owned
   Obsidian keys, package-local Obsidian plugin projection, managed ignore block
   and portable gate. JSON changes expose exact key-level before/after values;
   byte-owned assets expose hashes. Resolve every blocker before applying.
3. Run `setup_project.py apply --project-root <root> --json`, then
   `setup_project.py check --project-root <root> --json`. All three commands use
   the same convergence planner; apply rebuilds its authoritative plan after
   acquiring the mutation guard. Apply rolls back setup-owned paths that still
   match its exact postimage when the closing check fails; a concurrent edit is
   preserved and reported as a rollback conflict when observed at a target
   boundary. Mutating setup apply processes are serialized and every target is
   rechecked immediately before atomic replacement. Pause non-setup editors on
   all setup-managed targets during this short window; no portable filesystem
   primitive can conditionally replace against an uncooperative writer. Check
   rejects any operation still required.
4. Preserve authored documents, unknown project configuration fields and
   user-owned Obsidian knobs. Refresh only policy-asserted JSON keys. Preserve
   user-owned instruction companions through the separate host projection
   choice gate.
5. Preserve configured designation wording and retired-value history. Setup
   may add defaults for newly shipped document types, but never replaces a
   project-selected designation. Adding a default writes only
   `workspace/config.json`; setup never retitles authored notes as an implicit
   upgrade side effect. Intentional designation changes remain explicit
   configure/reconcile operations with their own reviewable plans.
6. The compatibility `--workspace workspace` argument is accepted; every other
   workspace value and every second managed vault is rejected. Legacy origin
   input is removed during migration; Requirement Flow determines request
   applicability. Repeated apply with the same package and project must
   produce an empty inspect plan.
7. Treat `workspace/docs/.obsidian/community-plugins.json` and each
   policy-owned `.obsidian/plugins/<id>/` directory as ignored local package
   projections. Refresh updates shipped files and removes package-retired
   assets from those owned directories. It leaves unrelated plugin directories
   alone. These files are validated locally but never committed in the
   consuming repository.
8. Run the portable vault gate and every compiler for a subtree that exists,
   including the approved-integrity check when the backlog is approved.
9. Review and commit the exact tracked diff, then start a fresh host session so
   the refreshed skills and hooks load.

Stage routing inspects Git only at a completed-stage handoff. The relevant
config, approved subtree, home note and stage map must be tracked, committed and
clean. Unrelated application work and the current draft stage are outside that
path set and do not block active authoring. A request without an approved,
committed backlog returns to Requirement Flow; an approved backlog proceeds to
Delivery Flow.

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
