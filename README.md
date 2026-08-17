# Agent Marketplace

Agent Marketplace ships one standalone, host-neutral Software Engineering Team
for Claude Code and Codex. The team takes a greenfield project from Business
Analysis through Solution Design, Design System, Experience Design and a
delivery-ready backlog.

## Catalog

<!-- counts:start -->
| Plugins | Agents | Entry skills | Knowledge skills |
|---|---|---|---|
| 1 | 14 | 15 | 16 |
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
tracked vault contract, ignored local Obsidian plugin projection, designation
map, managed ignore block and portable gate. Claude and Codex use the same
canonical workflows and project-local files.

## Greenfield path

```text
setup -> business-analysis -> solution-design -> design-system
       -> experience-design -> backlog-plan -> deliver (explicit)
```

The first four stages produce approved Git-tracked documents, which are their
complete state. `backlog-plan` creates the tracked Obsidian backlog and stops
before delivery activation.

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
Approved defects and improvements are tracked separately as
`workspace/docs/issues/<slug>.md` and may be filed upstream only by an explicit
project decision authority request.

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
tracked diff is committed. Delivery and delivery-lane runtime design is a
separate scope.

## Quality

```text
make check
```

runs validation, deterministic distribution generation, counts, release
surface checks and focused tests.
