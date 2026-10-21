# Architecture

This repository ships one standalone Software Engineering Team. Its canonical
behavior is host-neutral; Claude Code and Codex are packaging adapters.

## Invariants

1. Every enforceable rule is validated by `tools/validate.py` and `make check`.
2. Roles contain behavior and boundaries; domain knowledge lives in skills.
3. Entry skills are the only user surface. Internal skills are not user-facing.
4. Durable project truth is tracked files, never conversation memory.
5. One standalone Software Engineering Team owns one project checkout.
6. The project-local runtime is
   `<git-root>/.agentrof/agent-marketplace/.runtime/` and contains only ignored,
   disposable scratch and cache files. Deleting it cannot change project truth.
7. The project documents live in `workspace/docs/`, versioned with the
   project. No second workspace path is valid.
8. File names are stable slugs; membership is path-derived.
9. Authored titles are direct, natural phrases in the configured output
   language; IDs stay in aliases, while stable type keys remain shipped
   policy. Issue reporting is an external, stateless support workflow and is
   never a project evidence source.
10. Timestamps written by compilers come from UTC system time. User-authored
    approval timestamps are not accepted as evidence.
11. Distribution output under `dist/` is generated only by
    `tools/build_distributions.py`.
12. Every host is discovered through `platforms/<host>/adapter.json` and its
    adapter module. Host-specific path names, manifests, permissions, hooks,
    the pinned model catalog and the per-class model and effort profiles,
    and runtime behavior remain in that platform directory;
    central tooling
    only orchestrates registry discovery, canonical copying, provenance, and
    owned generated-tree replacement.
13. Implementation work runs on the fast lane: one task, one branch and one
    pull request to the target branch, verified by the project's CI and one
    reviewer. Release Management remains a later scope.
14. `workspace/config.json` is a closed bootstrap contract. Technology and
    datastore choices belong to accepted Solution decisions and commands to
    the operation documents.
    Beside the languages it holds only the overrides `class_models` and
    `role_classes`: a class slot's model and effort per host, within what
    the package's class map lets that slot take, and a role's class on
    every host.
    Setup writes neither, and the package catalog and profiles stay the
    defaults.
15. An `artifacts/` folder under `workspace/docs/` holds opaque, local
    files. Its content is neither a note nor workflow executable behavior;
    Markdown may link to real local artifacts.
16. Context is found, not packed. When `.agentrof/graphify` holds a graph,
    a role queries it as a starting point; otherwise it starts from the
    task's sources and grep. Either way it reads only what the change needs.
    The graph is generated, disposable and never project truth.
17. Maintainer issue work starts only from an explicit user instruction in an
    active maintainer session; GitHub issue events never start an agent. The
    protocol may prepare a pull request but never merge one without explicit
    approval. Stable release authority comes only from an explicit user request
    bound to an unambiguous PR set. Every pull request emits the required
    aggregate and two-host lifecycle contexts. Test partitions must account
    for every selected case; shared or unknown impact selects full coverage.
    Reused validation binds successful trusted workflow evidence to the exact
    tested tree and current coverage contract; missing evidence runs fresh
    tests, and both host lifecycles always run fresh. Release-owned files
    change only in a release commit, the last commit of a pull request, whose
    tree is the deterministic bump of its parent at its own commit date; the
    replay ignores ambient Git attributes, excludes, replacement refs and
    graph overlays. A release tags an approved `main` commit whose own push
    validation succeeded and never runs the tests again. Snapshot records are
    prefix-free;
    generated text uses LF, unknown/binary payloads remain byte-exact, and the
    tracked `package-modes.json` contract supplies platform-neutral executable
    modes. Schema-v4 package provenance binds the closed file inventory, hashes
    and executable set, one hash line per file, and carries no per-commit
    source identity; the release metadata records the source snapshot
    `build_id` and verification recomputes it. Public stable installs,
    exact-lease ref transactions, immutable Release reconciliation and
    clean-ref completion remain mandatory.
18. A process behaviour change ships as the new default. A choice a project
    may make differently is one optional key of `workspace/config.json`,
    validated by `project_config.py check`; today that is `pr_proof`
    (`remote_ci` by default, `local_record` for a project without remote CI).
    Rendered agents' model and effort lines follow each host's model catalog
    and execution profile. A user-armed session entry such as autopilot
    changes no output without an active grant, because its hooks exit
    silently.

Repository issue and release operations are documented separately in
[maintainer-operations-protocol.md](maintainer-operations-protocol.md).

## Ownership

- `plugins/software-engineering-team/`: canonical workflows, agents, skills,
  compilers and templates.
- `platforms/`: registered host adapters, native manifests, and overlays.
- `workspace/docs/`: consuming project's documents and task records.
- `tools/`: build, validation, release and scaffolding contracts.
