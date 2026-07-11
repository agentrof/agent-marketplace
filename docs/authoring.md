# Authoring guide

Every rule here is enforced by `tools/validate.py`. If a rule is not
enforced, it is not a rule; to change a rule, change the validator in the
same pull request.

## Component model

A plugin ships four component kinds:

- **Agents** (`agents/<role>.md`): platform-independent role constitutions.
  They carry judgment, never technology. Batch roles run as subagents;
  interactive roles run as personas in the main conversation.
- **Skills** (`skills/<name>/SKILL.md`): all knowledge lives here.
  Entry skills are the user surface; knowledge skills are loaded by agents.
- **Flows** (`flows/<name>.md`): internal state-machine procedures that
  entry skills delegate to. Not user-facing, not skills.
- **Templates** (`templates/`): files the setup entry materializes into a
  consuming project. Scanned by the validator like any shipped content.

## Naming

| thing | rule | example |
|---|---|---|
| plugin directory / name | kebab-case noun | software-team |
| agent file | agents/<role>.md, bare kebab | agents/code-reviewer.md |
| agent frontmatter name | `<plugin>-<file-stem>`, globally unique | software-team-code-reviewer |
| skill directory + frontmatter name | identical bare kebab | skills/python-fastapi/ |
| skill entry file | SKILL.md (uppercase) | skills/request/SKILL.md |
| skill subfolders | references/, scripts/, data/ | references/patterns.md |
| reserved checklists (tech skills) | fixed names | references/review-checklist.md, references/qa-checklist.md |
| flow file | flows/<name>.md | flows/develop.md |
| forbidden everywhere | double underscore in names; the em dash character; emoji in headings; hand-written derived counts; absolute or home paths | |

## Frontmatter

Agents carry exactly `name`, `description`, `model` (alias from the enum:
opus, sonnet, haiku, inherit). Descriptions are PASSIVE: no auto-trigger
phrasing; say the role is invoked by the plugin's flows.

Skills carry `name`, `description` and exactly one visibility flag:

- Entry skill (user surface): `disable-model-invocation: true`
- Knowledge skill (agent-loaded): `user-invocable: false`

## Size caps

| file | cap |
|---|---|
| agent body (below frontmatter) | 80 lines (target 40-75) |
| SKILL.md | 150 lines and 8 KB (warn at 120) |
| constitution.md | 60 lines |
| flows/*.md | 400 lines |
| references/*.md | warn above 500 lines |

## Agent template

Copy this shape exactly; only the content of the sections varies by role.

```markdown
---
name: software-team-backend-developer
description: Backend developer role for orchestrated team runs. Invoked by software-team flows with explicit inputs; not auto-triggered.
model: sonnet
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
description: Backend stack knowledge for the team's server-side work. Loaded by software-team agents during runs; not user-facing.
user-invocable: false
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

## Scaffolding

Use `tools/scaffold.py` to create components; its output passes
`make check` with zero findings by construction, and a test keeps it that
way.
