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
shared operations backbone every team plugin depends on. Claude resolves it
through plugin dependencies; the Codex marketplace installs it by default.

## Invariants

1. **Machine-enforced or not a rule.** Every content rule lives in
   `tools/validate.py` with a fixture that proves it fires. CI runs
   `make check` on every push and pull request; one finding is red. There
   are no exception files, allowlists or temporary waivers.
2. **Roles versus knowledge.** Agents are platform-independent role
   constitutions (Principles, Boundaries, Approach, Output Contract) with
   zero technology nouns. All technology and method knowledge lives in
   skills with progressive disclosure: a thin SKILL.md decision surface
   and depth in references/. Each agent declares a machine-readable
   `output_contract` (`prose` or `structured`); this states how the role
   returns results so a composer can refuse pairing a prose persona with
   schema forcing, and does not by itself stop the harness-side stall
   (anthropics/claude-code#79395).
3. **Encapsulation.** Entry skills are the only user surface. Claude marks
   them `disable-model-invocation: true`; Codex publishes only those entries
   and sets `policy.allow_implicit_invocation: false`. Knowledge skills stay
   internal. Agents are passive and run only when a flow spawns them.
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
   system, demo packages, and the generated decision logs; delivery
   state lives in the PMO database, read through its CLI. There are no
   memory tiers or mind maps; a missing-context problem is
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
10. **One clock.** Every timestamp in a durable artifact comes off the
   system clock in UTC through a script (the project-management-office
   CLI's `now` verb or the owning stamp verb), never out of the model's
   memory. Mechanical layers, not instructions, carry the rule: the CLI
   fills every database column itself, work-order init rejects stale key
   prefixes, the compilers reject future dates, a validator rule bans
   naive clock calls in plugin scripts, and the guard hook denies
   hand-typed stamp dates at write time.
11. **One lexicon, two axes.** output_language localizes only .md body
   prose under workspace/; terminology_language (default English)
   carries names, technical terms, code and comments, commits and PR
   bodies; the machine layer is fixed English. Mechanical layers, not
   instructions, carry the rule: the guard hook denies non-ASCII paths
   and branch names always, and non-ASCII commit and PR payloads
   unless terminology_language is configured non-English; the analysis
   compiler, landscape checker and contract checker enforce identifier
   positions as ASCII shapes.
12. **Two hosts, one semantic contract.** `skill-content/`, agents, flows,
   scripts and templates are canonical. Generated Claude and Codex discovery
   surfaces contain metadata and pointers, never forked workflow bodies.
   Host adapters map gates, native agent spawning, hook payloads and session
   boundaries while preserving the same PMO lifecycle and safety outcome.

## Repository layout

- `.claude-plugin/marketplace.json`: the Claude catalog registry.
- `.agents/plugins/marketplace.json`: the Codex catalog and install policy.
- `plugins/<team>/`: canonical content plus Claude/Codex source manifests and
  generated discovery wrappers. Full skills live in `skill-content/`.
- `codex-plugins/<team>/`: generated self-contained Codex distributions;
  never edit them by hand.
- `plugins/project-management-office/`: the operations backbone (central
  database CLI, hooks, the Control Tower launcher entry and its read-only
  web dashboard); a dependency of every team plugin, never a team itself.
- `docs/`: this map, the authoring guide, the orchestration spec.
- `tools/`: validator, host-surface generators, counts injector, scaffolder
  and their tests;
  `tools/data/models.json` (model aliases) and `tools/data/limits.json`
  (authoring size caps) are the policy files, validated like any other
  policy artifact.
- `memory/`: maintainer rules; excluded from all tooling.
- Research material stays out of shipped content and leaves the default
  branch at release.
