# Role Models

`/configure models` sets, for this project, the model and reasoning effort of
each purpose class of the active host and the class each role runs on. Every
step that spawns an agent names the purpose class of its work, such as
`mechanical`, `routine_review` or `security_review`; `scripts/model_class.py
resolve` turns it into the spawn's model and effort. A class that escalates
has a second slot, `<class>_escalated`, which a task of size `l`, rigor
`high` or a risk flag runs; `security_review` and `orchestration` never move.
The installed package's class map, `templates/model-classes.json`, pins
every slot of every host to a model and, where the model takes one, an
effort, and places every role, generated variants included, on a class.
`workspace/config.json` records only what the project changes:
`class_models`, per host and slot a `model` and an `effort`, and
`role_classes`, per role a class. A slot or role without an entry runs the
package value, so a later package change reaches it. The retired keys
`tier_models` and `role_tiers` fail `project_config.py check`; a setup
refresh drops them, and their intent is set again as classes.

## Procedure

1. Run `project_config.py classes --config workspace/config.json --host <active host>
   --model-list --json`. Per class slot it reports the roles on it, the package
   model and effort, the override, the model and effort in force with their
   sources and the efforts that model takes; per role its class, model and
   effort with their sources; per host the efforts that need the owner's
   confirmation with their evidence and sources, and the refused efforts
   with their reason. `model_choices` lists each slot's models: the package
   model, then the other models of the active host's own model list that the
   host's model picker shows, which leaves out the ones `model_list` names
   under `hidden`, or of the package catalog when `model_list` says the list
   is not available, then `session`, which runs the slot's roles on the
   session's model; each with the efforts it takes and its recommended
   effort. Report any error the report returns; the revision below repairs
   it.
2. For each class slot of the active host, ask the model first: the package model,
   recommended and listed first, then the other models of the report's
   `model_choices`, then `session`. Name the slot's roles. A question holds
   the choices in that order up to the per-question option bound the host
   contract names; when more remain than fit, it holds one fewer and its last
   option, `More choices`, opens the next question with the rest, so no
   question exceeds the bound. The package model's description says that it
   records no override, so a later package change reaches the project.
3. Then ask the effort for the chosen model from its `efforts`, within the
   same bound: lay out its choices in this fixed order: the recommended
   effort, the nearest choice below it, the nearest choice above it that the
   host does not confirm, then the rest from the lowest up, so a confirmed
   effort such as `max` comes last. On a bound of four options the first
   question offers the recommended effort, one level below, one level above
   and `More choices`, and the second the remaining levels, `max` among them.
   The recommended effort is the option recommended; every other choice's
   description says whether it is above or below it: a higher effort costs
   more time and tokens per role run, a lower one risks quality. A model with
   fewer than two effort choices, such as one that takes no effort, is
   reported, not asked. Never choose for the user and never skip a slot.
4. For an answer that the host confirms, such as `max`, ask a confirmation
   choice-gate question that states that effort's evidence and sources from
   the report, with the effort in force as the recommended first option.
   Only the owner's confirmation passes `--confirmed`.
5. Then ask whether any role should run on another class: one question whose
   recommended option keeps every role on its current class. When the owner
   moves roles, ask which from the report's roles, which cover every role,
   the `-lens` and `-mechanical` variants included, and for each one a
   question with its current class first and recommended, then the other
   classes; the package class's description says that it
   records no override.
6. When no answer changes a setting in force and the report shows no error,
   write nothing and stop.
7. Present the planned delta: each changed slot with its model and effort in
   force and the chosen ones, each moved role with its class, and the removal
   of each override that the report's errors name on a slot, host or role
   the installed package no longer has. Ask the approval choice gate. On
   rejection write nothing.
8. On approval, for each changed slot run `project_config.py set-class --config
   workspace/config.json --host <host> --class <class>`, adding `--escalated`
   for an escalated slot, with `--model <model>`
   and `--effort <effort>`, adding `--confirmed` for a confirmed effort, or
   with `--default` when both are the package's and for each slot override
   step 7 removes; for each moved role run `project_config.py set-role-class
   --config workspace/config.json --role <role>` with `--class <class>`, or
   with `--default` for its package class and for each role override step 7
   removes; then run `project_config.py check`.
9. Regenerate the host projection: run the host project generator that setup
   step 4 runs, `apply` with `--scope local`, and present its `roles` table:
   role, class, model, effort and source, with any `kept` file and `notice`
   it reports. That table is the one in effect, since the generator also
   applies model availability. Then run `project_config.py classes` and
   present the config-level effective map it prints, and show the exact Git
   diff of `workspace/config.json`.

## Rules

- A value equal to the package's records nothing, and a missing key keeps
  the package value.
- A refused effort, such as one that starts subagents of its own, is never
  offered or written.
- An override the installed package no longer takes stops setup and
  `project_config.py check`, naming the slot or role; this topic repairs it
  with another value or `--default`. `--default` removes any override the
  config holds, also for a slot, host or role the package no longer has.
- Generated role files are never edited by hand; this topic renders them
  again.
- Overrides for another host change through `/configure models` on that
  host; its projection follows at its next setup or refresh.
- Settings outside the project can still override or cap the model or
  effort a role runs at; the host contract names them.
