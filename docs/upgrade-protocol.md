# Upgrade protocol

An Agent Marketplace upgrade is a package replacement followed by one
convergent project refresh. The consuming repository's authored Markdown and
configuration remain the source of truth.

1. Build and validate all registered host distributions from the same source snapshot.
2. Run `setup_project.py inspect --project-root <root> --json`. This is a
   read-only, pre-mutation plan over the workspace contract, policy-owned
   Obsidian keys, package-local Obsidian plugin projection, managed ignore and
   checkout-attribute blocks and portable gate. JSON changes expose exact
   key-level before/after values; byte-owned assets expose hashes. Resolve
   every blocker before applying. On native Windows, inspect also returns the
   `git.core_longpaths` choice request while the repository's local Git
   config does not turn `core.longpaths` on, and apply refuses until
   `--choice git.core_longpaths=set|leave` answers it.
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
   projection choice gate. A Codex projection keeps its recorded execution
   profile and any model fallback its managed agent files record. A release
   that moves a tier to a newer pinned model rewrites the role files
   of an `auto` projection on this refresh and leaves an `inherit` projection
   unchanged. Claude Code roles change at the refresh that renders them again
   into `.claude/agents/` with the project's tier models, efforts and role
   tiers. Until a refresh renders them, on either host, the session start
   check reports the role files whose stamp is not the installed package's or
   the project config's, as after a pull that changes `tier_models` or
   `role_tiers`; `.codex/agents/` and `.claude/agents/` are ignored local
   projections, so a pull never updates them.
5. Config schema v2 has only team identity, language settings and the
   valid overrides `tier_models` and `role_tiers`, which setup keeps and never
   writes. An upgrade removes every field outside that closed shape without
   editing Markdown, aliases or links, and stops on an override the installed
   package no longer takes, naming the tier or role. Taxonomy additions and
   graph-color changes therefore never write `workspace/config.json`.
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
   the refreshed skills and hooks load. When the refresh first adds the managed
   `.gitattributes` block, a checkout whose governed files Git already converted
   runs the one-time re-checkout in the
   [setup skill](../plugins/software-engineering-team/skill-content/setup/SKILL.md).

Experience prototype interiors are author-owned. Inspect accepts arbitrary
regular files beneath Experience `artifacts/` directories and does not migrate,
interpret or synthesize their contents. It continues to protect only
compiler-owned `_generated/` and `_ledger/` state, path containment and file
identity required for safe lifecycle snapshots.

The next approval after an existing schema-v2 application receipt appends a
schema-v3 opaque snapshot receipt while preserving the verified historic hash
chain. No prototype file is rewritten or interpreted during that transition.

An invalid Experience path spelling does not make project diagnostics or
repair unreachable. The Bash hook snapshots any tree whose paths remain
lexically confined to `experience-design/` and whose bytes and filesystem
identity are recoverable. Canonical path rules are evaluated after a command
changes the vault, so the affected project chooses its own bounded rename,
archive or other repair. The package does not prescribe or execute that
project-local remediation. Symlinked, hard-linked or unreadable state remains
fail-closed because no trustworthy rollback snapshot can be created; direct
`pwd`, bounded `git status`, setup `inspect`/`check`, and Experience compiler
`check`/`status` remain available for diagnosis.

Stage routing inspects Git only at a completed-stage handoff. The relevant
config, approved subtree, home note and stage map must be tracked, committed and
clean. For Experience Design that path set includes the exact prototype artifact
inventory, process packages and compiler-owned application receipt/ledger state
selected by the approved transaction. Those Experience paths must match their
committed blobs byte for byte; setup's managed `.gitattributes` rule checks
`workspace/docs/` out without line-ending conversion, which keeps that true on
Windows. Unrelated product
application code and the current draft stage are outside a different stage's
path set and do not block active authoring. A request without an approved,
committed backlog returns to Requirement Flow; an approved backlog proceeds to
Delivery Flow.

The project-local `.agentrof/agent-marketplace/.runtime/` directory is
disposable and never participates in compatibility decisions. A refresh may
recreate it without changing Requirement or Delivery state.

## Version and build identity

- A `.changes/*.json` file declares release impact. The release commit, which
  `python3 tools/release.py bump` makes, is the only writer that bumps
  `versions.json`; host manifests expose that calendar release version,
  `YYYY.M.N`.
- Each generated package carries `.agent-marketplace-package.json` with its
  plugin and marketplace release versions, the closed file/hash inventory,
  executable paths and the closed `delivery_protocol` read/write capability.
  This schema-v4 metadata verifies the complete package and selects compatible
  Delivery record adapters; it is not project state. Generated UTF-8 text uses
  canonical LF bytes and release preparation rematerializes tracked files
  under its fixed Git checkout policy; unknown and binary payloads remain
  byte-exact.
- Packages carry no per-commit source identity, and each file hash has a line
  of its own, so pull requests that change different package files merge
  without a `dist/` conflict, except adds at one sort position, an add after a
  changed last entry, a removal next to another removal or an add, and a
  removal of the last entry beside a change to the entry before it. The
  deterministic snapshot `build_id` of the canonical sources that every host
  build shares is computed instead: the release commit records it in
  `.release/stable.json`, check-pr's replay reproduces it, and
  `python3 tools/release.py verify-release` recomputes it at the commit a
  release tags.
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
