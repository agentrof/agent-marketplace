# Process Policy

`/configure process` changes the project's process switches. The package
registry `data/process-switches.json` declares every switch: what it decides,
its owning flows, its values with their tradeoffs, the default that keeps
today's behaviour, its component metric and its promotion rule.
`workspace/docs/delivery/process-policy.md` records only the project's
explicit choices, one row per switch. A missing document, or a switch without
a row, follows the package default. A switch may declare owner-set parameters
for the values that take them, such as the limits of `story_size_budget`; its
Parameters table holds one row per parameter the owner sets, and the package
sets none until a promotion ships package limits, which apply to each
parameter the table leaves unset. A switch that only reports, such as
`step_budgets`, ships its targets as package limits from its first release.

## Procedure

1. Run `process_policy.py switches --docs workspace/docs`. It lists every
   switch with its summary, values, tradeoffs, default, metric, promotion rule
   and the value in force, from the policy or the default, and for a switch
   with parameters their declaration and the values set. Under `undeclared`
   it lists each policy row for a switch or parameter this package no longer
   declares. Report any errors it returns; the revision below repairs them.
2. Ask one choice-gate question per switch, in host calls no larger than the
   per-call bound the host contract names. The
   package default is the recommended option and comes first: it is today's
   measured behaviour, and every other value is an experiment that its
   promotion rule measures. Each option's description carries that value's
   registry tradeoffs; the question names the switch's metric and promotion
   unit. The default option's description says that it records no row, so a
   later promoted default reaches the project. Never choose for the user and
   never skip a switch. When the chosen value takes parameters, ask one
   question per declared parameter with its summary and type, and take the
   number, or for a `glob_list` the comma-separated path globs, from the
   owner's own answer: the package recommends none. Offer the
   value in force, if any, and unset, which leaves a package limit in force
   where the switch ships one. At least the declared `min_count` must be set,
   package limits included.
3. When no answer changes a value in force and `undeclared` lists no row,
   write nothing and stop.
4. Present the planned delta, each switch's value in force and the chosen
   value, each parameter set, changed or unset, each undeclared row the
   revision removes.
   Ask the approval choice gate. On rejection write nothing.
5. On approval run `init` when the document is absent or `begin-revision`
   when it is approved, then `set --switch <id> --value <value>` for each
   changed switch and `set --switch <id> --parameter <id> --value <number|globs>`,
   or `--default` to unset it, for each changed parameter, then
   `set --switch <id> --default` for each undeclared switch row and
   `set --switch <id> --parameter <id> --default` for each undeclared
   parameter row, `check` and `approve`. Choosing the default removes the switch's row, so a later
   promoted default also reaches it; a value that takes no parameters also
   removes the switch's parameter rows, which `set` reports as
   `removed_parameters`. Then run the scoped vault gate and show the exact Git
   diff.

## Rules

- `workspace/config.json` never holds a process choice.
- Values change only through this lifecycle; never hand-edit the Switches
  table or the lifecycle fields, nor the Parameters table.
- A default changes only in a package release, on the switch's promotion
  rule and the owner's approval; a project row is never a promotion.
- Flows read a value with `process_policy.py value --switch <id>` from the
  current policy. A draft or invalid policy is refused, never read.
