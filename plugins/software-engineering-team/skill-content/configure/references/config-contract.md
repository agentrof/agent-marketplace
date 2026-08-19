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
| Test, mutation and dependency-audit commands | `workspace/docs/operation/verification-contract.md` | QA Engineer |
| Runtime environment command and scenarios | `workspace/docs/operation/environment-contract.md` | DevOps Engineer |
| Maximum active Delivery Items | `workspace/docs/delivery/governance/governance.md` | Delivery Governance compiler |

No `scale`, `limits`, stack, source-directory or command field is accepted in
config. Product capacity and performance are concrete BA/Solution requirements,
not global configuration knobs. `max_parallel` remains a hard coordination
guard, but only inside approved Governance; an existing Fence receives it via
`delivery_git.py apply-governance`.

Before approving a change, show the exact consumer, lifecycle, downstream
effect and whether active Delivery requires a Fence handoff. A config refresh
does not invent Solution decisions from retired stack fields.
