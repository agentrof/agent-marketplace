# Contributing

Agent Marketplace ships a host-neutral Software Engineering Team for Claude Code
and Codex. Contributions should improve that team's supported workflow, preserve
project-owned data and work across the affected hosts and operating systems.

Read the repository's `AGENTS.md`, [architecture](docs/architecture.md) and
[authoring guide](docs/authoring.md) before editing. For a reproducible defect or
proposal, use the [issue forms](https://github.com/agentrof/agent-marketplace/issues/new/choose).
Security findings use [private reporting](SECURITY.md).

## Set up a development checkout

Clone the repository and install Python 3.14 and Git. Repository tooling uses the
Python standard library. Native installation checks additionally need the host
CLIs and their runtime dependencies, as defined by the
[CI policy](docs/ci.md). Local unit tests use isolated host fixtures.

```sh
git clone https://github.com/agentrof/agent-marketplace.git
cd agent-marketplace
git switch -c codex/describe-your-change
```

Use the registered host's branch prefix when working through its agent.

## Choose the source of truth

| Change | Edit |
| --- | --- |
| Portable behavior, methods and templates | `plugins/software-engineering-team/` |
| Shared host integration and technical launch definitions | `platforms/shared/` |
| Host permissions, packaging and model policy | `platforms/claude/` or `platforms/codex/` |
| Build, validation and release contracts | `tools/` and their tests |
| Reader and maintainer guidance | Root documentation and `docs/` |

Skills own domain methods. Flows coordinate tasks, proof, review and readiness.
Technical worker definitions bind compute and access and are generated from the
shared launch registry. Keep model IDs in host catalogs and profile maps.
The [authoring guide](docs/authoring.md) defines the skill template, reference
layout and evidence boundaries.

Use the scaffolder when creating a component. For example, create an internal
method with the supported hidden-skill interface.

```sh
python3 tools/scaffold.py new-skill --plugin software-engineering-team --name <name> --kind hidden
```

Keep critical boundaries in the active method. Put conditional detail in linked
references and repeatable mechanical checks in scripts. Reuse existing
project-config, transaction and validation helpers. Changes to a public skill,
config field or persisted record need an explicit compatibility assessment.

## Validate a change

1. Add or update a focused regression for changed behavior. Cover affected callers,
   old data and failure paths. Include operating-system and host differences when
   relevant.
2. Add `.changes/<short-kebab-summary>.json` with a non-empty `summary` and a
   `components` object. Each affected component uses `patch`, `minor` or `major`.
   Documentation, test and CI changes with no stable release effect use an empty
   `components` object. Impact is separate from the calendar release number.
3. Generate all registered distributions and derived README content. Stage the
   complete candidate and run the local gates.

```sh
python3 tools/build_distributions.py
make counts
git add <complete-candidate-paths>
make check-local
make verify-local
```

`check-local` runs fresh static gates and the changed candidate's selected unit
tests, with bounded parallel workers. `verify-local` checks the exact staged
candidate and its evidence immediately before commit. Partial staging and
worktree/index mismatches are rejected. Valid unchanged local results can be
reused for at most 24 hours. Integration tests run in pull request CI.

After committing, run `make check-pr` before pushing. This checks release impact
and confidentiality across the committed branch. Include the intended behavior,
verification and remaining limitations in the PR. Keep relevant documentation
and code in the same change.

Pull request CI runs every test on Linux, required platform tests on macOS and
Windows, static validation and CodeQL. Applicable host lifecycle checks verify
both native packages. Check the exact PR head before claiming readiness.
[CI commands and evidence contracts](docs/ci.md)

## Review and fix agents

For package PR reviews and fixes, use `make check-local` and
`make verify-local`. Beyond those targets, run at most the single test named
in a finding. Run a full test module, `make check`, or a `--full` local gate
only to reproduce a CI failure already reported on the exact PR head. Name
that failure and the reproduction command in the review evidence. Pull
request CI owns the full suite and required platform and host checks.

Fix agents stage the complete candidate before the local targets. Reviewers
stay read-only and use the owner's exact staged candidate and its local evidence.
If that candidate is unavailable, report the missing evidence without staging
or changing files. Verify remote checks against the exact PR head before
claiming readiness.

## Generated files, privacy and releases

- Generate `dist/` with `tools/build_distributions.py` and README inventory blocks
  with `make counts`.
- Preserve authored project files and unrelated host configuration. Setup and
  migrations must report conflicts and converge when repeated.
- Keep consumer names, private project data, credentials and identifying source
  links out of committed content and PRs. Follow the
  [confidentiality procedure](docs/maintainer-operations-protocol.md#confidentiality).
- Treat source loading, reported application and verified behavior as distinct
  evidence. A passing synthetic test cannot establish untested live-model behavior.
- Keep `versions.json`, `CHANGELOG.md` and `.release/stable.json` under the release
  tooling's ownership. The release commit is generated with `tools/release.py bump`.
- Maintainer issue work starts from explicit user selection. Merge approval and
  release authority follow the [maintainer protocol](docs/maintainer-operations-protocol.md).

The [Code of Conduct](CODE_OF_CONDUCT.md) applies to participation. Support requests
follow [SUPPORT.md](SUPPORT.md).

## Unit first

Process starts and file-system work, not Python, dominate this suite's time,
so every test picks the cheapest level that proves its rule:

- A rule a unit test can prove is proven by a unit test. Call the deciding
  function in process on synthetic in-memory input or a minimal temporary
  tree, with no Git and no subprocess. When a script decides inside its Git
  reads, extract the decision into a pure function the script calls.
- Each script or verb keeps exactly one real end-to-end smoke per refusal
  family, so the wiring between its reads and the rule stays tested.
- Integration tests are kept for what a unit test cannot prove: genuine races,
  the interplay of several Deliveries, and real Git semantics such as refs,
  worktrees, index flags, file modes, symlinks and line endings as Git records
  them.
- A refusal matrix is parametrized unit cases with `subTest` plus that one
  smoke. Independent scenarios are separate test methods sharing a helper.
- Converting a test never weakens it: the same exception type and message are
  expected, and the converted rule is broken once to see the test fail.
- Mark every test that starts Git or any other process, or writes outside
  its own temporary directory, with `@integration` from
  `tools/tests/levels.py`, on the method or on its class. The local gate runs
  only unit tests, fast and as many of the change's own as fit; pull request
  CI runs unit and integration tests. CI and local workers fail an unmarked
  test that starts a process, writes outside its own temporary directory or
  leaves a thread running, so the split stays true.

## Guarding the guard

Every validator check has a deliberately broken fixture:
`VALIDATOR_BUILDERS` in `tools/tests/test_validator_contract.py` holds one
builder per entry of `CHECKS` in `tools/validate.py`, and each builder breaks
the valid fixture repository so that its check reports it. A meta-test keeps
the two in lockstep, so adding a check without a builder turns the suite red.
If your PR changes validation behavior, it must change the builders in the
same commit.

Local unit workers also reject process starts during test-module loading and class/module fixtures, including attempts a fixture catches. Shared fixture seeds must be built in process and remain read-only; each unit test mutates only its own temporary copy.
