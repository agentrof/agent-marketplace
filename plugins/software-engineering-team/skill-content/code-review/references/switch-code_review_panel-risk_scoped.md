# Code Review Panel by Risk

These are the instructions of process switch `code_review_panel` at
`risk_scoped` for Delivery code review. A task binds this file only when the
project's Process Policy selects that value; at the default, `single_reader`,
code review runs as its flow, skill and role files describe. Inside a Delivery
read the value and its parameters with `process_policy.py value --switch
code_review_panel --delivery DLV-###`; the Delivery pins the policy it runs
under.

At this value the official `code-reviewer` always reads, exactly as at
`single_reader`, and a lens reader joins it only when the frozen change
triggers its lens. Every reader that joins runs, registers, is calibrated and
merges as `switch-code_review_panel-beside_official.md` in this directory
defines for `beside_official`; read it in full when the manifest carries
`code_review_panel`. This file decides only which lens readers join.

## Lens triggers

`data/lens-triggers.json` declares each lens's trigger and the parameter it
reads; the owner sets every parameter in the Process Policy, and the package
sets none.

- `changed_path_matches`: the lens joins when a path the frozen change adds,
  modifies or deletes matches a glob of its parameter, a glob list such as
  `src/auth/*,*secret*`. A glob matches as `fnmatch` does, so `*` also
  crosses a directory separator.
- `changed_lines_above`: the lens joins when the frozen change's lines added
  plus lines removed against its base, as `git diff --numstat` counts them
  outside `workspace/docs`, exceed its parameter.

By default `security-isolation` reads `risk_paths`, the owner's globs of
authorization, secrets, deletion and other owners' resources, and
`correctness-contract` and `conformance-tests` read `lens_changed_lines`. A
lens the data declares no trigger for always joins. A lens assignment joins
when any of its lenses does.

## Dispatch

1. Freeze the candidate and derive the code review manifest once, as the flow
   says. `delivery_verification.py` evaluates the triggers on the frozen
   change. When a lens joins, the manifest carries `code_review_panel` with
   only the joining assignments and its `lens_selection`: the changed lines
   and, per lens, whether it joined, its trigger, the matched paths or the
   threshold.
2. With `code_review_panel` in the manifest, dispatch, register, calibrate and
   merge the joining readers beside the official reviewer as at
   `beside_official`.
3. Without it no lens joined: the official reviewer reads alone and registers
   its result with `result`, as at `single_reader`, and `panel-result` and
   `merge-panel` refuse.

Each pass evaluates the triggers on the Item's whole frozen change against its
base, so a repair round keeps every lens an earlier round brought in unless
the repair shrinks the change below a trigger.

## Measurement

The project owner measures outside every task; no role acts on it. Record per
pass the `lens_selection`, the readers that read and every valid critical or
major finding later found that a left-out lens owns.
