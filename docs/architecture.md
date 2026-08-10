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
a config enum value plus tests. One plugin is not a team:
project-management-office is the shared operations backbone every team plugin
depends on. Claude resolves it through plugin dependencies. Codex users install
it explicitly before a team; marketplace default policy is advisory and never
the dependency contract.

## Invariants

1. **Machine-enforced or not a rule.** Every content rule lives in
   `tools/validate.py` with a fixture that proves it fires. CI runs
   `make check` on every push and pull request; one finding is red. There
   are no exception files, allowlists or temporary waivers. High-risk
   cross-host sub-contracts carry named adversarial cases, and public PMO
   command and dashboard route registries stay in lockstep with executable
   tests so a new surface cannot merge without a declared contract.
2. **Roles versus knowledge.** Agents are platform-independent role
   constitutions (Principles, Boundaries, Approach, Output Contract) with
   zero technology nouns. All technology and method knowledge lives in
   skills with progressive disclosure: a thin SKILL.md decision surface
   and depth in references/. Each agent declares a machine-readable
   `output_contract` (`prose` or `structured`); this states how the role
   returns results so a composer can refuse pairing a prose persona with
   schema forcing, and does not by itself stop the harness-side stall
   (anthropics/claude-code#79395).
3. **Encapsulation.** Entry skills are the only user surface. Canonical
   skills declare `exposure: entry` or `exposure: internal`; each host build
   maps that neutral declaration to its native discovery policy. Knowledge
   skills stay internal. Agents are passive and run only when a flow spawns them.
4. **Zero duplication.** Each fact exists once: shared method in a process
   skill, stack specifics inside the stack skill, shared flow bodies in
   flows/ files that entries delegate to.
5. **Derived counts are computed.** `tools/counts.py` injects counts into
   the single README marker block; `--check` is the CI drift gate. Numbers
   next to component nouns anywhere else fail validation.
6. **Output scoping.** Durable team outputs land under the consuming project's
   workspace. Transient plan and work-order snapshots live only under the
   owning checkout's `.agentrof/agent-marketplace/.runtime/`, anchored at its
   git root and ignored. Home paths, temp paths and absolute paths are banned
   output targets; plugin directories are read-only product content.
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
   project-management-office CLI. Agentrof owns the vendor home and Agent Marketplace owns
   its nested product home. Spawned agents never touch it; project-management-office's hooks record spawn/stop
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
12. **Two hosts, one semantic contract.** `plugins/` contains only
   host-neutral canonical skills, agents, flows, scripts and templates.
   Platform manifests, contracts and overlays live under `platforms/`.
   Generated Claude and Codex distributions contain metadata and pointers,
   never forked workflow bodies.
   Host adapters map gates, native agent spawning, hook payloads and session
   boundaries while preserving the same PMO lifecycle and safety outcome.
13. **Every team requires a ready PMO.** Claude manifests declare PMO as a
   native dependency. Codex installation documents PMO before the team because
   Codex manifests do not declare plugin dependencies. Both host contracts
   require the PMO-ready session signal before mutation. PMO records readiness
   against the host session id; the shared team PreToolUse guard denies Write,
   Edit, apply_patch and Bash when that session record is absent or unhealthy.
   This stops local mutation when PMO is missing, disabled, untrusted or failed
   to bootstrap. The scaffolder emits this mechanical contract and the validator
   rejects any team that drops it.
14. **One delivery team owns one project.** PMO remains the shared backbone,
   but workspace/config.json and setup-generated project agents may name only
   one team. A second team stops before mutation. This preserves bare native
   agent ids across hosts without collisions in Codex's project-wide
   .codex/agents namespace.
15. **One upgrade protocol across hosts.** Plugin updates may change runtime
   compatibility, but normal work never guesses whether the project is ready.
   PMO derives status from package provenance, cross-host versions, database
   schema, project UUID, component versions, and managed-surface hashes. A
   required upgrade locks normal marketplace mutation on both hosts. Ordered
   checksummed migrations operate on a candidate database and marker-owned
   project surfaces only; plans are fingerprint-bound, journaled, recoverable,
   and require a fresh session after success. Remote-backed projects remain
   locked on their upgrade branch until the configured target branch contains
   the exact managed upgrade identity. Dead locks are reclaimed only from
   a same-host process proven absent; orphan sessions require explicit
   owner-confirmed release.
   User-owned code and content are outside the writer set.
16. **Preparation is distinct from activation.** Greenfield work closes
   analysis, solution, design system, release experience and baseline backlog
   as separate owner-gated stages. Deterministic preparation routing names the
   next entry. An approved backlog is applied atomically but does not activate
   delivery. Existing-project features reuse the same stages in scoped mode and
   execute only the approved feature set and owner-approved prerequisites.
17. **Marketplace channels are closed snapshots.** A marketplace ref resolves
   its catalog and both host packages from the same checkout through relative
   sources. `main` is an explicit development preview, `stable` is the moving
   released channel, and a `vX.Y.Z` tag is immutable. Public install and smoke
   paths pin `stable`; a catalog may never redirect packages to another channel.

## Repository layout

- `.claude-plugin/marketplace.json`: the Claude catalog registry.
- `.agents/plugins/marketplace.json`: the Codex catalog and install policy.
- `versions.json`: the single cross-host stable version registry.
- `product.json`: the vendor and product namespace, home and host-cache registry.
- `.changes/`: pending release-impact declarations, one per normal pull request.
- `plugins/<team>/`: host-neutral canonical content. Full skills live in
  `skill-content/`; ordered compatibility contracts live in `migrations/`;
  agent frontmatter uses neutral exposure and reasoning enums.
- `platforms/<host>/<team>/`: host manifest, contract and overlay source;
  `platforms/shared/` contains runtime adapters used by both hosts. `_team`
  overlays are generated into every non-PMO plugin, so new teams inherit the
  same PMO guard and Codex project-agent generator without copied source.
- `dist/<host>/<team>/`: generated self-contained distributions; never edit
  them by hand.
- `.release/stable.json`: generated release-PR provenance used to reject stale
  or mismatched publication attempts.
- `plugins/project-management-office/`: the operations backbone (central
  database CLI, hooks, the Control Tower launcher entry and its read-only
  web dashboard); a dependency of every team plugin, never a team itself.
- `docs/`: this map, the authoring guide, orchestration spec and greenfield
  preparation contract.
- `tools/`: validator, host-surface generators, counts injector, scaffolder
  and their tests;
  `tools/data/models.json` (reasoning levels) and `tools/data/limits.json`
  (authoring size caps) are the policy files, validated like any other
  policy artifact.
- `memory/`: maintainer rules; excluded from all tooling.
- Research material stays out of shipped content and leaves the default
  branch at release.
