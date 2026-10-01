# Configuration Contract

`workspace/config.json` is a closed bootstrap contract, not a project-design
database. Its only keys are `schema_version`, `team_id`, `output_language` and
`terminology_language`. Setup deterministically reconstructs this shape,
preserving valid language values while dropping retired and unknown keys.

`output_language` and `terminology_language` are changed through
`project_config.py set`. Authored note titles are direct user-facing labels,
not configuration data; a language change does not rewrite them.

The other configuration targets are documents with their own lifecycle:

| Concern | Authoritative location | Owner |
|---|---|---|
| Technology, database, environment and integration choice | accepted Solution Design decision plus `_generated/capability-registry.json` | Solution Architect |
| Test, mutation and dependency-audit commands and the source of Delivery PR checks | `workspace/docs/operation/verification-contract.md` | QA Engineer |
| Runtime environment command and scenarios | `workspace/docs/operation/environment-contract.md` | DevOps Engineer |
| Maximum active Delivery Items | `workspace/docs/delivery/governance/governance.md` | Delivery Governance compiler |
| Role model and reasoning effort | the installed package's execution profile; a user override follows the host contract | User |
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
