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
4. Preserve authored documents and valid retained project configuration fields;
   setup removes unknown or retired configuration keys as part of the closed
   schema replacement. It refreshes only policy-asserted Obsidian JSON keys
   and preserves user-owned instruction companions through the separate host
   projection choice gate.
5. Config schema v2 has only team identity and language settings. An upgrade
   removes every field outside that closed shape without editing Markdown,
   aliases or links. Taxonomy additions and graph-color changes therefore never
   write `workspace/config.json`.
6. `workspace/` is the only managed workspace and every second managed vault
   is rejected. Requirement Flow determines request applicability. Repeated
   apply with the same package and project must produce an empty inspect plan.
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
recreate it without changing Requirement or Delivery state.

## Version and build identity

- A `.changes/*.json` file declares release impact. The release workflow is the
  only writer that bumps `versions.json`; host manifests expose that semantic
  plugin version.
- Each generated package carries `.agent-marketplace-package.json` with a
  deterministic snapshot `build_id`, source provenance, file hashes and the
  closed `delivery_protocol` read/write capability. This metadata verifies the
  package and selects compatible Delivery record adapters; it is not project
  state.
- Setup never copies a package version or build ID into project configuration,
  and upgrade never compares an old project build ID with a new one. Active
  Delivery compatibility is proven from package metadata plus the remote Fence
  and control-record protocol, without a project upgrade ledger.

When open Deliveries exist, the Delivery coordinator acquires the project Fence
in `upgrade` mode, quiesces active Items, validates every Integration and Item
control record with the advertised protocol adapters, applies only
package-owned schema changes, and releases all Delivery barriers atomically
before returning the Fence to `open`. Setup never performs a remote mutation.
If it discovers a protocol-1 Fence, complete the coordinator's
`upgrade-fence-v1` migration after all Slots are free and after Governance is
approved; protocol-1 state is otherwise readable only for that conversion.
