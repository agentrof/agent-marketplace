# Agent Marketplace

[![Validation](https://github.com/agentrof/agent-marketplace/actions/workflows/validate.yml/badge.svg)](https://github.com/agentrof/agent-marketplace/actions/workflows/validate.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

A Software Engineering Team plugin for **Claude Code and Codex**. Turn a request
into tracked tasks, implementation, risk-based testing, independent review and
verified delivery. Portable skills define the engineering methods. Host adapters
provide native workers, hooks and permissions.

The team works with your project's language, stack, repository and existing
commands. It keeps decisions and tasks in project-owned files, loads detailed
procedures when needed and coordinates parallel work within available capacity.

[Quick start](#quick-start) · [Capabilities](#capabilities) ·
[How delivery works](#how-delivery-works) · [Documentation](#documentation) ·
[Contributing](#contributing)

## Quick start

### 1. Prepare your environment

- A Git repository for the project you want to work on.
- Claude Code or Codex with plugin support and access to the configured models.
- Python **3.14** available as `python3` on the host's PATH.
- GitHub CLI (`gh`) authenticated when using GitHub issue and pull request flows.
- Your project's own build, test and development dependencies.

CI tests Linux, macOS and native Windows behavior. The exact host CLI versions
used by the installation checks are recorded in the
[host version policy](tools/data/host-cli-versions.json). Host features and model
availability still depend on your installed version and account.

### 2. Install the stable package

**Claude Code**. Run in the Claude Code command interface.

```text
/plugin marketplace add https://github.com/agentrof/agent-marketplace.git#stable
/plugin install software-engineering-team
```

**Codex**. Run in your terminal.

```sh
codex plugin marketplace add agentrof/agent-marketplace@stable
codex plugin add software-engineering-team@agent-marketplace
```

`stable` tracks published releases. This README on `main` can describe changes
that are awaiting publication. Check the [release notes](https://github.com/agentrof/agent-marketplace/releases)
for your installed version.

### 3. Set up the project

Open the project in your host and invoke **`software-engineering-team:setup`**
through its skill picker or command interface.

Setup inspects existing configuration, asks about consequential conflicts and
creates the missing project workspace. It refreshes managed instructions and
local worker definitions while preserving authored memory and decisions. It
also checks companion availability. Installing companions requires explicit
authorization. Complete any reported host reload and hook trust steps.

### 4. Deliver a change

Invoke **`software-engineering-team:deliver`** with an outcome and acceptance
criteria. For example, ask it to fix a pagination error, keep the response
contract compatible and prove the behavior with the relevant test.

For parallel work, provide a set of independent outcomes in the same request.
The team separates ownership, identifies shared dependencies and chooses
individual delivery or an authorized integration batch. You can inspect the
tasks, evidence and proposed changes as work progresses.

## Capabilities

### User-facing skills

| Skill | Use it to |
| --- | --- |
| `setup` | Integrate a project or explicitly migrate its managed configuration and instructions. |
| `configure` | Change project settings, workflow policy and model overrides. |
| `deliver` | Take a request through tasks, implementation, proof, review and delivery readiness. |
| `autopilot` | Authorize bounded delegated work, including issue detection and resolution. |
| `retrospective` | Inspect timing, usage, repeated failures and evidence-based improvement proposals. |
| `issue-report` | Diagnose a plugin problem, verify a local repair candidate and prepare an anonymized upstream report. |
| `companions-uninstall` | Remove supported companions under explicit removal authority. |

### Engineering methods

Delivery loads these internal methods according to the task's uncertainty and
consequences. A method can run in the current session or an appropriately scoped
worker.

| Method | Focus |
| --- | --- |
| `analyze` | Understand the request, challenge assumptions and establish acceptance criteria. |
| `solution-design` | Define boundaries, dependencies, service behavior and implementation tradeoffs. |
| `data-design` | Establish data meaning, ownership, relationships, constraints, tenancy and migration compatibility. |
| `experience-design` | Define interactions, states, accessibility and interface behavior. |
| `verify` | Select sufficient tests and environment checks, then bind proof to the candidate. |
| `review` | Independently assess correctness, security and applicable design contracts. |

<details>
<summary>Generated package inventory</summary>

<!-- counts:start -->
| Plugins | Agents | Entry skills | Knowledge skills |
|---|---|---|---|
| 1 | 0 | 7 | 6 |
<!-- counts:end -->

The agent count refers to canonical persona files. Native technical worker
definitions are generated by the host adapters. Inventory is maintained by
`make counts`.

</details>

## How delivery works

```text
Request → tasks and acceptance criteria → applicable design methods
        → implementation → proof and independent review → merge → readiness
```

- **One owner per task.** Tasks record outcomes, risks and evidence under
  `workspace/docs/backlog/tasks/`. Independent writers use disjoint allocations
  or isolated worktrees.
- **Parallelism with resource limits.** Independent tasks run concurrently.
  Proof and review can overlap on an immutable candidate. Shared environments
  have explicit ownership and capacity so parallel work does not create
  competing test environments by default.
- **Depth follows risk.** Small changes take a short path. Persisted data,
  security boundaries and irreversible effects receive their applicable checks
  regardless of the line count.
- **Proof follows the actual change.** Tests cover affected behavior and existing
  data compatibility. An authorized batch can use one atomic integration PR and
  proof for its combined candidate.
- **Completion includes readiness.** Valid proof and independent review cover the
  exact candidate, with blocking findings resolved. Merge follows the applicable
  authority. Delivery completes when the merged revision is ready in its
  intended environment.

Project testing and the marketplace's own CI have separate contracts. Delivery
uses the project's configured checks. Marketplace contributions follow the
repository's local and remote validation gates. [Delivery procedure](plugins/software-engineering-team/flows/fast-lane.md)

### Avoiding repeated tests

When one matching impact rule contains all configured `local_proof` commands,
`prove` selects that full proof once and omits narrower commands from other paths.
This uses exact command identity, not shell-command inference.

`prove` and `train prove` reuse successful evidence when their recorded inputs
remain valid and the project supplies `proof_reuse.identity_command`. That probe
identifies relevant external environment state. Changes to code, tests,
dependencies, commands or environment identity require fresh proof. Failed,
interrupted, expired or unverifiable evidence is rerun. Reuse retains the original
execution timestamps. [Proof configuration and limits](plugins/software-engineering-team/skill-content/deliver/references/local-proof.md)

### Workers, models and context

The compute profiles `light`, `work`, `review` and `critical` map to host-supported
models and effort. Access is a separate choice. Generated launch definitions
provide `light-worker`, `worker`, `reviewer`, `critical-worker` and
`critical-reviewer`. Project overrides live in `class_models.<host>.<profile>`.
Exact defaults belong to the [Claude profile map](platforms/claude/execution-profiles.json)
and [Codex profile map](platforms/codex/execution-profiles.json).

Covered native launches require a structured worker brief. The launch hook
supplies current project rules, memory, task and applicable methods, rejecting
missing or oversized context. Receipts distinguish prepared source delivery,
method loading, reported application and verified results. Requested model
settings are recorded separately from host-observed execution.

Hooks must be loaded and trusted to enforce their covered operations. Review
requires the host's verified read-only boundary. Source delivery cannot prove
semantic compliance by a model. The [host contracts](#documentation) document
those boundaries and the supported invocation paths.

## Background work and retrospectives

A bounded local observer collects completion, failure and session events into
retrospective reports. It runs separately from delivery workers, with no model
calls, test execution or remote requests. Reports use available timing, token,
cost and skill-use evidence, and identify missing or ambiguous measurements.

For active background work, `autopilot` requires a scoped grant. Its shared queue
coordinates detection (`detect`), issue resolution (`resolve`), deeper
retrospectives (`retro`) and plugin repair (`plugin_repair`). Attempts, capacity
and publication authority remain bounded. The session controller dispatches
workers. Continued execution after a session closes needs an explicitly
configured native scheduler or recovery mechanism.

Plugin repairs are verified against an isolated local candidate before an
upstream report or PR is published under authorization. Marketplace maintainer
work starts only from explicit user selection. [Orchestration and loop boundaries](docs/orchestration.md)

## Companion integrations

| Companion | Applied when |
| --- | --- |
| Ponytail | Implementing or reviewing the smallest complete change. |
| Graphify | Discovering relevant relationships through an available project graph, then verifying against source. |
| Impeccable | Designing or reviewing a user interface. |
| security-audit | Assessing security-sensitive behavior and boundaries. |

Applicable installed and enabled companions are mandatory in the team flow.
Availability, source loading and application are recorded separately. Missing
capabilities require a reason and sufficient core-method fallback evidence.
Graph creation and maintenance require project authorization.

Setup checks all companions together. Package updates preserve independently
installed integrations. The catalog below contains installation scope, checks
and project-file effects.

<details>
<summary>Companion installation and verification reference</summary>

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

</details>

## Project files and upgrades

| Location in your project | Purpose |
| --- | --- |
| `workspace/config.json` | Validated workflow settings and model overrides. |
| `workspace/docs/` | Project documentation, tasks and their evidence references. |
| `workspace/experience-design/` | Design rules and feature design artifacts. |
| `workspace/memory/me.md` | Explicit user rules and authority boundaries. |
| `workspace/memory/profile.md` | Learned preferences, separated from binding rules. |
| `workspace/memory/current-plan.md` | Active outcomes, work and open decisions. |
| `workspace/memory/product-decisions.md` | Accepted decisions with their sources and rationale. |
| `workspace/memory/agent-marketplace.md` | Managed team integration instructions. |
| `.agentrof/agent-marketplace/.runtime/` | Ignored local runtime records, proof caches and observations. |

Project instructions and host-local worker definitions are generated during
setup. Runtime state is reconstructible, but losing it requires reconciliation
of active work and external effects before retrying operations.

A package update does not automatically migrate every project. Use full setup
for first integration or changes requiring managed instructions and memory to
be reconciled. Its host generator runs `inspect`, `apply` and `check` with
`--scope all --seed-user-files`. Missing memory is created and existing authored
content is preserved.

For compatible projects with only worker-definition or model drift, refresh the
affected host's local projection. `--scope local` leaves tracked instructions
and memory outside that operation. A package version change alone does not
require setup. Each host refreshes its own local definitions. Complete the
reported reload before relying on updated skills or hooks. [Upgrade procedure](docs/upgrade-protocol.md)

## Documentation

| You want to… | Read |
| --- | --- |
| Understand ownership, parallel work and loops | [Orchestration](docs/orchestration.md) |
| Follow the detailed delivery procedure | [Delivery flow](plugins/software-engineering-team/flows/fast-lane.md) |
| Configure workflow and models | [Configuration contract](plugins/software-engineering-team/skill-content/configure/references/config-contract.md) |
| Install, refresh or migrate project integration | [Upgrade protocol](docs/upgrade-protocol.md) |
| Understand Claude-specific behavior | [Claude Code host contract](platforms/claude/software-engineering-team/host-contract.md) |
| Understand Codex-specific behavior | [Codex host contract](platforms/codex/software-engineering-team/host-contract.md) |
| Understand design boundaries | [Architecture](docs/architecture.md) |
| Author or change a skill | [Authoring guide](docs/authoring.md) |
| Understand test selection and evidence reuse | [CI and validation](docs/ci.md) |
| Maintain issues or publish a release | [Maintainer operations](docs/maintainer-operations-protocol.md) |

## Repository layout

```text
plugins/software-engineering-team/   Canonical skills, flows, scripts and templates
platforms/shared/                   Shared host integration and launch definitions
platforms/claude/                   Claude Code adapter, contracts and model policy
platforms/codex/                    Codex adapter, contracts and model policy
dist/                              Generated host packages
tools/                             Scaffolding, builds, validation and release tooling
docs/                              Architecture, authoring and operational guides
.changes/                          Pending release-impact declarations
.github/                           CI workflows and contribution forms
```

Edit canonical sources and regenerate registered host distributions. `dist/`
is build output. [Authoring and generation](docs/authoring.md)

## Contributing

Start with the [contribution guide](CONTRIBUTING.md) for source ownership,
changesets, test scope and review expectations. With Python 3.14 available,
build and validate your complete candidate before committing.

```sh
python3 tools/build_distributions.py
make counts
git add <complete-candidate-paths>
make check-local
make verify-local
```

After committing, run `make check-pr` before pushing. Local gates check the
staged candidate and its selected unit tests. Pull request CI runs the full
Linux suite, required macOS and Windows tests, static gates, CodeQL and applicable
native host lifecycle checks. Merge and release follow the maintainer protocol.

## Support and security

For reproducible defects, workflow questions and improvement proposals, use the
[issue forms](https://github.com/agentrof/agent-marketplace/issues/new/choose)
and follow the [support guide](SUPPORT.md). Include a minimal anonymized
reproduction. Community participation follows the [Code of Conduct](CODE_OF_CONDUCT.md).

Report vulnerabilities through the [private vulnerability reporting form](https://github.com/agentrof/agent-marketplace/security/advisories/new).
Keep credentials, private project data and security-sensitive details out of
public issues, pull requests and commits. [Security policy](SECURITY.md)

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
