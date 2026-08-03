# Contributing

This marketplace is a catalog of curated teams, not a parts store. Every
plugin is a complete, tested team; every component inside a plugin exists
to serve that team's flow. Contributions are judged against that thesis
first: a great component that does not make a team better does not belong
here.

## The rulebook is executable

Every rule in this repository is machine-enforced or it is not a rule.
`tools/validate.py` is the rulebook; `make check` runs it together with
the count-drift gate and the test suite. CI runs `make check` on every
push and pull request, and one finding is red. There are no exception
files, no allowlists, no temporary waivers. If you believe a rule is
wrong, change the rule in `tools/validate.py` in your PR and update its
fixture; do not work around it.

Before opening a PR:

```
make check
```

If it is green locally, it is green in CI.

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
orchestration model in [docs/orchestration.md](docs/orchestration.md).

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
5. Run `make check`, then `make counts` if the README counter table is
   now stale. Never edit counted numbers by hand.

The scaffolder updates both host surfaces and generated distributions.
After a manual canonical edit, run
`python3 tools/build_distributions.py` before `make check`.

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
- Model names in authored content. Model choice belongs to frontmatter
  configuration, never to prose.
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

## Guarding the guard

Every validator check has at least one deliberately broken fixture in
`tools/tests/`, and a meta-test keeps the check list and fixture list in
lockstep. Adding a check without a fixture turns the suite red. If your
PR changes validation behavior, it must change the fixtures in the same
commit.
