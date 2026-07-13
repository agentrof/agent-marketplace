# Architecture

The invariants this marketplace is built on. Detail lives in
[authoring.md](authoring.md) and [orchestration.md](orchestration.md);
this file is the map and must stay short.

## Product thesis

A catalog of CURATED TEAMS, not a parts store. Each plugin is a complete,
tested, end-to-end team; users install a team and run at the goal. Parts
are not sold separately: agents and knowledge skills are encapsulated
behind a small user surface of entry skills. Supported stacks are fixed
and tested; new stacks are added by the maintainer as a skills folder plus
a config enum value plus tests. One plugin is not a team: project-management-office is the
shared operations backbone every team plugin depends on (declared in
plugin.json dependencies, so it installs automatically).

## Invariants

1. **Machine-enforced or not a rule.** Every content rule lives in
   `tools/validate.py` with a fixture that proves it fires. CI runs
   `make check` on every push and pull request; one finding is red. There
   are no exception files, allowlists or temporary waivers.
2. **Roles versus knowledge.** Agents are platform-independent role
   constitutions (Principles, Boundaries, Approach, Output Contract) with
   zero technology nouns. All technology and method knowledge lives in
   skills with progressive disclosure: a thin SKILL.md decision surface
   and depth in references/.
3. **Encapsulation.** Entry skills are the only user surface
   (`disable-model-invocation: true`). Knowledge skills are hidden
   (`user-invocable: false`). Agents are passive: no auto-trigger
   descriptions; they run only when a flow spawns them.
4. **Zero duplication.** Each fact exists once: shared method in a process
   skill, stack specifics inside the stack skill, shared flow bodies in
   flows/ files that entries delegate to.
5. **Derived counts are computed.** `tools/counts.py` injects counts into
   the single README marker block; `--check` is the CI drift gate. Numbers
   next to component nouns anywhere else fail validation.
6. **Output scoping.** Everything a team produces lands under the consuming
   project's workspace, anchored at its git root. Home paths, temp paths
   and absolute paths are banned output targets; plugin directories are
   read-only product content.
7. **Files over conversation memory.** Durable knowledge exits through git
   channels: code, pull request bodies, analysis spaces with their
   compiler-generated views, living architecture documents, design
   system, demo packages, and the generated backlog and ledger views.
   There are no memory tiers or mind maps; a missing-context problem is
   a step-contract bug.
8. **One constitution.** Behavioral law lives in a single constitution file
   per plugin, pasted into every spawn prompt with an order-directory copy
   as fallback. Never per-agent copies, never an on-demand skill.
9. **Single-writer operations backbone.** Process state (projects, epics,
   stories, tasks with attempt history, dependency edges, DoD records,
   work orders, findings, audit events) lives in the project-management-office plugin's central
   database in the user-level data directory, written ONLY through the
   project-management-office CLI. Spawned agents never touch it; project-management-office's hooks record spawn/stop
   mechanics through the same CLI and a guard hook denies direct file
   writes. What must be reviewable in git is rendered from the database
   as generated views, never hand-written. The web dashboard is a READER:
   it opens the database read-only (mode=ro), exposes GET routes only,
   and scans the plugin catalog from the filesystem; it never writes.

## Repository layout

- `.claude-plugin/marketplace.json`: the catalog registry.
- `plugins/<team>/`: one complete team per plugin (agents, skills, flows,
  templates, constitution).
- `plugins/project-management-office/`: the operations backbone (central
  database CLI, hooks, the Control Tower launcher entry and its read-only
  web dashboard); a dependency of every team plugin, never a team itself.
- `docs/`: this map, the authoring guide, the orchestration spec.
- `tools/`: validator, counts injector, scaffolder and their tests.
- `memory/`: maintainer rules; excluded from all tooling.
- Research material stays out of shipped content and leaves the default
  branch at release.
