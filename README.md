# Agent Marketplace

Agent Marketplace ships one standalone, host-neutral Software Engineering Team
for Claude Code and Codex. The team takes a user Requirement through the
applicable analysis, design and backlog gates, then hands the approved backlog
to the Delivery Flow.

## Catalog

<!-- counts:start -->
| Plugins | Agents | Entry skills | Knowledge skills |
|---|---|---|---|
| 1 | 17 | 18 | 15 |
<!-- counts:end -->

Counts are maintained by `make counts`.

## Install

```text
/plugin marketplace add https://github.com/agentrof/agent-marketplace.git#stable
/plugin install software-engineering-team
```

```text
codex plugin marketplace add agentrof/agent-marketplace@stable
codex plugin add software-engineering-team@agent-marketplace
```

The hooks run `python3` from PATH and need Python 3.14 (macOS: `brew install python3`).

Start `software-engineering-team:setup` in the project. The setup entry uses
`scripts/setup_project.py inspect|apply|check` to converge the workspace,
tracked vault contract, ignored local Obsidian plugin projection, closed
configuration, managed ignore and checkout-attribute blocks and portable gate.
Claude Code and Codex use the same canonical workflows and project-local files.

## Companions

Setup can add optional third-party skills through their own official install
paths; none is copied into the package and none is required. Run
`scripts/setup_project.py companions check --project-root <root>` for a status
table and `companions apply` to install, write the deny rules, environment and
marker files, and print any command the owner must run by hand. Blocked
skills become `Skill(<plugin>:<skill>)` deny rules in `.claude/settings.json`
and `[[skills.config]]` entries with `enabled = false` in the user Codex
config; the environment is written to the Claude project settings.

<!-- companions:start -->

The team flow owns plans, task files, branches, worktrees, commits, pull requests, merge and review verdicts; a companion skill that runs its own version of those is not used. Companions are used when installed and relevant, never required.

### superpowers

Debugging, self-verification, review intake and skill-writing disciplines. Four skills add method the team lacks; the rest duplicate the deliver flow and stay blocked. [https://github.com/obra/superpowers](https://github.com/obra/superpowers), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | project | `claude plugin marketplace add obra/superpowers-marketplace`<br>`claude plugin install superpowers@superpowers-marketplace --scope project` | `claude plugin list --json` lists `superpowers@superpowers-marketplace` |
| codex | user | `codex plugin add superpowers@openai-curated` | `codex plugin list --json` lists `superpowers@openai-curated` |

Blocked: `brainstorming`, `writing-plans`, `executing-plans`, `subagent-driven-development`, `using-git-worktrees`, `finishing-a-development-branch`, `test-driven-development`, `dispatching-parallel-agents`, `requesting-code-review`, `diagnosing-superpowers`. They run their own plans, worktrees, branches, reviews and merges, which the team flow owns; diagnosing-superpowers drafts upstream issues that could carry project data.

Environment: `SUPERPOWERS_DISABLE_TELEMETRY=1`.

### humanizer

Removes AI-writing tells from prose. Pull request bodies, documents and issues read as written by the team, without changing what they say. [https://github.com/blader/humanizer](https://github.com/blader/humanizer), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | project | `claude plugin marketplace add blader/humanizer`<br>`claude plugin install humanizer@humanizer --scope project` | `claude plugin list --json` lists `humanizer@humanizer` |
| codex | project | `npx skills add blader/humanizer --agent codex -y` | `.agents/skills/humanizer/SKILL.md` exists |

### security-audit

Security review lens with a tenant data isolation reference. High-rigor reviews get a security method; guidance mode writes nothing. [https://github.com/cloudflare/security-audit-skill](https://github.com/cloudflare/security-audit-skill), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | project | `npx skills add https://github.com/cloudflare/security-audit-skill --skill security-audit --agent claude-code -y` | `.claude/skills/security-audit/SKILL.md` exists |
| codex | project | `npx skills add https://github.com/cloudflare/security-audit-skill --skill security-audit --agent codex -y` | `.agents/skills/security-audit/SKILL.md` exists |

### diagram-design

Architecture, delta, sequence and ER diagrams. Structural architecture changes get a drawn delta; no telemetry. [https://github.com/cathrynlavery/diagram-design](https://github.com/cathrynlavery/diagram-design), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | project | `claude plugin marketplace add cathrynlavery/diagram-design`<br>`claude plugin install diagram-design@diagram-design --scope project` | `claude plugin list --json` lists `diagram-design@diagram-design` |
| codex | user | `codex plugin marketplace add cathrynlavery/diagram-design`<br>`codex plugin add diagram-design@diagram-design` | `codex plugin list --json` lists `diagram-design@diagram-design` |

Setup writes `.diagram-design`.

### impeccable

Frontend design craft and read-only UI critique. Closes the frontend design quality gap; installed without hooks and with telemetry off. [https://github.com/pbakaus/impeccable](https://github.com/pbakaus/impeccable), Apache-2.0.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | project | `npx impeccable install --providers=claude --project --no-hooks -y` | `.claude/skills/impeccable/SKILL.md` exists |
| codex | project | `npx impeccable install --providers=codex --project --no-hooks -y` | `.agents/skills/impeccable/SKILL.md` exists |

Environment: `IMPECCABLE_CONTEXT_DIR=workspace/experience-design`, `IMPECCABLE_NO_TELEMETRY=1`.

Setup writes `.impeccable/config.json`.

Check fails on `DESIGN.md`, `PRODUCT.md`, `docs/DESIGN.md`, `docs/PRODUCT.md`. DESIGN.md and PRODUCT.md live only in workspace/experience-design.

<!-- companions:end -->

## Requirement and Delivery path

```text
/setup -> /requirement -> required stages -> /backlog-plan
        -> /delivery-plan -> /execution-plan -> /deliver
```

The Requirement record owns the impact matrix. Only required stages run, reuse
must cite an approved current package, and `not_applicable` requires a concrete
rationale. The approved backlog is the handoff to Delivery; no timebox,
velocity or release state is created.

The current lifecycle and Git coordination contract is documented in
[docs/requirement-delivery-protocol.md](docs/requirement-delivery-protocol.md).

## Backlog layout

```text
workspace/docs/backlog/
  backlog.md
  reviews/round-1-backlog-review.md
  epics/<epic-slug>/
    epic.md
    reviews/round-1-epic-review.md
    stories/<story-slug>/
      story.md
      test-plan.md
  _generated/{registry.json,board.md,dependency-map.md,test-coverage.md}
```

Each story names one accountable implementation role and any supporting roles
with concrete contributions. The epic review derives from its epic and
verifies its exact child story/test-plan set. The root review derives from the
backlog and relates to the exact epic set while covering cross-epic overlap,
dependency direction, ordering and global coverage. Test plans map acceptance
criteria and business rules to stable Given/When/Then scenarios and required
automation targets. Test execution remains a later delivery gate.
`/issue-report` is a stateless support path for Agent Marketplace defects and
improvements. It previews the exact GitHub payload in chat, files only after
explicit approval and never stores issue state in the consuming project.

## Local state and upgrades

The only project runtime is
`<git-root>/.agentrof/agent-marketplace/.runtime/`; it is ignored,
disposable scratch/cache state. Durable truth is tracked Markdown and JSON
under the project workspace. The Software Engineering Team installs as one
standalone plugin.

Marketplace package upgrades inspect one deterministic refresh plan, apply it
with closing-gate rollback, then prove that no operation remains. Authored
files, unknown project configuration and user-owned Obsidian knobs are
preserved. Policy-owned keys and package-local plugin files converge before the
tracked diff is committed. Release Management is deliberately a later scope.

## Quality

```text
make check
```

runs validation, deterministic distribution generation, counts, release
surface checks and focused tests.

## Security

Report vulnerabilities only through the repository's
[private vulnerability reporting form](https://github.com/agentrof/agent-marketplace/security/advisories/new).
Do not include security details in public issues, pull requests or commit
messages. See [SECURITY.md](SECURITY.md) for scope and disclosure guidance.

## License

Agentrof Agent Marketplace is licensed under the
[Apache License 2.0](LICENSE). Redistributions keep the attribution in
[NOTICE](NOTICE). The Agentrof names and logo are covered by
[TRADEMARKS.md](TRADEMARKS.md); the license grants no trademark rights.

Releases published before the change to Apache 2.0 remain available under
the MIT License they were released with.

### Built with Agentrof Agent Marketplace

If your project uses Agentrof Agent Marketplace, we would appreciate it if
you showed this badge in your README. It is optional.

[![Built with Agentrof Agent Marketplace](https://img.shields.io/badge/Built%20with-Agentrof%20Agent%20Marketplace-informational)](https://github.com/agentrof/agent-marketplace)

```markdown
[![Built with Agentrof Agent Marketplace](https://img.shields.io/badge/Built%20with-Agentrof%20Agent%20Marketplace-informational)](https://github.com/agentrof/agent-marketplace)
```
