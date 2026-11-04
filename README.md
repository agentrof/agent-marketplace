# Agent Marketplace

Agent Marketplace ships one standalone, host-neutral Software Engineering Team
for Claude Code and Codex. One delivery owner coordinates task-based work,
conditional methods, isolated workers, independent review and verified readiness.
Four compute profiles serve five small host launch definitions. Skills carry
the workflow and domain methods.

## Catalog

<!-- counts:start -->
| Plugins | Agents | Entry skills | Knowledge skills |
|---|---|---|---|
| 1 | 0 | 7 | 6 |
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

Ponytail, Graphify, Impeccable and security-audit are required for their relevant
work when installed and enabled. Check all four once with
`scripts/setup_project.py companions check --all --project-root <root> --json`.
Use `--companion <id>` for one selection. Installation requires explicit
authority through `companions apply`. Package updates preserve independently
installed integrations.

A lightweight check distinguishes known installation and enabled state from
unverified host loading. `--verify-load` requests supported host metadata probes.
Task evidence records actual source loading and worker-reported application.
Missing or disabled capabilities require an explicit reason and core fallback.
Use `companions uninstall --companion <id>` for selected removal, or the
`companions-uninstall` entry for explicitly requested complete removal.
The generated catalog below documents each integration's scope and side effects.

When an applicable graph already exists, use it to locate dependencies and
verify findings against current source. Graph creation and maintenance follow
explicit project authorization and stay outside unrelated task proof.

<!-- companions:start -->

The team flow owns tasks, write allocation, proof, review and merge decisions. Use each companion for its relevant work when installed and enabled. Setup checks availability without installing, uninstalling or changing global packages. Missing or unverified companions require a recorded fallback.

### ponytail

Smallest complete change, plain-language review and a shortcut debt ledger. Implementation and review use its scoped method without introducing another delivery loop. [https://github.com/DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | user | `claude plugin marketplace add DietrichGebert/ponytail`<br>`claude plugin install ponytail@ponytail --scope user` | `claude plugin list --json` lists `ponytail@ponytail` |
| codex | user | `codex plugin marketplace add DietrichGebert/ponytail`<br>`codex plugin add ponytail@ponytail` | `codex plugin list --json` lists `ponytail@ponytail` |

Load Ponytail explicitly for the current implementation or review. Existing hook settings remain user-owned; installation alone does not establish hook execution or skill use.

### graphify

Knowledge graph of the project's code and documents. Queries locate relevant relationships before source verification. Existing docs graphs can refresh after ordinary task merges when project Markdown changed. [https://github.com/Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify), Apache-2.0.

| Host | Scope | Install | Verify |
|---|---|---|---|
| cli | user | `uv tool install graphifyy` | `graphify --version` succeeds |
| git-hook | project | `graphify hook install` | `graphify hook status` succeeds and prints `post-commit: installed` |
| claude | user | `graphify install --platform claude` | `.claude/skills/graphify/SKILL.md` exists in the home directory |
| codex | user | `graphify install --platform codex` | `.codex/skills/graphify/SKILL.md` exists in the home directory |

uv not found: install it (https://docs.astral.sh/uv/getting-started/installation/), then rerun companions apply --companion graphify.

Environment: `GRAPHIFY_OUT=.agentrof/graphify`.

Setup writes `.agentrof/graphify/.gitignore`.

### impeccable

Frontend design craft and read-only UI critique. Interface work uses its design and review method. Explicit installation keeps hooks disabled and telemetry off. [https://github.com/pbakaus/impeccable](https://github.com/pbakaus/impeccable), Apache-2.0.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | user | `npx impeccable install --providers=claude --user --no-hooks -y` | `.claude/skills/impeccable/SKILL.md` exists in the home directory |
| codex | user | `npx impeccable install --providers=codex --user --no-hooks -y` | `.agents/skills/impeccable/SKILL.md` exists in the home directory |

Environment: `IMPECCABLE_CONTEXT_DIR=workspace/experience-design`, `IMPECCABLE_NO_TELEMETRY=1`.

Setup writes `.impeccable/config.json`.

Check fails on `DESIGN.md`, `PRODUCT.md`, `docs/DESIGN.md`, `docs/PRODUCT.md`. DESIGN.md and PRODUCT.md live only in workspace/experience-design.

### security-audit

Security review lens with a tenant data isolation reference. Security-sensitive work uses guidance within the existing task and review. A full audit needs its own authorized scope. [https://github.com/cloudflare/security-audit-skill](https://github.com/cloudflare/security-audit-skill), MIT.

| Host | Scope | Install | Verify |
|---|---|---|---|
| claude | user | `npx skills add https://github.com/cloudflare/security-audit-skill --skill security-audit --agent claude-code -g -y` | `.claude/skills/security-audit/SKILL.md` exists in the home directory |
| codex | user | `npx skills add https://github.com/cloudflare/security-audit-skill --skill security-audit --agent codex -g -y` | `.agents/skills/security-audit/SKILL.md` exists in the home directory |

<!-- companions:end -->

## Delivery

`/deliver` records coherent tasks under `workspace/docs/backlog/tasks/`, with
outcomes, criteria, risks and evidence. Each writing task has its own branch
and workspace. Analyze, solution-design, data-design, experience-design,
verify and review load only when the task needs them. Persisted data semantics
always receive the applicable data-design checks.

Independent tasks run concurrently within host and resource capacity. A warm
shared environment and an authorized atomic integration PR can share full
proof across a batch. Ordinary merges require proof and independent review
of the exact candidate with all blocking findings resolved. Completion also
requires the actual merged revision to be ready in its intended environment.
The executable procedure is [the delivery flow](plugins/software-engineering-team/flows/fast-lane.md).

The main-session defaults are Sol at medium effort on Codex and Sonnet at
medium effort on Claude Code. These are launch recommendations. Project model
overrides affect the four compute profiles without rewriting global settings.
Actual host observations determine which model and effort ran.

`/issue-report` previews an anonymized support payload in chat and publishes
only under explicit authorization. Consumer-side local repair follows the
host's candidate-loading path before an upstream source change is proposed.

## Local state and upgrades

Durable project truth is tracked Markdown and JSON in the project workspace.
Runtime scratch, proof caches and local coordination records are disposable.
Compatible package updates load current packaged methods. A release version
alone does not invalidate launch settings. Changed launch source or models
use a local projection refresh. Missing bootstrap and incompatible project
settings use an explicit setup or reviewed migration, preserving authored data.
Host reload requirements remain visible until the candidate is actually loaded.

## Quality

After generating all distributions, stage the complete candidate and run
`make check-local` followed by `make verify-local`. Local checks cover static
contracts and the selected tests for that candidate. Pull request CI owns the
full suite and required platform and host checks.

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
