# Reuse green live groups the delta cannot reach

These are the instructions of process switch `live_case_reuse` at
`delta_since_green`, selected in the project's Process Policy. At `off`, every
pass runs the live groups QA selects and nothing is reused.

## The impact map

The approved Verification Contract declares `live_group_paths` beside its live
groups: unique `<group>=<glob>` entries naming a declared group, and `*=<glob>`
entries for catch-all paths that change every group. Globs match repository
paths as `fnmatch` does, `*` crossing `/`. A group with no entry is never
reused. Name every path a group depends on, product code, fixtures, seed data
and the live adapter itself, and declare shared paths catch-all; an
incomplete map hides a regression in a reused group. The map is approved data:
a revision that changes it invalidates every recorded base.

## Bases

Each `run --kind live_test` that passes intact becomes the base of every group
it ran, kept in the Item's verification runtime across sessions. A run that
does not pass drops the base of every group it ran, so a group that failed is
never reused from an earlier green run.

A nightly or other full green run of an ancestor commit becomes a base the
same way: the coordinator runs `live-base --delivery <id> --story <id>
--commit <ancestor> --group <groups>` in the Item worktree, for example from a
scheduled job on the commit the Item was integrated on. It runs the approved
live command on that commit in a private clone, outside any reader, and only
an ancestor of the Item's candidate is accepted.

## A pass

The QA manifest's `live_reuse` names, per declared group, whether it may reuse
its base and why not, and `selected_groups`, the groups the delta since their
base selects; `start-pass` prints the same plan. Run the selected groups. The
run records each reusable group it does not run under `reused_live_base`,
with the base run's passed cases, and its approved command receives
`AGENTROF_LIVE_BASE`, a selection file with each group's base commit, and
`AGENTROF_LIVE_BASE_COMMIT` when every base shares one commit, so a project
runner can compute its own delta. `run --fresh` and a resumed attempt reuse
nothing.

A group reuses its base only while the base passed intact, is no older than
raw evidence may be, ran on an ancestor of the frozen candidate, ran the
approved live command and workdir for its groups under the same impact map and
in the declared environment of the run that reuses it, its output is
unchanged, and no path changed since the base matches one of its globs or a
catch-all glob. QA's final evidence check refuses a broken binding by name.

A finding that names its `live_group` may be closed on reused evidence: the
manifest's `carried_findings` marks it `close_on_reused_live` while its group
reuses its base and no file it names changed since that base. Any other open
finding is examined as before.

Reuse never replaces QA's independent assessment: QA still cites the run's
evidence, reads the reused cases and may run any group with `--fresh`.
