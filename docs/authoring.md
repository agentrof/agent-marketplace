# Authoring guide

Every rule here is enforced by `tools/validate.py`. If a rule is not
enforced, it is not a rule; to change a rule, change the validator in the
same pull request.

## Component model

A plugin ships four component kinds:

- **Agents** (`agents/<role>.md`): platform-independent role constitutions.
  They carry judgment, never technology. Batch roles run as subagents;
  interactive roles run as personas in the main conversation.
- **Skills** (`skill-content/<name>/SKILL.md`): all canonical knowledge lives
  here. Entry skills are the user surface; knowledge skills are loaded by
  agents. Host wrappers exist only in generated distributions.
- **Flows** (`flows/<name>.md`): internal state-machine procedures that
  entry skills delegate to. Not user-facing, not skills.
- **Templates** (`templates/`): files the setup entry materializes into a
  consuming project. Scanned by the validator like any shipped content.

## Naming

| thing | rule | example |
|---|---|---|
| plugin directory / name | kebab-case noun | software-engineering-team |
| agent file | agents/<role>.md, bare kebab | agents/code-reviewer.md |
| agent frontmatter name | identical bare file stem, unique within the plugin | code-reviewer |
| skill directory + frontmatter name | identical bare kebab, unique within the plugin | skill-content/python-fastapi/ |
| skill entry file | SKILL.md (uppercase) | skill-content/deliver/SKILL.md |
| skill subfolders | references/, scripts/, data/ | references/patterns.md |
| reserved checklists (tech skills) | fixed names | references/review-checklist.md, references/qa-checklist.md |
| flow file | flows/<name>.md | flows/develop.md |
| forbidden everywhere | double underscore in names; the em dash character; emoji in headings; hand-written derived counts; absolute or home paths | |

The component owns the short semantic name; the host owns namespacing. The
same role may therefore exist in more than one plugin without repository-wide
prefixes:

| identity layer | example |
|---|---|
| visible role title | Backend Developer |
| canonical agent id | backend-developer |
| Claude agent identity | software-engineering-team:backend-developer |
| Codex project agent | backend-developer |
| PMO role | backend_developer |

Public skill identity is namespaced on both hosts. Claude invokes
`/software-engineering-team:deliver`; Codex invokes
`$software-engineering-team:deliver`. Human-facing labels are title case with
intentional acronym casing, such as QA Engineer, UX Designer, DevOps Engineer
and Python FastAPI.

## Frontmatter

Agents carry `name`, `description`, `reasoning` (a host-neutral level from
the `reasoning_levels` enum in `tools/data/models.json`) and `output_contract`
(`prose` or `structured`: how the role hands results back; every current
persona is `prose`). A read-only role may also carry a `tools` whitelist
(`Read, Grep, Glob`) to deny write capability at spawn time. Descriptions
are PASSIVE: no auto-trigger phrasing; say the role is invoked by the
plugin's flows. `output_contract` declares the return channel so a composer
can refuse pairing a prose persona with a StructuredOutput-forcing harness;
it does not by itself prevent the harness-side stall (anthropics/claude-code#79395).

Skills carry `name`, `description` and one host-neutral exposure:

- Entry skill (user surface): `exposure: entry`
- Knowledge skill (agent-loaded): `exposure: internal`

The distribution builder maps this declaration to Claude-native visibility
metadata and creates Codex wrappers only for entry skills. Every Codex wrapper
ships `agents/openai.yaml` with `policy.allow_implicit_invocation: false`.
Do not edit either generated distribution.

## Time

Scripts read the clock timezone-aware in UTC only:
`datetime.now(timezone.utc)`. The validator's `naive_clock` rule fails
`date.today()`, no-arg `datetime.now()` and `utcnow()` in any plugin
script. A new stamp-like field (a date the process writes into an
artifact) ships with two things: an owning stamp verb that writes the
date itself, and its pattern registered in the guard hook's
STAMP_FIELD_PATTERNS table (project-management-office's hook_guard_db.py)
so a hand-typed date is denied at write time.

## Language

Shipped content is English everywhere; the content bans below enforce
the mechanical slice. The managed-project contract (two axes:
output_language for .md body prose, terminology_language for names,
technical terms, code and comments, commits and PR bodies, machine
layer fixed English) lives in docs/orchestration.md. In-body
identifier positions are enforced as ASCII machine-safe shapes by the
analysis compiler, the landscape checker and the contract checker; a
new identifier position (a table column or diagram kind that carries
technical names) registers itself in space-schema.json's naming block
or its owning checker, and ships with a firing fixture in the matching
test registry. The guard hook's commit and PR payload checks are the
config-aware points: they read the resolved project's
terminology_language. A language name appears in shipped content only
as the spelled-out default of an axis, as the fixed machine layer, or
as product-i18n subject matter; anything else is a hard-code defect.

## Size caps

The enforced values live in `tools/data/limits.json` (`authoring_caps`);
this table mirrors that file.

| file | cap |
|---|---|
| agent body (below frontmatter) | 80 lines (target 40-75) |
| SKILL.md | 150 lines and 8 KB (warn at 120) |
| constitution.md | 60 lines |
| flows/*.md | 424 lines |
| references/*.md | warn above 500 lines |

## Agent template

Copy this shape exactly; only the content of the sections varies by role.

```markdown
---
name: backend-developer
description: Backend developer role for orchestrated team runs. Invoked by software-engineering-team flows with explicit inputs; not auto-triggered.
reasoning: medium
output_contract: prose
---

# Backend Developer

Implements server-side features exactly as the approved architecture and
contract specify, in the smallest correct diff.

## Principles
- The approved contract and ownership map are law; deviations are
  escalated, never made silently.
- Minimum code that satisfies the acceptance criteria; nothing speculative.

## Boundaries
- Does: server-side implementation within the ownership map.
- Does not: change contracts or schema; defers to the owning role.

## Approach
1. Follow the constitution included in the spawn prompt; if absent, read
   the run folder copy.
2. Read the input files named in the spawn prompt, summaries first.
3. Work in small verifiable increments; run the project's checks as you go.
4. Stop and report blocked with a specific question when inputs conflict.

## Output Contract
- Exactly the artifacts named in the spawn prompt, at the given paths.
- End the reply with SELF-CHECK: each required element marked present or
  missing.
```

Required sections, validator-checked: one H1, then `## Principles`,
`## Boundaries`, `## Approach`, `## Output Contract`. Zero technology
nouns, version pins, model names or counts in the body.

## Skill template

```markdown
---
name: python-fastapi
description: Backend stack knowledge for the team's server-side work. Loaded by software-engineering-team agents during runs; not user-facing.
exposure: internal
---

# Python FastAPI

Prescriptive standards for the team's server-side stack.

## When to Use
- Loaded by the bound agent when implementing, reviewing or verifying
  server-side work.

## Core Rules
- DO state prescriptive decisions; DON'T write textbook exposition.
- Keep depth in references/ and link every reference file from here.

## References
- [patterns.md](references/patterns.md): worked patterns. Read when implementing an endpoint.
```

Required: a `## When to Use` section. Every file under references/ must be
linked from its SKILL.md, and every relative link must resolve. In
knowledge skills every reference link line ends with a read-when trigger
("... Read when <situation>."), validator-enforced: progressive disclosure
is a checkable property, not an intention. Tech skills additionally ship
both reserved checklists.

## Content admission test

Every candidate content block (a bullet, rule or table row) passes ALL of
these before it ships; a block that fails any gate is cut, not softened:

1. Artifact hook: it names the artifact and field it can change (decision
   log, backlog priority, contract error cases, brief BR-###), or it sits
   inside a [conditional] block.
2. Falsifiable decision shape: rewritable as IF-situation-THEN-choice or
   DO/DON'T such that a reviewer could catch a violation. "Know the
   principles" fails; the snapshot rule passes.
3. Flow-exercised or conditional: the flow step where the situation
   arises is nameable; otherwise the block enters only as [conditional]
   with a read-when trigger naming the future condition.
4. Single home: if the fact exists elsewhere, link, never restate.
   Altitude rule: WHAT a contract declares lives at architecture
   altitude; HOW it is realized lives only in the stack skill.
5. Ban-safe as written (see content bans below).
6. Cost-justified placement: SKILL.md surface only when it changes most
   spawns of the role; situational depth goes to references.
7. Cap-safe without evicting a higher-value block.

Form rule: a principle is written as decision test + self-check question
+ failure symptom, in DO/DON'T voice. A framework earns its NAME only
when the name indexes a runbook the agent executes (expand-contract,
ADR); otherwise paraphrase the procedure and drop the brand.

## Content bans (validator-enforced)

- The em dash character, anywhere in shipped content. Use a hyphen, comma
  or rewrite.
- Emoji in headings.
- Version pins and concrete model names inside plugins/ content (model
  aliases live only in agent frontmatter).
- Absolute paths and home-directory paths. All output paths are
  project-relative, anchored at the consuming project's workspace.
- References to the research material directory in shipped content.
- Hand-written derived counts. Counts exist only inside the README marker
  block and are injected by `tools/counts.py`.
- Wikilink syntax (`[[target|alias]]`) outside fenced blocks and inline
  code spans. Marketplace content links with standard relative markdown
  links; the wikilink grammar belongs to the product vault and ships only
  under a plugin's `templates/` (consumer-bound seeds). Backticked or
  fenced wikilinks render as code, not links, so illustrations stay legal.

## Product vault surface

A plugin that ships vault authoring rules declares every variation point
in one policy file, `skill-content/<skill>/data/vault-policy.json` (subtrees,
map notes, machine dirs, banned basenames, the vetted community-plugin
set, the graph search filter and ordered color-group queries, the hubs
ladder, tag namespaces, nav peer range, decision-tree grammars,
generated views, property types). The `vault_policy_shape` rule
validates the shape (including the graph-query grammar: no pipe-OR, no
tag wildcards) and holds the policy, the `templates/vault/` seeds and
the committed graph config in parity: a policy subtree or extra map
without a map seed, a stray seed the policy does not name, or a
`graph.json` whose colorGroups or search drift from the policy is an
error. The home seed is minimal and DYNAMIC: it links
nothing at seed and must NOT link any subtree map; subtree
map lines are added in the consuming project by the entry that births
each tree. The enable list `community-plugins.json` must equal the
policy's plugin set, and a vendored plugin directory under
`templates/vault/.obsidian/plugins/<id>/` must carry `manifest.json`,
`main.js` and `data.json` together with the manifest id matching the
directory. Files under `templates/**/.obsidian/plugins/` are vendored
third-party bundles: they are exempt from the `template_placeholders`
rule (their own template tokens are not substitutions this plugin's
skills owe). JSON files under `templates/**/.obsidian/` carry the vault
app's own key schema and are exempt from the snake_case key rule only
there; they must still parse. In a plugin shipping the vault-law skill,
every entry or flow that names the docs tree must also name
`obsidian-vault` (the `vault_wiring` rule): the law cannot be skipped
by omission.

## Dispatcher and popups

- Bodies reach plugin files only through the dispatcher grammar
  (`"$RUN" run "$TEAM" scripts/<x>.py`, `"$RUN" path "$TEAM"
  <relpath>`); the `script_references` rule verifies every named
  target ships.
- Every decision gate names the host-neutral choice gate at the gate site
  (`choice_gate`).
- The Codex host contract maps that gate to one concise, option-preserving
  turn-ending question. Mutating flows refuse Plan mode. Claude uses named
  plugin agents; Codex uses the setup-generated project agents, launches
  independent read-only work together, and waits for all results.
- Hook write normalization covers Claude Write/Edit and Codex `apply_patch`
  add, update, delete, move and multi-file payloads. Unparseable patches fail
  closed in safety hooks.

## Releases

A release bumps both platform manifests, both generated manifests and the
Claude marketplace entry in one commit. The validator errors on identity or
version drift; the Codex marketplace carries policy, not a second version
field. `make check` is hermetic: it opens no network sockets, needs no host CLI
and includes deterministic simulations of the complete Claude and Codex
lifecycle. CI runs it on the primary environment plus the supported Python and
operating-system compatibility matrix.

Before release, run `make release-check` on a machine carrying both host CLIs.
The real-host gate gives every team isolated state and verifies dependency or
explicit recovery, PMO session readiness, disable/enable, remove/reinstall,
Codex entry-skill discovery, internal-skill hiding and setup idempotency. The
`release-hosts` workflow repeats this gate on version tags, a weekly schedule
and explicit maintainer dispatch against current host CLIs.

Validator fixture lockstep is the minimum, not the whole test contract.
Cross-host packaging and PMO dependency branches use named adversarial cases.
Every public PMO parser leaf and dashboard API route is registered against an
executable semantic test. Hook patch normalization uses a shared golden corpus
on both safety implementations. Adding a command, route, host policy branch or
patch form requires its contract case in the same change.

## Scaffolding

Use `tools/scaffold.py` to create components; its output passes
`make check` with zero findings by construction, and a test keeps it that
way. Canonical edits are followed by
`python3 tools/build_distributions.py`; `make check` verifies both generated
trees are current.

## Depending on the operations backbone

Every team plugin records its process state (runs, stories, tasks,
findings) through the project-management-office plugin, never in its own files:

- Keep the Claude dependency in its manifest. Codex manifests have no plugin
  dependency field, so every Codex install surface lists PMO before the team.
  Keep PMO `INSTALLED_BY_DEFAULT` in the marketplace as an advisory policy,
  never as the install guarantee.
- Require the exact `AGENTROF_PMO_READY: project-management-office` session
  signal before either host mutates team state. On absence, run the host's
  read-only plugin inventory, stop without writes, and distinguish missing,
  disabled and hook/bootstrap failures in the recovery message.
- Keep the generated `team_guard.py` hook on Write, Edit and Bash for Claude,
  and on Write, Edit, apply_patch and Bash for Codex. It checks the PMO-owned
  session readiness record and the one-team-per-project ownership rule before
  the tool runs. Host instructions explain recovery; the hook enforces denial.
- Create every future team with `tools/scaffold.py`; `team_pmo_contract`
  rejects a Claude manifest without the dependency, a Codex visible surface
  without the requirement, or either host contract without the ready gate and
  recovery command.
- Invoke the CLI through the synced launcher in the user-level data
  directory (see the develop flow's state contract for the resolution
  line); never reference another plugin's install path, it is not a
  stable location.
- The distribution builder generates PMO's team namespace registry from all
  non-PMO plugin directories. Bare Codex spawns count only when the matching
  project TOML has that team's Agentrof ownership marker, so a user's
  same-named local agent is never attributed to the team.
- The single-writer rule is absolute: flows call the CLI; spawned agents
  never do; anything the owner must review in git is rendered from the
  database as a generated view, not hand-written.
