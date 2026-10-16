# Run live tests once, at Delivery integration

These are the instructions of process switch `live_verification` at
`delivery_integration`, selected in the project's Process Policy. At
`every_pass`, every story QA pass runs its live groups as before.

## Condition for a live run in a QA pass

A story QA pass requires a live run only when `live_verification` is
`every_pass`, or when `item_live_gate` is `tooling_items` and the Item changes
environment tooling. At `delivery_integration` with the gate off:

- Run no `run --kind live_test` in the pass and cite no live record.
- The pass record carries `live: deferred_to_integration`; a final verdict
  without a live run is consistent and is not a gap.
- Judge the Item on its unit and fixture tests, the code review and the
  fresh-environment check.

## Delivery integration

After every Item integrates, the coordinator runs the project's integration
live run once (`live_test_command` with the group value the project declares
for it). It selects the groups whose paths changed since their last green run,
read from each group's live base on the integration branch, and every case not
yet passed; a green group records its live base there. Every finding is
repaired and re-run by impact before the Delivery closes, and the run's
findings report is cited at closure.
