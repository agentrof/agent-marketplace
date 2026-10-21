# Configuration Contract

`workspace/config.json` is a closed bootstrap contract, not a project-design
database. Its keys are `schema_version`, `team_id`, `output_language`,
`terminology_language`, `tests_root`, `workspace_layout`, the optional
`pr_proof` and, once the project overrides the package's role settings, `class_models` and
`role_classes`. Setup deterministically
reconstructs this shape, preserving valid language values and overrides
while dropping retired and unknown keys. Setup writes neither key; an
override the installed package no longer takes stops setup, naming the slot
or role, and `project_config.py set-class --default` or `set-role-class
--default` removes it, also for a slot, host or role the package no longer
has. The retired `tier_models` and `role_tiers` fail the check; setup drops
them.

`output_language`, `terminology_language`, `tests_root`, `workspace_layout`,
`pr_proof`, `local_proof`, `role_routing` and `task_triggers` are changed
through `project_config.py set`. `local_proof` lists the proof commands,
`role_routing` and `task_triggers` hold project rules in the shape of the
deliver entry's `data/role-routing.json` and `data/task-triggers.json`. They
live here because `.agentrof/` is never tracked; setup writes none of them and
keeps the project's.
`pr_proof` says who proves a fast-lane task before its pull request:
`remote_ci` (the default when absent) leaves it to the project's CI;
`local_record` makes `open-pr` require a green `task_lane.py prove` record of
the clean HEAD, as the deliver entry's `references/local-proof.md` defines.
Setup writes no `pr_proof` and keeps the project's value.
`tests_root` (default `workspace/tests`) is where the project's tests live.
`workspace_layout` lists the only top-level folders a write may create under
`workspace/` (`workspace`) and `workspace/docs/` (`docs`); setup creates them
and the workspace guard refuses any other. Project-specific data goes to
`workspace/project/<name>/`. Setup writes both from the package defaults when
absent and keeps the project's values after. Authored note titles are direct user-facing labels,
not configuration data; a language change does not rewrite them.

`class_models` and `role_classes` are changed through `project_config.py
set-class` and `set-role-class`, which `/configure models` runs
(`references/role-models.md`). Per host `class_models` records a class slot's
`model`, an ID of the host's model list or `session`, and its `effort`,
within the efforts the installed package's class map lets that model take; a
missing key keeps the package value. `role_classes` moves a role to another
purpose class on every host.

The other configuration targets are documents with their own lifecycle:

| Concern | Authoritative location | Owner |
|---|---|---|
| Technology, database, environment and integration choice | accepted Solution Design decision plus `_generated/capability-registry.json` | Solution Architect |
| Test, mutation and dependency-audit commands | `workspace/docs/operation/` | QA Engineer |
| Runtime environment command and scenarios | `workspace/docs/operation/` | DevOps Engineer |
| Role model and reasoning effort | the installed package's class map, resolved per spawn by `model_class.py resolve`; `class_models` sets a class slot's model and effort per host and `role_classes` a role's class; a personal session-wide override follows the host contract | Package release for the defaults; `/configure models` for the project's overrides; User for a session-wide override |

No `scale`, `limits`, stack, source-directory or command field is accepted in
config: the config stays closed. Product capacity and performance are concrete
BA/Solution requirements, not global configuration knobs.

Before approving a change, show the exact consumer, lifecycle and downstream
effect. A config refresh
does not invent Solution decisions from retired stack fields.
