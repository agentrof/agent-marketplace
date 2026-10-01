# Configuration Contract

`workspace/config.json` is a closed bootstrap contract, not a project-design
database. Its keys are `schema_version`, `team_id`, `output_language`,
`terminology_language` and, once the project overrides the package's role
settings, `tier_models` and `role_tiers`. Setup deterministically
reconstructs this shape, preserving valid language values and overrides
while dropping retired and unknown keys. Setup writes neither key; an
override the installed package no longer takes stops setup, naming the tier
or role, and `project_config.py set-tier --default` or `set-role-tier
--default` removes it, also for a tier, host or role the package no longer
has.

`output_language` and `terminology_language` are changed through
`project_config.py set`. Authored note titles are direct user-facing labels,
not configuration data; a language change does not rewrite them.

`tier_models` and `role_tiers` are changed through `project_config.py
set-tier` and `set-role-tier`, which `/configure models` runs
(`references/role-models.md`). Per host `tier_models` records a tier's
`model`, an ID of the host's model list or `session`, and its `effort`,
within the efforts the installed package's tier map lets that model take; a
missing key keeps the package value. `role_tiers` moves a role, generated
variants included, between the high, medium and low tiers on every host.

The other configuration targets are documents with their own lifecycle:

| Concern | Authoritative location | Owner |
|---|---|---|
| Technology, database, environment and integration choice | accepted Solution Design decision plus `_generated/capability-registry.json` | Solution Architect |
| Test, mutation and dependency-audit commands and the source of Delivery PR checks | `workspace/docs/operation/verification-contract.md` | QA Engineer |
| Runtime environment command and scenarios | `workspace/docs/operation/environment-contract.md` | DevOps Engineer |
| Maximum active Delivery Items | `workspace/docs/delivery/governance/governance.md` | Delivery Governance compiler |
| Role model and reasoning effort | the installed package's tier map; `tier_models` sets a tier's model and effort per host and `role_tiers` a role's tier; a personal session-wide override follows the host contract | Package release for the defaults; `/configure models` for the project's overrides; User for a session-wide override |
| Process switch values | `workspace/docs/delivery/process-policy.md` over the package registry `data/process-switches.json` | Process Policy lifecycle, `/configure process` |

No `scale`, `limits`, stack, source-directory, command or process switch field
is accepted in config: the config stays closed, and the Process Policy is the
one place for process choices. Product capacity and performance are concrete
BA/Solution requirements, not global configuration knobs. `max_parallel`
remains a hard coordination guard, but only inside approved Governance; an
existing Fence receives it via `delivery_git.py apply-governance`.

Before approving a change, show the exact consumer, lifecycle, downstream
effect and whether active Delivery requires a Fence handoff. A config refresh
does not invent Solution decisions from retired stack fields.
