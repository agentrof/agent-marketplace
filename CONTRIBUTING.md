# Contributing

This marketplace is a catalog of curated teams, not a parts store. Every
plugin is a complete, tested team; every component inside a plugin exists
to serve that team's flow. Contributions are judged against that thesis
first: a great component that does not make a team better does not belong
here.

## The rulebook is executable

Every rule in this repository is machine-enforced or it is not a rule.
`tools/validate.py` is the rulebook; `make check` runs it together with
the count-drift gate and the complete test suite. CI runs fresh static gates
and independently selects and verifies the required platform test matrix on
push, pull request and merge queue groups. One finding is red. There are no
exception files, no allowlists, no temporary waivers. If you believe a rule is
wrong, change the rule in `tools/validate.py` in your PR and update its
fixture; do not work around it.

Before committing and opening a PR, with `python3` running Python 3.14, the
one version the plugin supports and CI tests:

```text
python3 tools/build_distributions.py
git add <complete-change-paths>
make check-local
make verify-local
```

`check-local` always runs static gates and the change's own tests from the
full branch base-to-staged-candidate diff, with four isolated workers by
default: the changed test methods, the tests that name a changed input or a
changed function, and the modules that import a changed test helper, within a
budget of about 40 seconds.
Pull request CI runs every test on Linux and each system's own tests on macOS
and Windows, so the local gate never runs the whole suite on its own;
`python3 tools/ci_local.py check --staged --full` runs every test on request,
for a change whose host-specific behavior CI cannot cover. After committing and
before pushing, run `make check-pr`, the confidentiality and release-impact
scan of the committed branch. Partial staging and worktree/index mismatches are rejected. Identical successful local results can be reused for
at most 24 hours; a changed or failed candidate invalidates them. Verify the
exact staged candidate again immediately before commit. `make check` remains
the exhaustive local gate. Independent remote platform checks and release
validation remain required; no Git hooks are installed. See
[CI validation](docs/ci.md) for the commands and evidence boundaries.

Before running the gate, add `.changes/<short-kebab-summary>.json`. It must contain
a non-empty `summary` and a `components` object. Use `patch`, `minor`, or
`major` for every affected plugin or `agent-marketplace`; use an empty object
for documentation, test, and CI changes with no stable release effect. The
impact never chooses the version: a release is named `YYYY.M.N` by the
[calendar](docs/maintainer-operations-protocol.md#calendar-versions), and the
marketplace and every plugin carry that one version. Never
edit `versions.json`, `CHANGELOG.md` or `.release/stable.json` by hand: only
the release commit changes them, the last commit of a pull request, made by
`python3 tools/release.py bump`, as the
[maintainer protocol](docs/maintainer-operations-protocol.md#flow-b-explicit-release-to-clean-main)
describes; merging that pull request publishes the release. A local pass establishes the local candidate result; CI
independently verifies its platform and host coverage.

Security findings do not belong in public issues, pull requests or commit
messages. Use the repository's [private vulnerability reporting form](https://github.com/agentrof/agent-marketplace/security/advisories/new)
and follow [SECURITY.md](SECURITY.md).

## Component model

- Agents are platform-independent roles: short constitutions with fixed
  sections (Principles, Boundaries, Approach, Output Contract). They
  carry zero technology knowledge.
- Skills under `skill-content/` carry all technology and capability
  knowledge. Entry skills are the user surface; knowledge skills are internal
  and load only through the team's flows. Host wrapper trees are generated.
- Flows are the orchestration prose under `flows/`; entries stay thin
  and delegate to them.

The full contracts, caps and templates live in [docs/authoring.md](docs/authoring.md);
the invariants behind them in [docs/architecture.md](docs/architecture.md); the
orchestration model in [docs/orchestration.md](docs/orchestration.md); and the
current lifecycle and Git protocol in
[docs/requirement-delivery-protocol.md](docs/requirement-delivery-protocol.md).
Repository maintainers use the separate, manually invoked
[issue and release operations protocol](docs/maintainer-operations-protocol.md);
an issue event never starts an agent and issue text never grants merge or
release authority. When `main` requires the merge queue, a green PR need not be
brought up to date before merging: the queue runs the required checks again on
the exact commit it merges. The protocol lists the
[repository settings](docs/maintainer-operations-protocol.md#repository-settings)
the owner enables for it.

## Add a knowledge skill: walkthrough

1. Scaffold it: `python3 tools/scaffold.py new-skill --plugin <plugin> --name <name> --kind hidden`.
   The generated skeleton already passes `make check`.
2. Write the SKILL.md as a decision surface: what to do and what never to
   do, in DO/DON'T voice. Respect the size caps; depth goes into
   `references/` files, each linked from SKILL.md.
3. If the skill describes a technology stack, ship both reserved
   checklist files: `references/review-checklist.md` and
   `references/qa-checklist.md`. The review and QA process skills compose
   with them at run time.
4. Scripts under `scripts/` must be stdlib-only and runnable from any
   working directory. Outputs are anchored at the consuming project's
   git root, never at user or system level.
5. Run `make counts` if the README counter table is stale, regenerate
   distributions, stage the complete change and run `make check-local` followed
   by `make verify-local`. Never edit counted numbers by hand.

The scaffolder creates one host-neutral
`templates/project-instructions/team.md`, creates platform source for every
registered adapter, updates native marketplace registries, and rebuilds all
distributions. Host-specific project instruction behavior belongs only in the
relevant platform adapter fragments. The files every host loads as user context
are listed once in
`platforms/shared/_team/overlay/templates/project-instructions/user-context.json`,
and each host fragment renders that list on its one `{{user_context}}` line.
After a manual canonical edit, run
`python3 tools/build_distributions.py` before staging and `make check-local`.

New stacks for the software team (a config enum value plus a skills
folder plus tests) are maintainer releases: the team ships tested stacks
only and never degrades silently. Open an issue first.

## Named anti-patterns

These are the failure modes this repository was built against. PRs that
reintroduce them will be rejected, and most of them are caught by the
validator.

- Hand-written counts. Derived numbers drift the moment content changes;
  the only counts live in the README marker block, injected by
  `tools/counts.py`.
- Per-agent knowledge copies. Shared content is written once and
  referenced; hand-synced copies rot.
- Auto-trigger agent descriptions. Team agents are passive and run only
  via explicit spawns from flows; trigger phrases turn a curated flow
  into a lottery.
- Version pins and vendor bias in content. Pinned versions rot; content
  is written in principle language and stays valid across releases.
- Model names in authored content. Agents declare a reasoning tier; each
  host's `platforms/<host>/execution-profiles.json` maps it to a class of the
  host's pinned `model-catalog.json` and an effort, never prose.
- Absolute or user-level paths. All outputs are project-relative;
  writing outside the consuming project's tree is a structural leak.
- Technology nouns in agent bodies. The moment a role names a framework,
  the role stops being swappable; stacks live in skills and config.
- Mega-commands and pseudo-code prompts. Orchestration is a state
  machine in prose with mechanical artifact checks, not a thousand-line
  script the model is asked to imitate.
- Report-file exhaust. Durable knowledge exits through git channels:
  code, PR bodies, living documents. Transient findings live in run
  folders and die with them.
- Memory tiers and mind-maps. A missing-context problem is a
  step-contract bug; fix the contract, do not add a buffer.

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

## Guarding the guard

Every validator check has a deliberately broken fixture:
`VALIDATOR_BUILDERS` in `tools/tests/test_validator_contract.py` holds one
builder per entry of `CHECKS` in `tools/validate.py`, and each builder breaks
the valid fixture repository so that its check reports it. A meta-test keeps
the two in lockstep, so adding a check without a builder turns the suite red.
If your PR changes validation behavior, it must change the builders in the
same commit.
