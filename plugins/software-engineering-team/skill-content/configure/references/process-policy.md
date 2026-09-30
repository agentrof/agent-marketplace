# Process Policy

`/configure process` changes the project's process switches. The package
registry `data/process-switches.json` declares every switch: what it decides,
its owning flows, its values with their tradeoffs, the default that keeps
today's behaviour, its component metric and its promotion rule.
`workspace/docs/delivery/process-policy.md` records only the project's
explicit choices, one row per switch. A missing document, or a switch without
a row, follows the package default.

## Procedure

1. Run `process_policy.py switches --docs workspace/docs`. It lists every
   switch with its summary, values, tradeoffs, default, metric, promotion rule
   and the value in force, from the policy or the default. Report any errors
   it returns; the revision below repairs them.
2. Ask one choice-gate question per switch, at most four per host call. The
   package default is the recommended option and comes first: it is today's
   measured behaviour, and every other value is an experiment that its
   promotion rule measures. Each option's description carries that value's
   registry tradeoffs; the question names the switch's metric and promotion
   unit. Never choose for the user and never skip a switch.
3. When no answer changes a value in force, write nothing and stop.
4. Present the planned delta, each switch's value in force and the chosen
   value, and name every Delivery that is scope- or execution-approved: it
   pins the policy revision it runs under, so after the new revision is
   approved its checks refuse it until its execution plan is revised and
   approved again, which pins the new revision. A Delivery in review or later
   keeps its pin. Ask the approval choice gate. On rejection write nothing.
5. On approval run `init` when the document is absent or `begin-revision`
   when it is approved, then `set --switch <id> --value <value>` for each
   changed switch, `check` and `approve`. Choosing the default removes the
   switch's row, so a later promoted default also reaches it. `approve`
   renders the Delivery map. Then run the scoped vault gate and show the
   exact Git diff.

## Rules

- `workspace/config.json` never holds a process choice.
- Values change only through this lifecycle; never hand-edit the Switches
  table or the lifecycle fields.
- A default changes only in a package release, on the switch's promotion
  rule and the owner's approval; a project row is never a promotion.
- Flows read a value with `process_policy.py value --switch <id>`, inside a
  Delivery with `--delivery DLV-###`, which refuses a drifted pin. A draft or
  invalid policy is refused, never read.
