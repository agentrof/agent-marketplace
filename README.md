# Agent Marketplace

Agent Marketplace ships one standalone, host-neutral Software Engineering Team
for Claude Code and Codex. The team turns each request into one task, one
branch and one pull request, verified by the project's CI and one reviewer.

## Catalog

<!-- counts:start -->
| Plugins | Agents | Entry skills | Knowledge skills |
|---|---|---|---|
| 1 | 11 | 6 | 10 |
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
closed configuration and the managed ignore and checkout-attribute blocks.
Claude Code and Codex use the same canonical workflows and project-local files.

## Companions

Codex checks the selected catalog, enabled installation and required skills through the host loader. Remote skills must also return readable content; an unavailable host check fails verification instead of treating files on disk as proof.

Setup can add optional third-party skills through their own official install
paths; none is copied into the package and none is required. Run
`scripts/setup_project.py companions check --project-root <root>` for a status
table and `companions apply` to install, write the deny rules, environment and
marker files, and print any command the owner must run by hand. Blocked
skills become `Skill(<plugin>:<skill>)` deny rules in `.claude/settings.json`
and `[[skills.config]]` entries with `enabled = false` in the user Codex
config; the environment is written to the Claude project settings.

### Setup

1. Run `scripts/setup_project.py companions apply --project-root <root>`
   (add `--host claude` or `--host codex` for one host). It installs every
   missing companion: the Claude plugins and project skills, the Codex
   plugins and skills when the `codex` CLI is present, the graphify CLI
   through `uv`, its skill for each host and its git hooks.
2. Run what it prints. `RUN` lines are install commands that did not
   succeed; `APP` lines are steps for the owner, once: without the `codex`
   CLI, add the listed plugins in the Codex app via `/plugins`, and install
   `uv` if it is missing. `NOTE` lines are one-time owner steps the check
   cannot see, such as trusting the ponytail hooks in Codex `/hooks`.
3. Run `scripts/setup_project.py companions check --project-root <root>`.
   It exits 0 when every row reads `yes` or `-` and nothing is printed below
   the table.

The docs graph is built once by the owner
(`GRAPHIFY_OUT=.agentrof/graphify/docs graphify extract workspace/docs --backend claude-cli`);
after that, deliver refreshes it in the background for each merged task
that changed Markdown under `workspace/docs`, logging to
`.agentrof/graphify/refresh.log`.

<!-- companions:start -->

The team flow owns plans, task files, branches, worktrees, commits, pull requests, merge and review verdicts; a companion skill that runs its own version of those is not used. Companions are used when installed and relevant, never required.

### superpowers

Debugging, self-verification, review intake and skill-writing disciplines. Four skills add method the team lacks; the rest duplicate the deliver flow and stay blocked. [https://github.com/obra/superpowers](https://github.com/obra/superpowers), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | user | `claude plugin marketplace add obra/superpowers-marketplace`<br>`claude plugin install superpowers@superpowers-marketplace --scope user` | `claude plugin list --json` lists `superpowers@superpowers-marketplace` |
| codex | user | `codex plugin add superpowers@openai-curated-remote` | `codex plugin list --json` lists `superpowers@openai-curated-remote` |

Blocked: `brainstorming`, `writing-plans`, `executing-plans`, `subagent-driven-development`, `using-git-worktrees`, `finishing-a-development-branch`, `test-driven-development`, `dispatching-parallel-agents`, `requesting-code-review`, `diagnosing-superpowers`. They run their own plans, worktrees, branches, reviews and merges, which the team flow owns; diagnosing-superpowers drafts upstream issues that could carry project data.

Environment: `SUPERPOWERS_DISABLE_TELEMETRY=1`.

### humanizer

Removes AI-writing tells from prose. Pull request bodies, documents and issues read as written by the team, without changing what they say. [https://github.com/blader/humanizer](https://github.com/blader/humanizer), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | user | `claude plugin marketplace add blader/humanizer`<br>`claude plugin install humanizer@humanizer --scope user` | `claude plugin list --json` lists `humanizer@humanizer` |
| codex | user | `npx skills add blader/humanizer --agent codex -g -y` | `.agents/skills/humanizer/SKILL.md` exists in the home directory |

### security-audit

Security review lens with a tenant data isolation reference. High-rigor reviews get a security method; guidance mode writes nothing. [https://github.com/cloudflare/security-audit-skill](https://github.com/cloudflare/security-audit-skill), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | user | `npx skills add https://github.com/cloudflare/security-audit-skill --skill security-audit --agent claude-code -g -y` | `.claude/skills/security-audit/SKILL.md` exists in the home directory |
| codex | user | `npx skills add https://github.com/cloudflare/security-audit-skill --skill security-audit --agent codex -g -y` | `.agents/skills/security-audit/SKILL.md` exists in the home directory |

### diagram-design

Architecture, delta, sequence and ER diagrams. Structural architecture changes get a drawn delta; no telemetry. [https://github.com/cathrynlavery/diagram-design](https://github.com/cathrynlavery/diagram-design), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | user | `claude plugin marketplace add cathrynlavery/diagram-design`<br>`claude plugin install diagram-design@diagram-design --scope user` | `claude plugin list --json` lists `diagram-design@diagram-design` |
| codex | user | `codex plugin marketplace add cathrynlavery/diagram-design`<br>`codex plugin add diagram-design@diagram-design` | `codex plugin list --json` lists `diagram-design@diagram-design` |

Setup writes `.diagram-design`.

### impeccable

Frontend design craft and read-only UI critique. Closes the frontend design quality gap; installed without hooks and with telemetry off. [https://github.com/pbakaus/impeccable](https://github.com/pbakaus/impeccable), Apache-2.0.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | user | `npx impeccable install --providers=claude --user --no-hooks -y` | `.claude/skills/impeccable/SKILL.md` exists in the home directory |
| codex | user | `npx impeccable install --providers=codex --user --no-hooks -y` | `.agents/skills/impeccable/SKILL.md` exists in the home directory |

Environment: `IMPECCABLE_CONTEXT_DIR=workspace/experience-design`, `IMPECCABLE_NO_TELEMETRY=1`.

Setup writes `.impeccable/config.json`.

Check fails on `DESIGN.md`, `PRODUCT.md`, `docs/DESIGN.md`, `docs/PRODUCT.md`. DESIGN.md and PRODUCT.md live only in workspace/experience-design.

### graphify

Knowledge graph of the project's code and documents. Roles find context with a graph query instead of reading many files; the code graph refreshes through git hooks on commit, checkout and merge, the docs graph in the background after deliver merges a task. [https://github.com/Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify), Apache-2.0.

| Host | Scope | Install | Verify |
|---|---|---|---|
| cli | user | `uv tool install graphifyy` | `graphify --version` succeeds |
| git-hook | project | `graphify hook install` | `graphify hook status` succeeds and prints `post-commit: installed` |
| claude | user | `graphify install --platform claude` | `.claude/skills/graphify/SKILL.md` exists in the home directory |
| codex | user | `graphify install --platform codex` | `.codex/skills/graphify/SKILL.md` exists in the home directory |

uv not found: install it (https://docs.astral.sh/uv/getting-started/installation/), then rerun companions apply.

Environment: `GRAPHIFY_OUT=.agentrof/graphify`.

Setup writes `.agentrof/graphify/.gitignore`, `workspace/docs/.graphifyignore`.

### ponytail

Smallest complete change, plain-language review and a shortcut debt ledger. Developer roles write less code without dropping callers, tests or safety; review and retrospective get its review and debt reports. [https://github.com/DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | user | `claude plugin marketplace add DietrichGebert/ponytail`<br>`claude plugin install ponytail@ponytail --scope user` | `claude plugin list --json` lists `ponytail@ponytail` |
| codex | user | `codex plugin marketplace add DietrichGebert/ponytail`<br>`codex plugin add ponytail@ponytail` | `codex plugin list --json` lists `ponytail@ponytail` |

Codex runs the ponytail hooks only after the owner trusts them once in /hooks.

Environment: `PONYTAIL_DEFAULT_MODE=full`, `PONYTAIL_SUBAGENT_MATCHER=^software-engineering-team(:|-)(backend-developer|frontend-developer|devops-engineer)(-mechanical)?$`.

<!-- companions:end -->

## Fast lane

```text
/setup -> /deliver -> task -> branch -> pull request -> review -> merge
```

`/deliver` turns a request into one task under
`workspace/docs/backlog/tasks/` with its acceptance criteria and tests, the
owner approves it, and the owning developer role writes the change on its own
branch. The project's CI and the routed reviewers verify the pull request,
with the read-only `security-reviewer` in parallel on a risky change; it
merges with 0 critical and 0 major findings. The flow is defined in
`plugins/software-engineering-team/flows/fast-lane.md`.

Start each `/deliver` task, parallel ones included, in its own fresh session
on Sonnet: Claude `claude --model sonnet` (or `/model` in the app), Codex
`codex -m gpt-6.1-sol -c model_reasoning_effort=medium`.

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
files and unknown project configuration are preserved; the package-owned
surfaces converge before the tracked diff is committed. Release Management is deliberately a later scope.

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
