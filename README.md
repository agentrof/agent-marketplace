# Agent Marketplace

Agent Marketplace ships one standalone, host-neutral Software Engineering Team
for Claude Code, Codex, and the project-local OpenCode Terminal host. The team takes a user Requirement through the
applicable analysis, design and backlog gates, then hands the approved backlog
to the Delivery Flow.

## Catalog

<!-- counts:start -->
| Plugins | Agents | Entry skills | Knowledge skills |
|---|---|---|---|
| 1 | 17 | 15 | 15 |
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

Start `software-engineering-team:setup` in the project. The setup entry uses
`scripts/setup_project.py inspect|apply|check` to converge the workspace,
tracked vault contract, ignored local Obsidian plugin projection, closed
configuration, managed ignore block and portable gate. Claude Code and Codex
use the same canonical workflows and project-local files.

### OpenCode Terminal Host Support

OpenCode is deliberately not installed through a global Agent Marketplace
marketplace command. Check out an exact Agent Marketplace release tag, then
project its generated package into the target project:

```text
python3 -B dist/opencode/software-engineering-team/scripts/project_opencode.py inspect \
  --project-root /absolute/path/to/project --json
python3 -B dist/opencode/software-engineering-team/scripts/project_opencode.py apply \
  --project-root /absolute/path/to/project --clients-stopped
```

The target may be a Git worktree or an ordinary directory. Projection installs
only the Agent Marketplace-owned files under
`.opencode/agentrof/agent-marketplace/` plus named OpenCode discovery files;
it preserves `opencode.json`, `opencode.jsonc`, OpenCode's own bootstrap files,
and unrelated `.opencode` content. A tracked `.opencode` is refused before any
write. The root managed ignore block includes `/.opencode/`.

OpenCode Terminal support is pinned to **OpenCode 1.18.17**. Interactive TUI
workflows may ask normal conversational text questions; a native popup is not
required. Bind the runtime once, then use `opencode run --dir
<absolute-project-path> --command <entry-id> --agent
software-engineering-team --format json` only for the package's explicitly
choice-free entries (currently `/issue-report`). `--dir` is mandatory in the
support contract so a `run` invocation cannot bind to the caller's directory.
Desktop, web/serve, ACP, attach, `--pure`, `--auto`, `--fork`, experimental
code mode, and unapproved third-party plugins are outside the support contract.
Before apply, update, prune, or uninstall, stop OpenCode clients for that
project and pass `--clients-stopped` where required.

The OpenCode release gate is intentionally stricter than this local install
guide: it requires the pinned binary, deterministic fake-provider CLI/hook
probe, and an interactive TUI PTY/ConPTY probe on the exact release candidate.
The TUI and real WSL2 evidence are release blockers, not claims inferred from
the non-interactive `run` test.

After projection, use:

```text
python3 -B .opencode/agentrof/agent-marketplace/manage.py check
python3 -B .opencode/agentrof/agent-marketplace/manage.py bind-runtime --opencode /absolute/path/to/opencode
python3 -B .opencode/agentrof/agent-marketplace/manage.py prune --clients-stopped
python3 -B .opencode/agentrof/agent-marketplace/manage.py uninstall --clients-stopped
```

Run `bind-runtime` before the first protected Agent Marketplace mutation, and
again whenever the OpenCode executable changes. `check` verifies the recorded
OpenCode/Python executable hashes and the pinned version. The current OpenCode
CLI plugin API does not expose a reliable identity for the parent executable in
`run` mode, so the exact running-binary check is enforced by the release host
matrix rather than being claimed as an in-plugin guarantee. Runtime binding and
`check` reject an effective OpenCode plugin set other than the generated
project plugin; arbitrary third-party plugin coexistence is intentionally
outside support.

The project plugin passes every supported `write`, `edit`, `apply_patch`,
and `bash` through the package's canonical, hash-pinned `vault_hook.py`
before and after execution. This makes machine-owned workspace configuration,
vault rules, call correlation, and Bash post-validation mechanical in
OpenCode rather than prompt-only conventions. Unknown or experimental tools
fail closed.

The source checkout may be removed after a successful projection. Each project
retains its own immutable package version. The installer verifies annotated-tag
consistency and package hashes when used from a stable checkout; this is
provenance and corruption detection, not cryptographic authenticity against a
hostile local checkout.

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
